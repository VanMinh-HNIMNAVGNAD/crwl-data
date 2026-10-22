"""
Media Dispatcher Engine.
Coordinates intelligent platform extraction, prioritization, and automatic fallbacks
across yt-dlp, gallery-dl, tiktok embed resolver, movie extractor, and Playwright stream sniffer.
Ported from MediaDispatcherService into standalone Python.
"""

import re
from typing import Optional, Dict, Any, List
from .models import MediaImage, MediaMetadata, ProfileCrawlResult, ResolveUrlResult, StreamFormat
from .resolver.url_resolver import UrlResolver, dns_blocked_host
from .extractors.base import BaseExtractor
from .extractors.ytdlp import PlaylistUrlError, YtDlpExtractor
from .extractors.gallery import GalleryDlExtractor, InstagramApiChanged, PlatformBlocked
from .extractors.tiktok import TikTokExtractor
from .extractors.movie import MovieExtractor
from .extractors.direct import DirectImageExtractor
from .extractors.web_scraper import WebScraperExtractor
from .extractors.page_media import LoginRequiredError, PageMediaExtractor, NoPostMediaError, describe_media
from .extractors.media_probe import MANIFEST_EXTS, SEGMENT_EXTS, is_direct_media_url, is_segment_url, path_ext
from .extractors.stream_sniffer import get_sniffer
from .extractors.story import StoryExtractor
from .cookies.browser_cookies import has_login_session, registrable_domain


class _LooksLikePage(Exception):
    """Link có đuôi tệp media nhưng máy chủ trả về trang web (yt-dlp: "Unsupported URL")."""

    def __init__(self, cause: Exception):
        super().__init__(str(cause))
        self.cause = cause


class MediaDispatcher(BaseExtractor):
    """Bộ điều phối bóc tách phương tiện thông minh"""

    def __init__(self):
        super().__init__()
        self.resolver = UrlResolver()
        self.ytdlp = YtDlpExtractor()
        self.gallery = GalleryDlExtractor()
        self.tiktok = TikTokExtractor()
        self.movie = MovieExtractor(self.ytdlp)
        self.direct = DirectImageExtractor()
        self.web_scraper = WebScraperExtractor()
        self.story = StoryExtractor()
        self.sniffer = get_sniffer()  # Playwright-based stream sniffer (lazy)
        # Lấy hết ảnh/video của bài viết trên trang bất kỳ (dùng chung Chromium của sniffer)
        self.page_media = PageMediaExtractor(self.sniffer)

    # ─────────────────────────────────────────────────────────────────────────
    # URL Classification
    # ─────────────────────────────────────────────────────────────────────────

    # Nhận diện nền tảng PHẢI dựa trên hostname, không phải substring của cả URL.
    # `"x.com/" in url` từng khớp nhầm vox.com/, netflix.com/, fox.com/... và
    # `"youtube.com" in url` khớp cả `evil.com/?ref=https://youtube.com/x`,
    # khiến dispatcher định tuyến sang engine sai.
    _VIDEO_AUDIO_DOMAINS = (
        "youtube.com", "youtu.be",
        "tiktok.com",
        "twitch.tv",
        "dailymotion.com", "dai.ly",
        "soundcloud.com",
        "nicovideo.jp", "nico.ms",
        "bilibili.com", "b23.tv",
        "rumble.com", "odysee.com",
        "redd.it",
    )

    _GALLERY_DOMAINS = (
        "pinterest.com", "pin.it",
        "imgur.com", "flickr.com", "deviantart.com", "artstation.com",
        "danbooru.donmai.us", "gelbooru.com", "safebooru.org",
        "pixiv.net", "redd.it",
        "threads.com", "threads.net", "bsky.app", "tumblr.com",
        "x.com", "twitter.com",
    )

    _FACEBOOK_DOMAINS = ("facebook.com", "fb.com", "fb.watch", "fb.me")

    @staticmethod
    def _hostname_of(url: str) -> str:
        import urllib.parse
        raw = (url or "").strip()
        if not raw:
            return ""
        if not raw.lower().startswith(("http://", "https://")):
            raw = "https://" + raw
        try:
            return (urllib.parse.urlparse(raw).hostname or "").lower().rstrip(".")
        except Exception:
            return ""

    @classmethod
    def _host_is(cls, hostname: str, domain: str) -> bool:
        """hostname == domain hoặc là subdomain của domain (không khớp 'vox.com' với 'x.com')."""
        return bool(hostname) and (hostname == domain or hostname.endswith("." + domain))

    @classmethod
    def _host_in(cls, hostname: str, domains) -> bool:
        return any(cls._host_is(hostname, d) for d in domains)

    @staticmethod
    def _path_of(url: str) -> str:
        import urllib.parse
        raw = (url or "").strip()
        if not raw.lower().startswith(("http://", "https://")):
            raw = "https://" + raw
        try:
            p = urllib.parse.urlparse(raw)
            return ((p.path or "") + ("?" + p.query if p.query else "")).lower()
        except Exception:
            return raw.lower()

    def is_video_audio_platform(self, url: str) -> bool:
        host = self._hostname_of(url)
        if self._host_in(host, self._VIDEO_AUDIO_DOMAINS):
            # redd.it chỉ tính là video khi là v.redd.it
            if self._host_is(host, "redd.it"):
                return host.startswith("v.")
            return True

        # Facebook Video / Reel / Watch
        if self._host_in(host, self._FACEBOOK_DOMAINS):
            if self._host_is(host, "fb.watch"):
                return True
            path = self._path_of(url)
            fb_video_markers = ("/reel", "/reels", "/watch", "/videos", "/video", "/share/r", "/share/v", "/stories")
            return any(m in path for m in fb_video_markers)

        return False

    def is_story_url(self, url: str) -> bool:
        """URL là Story Facebook hoặc Instagram?"""
        host = self._hostname_of(url)
        path = self._path_of(url)

        # Instagram Story: /stories/USERNAME/ID/
        if self._host_in(host, ("instagram.com", "instagr.am")):
            return "/stories/" in path

        # Facebook Story: /stories/...
        if self._host_in(host, self._FACEBOOK_DOMAINS):
            return "/stories/" in path

        return False

    def is_gallery_platform(self, url: str) -> bool:
        host = self._hostname_of(url)
        path = self._path_of(url)

        if self._host_in(host, ("instagram.com", "instagr.am")):
            return any(p in path for p in ("/p/", "/reel/", "/reels/", "/stories/", "/tv/"))

        # Facebook Photos / Posts / Albums
        if self._host_in(host, self._FACEBOOK_DOMAINS):
            fb_gallery_markers = ("/photo", "/photos", "/posts", "/media/set", "story.php", "permalink.php", "/share/p")
            return any(m in path for m in fb_gallery_markers)

        # TikTok photo slideshow
        if self._host_is(host, "tiktok.com"):
            return "/photo/" in path

        if self._host_is(host, "reddit.com"):
            return "/gallery" in path or "/r/" in path

        if self._host_is(host, "redd.it"):
            return not host.startswith("v.")

        if "mastodon" in host:
            return True

        return self._host_in(host, self._GALLERY_DOMAINS)

    # ─────────────────────────────────────────────────────────────────────────
    # Main Extract
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _is_auth_error(err: Exception) -> bool:
        """Kiểm tra xem lỗi có phải do yêu cầu đăng nhập không"""
        msg = str(err).lower()
        return any(k in msg for k in (
            "login", "auth", "sign in", "unauthorized", "401",
            "cookies needed", "logged in", "requires authentication",
            "empty media response", "cannot parse data", "private",
            "redirect to login", "not accessible", "403",
            "yêu cầu đăng nhập", "cần đăng nhập",
        ))

    @staticmethod
    def _short_reason(err: Optional[Exception]) -> str:
        """Câu lỗi gốc của yt-dlp/gallery-dl, bỏ tiền tố "ERROR: [youtube] <id>:" và phần hướng dẫn dài."""
        if err is None:
            return ""
        text = str(err).strip()
        m = re.search(r"ERROR:\s*(?:\[[^\]]+\]\s*)?(?:[\w-]+:\s*)?(.*)", text)
        reason = (m.group(1) if m else text).split(". ")[0].strip()
        return reason[:160]

    @classmethod
    def _make_login_error(cls, platform_url: str, cause: Optional[Exception] = None) -> RuntimeError:
        """Tạo thông báo lỗi đăng nhập thân thiện"""
        if "facebook.com" in platform_url or "fb.watch" in platform_url:
            name = "Facebook"
        elif "instagram.com" in platform_url:
            name = "Instagram"
        elif "reddit.com" in platform_url or "redd.it" in platform_url:
            name = "Reddit"
        elif "tiktok.com" in platform_url:
            name = "TikTok"
        elif "x.com" in platform_url or "twitter.com" in platform_url:
            name = "X (Twitter)"
        elif "youtube.com" in platform_url or "youtu.be" in platform_url:
            name = "YouTube"
        elif "threads.com" in platform_url or "threads.net" in platform_url:
            name = "Threads"
        else:
            name = "nền tảng này"
        detail = cls._short_reason(cause)
        return RuntimeError(
            f"Nội dung {name} yêu cầu đăng nhập. "
            f"Hãy đăng nhập {name} trên trình duyệt (app tự dùng trình duyệt đang đăng nhập), "
            f"hoặc vào Cookie Manager (🍪), chọn tab {name} và dán cookie."
            + (f" (Chi tiết: {detail})" if detail else "")
        )

    @staticmethod
    def _dns_blocked_error(host: str) -> RuntimeError:
        return RuntimeError(
            f"Mạng của bạn đang chặn {host} — DNS trả về địa chỉ 127.0.0.1 nên app không kết nối "
            f"được (trình duyệt vẫn vào được nếu nó dùng DNS-over-HTTPS). Hãy đổi DNS của máy hoặc "
            f"router sang 1.1.1.1 / 8.8.8.8, hoặc đặt proxy trong Cài đặt rồi thử lại."
        )

    # yt-dlp tự ghi "please report this issue" khi extractor của một trang không còn đọc
    # được trang đó — tức là trang vừa đổi cấu trúc, không phải lỗi của người dùng.
    _SITE_CHANGED_RE = re.compile(
        r"please report this issue|cannot parse data|unable to extract (?:initial state|[a-z_ ]*data)",
        re.IGNORECASE,
    )

    @classmethod
    def _is_site_changed_error(cls, err: Optional[Exception]) -> bool:
        return bool(err) and bool(cls._SITE_CHANGED_RE.search(str(err)))

    @classmethod
    def _site_changed_error(cls, platform_url: str, err: Exception) -> RuntimeError:
        name = cls._platform_name(platform_url)
        detail = cls._short_reason(err)
        return RuntimeError(
            f"{name} vừa thay đổi cách tải dữ liệu nên yt-dlp chưa đọc được liên kết này. "
            f"Mở Công cụ (⚙) → yt-dlp → \"Bản mới nhất\" rồi thử lại; nếu vẫn lỗi thì bản sửa "
            f"chưa phát hành, hãy thử lại sau vài ngày."
            + (f" (Chi tiết: {detail})" if detail else "")
        )

    @classmethod
    def _platform_name(cls, url: str) -> str:
        try:
            _, name = UrlResolver.detect_platform(url)
        except Exception:
            name = None
        return name if name and name not in ("Web Chung",) else "Trang này"

    @staticmethod
    def _normalize_bilibili_url(url: str) -> str:
        """`bilibili.com/en/video/BV...` (giao diện tiếng Anh, 10/2026) → `/video/BV...`.

        yt-dlp chỉ nhận dạng `/video/`: link `/en/` rơi vào bộ đọc trang chung và trả về
        một "định dạng" duy nhất là chính trang HTML — một video giả không tải được.
        """
        return re.sub(
            r"^(https?://(?:www\.|m\.)?bilibili\.com)/[a-z]{2}(?:-[a-z]{2})?/(video/)",
            r"\1/\2", url.strip(), flags=re.IGNORECASE,
        )

    @staticmethod
    def _has_real_media_stream(res: Optional[MediaMetadata]) -> bool:
        """Kết quả web_scraper có trỏ tới TỆP video/luồng phát thật không.

        Trên trang của nền tảng video, web_scraper chỉ thấy ảnh bìa og:image và
        og:video là trang nhúng (youtube.com/embed/...) — không phải media tải được.
        """
        return bool(res and res.streams) and any(
            WebScraperExtractor.is_video_url(s.url or "") for s in res.streams
        )

    def extract(self, url: str, browser: Optional[str] = None) -> MediaMetadata:
        """Trích xuất thông tin media từ 1 liên kết duy nhất với cơ chế fallback tự động"""
        trimmed = url.strip()

        # 0. Giải mã liên kết rút gọn nếu có và chuẩn hóa link đơn
        resolved = self.resolver.resolve_url(trimmed)
        target_url = resolved.resolved_url if resolved.is_shortened else trimmed
        target_url = self._normalize_facebook_single_url(target_url)
        target_url = self._normalize_bilibili_url(target_url)

        # 1. Tên miền bị DNS chặn: mọi engine đều chỉ nhận "Connection refused"
        blocked = dns_blocked_host(target_url)
        if blocked:
            raise self._dns_blocked_error(blocked)

        # 2. Story (Facebook / Instagram) → ưu tiên StoryExtractor chuyên biệt
        #    Story CDN URL hết hạn cực nhanh (5-15 phút) nên phải xử lý ngay,
        #    không để rơi vào chuỗi fallback dài.
        if self.is_story_url(target_url):
            try:
                self.log(f"Story URL → StoryExtractor: {target_url}")
                res = self.story.extract(target_url, browser=browser)
                return self._enhance_metadata(res, target_url)
            except Exception as story_err:
                self.warn(f"StoryExtractor thất bại ({story_err}), thử gallery-dl fallback...")
                # Instagram Story: gallery-dl có thể xử lý được
                if "instagram.com" in target_url:
                    try:
                        res = self.gallery.extract_gallery(target_url, browser=browser, timeout=20)
                        return self._enhance_metadata(res, target_url)
                    except Exception as gal_err:
                        if self._is_auth_error(gal_err) or self._is_auth_error(story_err):
                            raise self._make_login_error(target_url)
                        self.warn(f"gallery-dl story fallback cũng thất bại: {gal_err}")
                # Nếu lỗi auth → thông báo rõ ràng
                if self._is_auth_error(story_err):
                    raise self._make_login_error(target_url)
                raise story_err

        # 3. Tệp ảnh trực tiếp (CDN)
        if self.direct.is_direct_image_url(target_url):
            try:
                self.log(f"Trực tiếp CDN image: {target_url}")
                res = self.direct.extract(target_url)
                return self._enhance_metadata(res, target_url)
            except Exception as e:
                self.warn(f"Direct image failed ({e}), fallback...")

        # 3b. Link thẳng tới tệp / luồng media (.m3u8, .mpd, .mp4, .mp3, mảnh .ts/.m4s...)
        #     ngoài các nền tảng đã biết (link CDN của v.redd.it, TikTok... vẫn để
        #     extractor của nền tảng xử lý vì nó ghép được cả hình lẫn tiếng).
        page_ytdlp_err: Optional[Exception] = None
        if (
            is_direct_media_url(target_url)
            and not self.is_video_audio_platform(target_url)
            and not self.is_gallery_platform(target_url)
        ):
            self.log(f"Link tệp / luồng media trực tiếp: {target_url}")
            try:
                res = self._extract_direct_media(target_url, browser)
                return self._enhance_metadata(res, target_url)
            except _LooksLikePage as page:
                # Đuôi tệp media nhưng thực ra là TRANG web (vd. ".../video.mp4" trả HTML
                # có player): đi tiếp chuỗi đọc trang như mọi liên kết khác.
                self.log("Link có đuôi tệp media nhưng là trang web → đọc như trang thường")
                page_ytdlp_err = page.cause

        # 4. Trang phim / stream HLS
        # MovieExtractor luôn kết thúc bằng yt-dlp trên chính URL này; nếu nó (hoặc bước
        # 3b) đã thất bại thì bước 7 không chạy yt-dlp lần nữa (trước đây tốn thêm 30-60 giây).
        movie_err: Optional[Exception] = page_ytdlp_err
        if self.movie.is_movie_or_stream_url(target_url):
            try:
                self.log(f"Phim / HLS Stream: {target_url}")
                res = self.movie.extract(target_url, browser=browser)
                return self._enhance_metadata(res, target_url)
            except Exception as e:
                self.warn(f"Movie extractor error ({e})")
                movie_err = e

        # 5. Nền tảng video/audio -> ưu tiên yt-dlp
        if self.is_video_audio_platform(target_url):
            is_fb = "facebook.com" in target_url or "fb.watch" in target_url or "fb.com" in target_url
            try:
                self.log(f"Nền tảng Video/Audio -> yt-dlp: {target_url}")
                # Với TikTok single video, ưu tiên chạy không cookies nếu không chỉ định rõ
                b_override = "none" if ("tiktok.com" in target_url and (not browser or browser == "auto")) else browser
                res = self.ytdlp.extract_metadata(target_url, browser=b_override)
                return self._enhance_metadata(res, target_url)
            except PlaylistUrlError:
                raise
            except Exception as yt_err:
                # Nếu TikTok bị chặn, thử lại không cookies
                if "tiktok.com" in target_url and browser and browser != "none":
                    try:
                        self.log("TikTok video thử lại không dùng cookies ('none')...")
                        res = self.ytdlp.extract_metadata(target_url, browser="none")
                        return self._enhance_metadata(res, target_url)
                    except PlaylistUrlError:
                        raise
                    except Exception:
                        pass
                # Fallback sang gallery-dl
                self.warn(f"Video yt-dlp thất bại ({yt_err}), đang thử gallery-dl fallback...")
                try:
                    res = self.gallery.extract_gallery(target_url, browser=browser)
                    self._fill_video_posters(res, target_url, browser)
                    return self._enhance_metadata(res, target_url)
                except PlatformBlocked:
                    raise
                except Exception as gal_err:
                    # "Cannot parse data" của Facebook thường là trang chặn khách — nhưng
                    # khi ĐÃ có phiên đăng nhập thì đó là Facebook đổi cách nạp video
                    # (10/2026: dữ liệu video không còn nằm trong HTML). Bảo người dùng
                    # đăng nhập lại là sai hướng.
                    fb_site_changed = (
                        is_fb and self._is_site_changed_error(yt_err)
                        and has_login_session(target_url, browser)
                    )
                    # Nếu cả 2 đều lỗi auth -> trả thông báo đăng nhập rõ ràng
                    if is_fb and not fb_site_changed and (self._is_auth_error(yt_err) or self._is_auth_error(gal_err)):
                        raise self._make_login_error(target_url, yt_err)
                    # Video giới hạn tuổi / riêng tư / YouTube đòi "xác nhận không phải bot":
                    # trang vẫn có ảnh bìa nên web_scraper từng trả "thành công" một album
                    # chỉ gồm ảnh bìa, che mất lỗi thật là cần cookie đăng nhập.
                    if self._is_auth_error(yt_err) and not fb_site_changed:
                        raise self._make_login_error(target_url, yt_err)
                    self.warn(f"gallery-dl fallback cũng thất bại ({gal_err}), web_scraper fallback...")
                    try:
                        res = self.web_scraper.extract(target_url)
                        # Chỉ nhận khi tìm được TỆP video thật. Ảnh bìa hay trang nhúng
                        # của chính nền tảng không phải nội dung tải được.
                        if self._has_real_media_stream(res):
                            return self._enhance_metadata(res, target_url)
                    except Exception:
                        pass
                if self._is_site_changed_error(yt_err):
                    raise self._site_changed_error(target_url, yt_err) from yt_err
                raise yt_err

        # 6. Nền tảng gallery/album -> ưu tiên gallery-dl
        if self.is_gallery_platform(target_url):
            is_instagram = "instagram.com" in target_url or "instagr.am" in target_url
            is_reddit = "reddit.com" in target_url or "redd.it" in target_url
            is_fb_gallery = "facebook.com" in target_url or "fb.com" in target_url
            needs_auth = is_instagram or is_fb_gallery or is_reddit
            try:
                self.log(f"Nền tảng Gallery -> gallery-dl: {target_url}")
                res = self.gallery.extract_gallery(target_url, browser=browser)

                # Đối với Reddit video, tăng cường formats từ yt-dlp nếu có
                if is_reddit and res.type == "video":
                    try:
                        yt_res = self.ytdlp.extract_metadata(target_url, browser=browser)
                        if yt_res and yt_res.streams:
                            return self._enhance_metadata(yt_res, target_url)
                    except Exception:
                        pass

                self._fill_video_posters(res, target_url, browser)
                return self._enhance_metadata(res, target_url)
            except PlatformBlocked:
                # Nền tảng chặn theo chính sách (Reddit chặn khách...): yt-dlp và đọc trang
                # chỉ ra cùng lỗi hoặc một album avatar giả.
                raise
            except Exception as gal_err:
                # Nếu lỗi auth đối với platform yêu cầu đăng nhập -> trả thông báo rõ ràng
                if needs_auth and self._is_auth_error(gal_err):
                    # Thử yt-dlp trước khi báo lỗi
                    try:
                        res = self.ytdlp.extract_metadata(target_url, browser=browser)
                        return self._enhance_metadata(res, target_url)
                    except PlaylistUrlError:
                        raise
                    except Exception as yt_err2:
                        # gallery-dl đã nói rõ là cần đăng nhập: lỗi khác của yt-dlp (vd.
                        # "There is no video in this post" với bài ảnh Instagram) chỉ che
                        # mất nguyên nhân thật.
                        raise self._make_login_error(target_url, gal_err) from yt_err2
                self.warn(f"gallery-dl thất bại ({gal_err}), yt-dlp fallback...")
                try:
                    res = self.ytdlp.extract_metadata(target_url, browser=browser)
                    return self._enhance_metadata(res, target_url)
                except PlaylistUrlError:
                    raise
                except Exception as yt_err:
                    # Reddit / Instagram khi yt-dlp nói rõ cần đăng nhập mà máy không có phiên
                    # đăng nhập nào: trang của khách chỉ có avatar và ảnh của bài khác (Reddit
                    # khách từng ra "4 ảnh và 1 video" — 3 ảnh của bài liên quan). Facebook
                    # thì không: trang ảnh công khai vẫn cho khách thấy chính ảnh đó.
                    guest_page_is_junk = is_instagram or is_reddit
                    if guest_page_is_junk and self._is_auth_error(yt_err) and not has_login_session(target_url, browser):
                        raise self._make_login_error(target_url, yt_err) from gal_err
                    self.warn(f"yt-dlp thất bại ({yt_err}), đọc trực tiếp ảnh/video của bài viết...")
                    # Trước đây chỉ còn web_scraper: với trang mạng xã hội nó chỉ thấy ảnh
                    # og:image (thẻ chia sẻ) nên trả về một "album" 1 ảnh giả. Nay mở trang
                    # (kèm cookie đăng nhập) và lấy media trong chính bài viết.
                    try:
                        res = self.page_media.extract(
                            target_url, browser=browser, platform=self._platform_id(target_url)
                        )
                        return self._enhance_metadata(res, target_url)
                    except Exception as page_err:
                        # Trang chỉ cho khách xem bản thay thế (vd. ảnh mờ của bài X nhạy
                        # cảm): web_scraper bên dưới sẽ nhặt đúng ảnh đó (og:image) và báo
                        # "thành công" một album giả.
                        if self._is_login_wall_error(page_err):
                            raise
                        self.warn(f"Không đọc được media của bài viết: {page_err}")
                    try:
                        res = self.web_scraper.extract(target_url)
                        return self._enhance_metadata(res, target_url)
                    except Exception:
                        raise RuntimeError(str(gal_err) or str(yt_err) or "Không thể trích xuất nội dung từ liên kết này")

        # 7. URL không rõ -> yt-dlp -> gallery-dl -> media của bài viết -> Playwright sniffer
        #
        # "Trang web bất kỳ" thường là MỘT BÀI VIẾT có nhiều ảnh và/hoặc video. Trước đây:
        # bài chỉ có ảnh → "Không tìm được nguồn video"; bài nhiều video → chỉ video đầu;
        # bài nhúng 1 video YouTube + 10 ảnh → chỉ video YouTube; bài nhúng 2 video →
        # lỗi "đây là danh sách". Nay yt-dlp chỉ được giữ nguyên kết quả khi chính trang
        # là trang video của một nền tảng nó hỗ trợ; còn lại lấy toàn bộ media của bài.
        yt_err: Optional[Exception] = movie_err
        if movie_err is None:
            try:
                self.log(f"URL không xác định -> yt-dlp first: {target_url}")
                res = self.ytdlp.extract_metadata(target_url, browser=browser)
                whole_post = self._whole_post_media(target_url, res, browser)
                return self._enhance_metadata(whole_post or res, target_url)
            except PlaylistUrlError as pl_err:
                # Trang nhiều video / playlist của site yt-dlp hỗ trợ: báo đúng thay vì để
                # web_scraper/Playwright vớt đại một ảnh hay một luồng trên trang.
                # Ngoại lệ: các mục trỏ sang tên miền KHÁC — đó là bài viết nhúng nhiều
                # video (YouTube, Vimeo...), cần lấy cả bài.
                # (Trang phim đã qua bước 4 — ở đó vẫn để sniffer thử tìm luồng thật.)
                if self._is_embedded_video_list(target_url, pl_err):
                    self.log("Danh sách video là các video NHÚNG trong bài viết → lấy cả bài")
                    try:
                        res = self.page_media.extract(target_url, browser=browser)
                        return self._enhance_metadata(res, target_url)
                    except Exception as page_err:
                        self.warn(f"Không đọc được media của bài viết: {page_err}")
                raise
            except Exception as e:
                yt_err = e
        self.warn(f"yt-dlp thất bại ({yt_err}), gallery-dl fallback...")
        try:
            res = self.gallery.extract_gallery(target_url, browser=browser)
            self._fill_video_posters(res, target_url, browser)
            return self._enhance_metadata(res, target_url)
        except Exception as gal_err:
            self.warn(f"gallery-dl thất bại ({gal_err}), đọc media của bài viết...")
            try:
                res = self.page_media.extract(target_url, browser=browser)
                return self._enhance_metadata(res, target_url)
            except NoPostMediaError as page_err:
                # Đã mở trang bằng trình duyệt thật mà không thấy gì: dò mạng lần nữa
                # cũng không ra thêm, chỉ tốn thêm 20-40 giây.
                if page_err.dynamic_ran:
                    raise RuntimeError(str(page_err))
                self.warn(f"Không thấy media trong bài viết ({page_err}), thử Playwright stream sniffer...")
            except Exception as page_err:
                if self._is_login_wall_error(page_err):
                    raise
                self.warn(f"Không đọc được bài viết ({page_err}), thử Playwright stream sniffer...")
            # Thử Playwright sniffer (headless browser intercept)
            return self._try_playwright_sniff(target_url, gal_err, yt_err, browser)

    # ─────────────────────────────────────────────────────────────────────────
    # Link tệp / luồng media trực tiếp
    # ─────────────────────────────────────────────────────────────────────────

    _SEGMENT_ONLY_NOTE = (
        "Đây chỉ là MỘT MẢNH vài giây của luồng phát (.ts / .m4s) và không tìm thấy danh "
        "sách phát đầy đủ chứa nó. Muốn tải cả video, hãy dán link danh sách phát "
        "(.m3u8 / .mpd) hoặc link trang chứa video."
    )

    def _extract_direct_media(self, url: str, browser: Optional[str]) -> MediaMetadata:
        """Link thẳng tới tệp / luồng media: chỉ yt-dlp (kèm ffprobe) đọc được.

        Trước đây khi yt-dlp thất bại (403 vì thiếu Referer, link ký đã hết hạn),
        dispatcher còn chạy gallery-dl, đọc "trang" bằng HTML tĩnh rồi mở cả Chromium
        (thêm 5-40 giây) — và Chromium luôn "thấy" chính link đó trong lưu lượng mạng,
        nên app báo phân tích xong một video không bao giờ tải được.

        Ném `_LooksLikePage` khi link hoá ra là trang web (yt-dlp: "Unsupported URL")
        để dispatcher đọc nó như trang thường.
        """
        prober = self.ytdlp.prober
        info = None
        fragment = False
        if is_segment_url(url):
            # Mảnh .m4s / .ts chỉ dài vài giây (mảnh .m4s còn không tự phát được): tìm
            # danh sách phát chứa nó để tải nguyên video.
            info = prober.probe(url)
            fragment = self._is_stream_fragment(url, info)
            if fragment:
                manifest = prober.find_manifest(url)
                if manifest:
                    self.log(f"Mảnh luồng → danh sách phát đầy đủ: {manifest}")
                    try:
                        res = self.ytdlp.extract_metadata(manifest, browser=browser)
                        res.platform = "movie"
                        if not res.description:
                            res.description = (
                                "ℹ️ Đã tìm thấy danh sách phát đầy đủ chứa mảnh này — tải về được nguyên video."
                            )
                        return res
                    except PlaylistUrlError:
                        raise
                    except Exception as err:
                        self.warn(f"Không đọc được danh sách phát {manifest}: {err}")

        try:
            res = self.ytdlp.extract_metadata(url, browser=browser)
        except PlaylistUrlError:
            raise
        except Exception as err:
            # yt-dlp nhận ra mọi tệp / luồng media (kể cả "application/octet-stream");
            # "Unsupported URL" nghĩa là máy chủ trả về một trang HTML.
            if "Unsupported URL" in str(err) and path_ext(url) not in (MANIFEST_EXTS | SEGMENT_EXTS):
                raise _LooksLikePage(err) from err
            raise RuntimeError(self._direct_media_error(url, err)) from err

        if path_ext(url) in MANIFEST_EXTS:
            res.platform = "movie"
        if fragment:
            if info is None and prober.is_available():
                # yt-dlp mở được nhưng ffprobe không đọc nổi: mảnh fMP4 thiếu phần đầu
                # (init) — tải về cũng là một tệp không phát được.
                res.streams = []
                res.description = f"⚠️ {self._SEGMENT_ONLY_NOTE}"
            else:
                res.description = f"ℹ️ {self._SEGMENT_ONLY_NOTE}"
        return res

    @staticmethod
    def _is_stream_fragment(url: str, info) -> bool:
        """Link .ts / .m4s là MỘT MẢNH của luồng (không phải cả video)?

        Mảnh .m4s của HLS-fMP4 / DASH không chứa phần đầu (moov) nên ffprobe không đọc
        được; .m4s đọc được là cả một luồng trong một tệp (DASH một tệp, vd. Bilibili).
        Mảnh .ts thì tự phát được nên xét theo thời lượng.
        """
        if path_ext(url) == "ts":
            return info is None or info.is_short
        return info is None

    @classmethod
    def _direct_media_error(cls, url: str, err: Exception) -> str:
        text = str(err)
        kind = "luồng phát" if path_ext(url) in (MANIFEST_EXTS | SEGMENT_EXTS) else "tệp media"
        if re.search(r"HTTP Error 40[13]|Forbidden|Unauthorized", text, re.IGNORECASE):
            return (
                f"Máy chủ từ chối mở {kind} này (HTTP 403) — link .m3u8 / .m4s / .mp4 lấy từ trình duyệt "
                f"thường chỉ dùng được khi mở từ trang gốc, hoặc đã hết hạn sau vài phút. Hãy dán link "
                f"TRANG chứa video để app tự lấy luồng mới."
            )
        if re.search(r"HTTP Error 4(?:04|10)", text, re.IGNORECASE):
            return (
                f"Không còn tìm thấy {kind} này trên máy chủ (HTTP 404) — link lấy từ trình duyệt thường "
                f"hết hạn sau vài phút. Hãy dán link TRANG chứa video để app tự lấy luồng mới."
            )
        if "quá thời gian" in text:
            return text
        return f"Không đọc được {kind} này. (Chi tiết: {cls._short_reason(err) or text[:160]})"

    # ─────────────────────────────────────────────────────────────────────────
    # Crawl Profile
    # ─────────────────────────────────────────────────────────────────────────

    def crawl_profile(
        self,
        profile_url: str,
        limit: int = 50,
        media_type: str = "all",  # 'all' | 'video' | 'image'
        platform_hint: Optional[str] = None,
        browser: Optional[str] = None,
        range_start: Optional[int] = None,
        range_end: Optional[int] = None,
    ) -> ProfileCrawlResult:
        """Quét toàn bộ hồ sơ (Profile / Channel / Playlist / Board)"""
        target_url = profile_url.strip()
        clean_hint = (platform_hint or "").lower()

        # Chuẩn hóa nếu truyền vào username
        if target_url.startswith("@") or ("." not in target_url and "/" not in target_url):
            username = target_url.lstrip("@")
            if clean_hint == "tiktok":
                target_url = f"https://www.tiktok.com/@{username}"
            elif clean_hint == "instagram":
                target_url = f"https://www.instagram.com/{username}/reels/" if media_type == "video" else f"https://www.instagram.com/{username}/posts/"
            elif clean_hint in ("facebook", "fb"):
                target_url = f"https://www.facebook.com/{username}/photos"
            elif clean_hint == "pinterest":
                target_url = f"https://www.pinterest.com/{username}/"
            elif clean_hint == "reddit":
                target_url = f"https://www.reddit.com/user/{username}/"
            elif clean_hint in ("x", "twitter"):
                target_url = f"https://x.com/{username}/media"
            elif clean_hint == "soundcloud":
                target_url = f"https://soundcloud.com/{username}"
            elif clean_hint == "twitch":
                target_url = f"https://www.twitch.tv/{username}/videos"
            else:
                target_url = f"https://www.youtube.com/@{username}/videos"

        # Giải mã link rút gọn nếu có
        resolved = self.resolver.resolve_url(target_url)
        if resolved.is_shortened:
            target_url = resolved.resolved_url

        # Chuẩn hóa URL Facebook / X / Instagram sang URL subpath bóc tách tối ưu
        target_url = self._normalize_facebook_url(target_url, media_type)
        target_url = self._normalize_x_url(target_url)
        target_url = self._normalize_instagram_url(target_url, media_type)

        blocked = dns_blocked_host(target_url)
        if blocked:
            raise self._dns_blocked_error(blocked)

        is_youtube = clean_hint == "youtube" or any(d in target_url for d in ("youtube.com", "youtu.be"))
        is_tiktok = clean_hint == "tiktok" or "tiktok.com" in target_url
        is_soundcloud = clean_hint == "soundcloud" or "soundcloud.com" in target_url
        is_twitch = clean_hint == "twitch" or "twitch.tv" in target_url

        # YouTube / SoundCloud / Twitch -> yt-dlp
        if is_youtube or is_soundcloud or is_twitch:
            self.log(f"Quét profile video/audio qua yt-dlp: {target_url}")
            return self.ytdlp.extract_playlist(
                target_url,
                limit=limit,
                browser=browser,
                from_item=range_start,
                to_item=range_end,
            )

        # TikTok profile -> ưu tiên yt-dlp không dùng cookies, tự động fallback sang tiktok embed resolver
        if is_tiktok:
            tiktok_browser = "none" if (not browser or browser == "auto" or browser == "edge") else browser
            try:
                self.log(f"TikTok profile -> yt-dlp (limit: {limit}, browser: {tiktok_browser}): {target_url}")
                res = self.ytdlp.extract_playlist(
                    target_url,
                    limit=limit,
                    browser=tiktok_browser,
                    from_item=range_start,
                    to_item=range_end,
                )
                if res.media:
                    res.platform = "tiktok"
                    return res
            except Exception as yt_err:
                self.warn(f"TikTok yt-dlp thất bại ({yt_err}), đang thử TikTok embed resolver...")

            # Cứu hộ bằng TikTok embed resolver (curl_cffi với TLS impersonation)
            try:
                embed_res = self.tiktok.resolve_profile(target_url, limit=limit)
                if embed_res and embed_res.media:
                    self.log(f"✅ TikTok embed resolver thành công: {len(embed_res.media)} videos")
                    return embed_res
            except Exception as emb_err:
                self.warn(f"TikTok embed resolver thất bại: {emb_err}")

            # Thử tiếp gallery-dl fallback
            self.warn("Thử tiếp gallery-dl fallback...")
            try:
                res = self.gallery.crawl_profile(
                    target_url,
                    limit=limit,
                    media_type=media_type,
                    browser=browser,
                    range_start=range_start,
                    range_end=range_end,
                )
                if res.media:
                    res.platform = "tiktok"
                    return res
            except Exception as gal_err:
                self.warn(f"TikTok gallery-dl cũng thất bại: {gal_err}")

            raise RuntimeError(f"Không thể quét tài khoản TikTok từ {target_url}")

        # Mạng xã hội hình ảnh (Instagram, Pinterest, Reddit, Twitter...) -> gallery-dl
        gal_err_saved: Optional[Exception] = None
        try:
            self.log(f"Quét profile mạng xã hội qua gallery-dl: {target_url}")
            res = self.gallery.crawl_profile(
                target_url,
                limit=limit,
                media_type=media_type,
                browser=browser,
                range_start=range_start,
                range_end=range_end,
            )
            if res.media:
                return res
            # gallery-dl thành công nhưng trả về rỗng (không có exception)
            # → lưu lại để fallback bên dưới xử lý
            gal_err_saved = self.gallery.empty_crawl_error(target_url, browser)
        except InstagramApiChanged as ig_err:
            gal_err_saved = ig_err
            reels = self._instagram_reels_fallback(
                target_url, ig_err, limit, media_type, browser, range_start, range_end
            )
            if reels:
                return reels
        except Exception as gal_err:
            gal_err_saved = gal_err

        # Fallback yt-dlp cho Facebook (Groups, Profile, Page...)
        if "facebook.com" in target_url or "fb.com" in target_url:
            self.warn(f"Facebook gallery-dl thất bại ({gal_err_saved}), yt-dlp fallback...")
            try:
                return self.ytdlp.extract_playlist(
                    target_url,
                    limit=limit,
                    browser=browser,
                    from_item=range_start,
                    to_item=range_end,
                )
            except Exception as yt_err:
                self.warn(f"Facebook yt-dlp cũng thất bại: {yt_err}")

        if gal_err_saved:
            raise gal_err_saved
        raise RuntimeError(f"Không tìm thấy tệp phương tiện nào từ {target_url}")

    # Mục Bài viết / Ảnh của một tài khoản Instagram (cùng dùng truy vấn danh sách bài đăng)
    _IG_FEED_URL_RE = re.compile(r"^(https?://(?:www\.)?instagram\.com/[^/?#]+)/(?:posts|photos)/?$", re.IGNORECASE)

    def _instagram_reels_fallback(
        self,
        url: str,
        err: Exception,
        limit: int,
        media_type: str,
        browser: Optional[str],
        range_start: Optional[int],
        range_end: Optional[int],
    ) -> Optional[ProfileCrawlResult]:
        """Mục Bài viết lỗi vì Instagram đổi API → quét tạm mục Reels (dùng truy vấn riêng).

        10/2026: truy vấn danh sách bài đăng thiếu biến bắt buộc mới, trong khi truy vấn
        Reels vẫn chạy. Trả None khi không áp dụng được (người dùng chỉ cần ảnh...).
        """
        m = self._IG_FEED_URL_RE.match(url.split("?")[0])
        if not m or media_type == "image":
            return None
        reels_url = f"{m.group(1)}/reels/"
        self.warn(f"Mục Bài viết Instagram lỗi ({err}) → quét tạm mục Reels: {reels_url}")
        try:
            res = self.gallery.crawl_profile(
                reels_url,
                limit=limit,
                media_type=media_type,
                browser=browser,
                range_start=range_start,
                range_end=range_end,
            )
        except Exception as reels_err:
            self.warn(f"Quét Reels cũng thất bại: {reels_err}")
            return None
        if not res.media:
            return None
        res.notice = (
            "Instagram vừa đổi API của mục Bài viết nên tạm thời chỉ quét được Reels (video) — ảnh và "
            "bài nhiều ảnh chưa lấy được. Sau 1-2 ngày, mở Công cụ (⚙) → gallery-dl → \"Bản mới nhất\" "
            "rồi quét lại để lấy đủ."
        )
        return res

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _fill_video_posters(self, res: MediaMetadata, target_url: str, browser: Optional[str]) -> None:
        """Bổ sung ảnh poster cho các video gallery-dl trả về mà không có ảnh bìa.

        Các video trong cùng một bài đăng dùng chung link bài viết, nhưng mỗi video
        có media ID riêng nằm trong URL file (vd. video.twimg.com/amplify_video/<id>/...),
        trùng với `id` yt-dlp trả về — nên ghép poster theo ID là chính xác.
        """
        videos = [img for img in (res.images or []) if img.type == "video" and not img.thumb]
        if not videos:
            return
        try:
            posters = self.ytdlp.extract_video_posters(target_url, browser=browser)
        except Exception as e:  # thiếu poster thì UI hiện placeholder, không làm hỏng kết quả
            self.warn(f"Không lấy được poster video qua yt-dlp: {e}")
            return
        if not posters:
            return

        unmatched = []
        for img in videos:
            match = next((thumb for mid, thumb in posters if len(mid) >= 6 and mid in (img.url or "")), None)
            if match:
                img.thumb = match
            else:
                unmatched.append(img)
        # Không ghép được theo ID mà số lượng khớp nhau thì ghép theo thứ tự trong bài.
        if unmatched and len(unmatched) == len(videos) == len(posters):
            for img, (_, thumb) in zip(unmatched, posters):
                img.thumb = thumb

        if not res.thumbnail:
            res.thumbnail = next((img.thumb for img in res.images if img.thumb), None)
            res.high_res_thumbnail = res.high_res_thumbnail or res.thumbnail

    # yt-dlp xử lý bằng bộ đọc TRANG CHUNG (không phải extractor riêng của một nền tảng)
    _GENERIC_YTDLP_EXTRACTORS = frozenset({"generic", "html5mediaembed", "html5"})

    @classmethod
    def _same_site(cls, url_a: str, url_b: str) -> bool:
        a = registrable_domain(cls._hostname_of(url_a))
        b = registrable_domain(cls._hostname_of(url_b))
        return bool(a) and a == b

    def _is_embedded_video_list(self, target_url: str, err: PlaylistUrlError) -> bool:
        urls = getattr(err, "entry_urls", None) or []
        return bool(urls) and not any(self._same_site(u, target_url) for u in urls)

    @staticmethod
    def _is_login_wall_error(err: Exception) -> bool:
        return isinstance(err, LoginRequiredError) or "yêu cầu đăng nhập" in str(err)

    def _platform_id(self, url: str) -> str:
        try:
            platform, _ = self.resolver.detect_platform(url)
        except Exception:
            platform = None
        return platform or "generic"

    def _whole_post_media(self, target_url: str, ytdlp_res: MediaMetadata, browser: Optional[str]) -> Optional[MediaMetadata]:
        """Video yt-dlp tìm được chỉ là MỘT phần của bài viết? → trả về cả bài, hoặc None.

        Hai dấu hiệu: yt-dlp dùng bộ đọc trang chung (trang có thẻ <video>), hoặc video
        nằm ở tên miền khác trang (bài viết nhúng video YouTube/Vimeo...).
        """
        # Link là chính tệp media (không phải trang web): không có "bài viết" nào để đọc.
        if ytdlp_res.is_direct_media:
            return None
        key = (ytdlp_res.extractor_key or "").lower()
        generic = key in self._GENERIC_YTDLP_EXTRACTORS
        embedded = bool(ytdlp_res.webpage_url) and not self._same_site(ytdlp_res.webpage_url, target_url)
        if not (generic or embedded):
            return None
        try:
            page = self.page_media.extract(target_url, browser=browser)
        except Exception as err:
            self.warn(f"Không đọc được phần còn lại của bài viết: {err}")
            return None
        items = list(page.images or [])
        # Trang chỉ có đúng video này: giữ kết quả yt-dlp (có đủ lựa chọn chất lượng)
        if not items or (len(items) == 1 and items[0].type == "video" and (ytdlp_res.page_video_count or 1) <= 1):
            return None
        # page_media không thấy video (player dựng bằng JS lạ, nhúng từ nơi khác) mà
        # yt-dlp thì thấy: thêm nó vào đầu album, tải qua yt-dlp bằng chính URL nó đã đọc.
        # Riêng thẻ <video> thường của chính trang (HTML5MediaEmbed) thì page_media cũng
        # đã thấy và đã loại vì nằm NGOÀI bài viết (vd. video hướng dẫn "thêm vào màn
        # hình chính" trong khung giao diện của diễn đàn) — không được đưa lại vào.
        if key not in ("html5mediaembed", "html5") and not any(i.type == "video" for i in items):
            video_url = target_url if generic else (ytdlp_res.webpage_url or target_url)
            items.insert(0, MediaImage(
                id=0, url=video_url, title=ytdlp_res.title, type="video",
                thumb=ytdlp_res.thumbnail, duration=ytdlp_res.duration, via="ytdlp",
            ))
            for idx, item in enumerate(items, 1):
                item.id = idx
            page.images = items
            page.type = "album"
            page.streams = None
            page.description = describe_media(items)
        self.log(f"Video chỉ là một phần của bài viết → trả về cả bài ({len(items)} tệp)")
        return page

    def _try_playwright_sniff(
        self,
        target_url: str,
        gal_err: Exception,
        yt_err: Exception,
        browser: Optional[str] = None,
    ) -> MediaMetadata:
        """Dùng Playwright headless để sniff stream URLs bị ẩn qua XHR/fetch"""
        import urllib.parse
        parsed = urllib.parse.urlparse(target_url)
        domain = parsed.netloc or target_url
        title = None

        if not self.sniffer.is_available():
            raise RuntimeError(
                f"Cả yt-dlp, gallery-dl, web_scraper đều không trích xuất được. "
                f"Playwright chưa được cài: pip install playwright && playwright install chromium. "
                f"Lỗi ban đầu: {yt_err}"
            )

        self.log(f"Playwright stream sniffer đang quét: {target_url}")
        sniff_result = self.sniffer.sniff_streams(target_url, browser=browser or "auto")

        if sniff_result.get("error") and not sniff_result.get("streams"):
            raise RuntimeError(
                f"Playwright sniffer lỗi: {sniff_result['error']}. "
                f"Lỗi ban đầu: {yt_err or gal_err}"
            )

        streams = sniff_result.get("streams") or []
        images = sniff_result.get("images") or []

        if not streams and images:
            self.log(f"Playwright tìm thấy {len(images)} ảnh từ {target_url}")
            is_fb = "facebook.com" in target_url or "fb.com" in target_url
            return MediaMetadata(
                id=str(abs(hash(target_url))),
                platform="facebook" if is_fb else "generic",
                title=None,
                author=None,
                author_url=None,
                duration=None,
                views=None,
                thumbnail=images[0].thumb or images[0].url if images else None,
                type="album",
                original_url=target_url,
                description=f"🔍 Tìm thấy {len(images)} ảnh chất lượng cao.",
                images=images,
            )

        if not streams:
            # Tìm không ra — thông báo rõ ràng thay vì crash
            self.warn(f"Playwright không tìm thấy stream từ {target_url}")
            return MediaMetadata(
                id=str(abs(hash(target_url))),
                platform="generic",
                title=None,
                author=None,
                author_url=None,
                duration=None,
                views=None,
                thumbnail=None,
                type="video",
                original_url=target_url,
                description=(
                    "⚠️ Không tìm được nguồn video. Player của trang này có thể dùng DRM, "
                    "mã hóa phức tạp, hoặc token đã hết hạn. Không thể tải về."
                ),
                streams=[],
            )

        self.log(f"Playwright tìm thấy {len(streams)} stream(s) từ {target_url}")
        return MediaMetadata(
            id=str(abs(hash(target_url))),
            platform="generic",
            title=None,
            author=None,
            author_url=None,
            duration=None,
            views=None,
            thumbnail=None,
            type="video",
            original_url=target_url,
            description=f"🔍 Phát hiện {len(streams)} nguồn stream qua network interceptor.",
            streams=streams,
        )

    @staticmethod
    def _normalize_facebook_single_url(url: str) -> str:
        """
        Chuẩn hóa link Facebook đơn (Reels, Videos, Photos, Watch, Share) sang định dạng chuẩn
        mà yt-dlp và gallery-dl hỗ trợ tối đa.
        """
        import re
        if "facebook.com" not in url.lower() and "fb.watch" not in url.lower() and "fb.com" not in url.lower():
            return url

        clean = url.strip()

        # 1. Chuyển m.facebook.com sang www.facebook.com
        if "m.facebook.com" in clean:
            clean = clean.replace("m.facebook.com", "www.facebook.com")

        # 2. /share/v/DIGITS -> /watch/?v=DIGITS
        m_v = re.search(r"/share/v/(\d+)", clean)
        if m_v:
            return f"https://www.facebook.com/watch/?v={m_v.group(1)}"

        # 3. /share/r/DIGITS -> /reel/DIGITS
        m_r = re.search(r"/share/r/(\d+)", clean)
        if m_r:
            return f"https://www.facebook.com/reel/{m_r.group(1)}"

        # 4. /share/p/DIGITS -> /photo/?fbid=DIGITS
        m_p = re.search(r"/share/p/(\d+)", clean)
        if m_p:
            return f"https://www.facebook.com/photo/?fbid={m_p.group(1)}"

        # 5. /stories/ → chuẩn hóa mobile → www
        if "/stories/" in clean.lower():
            clean = clean.replace("m.facebook.com", "www.facebook.com")
            if "www.facebook.com" not in clean and "facebook.com" in clean:
                clean = re.sub(r"(?:web|touch)\.facebook\.com", "www.facebook.com", clean)

        return clean

    @staticmethod
    def _normalize_facebook_url(url: str, media_type: str = "all") -> str:
        """
        Chuyển đổi URL Facebook profile/groups sang URL mà gallery-dl hỗ trợ.

        gallery-dl hỗ trợ:
          - facebook.com/USERNAME/photos          (ảnh)
          - facebook.com/USERNAME/photos_albums   (albums)
          - facebook.com/USERNAME/videos          (video - thường thất bại, dùng yt-dlp)
        gallery-dl KHÔNG hỗ trợ:
          - facebook.com/USERNAME/               (timeline chung)
          - facebook.com/groups/GROUP_ID/        (groups)
          - facebook.com/profile.php?id=...      (profile ID)
        """
        import re
        if "facebook.com" not in url.lower() and "fb.com" not in url.lower():
            return url

        lower = url.lower()

        # Đã có subpath cụ thể mà gallery-dl hỗ trợ → giữ nguyên
        supported_paths = ("/photos", "/photos_albums", "/avatar", "/videos")
        if any(p in lower for p in supported_paths):
            return url

        # Groups URL → giữ nguyên để yt-dlp xử lý (gallery-dl không hỗ trợ)
        if "/groups/" in lower:
            return url

        # profile.php?id=... → giữ nguyên
        if "profile.php" in lower:
            return url

        # Các URL có dạng facebook.com/USERNAME hoặc facebook.com/USERNAME/
        # Chuyển sang /photos hoặc /videos tùy media_type
        match = re.match(
            r"(https?://(?:www\.)?facebook\.com/)([^/?#]+)(?:/)?$",
            url,
            re.IGNORECASE,
        )
        if match:
            base = match.group(1)
            username = match.group(2)
            # Bỏ qua các path đặc biệt của Facebook
            if username.lower() in ("watch", "reel", "reels", "groups", "pages", "marketplace", "gaming", "events"):
                return url
            if media_type == "video":
                return f"{base}{username}/videos"
            else:
                return f"{base}{username}/photos"

        return url

    @staticmethod
    def _normalize_x_url(url: str) -> str:
        """
        Chuẩn hóa URL X/Twitter profile (ví dụ https://x.com/wildrift?s=20)
        thành https://x.com/wildrift/media để gallery-dl bóc tách media trực tiếp.
        """
        import re
        if "twitter.com" not in url.lower() and "x.com" not in url.lower():
            return url

        # Tách query parameters như ?s=20
        clean = url.split("?")[0].rstrip("/")
        match = re.match(r"(https?://(?:www\.)?(?:x\.com|twitter\.com)/)([^/?#]+)(?:/([a-zA-Z0-9_-]+))?$", clean, re.IGNORECASE)
        if match:
            base = match.group(1)
            username = match.group(2)
            subpath = (match.group(3) or "").lower()

            # Bỏ qua các system routes
            if username.lower() in ("home", "explore", "notifications", "messages", "search", "i", "settings"):
                return url

            # Nếu là URL post/tweet (status/123...) thì giữ nguyên
            if username.lower() == "status" or subpath == "status":
                return url

            # Đã có subpath media hoặc timeline
            if subpath in ("media", "timeline", "likes"):
                return clean

            # Chuyển user profile thông thường sang /media
            return f"{base}{username}/media"

        return url

    @staticmethod
    def _normalize_instagram_url(url: str, media_type: str = "all") -> str:
        """
        Chuẩn hóa URL Instagram profile (ví dụ https://www.instagram.com/hn950421g/)
        thành https://www.instagram.com/hn950421g/posts/ (hoặc /reels/) để gallery-dl trích xuất.
        """
        import re
        if "instagram.com" not in url.lower():
            return url

        clean = url.split("?")[0].rstrip("/")
        match = re.match(r"(https?://(?:www\.)?instagram\.com/)([^/?#]+)(?:/([a-zA-Z0-9_-]+))?$", clean, re.IGNORECASE)
        if match:
            base = match.group(1)
            username = match.group(2)
            subpath = (match.group(3) or "").lower()

            # Bỏ qua post đơn, reel đơn, system paths
            if username.lower() in ("p", "reel", "reels", "stories", "explore", "direct", "accounts"):
                return url

            # Nếu đã có subpath cụ thể
            if subpath in ("posts", "photos", "reels", "tagged", "channel"):
                return f"{clean}/"

            if media_type == "video":
                return f"{base}{username}/reels/"
            if media_type == "image":
                # `/photos/` là extractor riêng của gallery-dl, đã loại sẵn reel
                # nên không phải tải về rồi lọc bỏ. Carousel vẫn được bung đủ ảnh.
                return f"{base}{username}/photos/"
            return f"{base}{username}/posts/"

        return url

    @staticmethod
    def _enhance_metadata(res: MediaMetadata, url: str) -> MediaMetadata:
        lower = url.lower()
        if any(p in lower for p in ("/reel/", "/reels/", "/share/r/")):
            res.is_reel = True
        if "/shorts/" in lower:
            res.is_short = True
        return res
