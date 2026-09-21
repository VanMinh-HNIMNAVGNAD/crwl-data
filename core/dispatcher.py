"""
Media Dispatcher Engine.
Coordinates intelligent platform extraction, prioritization, and automatic fallbacks
across yt-dlp, gallery-dl, tiktok embed resolver, movie extractor, and Playwright stream sniffer.
Ported from MediaDispatcherService into standalone Python.
"""

from typing import Optional, Dict, Any, List
from .models import MediaMetadata, ProfileCrawlResult, ResolveUrlResult, StreamFormat
from .resolver.url_resolver import UrlResolver
from .extractors.base import BaseExtractor
from .extractors.ytdlp import YtDlpExtractor
from .extractors.gallery import GalleryDlExtractor
from .extractors.tiktok import TikTokExtractor
from .extractors.movie import MovieExtractor
from .extractors.direct import DirectImageExtractor
from .extractors.web_scraper import WebScraperExtractor
from .extractors.stream_sniffer import get_sniffer


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
        self.sniffer = get_sniffer()  # Playwright-based stream sniffer (lazy)

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
        "threads.net", "bsky.app", "tumblr.com",
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
            fb_video_markers = ("/reel", "/reels", "/watch", "/videos", "/video", "/share/r", "/share/v")
            return any(m in path for m in fb_video_markers)

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
    def _make_login_error(platform_url: str) -> RuntimeError:
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
        else:
            name = "nền tảng này"
        return RuntimeError(
            f"Nội dung {name} yêu cầu đăng nhập. "
            f"Vui lòng vào Cookie Manager (🍪), chọn tab {name} và dán cookie từ trình duyệt."
        )

    def extract(self, url: str, browser: Optional[str] = None) -> MediaMetadata:
        """Trích xuất thông tin media từ 1 liên kết duy nhất với cơ chế fallback tự động"""
        trimmed = url.strip()

        # 0. Giải mã liên kết rút gọn nếu có và chuẩn hóa link đơn
        resolved = self.resolver.resolve_url(trimmed)
        target_url = resolved.resolved_url if resolved.is_shortened else trimmed
        target_url = self._normalize_facebook_single_url(target_url)

        # 1. Tệp ảnh trực tiếp (CDN)
        if self.direct.is_direct_image_url(target_url):
            try:
                self.log(f"Trực tiếp CDN image: {target_url}")
                res = self.direct.extract(target_url)
                return self._enhance_metadata(res, target_url)
            except Exception as e:
                self.warn(f"Direct image failed ({e}), fallback...")

        # 2. Trang phim / stream HLS
        if self.movie.is_movie_or_stream_url(target_url):
            try:
                self.log(f"Phim / HLS Stream: {target_url}")
                res = self.movie.extract(target_url, browser=browser)
                return self._enhance_metadata(res, target_url)
            except Exception as e:
                self.warn(f"Movie extractor error ({e})")

        # 3. Nền tảng video/audio -> ưu tiên yt-dlp
        if self.is_video_audio_platform(target_url):
            is_fb = "facebook.com" in target_url or "fb.watch" in target_url or "fb.com" in target_url
            try:
                self.log(f"Nền tảng Video/Audio -> yt-dlp: {target_url}")
                # Với TikTok single video, ưu tiên chạy không cookies nếu không chỉ định rõ
                b_override = "none" if ("tiktok.com" in target_url and (not browser or browser == "auto")) else browser
                res = self.ytdlp.extract_metadata(target_url, browser=b_override)
                return self._enhance_metadata(res, target_url)
            except Exception as yt_err:
                # Nếu TikTok bị chặn, thử lại không cookies
                if "tiktok.com" in target_url and browser and browser != "none":
                    try:
                        self.log("TikTok video thử lại không dùng cookies ('none')...")
                        res = self.ytdlp.extract_metadata(target_url, browser="none")
                        return self._enhance_metadata(res, target_url)
                    except Exception:
                        pass
                # Fallback sang gallery-dl
                self.warn(f"Video yt-dlp thất bại ({yt_err}), đang thử gallery-dl fallback...")
                try:
                    res = self.gallery.extract_gallery(target_url, browser=browser)
                    return self._enhance_metadata(res, target_url)
                except Exception as gal_err:
                    # Nếu cả 2 đều lỗi auth -> trả thông báo đăng nhập rõ ràng
                    if is_fb and (self._is_auth_error(yt_err) or self._is_auth_error(gal_err)):
                        raise self._make_login_error(target_url)
                    self.warn(f"gallery-dl fallback cũng thất bại ({gal_err}), web_scraper fallback...")
                    try:
                        res = self.web_scraper.extract(target_url)
                        if res and (res.streams or res.images):
                            return self._enhance_metadata(res, target_url)
                    except Exception:
                        pass
                raise yt_err

        # 4. Nền tảng gallery/album -> ưu tiên gallery-dl
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

                return self._enhance_metadata(res, target_url)
            except Exception as gal_err:
                # Nếu lỗi auth đối với platform yêu cầu đăng nhập -> trả thông báo rõ ràng
                if needs_auth and self._is_auth_error(gal_err):
                    # Thử yt-dlp trước khi báo lỗi
                    try:
                        res = self.ytdlp.extract_metadata(target_url, browser=browser)
                        return self._enhance_metadata(res, target_url)
                    except Exception as yt_err2:
                        if self._is_auth_error(yt_err2):
                            raise self._make_login_error(target_url)
                        raise yt_err2
                self.warn(f"gallery-dl thất bại ({gal_err}), yt-dlp fallback...")
                try:
                    res = self.ytdlp.extract_metadata(target_url, browser=browser)
                    return self._enhance_metadata(res, target_url)
                except Exception as yt_err:
                    self.warn(f"yt-dlp thất bại ({yt_err}), web_scraper fallback...")
                    try:
                        res = self.web_scraper.extract(target_url)
                        return self._enhance_metadata(res, target_url)
                    except Exception:
                        raise RuntimeError(str(gal_err) or str(yt_err) or "Không thể trích xuất nội dung từ liên kết này")

        # 5. URL không rõ -> yt-dlp -> gallery-dl -> web_scraper -> Playwright sniffer
        try:
            self.log(f"URL không xác định -> yt-dlp first: {target_url}")
            res = self.ytdlp.extract_metadata(target_url, browser=browser)
            return self._enhance_metadata(res, target_url)
        except Exception as yt_err:
            self.warn(f"yt-dlp thất bại ({yt_err}), gallery-dl fallback...")
            try:
                res = self.gallery.extract_gallery(target_url, browser=browser)
                return self._enhance_metadata(res, target_url)
            except Exception as gal_err:
                self.warn(f"gallery-dl thất bại ({gal_err}), web_scraper fallback...")
                try:
                    res = self.web_scraper.extract(target_url)
                    # Nếu web_scraper tìm thấy streams → trả về
                    if res.streams:
                        return self._enhance_metadata(res, target_url)
                    # Nếu không có streams → thử Playwright sniffer
                    raise RuntimeError("web_scraper không tìm được stream")
                except Exception as web_err:
                    # Thử Playwright sniffer (headless browser intercept)
                    self.warn(f"web_scraper thất bại ({web_err}), thử Playwright stream sniffer...")
                    return self._try_playwright_sniff(target_url, gal_err, yt_err)


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
            gal_err_saved = RuntimeError(f"gallery-dl không tìm thấy media từ {target_url}")
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

    # ─────────────────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _try_playwright_sniff(self, target_url: str, gal_err: Exception, yt_err: Exception) -> MediaMetadata:
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
        sniff_result = self.sniffer.sniff_streams(target_url)

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
