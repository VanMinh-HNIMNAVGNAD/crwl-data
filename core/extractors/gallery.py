"""
gallery-dl Extractor Engine.
Wraps gallery-dl binary to extract rich image albums, photo sets, and profile crawl data
(Instagram, Pinterest, Reddit, Threads, Bluesky, Tumblr, Pixiv, etc.).
Ported from GalleryDlService into standalone Python.
"""

import json
import math
import os
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from typing import Optional, List, Dict, Any, Tuple
from .base import BaseExtractor
from .ytdlp import YtDlpExtractor
from ..cancellation import attach_request, current_request_id, detach_request, raise_if_cancelled
from ..models import (
    MediaMetadata,
    MediaImage,
    StreamFormat,
    CrawlMediaItem,
    ProfileCrawlResult,
)
from ..cookies.browser_cookies import get_browser_cookies_txt


class GalleryDlExtractor(BaseExtractor):
    """Wrapper cho gallery-dl binary"""

    # ── Quét theo BÀI ĐĂNG thay vì theo TỆP ──────────────────────────────
    # gallery-dl `--range` đếm theo TỆP. Với tài khoản nhiều ảnh mỗi bài
    # (carousel Instagram, album Facebook, post nhiều ảnh trên X/Bluesky),
    # "--range 1-20" có thể chỉ phủ hết 4 bài rồi CẮT NGANG bài thứ 5 — người
    # dùng thấy bài đăng bị thiếu ảnh. Nay số lượng người dùng nhập được hiểu là
    # SỐ BÀI ĐĂNG, còn mọi tệp bên trong mỗi bài đều được giữ đủ.

    # Những extractor đọc được `max-posts` (giới hạn chính xác theo bài đăng).
    _MAX_POSTS_CATEGORIES = frozenset({
        "instagram", "pixiv", "kemono", "artstation", "pawchive",
    })

    # Với extractor không hỗ trợ `max-posts`, nới rộng --range theo số tệp ước
    # lượng mỗi bài rồi cắt lại đúng số bài ở phía chúng ta.
    _FILES_PER_POST_HEADROOM = int(os.environ.get("CRWL_FILES_PER_POST", "5"))
    _MAX_RANGE_FILES = int(os.environ.get("CRWL_MAX_RANGE_FILES", "800"))

    # Bung bài đăng bị thiếu tệp bằng cách bóc tách riêng từng bài. Mỗi lần bung
    # là một tiến trình gallery-dl nên phải chặn trên cả số lượng lẫn song song.
    _MAX_EXPAND_POSTS = int(os.environ.get("CRWL_MAX_EXPAND_POSTS", "40"))
    _EXPAND_WORKERS = max(1, int(os.environ.get("CRWL_EXPAND_WORKERS", "3")))
    # Ngân sách thời gian cho toàn bộ việc bung bài đăng. Hết ngân sách thì dừng
    # và trả về những gì đã có — thà thiếu vài bài còn hơn để Rust sidecar timeout
    # rồi mất trắng cả lượt quét.
    _EXPAND_BUDGET_SEC = float(os.environ.get("CRWL_EXPAND_BUDGET_SEC", "120"))
    _EXPAND_POST_TIMEOUT = int(os.environ.get("CRWL_EXPAND_POST_TIMEOUT", "30"))

    # Khoá nhận diện bài đăng, theo thứ tự ưu tiên (đủ dùng cho instagram,
    # twitter/X, bluesky, facebook, reddit, pinterest, threads, tumblr...).
    _POST_KEY_FIELDS = (
        "post_url", "post_shortcode", "post_id", "sidecar_media_id",
        "tweet_id", "uri", "shortcode", "gallery_id", "album_id",
    )

    def __init__(self):
        super().__init__()
        self.binary_path = self.find_binary("gallery-dl", "GALLERY_DL_PATH")

    def is_available(self) -> bool:
        return self.binary_path is not None and os.path.exists(self.binary_path)

    def get_base_args(self, target_url: str = "", browser: Optional[str] = None) -> Tuple[List[str], Optional[str]]:
        args = [
            "--sleep-request", "0",
        ]
        tmp_cookie_file = None

        if browser != "none":
            domain = None
            if target_url:
                import urllib.parse
                try:
                    parsed = urllib.parse.urlparse(target_url)
                    domain = (parsed.hostname or parsed.netloc or None)
                except Exception:
                    pass
            exported = get_browser_cookies_txt(browser or "auto", domain=domain)
            if exported and os.path.exists(exported):
                args.extend(["--cookies", exported])
                tmp_cookie_file = exported

        return args, tmp_cookie_file

    def extract_gallery(self, url: str, browser: Optional[str] = None, timeout: int = 30) -> MediaMetadata:
        """Trích xuất danh sách phương tiện (ảnh + video) từ bài viết/album đơn lẻ"""
        if not self.is_available():
            raise RuntimeError("gallery-dl binary không được tìm thấy trên hệ thống.")

        args, tmp_cookie = self.get_base_args(target_url=url, browser=browser)
        cmd = [self.binary_path, *args, "-j", url]

        self.log(f"gallery-dl extract: {url}")
        try:
            code, stdout, stderr = self.run_process(cmd, timeout=timeout)
        finally:
            self._cleanup_cookie(tmp_cookie)

        if code != 0 and not stdout.strip():
            raise RuntimeError(f"gallery-dl extract thất bại: {stderr.strip() or f'Exit code {code}'}")

        raw_entries = self._parse_json(stdout)

        auth_reason = self._detect_auth_error(raw_entries, stderr)
        if auth_reason:
            raise RuntimeError(self._login_required_message(url, auth_reason))

        if not raw_entries:
            raise ValueError("Không tìm thấy dữ liệu phương tiện từ liên kết qua gallery-dl.")

        return self._normalize_gallery(raw_entries, url)

    def crawl_profile(
        self,
        profile_url: str,
        limit: int = 50,
        media_type: str = "all",  # 'all' | 'video' | 'image'
        browser: Optional[str] = None,
        range_start: Optional[int] = None,
        range_end: Optional[int] = None,
        timeout: Optional[int] = None,
        _depth: int = 0,
    ) -> ProfileCrawlResult:
        """Quét profile / channel / subreddit / board"""
        if not self.is_available():
            raise RuntimeError("gallery-dl binary không được tìm thấy trên hệ thống.")

        # Quét càng nhiều bài thì càng cần nhiều thời gian; một mốc cố định khiến
        # lựa chọn "Tất cả" luôn chết vì timeout.
        if timeout is None:
            timeout = self._timeout_for(limit, range_start, range_end)

        args, tmp_cookie = self.get_base_args(target_url=profile_url, browser=browser)

        # `range_start`/`range_end` và `limit` nay tính theo BÀI ĐĂNG.
        if range_start and range_end and range_end >= range_start:
            first_post, last_post = range_start, range_end
        elif limit and limit > 0:
            first_post, last_post = 1, limit
        else:
            first_post, last_post = 1, 0  # 0 = quét toàn bộ

        cmd = [self.binary_path, *args, "-j"]
        category = self._category_of(profile_url)
        limit_desc = "all"

        if last_post > 0:
            if category in self._MAX_POSTS_CATEGORIES:
                # Giới hạn chính xác theo bài đăng; KHÔNG dùng --range để không
                # cắt ngang carousel.
                cmd.extend(["-o", f"max-posts={last_post}"])
                limit_desc = f"max-posts={last_post}"
            else:
                budget = min(
                    self._MAX_RANGE_FILES,
                    max(last_post, last_post * self._FILES_PER_POST_HEADROOM),
                )
                cmd.extend(["--range", f"1-{budget}"])
                limit_desc = f"posts 1-{last_post} (<= {budget} tệp)"

        cmd.append(profile_url)

        self.log(f"gallery-dl crawl ({limit_desc}): {profile_url}")
        try:
            code, stdout, stderr = self.run_process(cmd, timeout=timeout)
        finally:
            self._cleanup_cookie(tmp_cookie)

        raw_entries = self._parse_json(stdout)

        # gallery-dl -j báo lỗi qua entry [-1, {...}] trên STDOUT (không phải stderr),
        # nên phải soi cả hai nguồn mới nhận ra được trường hợp thiếu cookie đăng nhập.
        auth_reason = self._detect_auth_error(raw_entries, stderr)
        if auth_reason:
            raise RuntimeError(self._login_required_message(profile_url, auth_reason))

        # Kiểm tra nếu gallery-dl trả về thông điệp Message.Queue (code 6) mà chưa bóc tách media (code 3)
        has_media = any(isinstance(x, list) and len(x) >= 2 and x[0] == 3 for x in (raw_entries or []))
        queued_urls = [
            x[1] for x in (raw_entries or [])
            if isinstance(x, list) and len(x) >= 2 and x[0] == 6 and isinstance(x[1], str) and x[1] != profile_url
        ]
        if not has_media and queued_urls:
            if _depth >= 3:
                raise RuntimeError(
                    f"gallery-dl chuyển tiếp URL quá nhiều lần mà không ra media: {profile_url}"
                )
            child_url = queued_urls[0]
            self.log(f"gallery-dl chuyển tiếp URL con ({limit_desc}): {child_url}")
            return self.crawl_profile(
                child_url,
                limit=limit,
                media_type=media_type,
                browser=browser,
                range_start=range_start,
                range_end=range_end,
                timeout=timeout,
                _depth=_depth + 1,
            )

        if not raw_entries:
            if code == -1 and "Timeout" in (stderr or ""):
                raise RuntimeError(
                    f"Quét quá thời gian {timeout}s. Hãy giảm số lượng cần quét hoặc chọn 'Khoảng' nhỏ hơn."
                )
            if code != 0:
                raise RuntimeError(f"gallery-dl crawl thất bại: {stderr.strip() or f'Exit code {code}'}")
            return ProfileCrawlResult(
                platform="social",
                name=None,
                handle=None,
                url=profile_url,
                avatar=None,
                stats="Đã quét 0 tệp",
                media=[],
                total_count=0,
            )

        # Gom tệp theo bài đăng → cắt đúng số bài người dùng yêu cầu → bung
        # những bài còn thiếu tệp. Nhờ vậy mỗi bài đăng giữ được ĐỦ ảnh/video
        # thay vì chỉ một ảnh đại diện.
        groups = self._group_by_post(raw_entries)
        if last_post > 0:
            groups = groups[first_post - 1:last_post]
        groups = self._expand_incomplete_posts(groups, browser)

        return self._parse_crawl_result(groups, raw_entries, profile_url, media_type)


    # ─────────────────────────────────────────────────────────────────────────
    # Gom nhóm theo bài đăng & bung bài đăng bị thiếu tệp
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _category_of(url: str) -> str:
        """Đoán category gallery-dl từ hostname (instagram, twitter, ...)."""
        host = ""
        try:
            raw = (url or "").strip()
            if not raw.lower().startswith(("http://", "https://")):
                raw = "https://" + raw
            host = (urllib.parse.urlparse(raw).hostname or "").lower()
        except Exception:
            return ""
        table = {
            "instagram.com": "instagram", "instagr.am": "instagram",
            "x.com": "twitter", "twitter.com": "twitter",
            "facebook.com": "facebook", "fb.com": "facebook",
            "pinterest.com": "pinterest", "pin.it": "pinterest",
            "reddit.com": "reddit", "redd.it": "reddit",
            "threads.net": "threads", "threads.com": "threads",
            "bsky.app": "bluesky", "tumblr.com": "tumblr",
            "pixiv.net": "pixiv", "artstation.com": "artstation",
            "kemono.su": "kemono", "kemono.party": "kemono",
            "weibo.com": "weibo", "deviantart.com": "deviantart",
        }
        for domain, category in table.items():
            if host == domain or host.endswith("." + domain):
                return category
        return ""

    @classmethod
    def _post_key(cls, meta: Dict[str, Any], fallback: str) -> str:
        """Khoá nhận diện bài đăng chứa tệp này."""
        if isinstance(meta, dict):
            for field in cls._POST_KEY_FIELDS:
                value = meta.get(field)
                if isinstance(value, (str, int)) and str(value).strip():
                    return f"{field}:{value}"
        return fallback

    @classmethod
    def _post_url_of(cls, meta: Dict[str, Any]) -> Optional[str]:
        """URL của bài đăng, dùng để bóc tách lại toàn bộ tệp bên trong."""
        if not isinstance(meta, dict):
            return None
        for field in ("post_url", "webpage_url", "page_url", "permalink"):
            value = meta.get(field)
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                return value
        shortcode = meta.get("post_shortcode") or meta.get("shortcode")
        category = str(meta.get("category") or "").lower()
        if shortcode and category == "instagram":
            return f"https://www.instagram.com/p/{shortcode}/"

        # Bluesky chỉ đưa ra `uri` dạng at:// — dựng lại URL web từ handle tác giả.
        if category == "bluesky":
            uri = str(meta.get("uri") or "")
            author = meta.get("author") if isinstance(meta.get("author"), dict) else {}
            handle = author.get("handle") or meta.get("username")
            rkey = uri.rsplit("/", 1)[-1] if uri else ""
            if handle and rkey:
                return f"https://bsky.app/profile/{handle}/post/{rkey}"

        # X/Twitter: gallery-dl đặt tweet_id + author.name
        if category == "twitter":
            tweet_id = meta.get("tweet_id") or meta.get("post_id")
            author = meta.get("author") if isinstance(meta.get("author"), dict) else {}
            handle = author.get("name") or meta.get("username")
            if tweet_id and handle:
                return f"https://x.com/{handle}/status/{tweet_id}"

        return None

    def _group_by_post(self, raw_entries: Optional[List[Any]]) -> List[Dict[str, Any]]:
        """Gom các entry code-3 thành từng bài đăng, giữ nguyên thứ tự xuất hiện."""
        order: List[str] = []
        buckets: Dict[str, Dict[str, Any]] = {}

        for position, item in enumerate(raw_entries or []):
            if not (isinstance(item, list) and len(item) >= 2 and item[0] == 3):
                continue
            meta = item[2] if len(item) > 2 and isinstance(item[2], dict) else {}
            key = self._post_key(meta, f"_entry_{position}")
            if key not in buckets:
                order.append(key)
                buckets[key] = {"key": key, "entries": [], "url": self._post_url_of(meta)}
            bucket = buckets[key]
            bucket["entries"].append(item)
            if not bucket["url"]:
                bucket["url"] = self._post_url_of(meta)

        return [buckets[k] for k in order]

    @staticmethod
    def _declared_file_count(entries: List[Any]) -> int:
        """Số tệp mà gallery-dl KHAI BÁO bài đăng này có (`count` / `num`)."""
        declared = 0
        for entry in entries:
            meta = entry[2] if len(entry) > 2 and isinstance(entry[2], dict) else {}
            for field in ("count", "num"):
                try:
                    declared = max(declared, int(meta.get(field) or 0))
                except (TypeError, ValueError):
                    continue
        return declared

    @classmethod
    def _is_incomplete_post(cls, entries: List[Any]) -> bool:
        """Bài đăng này có bị trả thiếu tệp không?

        Hai nguồn thiếu tệp đã gặp thực tế:
          1. `count`/`num` khai báo nhiều hơn số tệp nhận được — xảy ra khi
             `--range` cắt ngang bài đăng.
          2. Instagram qua GraphQL khi phiên đăng nhập không đầy đủ: bài carousel
             (`typename == "GraphSidecar"`) không kèm `edge_sidecar_to_children`
             nên gallery-dl chỉ phát ra ĐÚNG MỘT tệp — chính là ảnh bìa.
        """
        seen = len(entries)
        if seen == 0:
            return False
        if cls._declared_file_count(entries) > seen:
            return True
        if seen > 1:
            return False
        meta = entries[0][2] if len(entries[0]) > 2 and isinstance(entries[0][2], dict) else {}
        # `typename` chỉ do nhánh GraphQL của Instagram đặt. Một bài GraphSidecar
        # thật luôn có từ 2 ảnh trở lên, nên thấy đúng 1 tệp nghĩa là children đã
        # bị lược bỏ và ta đang cầm ảnh bìa.
        #
        # KHÔNG dùng `sidecar_media_id` làm dấu hiệu: nhánh REST chỉ đặt nó khi
        # `carousel_media` CÓ MẶT — mà lúc đó mọi ảnh đã được bung sẵn. Bài
        # carousel đúng 1 ảnh sẽ bị bung lại vô ích, tốn thêm một tiến trình
        # gallery-dl cho mỗi bài.
        return str(meta.get("typename") or "") == "GraphSidecar"

    def _fetch_post_entries(self, post_url: str, browser: Optional[str]) -> List[Any]:
        """Bóc tách riêng một bài đăng để lấy ĐẦY ĐỦ tệp bên trong."""
        args, tmp_cookie = self.get_base_args(target_url=post_url, browser=browser)
        cmd = [self.binary_path, *args, "-j", post_url]
        try:
            code, stdout, stderr = self.run_process(cmd, timeout=self._EXPAND_POST_TIMEOUT)
        finally:
            self._cleanup_cookie(tmp_cookie)

        if code != 0 and not (stdout or "").strip():
            self.warn(f"Không bung được bài đăng {post_url}: {(stderr or '').strip()[:120]}")
            return []
        parsed = self._parse_json(stdout)
        if self._detect_auth_error(parsed, stderr):
            return []
        return [
            x for x in (parsed or [])
            if isinstance(x, list) and len(x) >= 2 and x[0] == 3
        ]

    def _expand_incomplete_posts(
        self,
        groups: List[Dict[str, Any]],
        browser: Optional[str],
    ) -> List[Dict[str, Any]]:
        """Bóc tách lại những bài đăng bị trả thiếu tệp, thay tại chỗ."""
        targets = [
            g for g in groups
            if g.get("url") and self._is_incomplete_post(g["entries"])
        ]
        if not targets:
            return groups

        capped = targets[: self._MAX_EXPAND_POSTS]
        if len(targets) > len(capped):
            self.warn(
                f"Có {len(targets)} bài đăng thiếu tệp, chỉ bung {len(capped)} bài đầu "
                f"(đặt CRWL_MAX_EXPAND_POSTS để nới giới hạn)."
            )
        self.log(f"Đang bung {len(capped)} bài đăng để lấy đủ ảnh bên trong...")

        raise_if_cancelled()
        parent_req = current_request_id()
        deadline = time.monotonic() + self._EXPAND_BUDGET_SEC
        skipped = 0

        def worker(group: Dict[str, Any]) -> Tuple[Dict[str, Any], List[Any]]:
            # Luồng con sinh ra với req_id rỗng; không gắn lại thì tiến trình
            # gallery-dl nó tạo sẽ không nằm trong sổ huỷ và nút "Hủy" vô tác dụng.
            previous = attach_request(parent_req)
            try:
                if time.monotonic() >= deadline:
                    return group, []
                raise_if_cancelled()
                return group, self._fetch_post_entries(group["url"], browser)
            except Exception as err:  # một bài lỗi không được làm hỏng cả lượt quét
                self.warn(f"Bung bài đăng thất bại ({group.get('url')}): {err}")
                return group, []
            finally:
                detach_request(previous)

        workers = min(self._EXPAND_WORKERS, len(capped))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="crwl-post") as pool:
            for group, entries in pool.map(worker, capped):
                if len(entries) > len(group["entries"]):
                    self.log(
                        f"Bài đăng {group.get('url')}: {len(group['entries'])} → {len(entries)} tệp"
                    )
                    group["entries"] = entries
                elif not entries:
                    skipped += 1

        if skipped:
            self.warn(f"{skipped} bài đăng chưa bung được (hết thời gian hoặc lỗi mạng).")

        raise_if_cancelled()
        return groups

    # ─────────────────────────────────────────────────────────────────────────
    # Normalizers
    # ─────────────────────────────────────────────────────────────────────────

    def _normalize_gallery(self, raw_entries: List[Any], original_url: str) -> MediaMetadata:
        images: List[MediaImage] = []
        category = "social"
        author = None
        author_url = None
        title = None
        description = None

        index = 1
        for item in raw_entries:
            if not isinstance(item, list) or len(item) < 2:
                continue
            code_type = item[0]

            if code_type == 2 and isinstance(item[1], dict):
                meta = item[1]
                category = meta.get("category") or category
                raw_author = self._extract_author(meta)
                if raw_author:
                    author = raw_author
                raw_author_url = meta.get("author", {}).get("url") if isinstance(meta.get("author"), dict) else None
                if raw_author_url:
                    author_url = raw_author_url
                raw_title = meta.get("title") or meta.get("grid_title") or meta.get("text")
                if raw_title:
                    title = str(raw_title).strip()
                raw_desc = meta.get("description") or meta.get("text") or ""
                if raw_desc:
                    description = str(raw_desc).strip()

            elif code_type == 3 and len(item) >= 2:
                raw_url = item[1]
                meta = item[2] if len(item) > 2 and isinstance(item[2], dict) else {}
                media_url = meta.get("video_url") or raw_url

                if isinstance(media_url, str) and media_url.startswith("ytdl:"):
                    reddit_fb = meta.get("media", {}).get("reddit_video", {}).get("fallback_url") if isinstance(meta.get("media"), dict) else None
                    media_url = reddit_fb or media_url.replace("ytdl:", "")

                ext = (meta.get("extension") or meta.get("ext") or self._extract_ext(media_url) or "jpg").lower()
                is_video = ext in ("mp4", "webm", "mov", "m4v", "m3u8", "ts") or bool(meta.get("video_url")) or bool(meta.get("is_video"))
                is_gif = ext == "gif"
                item_type = "video" if is_video else ("gif" if is_gif else "image")

                media_title = meta.get("filename") or meta.get("title") or None
                res = f"{meta['width']}x{meta['height']}" if meta.get("width") and meta.get("height") else None
                size_str = self.format_bytes(meta["filesize"]) if meta.get("filesize") else None
                thumb = meta.get("display_url") or meta.get("thumbnail") or (None if is_video else media_url)

                if meta.get("category"):
                    category = meta["category"]

                images.append(
                    MediaImage(
                        id=index,
                        url=media_url,
                        title=media_title,
                        resolution=res,
                        size=size_str,
                        type=item_type,
                        ext=ext,
                        thumb=thumb,
                        duration=self._duration_of(meta) if is_video else None,
                    )
                )
                index += 1

        if not images:
            raise ValueError("Không thể bóc tách được hình ảnh nào từ album này.")

        is_single_video = len(images) == 1 and images[0].type == "video"
        streams = []
        if is_single_video:
            quality_str = f"{images[0].resolution} — Video gốc chất lượng cao" if images[0].resolution else "Video gốc chất lượng cao"
            streams.append(
                StreamFormat(
                    format_id="original_video",
                    quality=quality_str,
                    format=(images[0].ext or "mp4").upper(),
                    size=images[0].size,
                    raw_size=None,
                    stream_type="full",
                    has_audio=True,
                    has_video=True,
                    url=images[0].url,
                )
            )

        # Thumbnail phải là ẢNH. Không bao giờ fallback sang URL video: webview
        # (WebKitGTK) giải mã file .mp4 đặt trong <img> thành toàn bộ khung hình
        # thô trong RAM — một video 720p dài 1 phút đã tốn vài GB và treo cả máy.
        first_thumb = next(
            (img.thumb or img.url for img in images if img.type != "video"),
            next((img.thumb for img in images if img.thumb), None),
        )
        platform = category.lower()
        if "twitter" in platform or "x.com" in platform:
            platform = "x"

        final_title = images[0].title if is_single_video else (f"{title} ({len(images)} tệp)" if title else None)

        return MediaMetadata(
            id=str(abs(hash(original_url))) if original_url else str(int(time.time() * 1000)),
            platform=platform,
            title=final_title,
            author=author,
            author_url=author_url,
            duration=None,
            views=None,
            thumbnail=first_thumb,
            high_res_thumbnail=first_thumb,
            type="video" if is_single_video else "album",
            original_url=original_url,
            description=description or None,
            streams=streams if streams else None,
            images=images,
        )

    def _parse_crawl_result(
        self,
        groups: List[Dict[str, Any]],
        raw_entries: Optional[List[Any]],
        profile_url: str,
        media_type: str,
    ) -> ProfileCrawlResult:
        media: List[CrawlMediaItem] = []
        platform = "social"
        author = None
        avatar = None

        # Thông tin tài khoản nằm ở entry code-2 (Message.Directory).
        for item in (raw_entries or []):
            if isinstance(item, list) and len(item) >= 2 and item[0] == 2 and isinstance(item[1], dict):
                meta = item[1]
                if meta.get("category"):
                    platform = meta["category"]
                found_author = self._extract_author(meta)
                if found_author:
                    author = found_author
                found_avatar = self._extract_avatar(meta)
                if found_avatar:
                    avatar = found_avatar

        idx = 1
        post_count = 0
        for group in groups:
            entries = group.get("entries") or []
            post_url = group.get("url")
            total_in_post = len(entries)
            kept_in_post = 0

            for position, item in enumerate(entries, 1):
                raw_url = item[1]
                meta = item[2] if len(item) > 2 and isinstance(item[2], dict) else {}
                media_url = meta.get("video_url") or raw_url
                ext = (meta.get("extension") or meta.get("ext") or self._extract_ext(media_url) or "").lower()
                is_video = ext in ("mp4", "webm", "mov", "m4v", "m3u8", "ts") or bool(meta.get("video_url"))

                if media_type == "video" and not is_video:
                    continue
                if media_type == "image" and is_video:
                    continue

                if meta.get("category"):
                    platform = meta["category"]
                item_author = self._extract_author(meta)
                if item_author:
                    author = item_author
                item_avatar = self._extract_avatar(meta)
                if item_avatar:
                    avatar = item_avatar

                item_title = meta.get("title") or meta.get("filename") or None
                # Video không có ảnh poster thì để trống, KHÔNG dùng chính URL video
                # (xem chú thích ở _normalize_gallery).
                thumb_url = meta.get("display_url") or (meta.get("thumbnail") if is_video else media_url) or None

                quality_str = f"{meta['width']}x{meta['height']}" if meta.get("width") and meta.get("height") else None
                size_str = self.format_bytes(meta["filesize"]) if meta.get("filesize") else None

                try:
                    num_in_post = int(meta.get("num") or position)
                except (TypeError, ValueError):
                    num_in_post = position

                media.append(
                    CrawlMediaItem(
                        id=idx,
                        type="video" if is_video else "image",
                        title=item_title,
                        thumb=thumb_url,
                        url=media_url,
                        duration=self._duration_of(meta) if is_video else None,
                        quality=quality_str,
                        size=size_str,
                        author=author,
                        post_id=str(meta.get("post_shortcode") or meta.get("post_id") or "") or None,
                        post_url=post_url,
                        index_in_post=num_in_post if total_in_post > 1 else None,
                        total_in_post=total_in_post if total_in_post > 1 else None,
                    )
                )
                idx += 1
                kept_in_post += 1

            if kept_in_post:
                post_count += 1

        clean_handle = f"@{author.lower().replace(' ', '')}" if author else None
        stats = f"Đã quét {len(media)} tệp phương tiện"
        if post_count:
            stats += f" từ {post_count} bài đăng"

        return ProfileCrawlResult(
            platform=platform,
            name=author,
            handle=clean_handle,
            url=profile_url,
            avatar=avatar,
            stats=stats,
            media=media,
            total_count=len(media),
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _cleanup_cookie(path: Optional[str]) -> None:
        """Xoá file cookie tạm dù request thành công hay ném lỗi."""
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass

    @staticmethod
    def _timeout_for(limit: int, range_start: Optional[int], range_end: Optional[int]) -> int:
        """Ước lượng thời gian chờ theo số lượng mục cần quét (giây)."""
        if range_start and range_end and range_end >= range_start:
            count = range_end - range_start + 1
        elif limit and limit > 0:
            count = limit
        else:
            count = 0  # 0 = quét toàn bộ
        if count <= 0:
            return 540
        # `count` nay là SỐ BÀI ĐĂNG, mỗi bài có thể chứa nhiều tệp nên tốn thời
        # gian hơn trước. ~3s mỗi bài, kẹp trong khoảng 90s..540s.
        return max(90, min(540, 60 + int(count * 3)))

    # Dấu hiệu gallery-dl gặp lỗi do thiếu cookie đăng nhập.
    # 'username' KeyError là cách Instagram báo "chưa đăng nhập" khi bóc tách profile.
    _AUTH_SIGNATURES = (
        "authrequired", "authenticationerror", "authorizationerror",
        "authentication required", "authenticated cookies needed",
        "login required", "please login", "must be logged in",
        "401 unauthorized", "403 forbidden", "http 401", "http 403",
        "keyerror: 'username'",
        "account is private", "private account",
    )

    @classmethod
    def _detect_auth_error(cls, raw_entries: Optional[List[Any]], stderr: str) -> Optional[str]:
        """Trả về mô tả lỗi nếu gallery-dl thất bại vì thiếu quyền đăng nhập."""
        haystacks: List[str] = []

        for x in (raw_entries or []):
            if not (isinstance(x, list) and len(x) >= 2 and x[0] == -1):
                continue
            payload = x[1]
            if isinstance(payload, dict):
                # gallery-dl trả lỗi dạng {"error": "KeyError", "message": "'username'"}.
                # Ghép lại thành "keyerror: 'username'" để so khớp CHÍNH XÁC, thay vì
                # bắt bừa chuỗi 'username' ở bất kỳ đâu (gây báo nhầm "cần đăng nhập").
                err = str(payload.get("error") or "")
                msg = str(payload.get("message") or "")
                haystacks.append(f"{err}: {msg}".strip(": ") or str(payload))
            else:
                haystacks.append(str(payload))

        if stderr:
            haystacks.append(stderr)

        for text in haystacks:
            low = text.lower()
            if any(sig in low for sig in cls._AUTH_SIGNATURES):
                return text.strip()[:200]
        return None

    @staticmethod
    def _login_required_message(target_url: str, reason: str) -> str:
        lower = (target_url or "").lower()
        if "instagram.com" in lower or "instagr.am" in lower:
            name, tab = "Instagram", "Instagram"
        elif "facebook.com" in lower or "fb.com" in lower or "fb.watch" in lower:
            name, tab = "Facebook", "Facebook"
        elif "threads.net" in lower:
            name, tab = "Threads", "Threads"
        elif "x.com" in lower or "twitter.com" in lower:
            name, tab = "X (Twitter)", "Twitter"
        elif "reddit.com" in lower or "redd.it" in lower:
            name, tab = "Reddit", "Reddit"
        elif "tiktok.com" in lower:
            name, tab = "TikTok", "TikTok"
        else:
            name, tab = "Trang này", "nền tảng tương ứng"
        return (
            f"{name} yêu cầu đăng nhập để xem nội dung này. "
            f"Hãy đăng nhập {name} trên Firefox/Edge rồi thử lại, "
            f"hoặc mở Cookie Manager (🍪) → tab {tab} và dán cookie từ trình duyệt. "
            f"(Chi tiết: {reason})"
        )

    @staticmethod
    def _parse_json(stdout_data: str) -> Optional[List[Any]]:
        if not stdout_data or not stdout_data.strip():
            return None
        trimmed = stdout_data.strip()
        s_idx = trimmed.find("[")
        e_idx = trimmed.rfind("]")
        if s_idx != -1 and e_idx != -1 and e_idx > s_idx:
            try:
                parsed = json.loads(trimmed[s_idx : e_idx + 1])
                return parsed if isinstance(parsed, list) else None
            except Exception:
                return None
        return None

    @staticmethod
    def _extract_author(meta: Dict[str, Any]) -> Optional[str]:
        if not isinstance(meta, dict):
            return None
        for key in ("author", "user", "uploader", "owner", "pinner", "account"):
            val = meta.get(key)
            if isinstance(val, dict):
                return val.get("name") or val.get("username") or val.get("nick")
            if isinstance(val, str) and val.strip():
                return val.strip()
        return meta.get("username") or meta.get("nick")

    @staticmethod
    def _extract_avatar(meta: Dict[str, Any]) -> Optional[str]:
        if not isinstance(meta, dict):
            return None
        for key in ("author", "user", "owner", "pinner"):
            val = meta.get(key)
            if isinstance(val, dict):
                return val.get("avatar") or val.get("profile_image_url") or val.get("image_url")
        return meta.get("avatar_url") or meta.get("profile_pic_url")

    @staticmethod
    def _duration_of(meta: Dict[str, Any]) -> Optional[str]:
        try:
            return YtDlpExtractor.format_duration(float(meta.get("duration") or 0))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _extract_ext(url: str) -> Optional[str]:
        try:
            clean = url.split("?")[0].split("#")[0]
            ext = clean.split(".")[-1].lower()
            if 2 <= len(ext) <= 5 and ext.isalnum():
                return ext
        except Exception:
            pass
        return None

    @staticmethod
    def format_bytes(b: Any) -> str:
        try:
            b = int(b)
        except (TypeError, ValueError):
            return "0 B"
        if b <= 0:
            return "0 B"
        sizes = ["B", "KB", "MB", "GB", "TB", "PB"]
        # Kẹp chỉ số trong phạm vi `sizes`, nếu không tệp >= 1 TB sẽ gây IndexError
        i = max(0, min(int(math.floor(math.log(b, 1024))), len(sizes) - 1))
        p = math.pow(1024, i)
        s = round(b / p, 1)
        return f"{s} {sizes[i]}"
