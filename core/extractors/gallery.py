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
from typing import Optional, List, Dict, Any, Tuple
from .base import BaseExtractor
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
        code, stdout, stderr = self.run_process(cmd, timeout=timeout)

        if tmp_cookie and os.path.exists(tmp_cookie):
            try:
                os.remove(tmp_cookie)
            except Exception:
                pass

        if code != 0 and not stdout.strip():
            raise RuntimeError(f"gallery-dl extract thất bại: {stderr.strip() or f'Exit code {code}'}")

        raw_entries = self._parse_json(stdout)
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
        timeout: int = 60,
    ) -> ProfileCrawlResult:
        """Quét profile / channel / subreddit / board"""
        if not self.is_available():
            raise RuntimeError("gallery-dl binary không được tìm thấy trên hệ thống.")

        args, tmp_cookie = self.get_base_args(target_url=profile_url, browser=browser)
        if range_start and range_end and range_end >= range_start:
            range_spec = f"{range_start}-{range_end}"
        elif limit and limit > 0:
            range_spec = f"1-{limit}"
        else:
            range_spec = ""

        cmd = [self.binary_path, *args, "-j"]
        if range_spec:
            cmd.extend(["--range", range_spec])
        cmd.append(profile_url)

        self.log(f"gallery-dl crawl ({range_spec or 'all'}): {profile_url}")
        code, stdout, stderr = self.run_process(cmd, timeout=timeout)

        if tmp_cookie and os.path.exists(tmp_cookie):
            try:
                os.remove(tmp_cookie)
            except Exception:
                pass

        raw_entries = self._parse_json(stdout)

        # Kiểm tra lỗi yêu cầu xác thực / cookies
        auth_err = False
        if any(isinstance(x, list) and len(x) >= 2 and x[0] == -1 and isinstance(x[1], dict) and "Auth" in str(x[1]) for x in (raw_entries or [])):
            auth_err = True
        if "AuthRequired" in stderr or "authenticated cookies needed" in stderr or "401 Unauthorized" in stderr or "KeyError: 'username'" in stderr:
            auth_err = True

        if auth_err:
            raise RuntimeError(
                f"Tài khoản hoặc trang này yêu cầu đăng nhập ({profile_url}). "
                f"Vui lòng đăng nhập tài khoản trên trình duyệt (Firefox / Edge) hoặc chọn đúng trình duyệt trên thanh tiêu đề để sử dụng Cookies."
            )

        # Kiểm tra nếu gallery-dl trả về thông điệp Message.Queue (code 6) mà chưa bóc tách media (code 3)
        has_media = any(isinstance(x, list) and len(x) >= 2 and x[0] == 3 for x in (raw_entries or []))
        queued_urls = [
            x[1] for x in (raw_entries or [])
            if isinstance(x, list) and len(x) >= 2 and x[0] == 6 and isinstance(x[1], str) and x[1] != profile_url
        ]
        if not has_media and queued_urls:
            child_url = queued_urls[0]
            self.log(f"gallery-dl chuyển tiếp URL con ({range_spec or 'all'}): {child_url}")
            return self.crawl_profile(
                child_url,
                limit=limit,
                media_type=media_type,
                browser=browser,
                range_start=range_start,
                range_end=range_end,
                timeout=timeout,
            )

        if not raw_entries:
            if code != 0:
                raise RuntimeError(f"gallery-dl crawl thất bại: {stderr.strip() or f'Exit code {code}'}")
            return ProfileCrawlResult(
                platform="social",
                name="Tài khoản",
                handle="@profile",
                url=profile_url,
                avatar="",
                stats="Đã quét 0 tệp",
                media=[],
                total_count=0,
            )

        return self._parse_crawl_result(raw_entries, profile_url, media_type)


    # ─────────────────────────────────────────────────────────────────────────
    # Normalizers
    # ─────────────────────────────────────────────────────────────────────────

    def _normalize_gallery(self, raw_entries: List[Any], original_url: str) -> MediaMetadata:
        images: List[MediaImage] = []
        category = "social"
        author = "Người dùng"
        author_url = original_url
        title = "Bộ sưu tập đa phương tiện"
        description = ""

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

                media_title = meta.get("filename") or meta.get("title") or (f"Video {index}" if is_video else f"Ảnh {index}")
                res = f"{meta['width']}x{meta['height']}" if meta.get("width") and meta.get("height") else ("Video HD" if is_video else "Ảnh HD")
                size_str = self.format_bytes(meta["filesize"]) if meta.get("filesize") else "Tự động"
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
                    )
                )
                index += 1

        if not images:
            raise ValueError("Không thể bóc tách được hình ảnh nào từ album này.")

        is_single_video = len(images) == 1 and images[0].type == "video"
        streams = []
        if is_single_video:
            streams.append(
                StreamFormat(
                    format_id="original_video",
                    quality=f"{images[0].resolution or 'HD 1080p'} — Video gốc chất lượng cao",
                    format=(images[0].ext or "mp4").upper(),
                    size=images[0].size or "Tự động",
                    stream_type="full",
                    has_audio=True,
                    has_video=True,
                    url=images[0].url,
                )
            )

        first_thumb = images[0].thumb or images[0].url
        platform = category.lower()
        if "twitter" in platform or "x.com" in platform:
            platform = "x"

        return MediaMetadata(
            id=str(int(os.times().system * 1000)),
            platform=platform,
            title=images[0].title if is_single_video else f"{title} ({len(images)} tệp)",
            author=author,
            author_url=author_url,
            duration="1 video" if is_single_video else f"{len(images)} hình ảnh",
            views="Chất lượng gốc",
            thumbnail=first_thumb,
            high_res_thumbnail=first_thumb,
            type="video" if is_single_video else "album",
            original_url=original_url,
            description=description or None,
            streams=streams if streams else None,
            images=images,
        )

    def _parse_crawl_result(self, raw_entries: List[Any], profile_url: str, media_type: str) -> ProfileCrawlResult:
        media: List[CrawlMediaItem] = []
        platform = "social"
        author = "Người dùng"
        avatar = ""

        idx = 1
        for item in raw_entries:
            if not isinstance(item, list) or len(item) < 2:
                continue

            if item[0] == 2 and isinstance(item[1], dict):
                meta = item[1]
                if meta.get("category"):
                    platform = meta["category"]
                found_author = self._extract_author(meta)
                if found_author:
                    author = found_author
                found_avatar = self._extract_avatar(meta)
                if found_avatar:
                    avatar = found_avatar

            elif item[0] == 3 and len(item) >= 2:
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

                item_title = meta.get("title") or meta.get("filename") or (f"Video {idx}" if is_video else f"Hình ảnh {idx}")
                thumb_url = meta.get("display_url") or (meta.get("thumbnail") if is_video else media_url) or media_url

                media.append(
                    CrawlMediaItem(
                        id=idx,
                        type="video" if is_video else "image",
                        title=str(item_title),
                        thumb=thumb_url,
                        url=media_url,
                        duration="Video" if is_video else "Ảnh HD",
                        quality=f"{meta['width']}x{meta['height']}" if meta.get("width") and meta.get("height") else "HD",
                        size=self.format_bytes(meta["filesize"]) if meta.get("filesize") else "Tự động",
                        author=author,
                    )
                )
                idx += 1

        clean_handle = f"@{author.lower().replace(' ', '')}" if author else "@profile"

        return ProfileCrawlResult(
            platform=platform,
            name=author,
            handle=clean_handle,
            url=profile_url,
            avatar=avatar,
            stats=f"Đã quét {len(media)} tệp phương tiện",
            media=media,
            total_count=len(media),
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────────

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
    def format_bytes(b: int) -> str:
        if not b or b <= 0:
            return "0 B"
        sizes = ["B", "KB", "MB", "GB"]
        i = int(math.floor(math.log(b, 1024)))
        p = math.pow(1024, i)
        s = round(b / p, 1)
        return f"{s} {sizes[i]}"
