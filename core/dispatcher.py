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

    def is_video_audio_platform(self, url: str) -> bool:
        lower = url.lower()
        video_platforms = [
            "youtube.com", "youtu.be",
            "tiktok.com",
            "twitch.tv", "clips.twitch.tv",
            "dailymotion.com", "dai.ly",
            "soundcloud.com",
            "nicovideo.jp", "nico.ms",
            "bilibili.com", "b23.tv",
            "rumble.com", "odysee.com",
            "facebook.com/reel", "facebook.com/reels", "facebook.com/watch",
            "facebook.com/share/r", "facebook.com/share/v", "facebook.com/video",
            "facebook.com/videos", "/videos/", "fb.watch",
            "v.redd.it",
        ]
        return any(d in lower for d in video_platforms)

    def is_gallery_platform(self, url: str) -> bool:
        lower = url.lower()
        if "instagram.com" in lower or "instagr.am" in lower:
            return any(p in lower for p in ("/p/", "/reel/", "/reels/", "/stories/", "/tv/"))

        gallery_domains = [
            "pinterest.com", "pin.it",
            "imgur.com", "flickr.com", "deviantart.com", "artstation.com",
            "danbooru", "gelbooru", "safebooru", "pixiv.net",
            "reddit.com/gallery", "reddit.com/r/", "redd.it",
            "threads.net", "bsky.app", "mastodon", "tumblr.com",
            "/photo/",  # TikTok photo slideshow
            "x.com/", "twitter.com/",
            "facebook.com/photo", "facebook.com/posts",
        ]
        return any(d in lower for d in gallery_domains)

    # ─────────────────────────────────────────────────────────────────────────
    # Main Extract
    # ─────────────────────────────────────────────────────────────────────────

    def extract(self, url: str, browser: Optional[str] = None) -> MediaMetadata:
        """Trích xuất thông tin media từ 1 liên kết duy nhất với cơ chế fallback tự động"""
        trimmed = url.strip()

        # 0. Giải mã liên kết rút gọn nếu có
        resolved = self.resolver.resolve_url(trimmed)
        target_url = resolved.resolved_url if resolved.is_shortened else trimmed

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
                # Nếu yt-dlp lỗi, fallback sang gallery-dl cho TikTok
                if "tiktok.com" in target_url:
                    self.warn(f"TikTok -> gallery-dl fallback: {target_url}")
                    try:
                        res = self.gallery.extract_gallery(target_url, browser=browser)
                        return self._enhance_metadata(res, target_url)
                    except Exception:
                        pass
                raise yt_err

        # 4. Nền tảng gallery/album -> ưu tiên gallery-dl
        if self.is_gallery_platform(target_url):
            try:
                self.log(f"Nền tảng Gallery -> gallery-dl: {target_url}")
                res = self.gallery.extract_gallery(target_url, browser=browser)

                # Đối với Reddit video, tăng cường formats từ yt-dlp nếu có
                is_reddit = "reddit.com" in target_url or "redd.it" in target_url
                if is_reddit and res.type == "video":
                    try:
                        yt_res = self.ytdlp.extract_metadata(target_url, browser=browser)
                        if yt_res and yt_res.streams:
                            return self._enhance_metadata(yt_res, target_url)
                    except Exception:
                        pass

                return self._enhance_metadata(res, target_url)
            except Exception as gal_err:
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

        # Chuẩn hóa URL Facebook: chuyển profile/groups thông thường sang URL gallery-dl hỗ trợ
        target_url = self._normalize_facebook_url(target_url, media_type)

        is_youtube = clean_hint == "youtube" or any(d in target_url for d in ("youtube.com", "youtu.be", "/playlist"))
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
        title = f"Media từ {domain}"

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

        if not streams:
            # Tìm không ra — thông báo rõ ràng thay vì crash
            self.warn(f"Playwright không tìm thấy stream từ {target_url}")
            return MediaMetadata(
                id=str(abs(hash(target_url))),
                platform="generic",
                title=title,
                author=domain,
                author_url=target_url,
                duration="Không xác định",
                views="Không xác định",
                thumbnail="",
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
            title=title,
            author=domain,
            author_url=target_url,
            duration="Không xác định",
            views="Không xác định",
            thumbnail="",
            type="video",
            original_url=target_url,
            description=f"🔍 Phát hiện {len(streams)} nguồn stream qua network interceptor.",
            streams=streams,
        )

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
    def _enhance_metadata(res: MediaMetadata, url: str) -> MediaMetadata:
        lower = url.lower()
        if any(p in lower for p in ("/reel/", "/reels/", "/share/r/")):
            res.is_reel = True
        if "/shorts/" in lower:
            res.is_short = True
        return res
