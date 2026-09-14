"""
Hidden Stream Sniffer — Playwright-based Network Interceptor.

Chạy headless Chromium để intercept các request network (XHR, Fetch, Media)
và tìm nguồn media thực sự bị ẩn trong các player JS hiện đại.

Hỗ trợ phát hiện:
- Direct stream URLs (.m3u8, .mpd, .mp4, .ts, .m4s, .webm)
- HLS/DASH manifest files
- Blob URL với Media Source Extensions (MSE)
- JSON API responses chứa stream URL
- Iframe embed players

Không hỗ trợ (hardware DRM):
- Widevine / PlayReady / FairPlay encrypted streams
- Token-based streams hết hạn ngay khi load (< 1s)
"""

import re
import sys
import time
from typing import Optional, List, Dict, Any
from .base import BaseExtractor
from ..models import StreamFormat, MediaImage
from ..cookies.browser_cookies import get_browser_cookies_txt
import http.cookiejar

# Media URL patterns để lọc các request có giá trị
MEDIA_URL_PATTERNS = [
    r'\.m3u8(?:[?#]|$)',
    r'\.mpd(?:[?#]|$)',
    r'\.mp4(?:[?#]|$)',
    r'\.ts(?:[?#]|$)',
    r'\.m4s(?:[?#]|$)',
    r'\.m4v(?:[?#]|$)',
    r'\.webm(?:[?#]|$)',
    r'\.mp3(?:[?#]|$)',
    r'\.aac(?:[?#]|$)',
    r'\.ogg(?:[?#]|$)',
    r'/hls/',
    r'/dash/',
    r'/manifest',
    r'/playlist',
    r'/chunklist',
    r'master\.m3u8',
    r'index\.m3u8',
]

# Domains nào là embed player đáng theo dõi
EMBED_PLAYER_PATTERNS = [
    r'embed\.',
    r'/embed/',
    r'/player/',
    r'player\.',
    r'vidsrc\.me',
    r'vidsrc\.to',
    r'streamtape\.com',
    r'doodstream\.com',
    r'mixdrop\.co',
    r'streamwish\.com',
    r'filemoon\.sx',
    r'upstream\.to',
    r'voe\.sx',
    r'rabbitstream\.net',
    r'megacloud\.tv',
    r'jwplayer',
    r'plyr',
    r'vidcloud',
]

# JSON response keys thường chứa stream URL
STREAM_JSON_KEYS = [
    'file', 'src', 'url', 'source', 'stream', 'video', 'hls', 'dash',
    'mp4', 'stream_url', 'video_url', 'media_url', 'playback_url',
    'sources', 'tracks', 'playlist', 'embed', 'direct',
]


class PlaywrightStreamSniffer(BaseExtractor):
    """Dùng Playwright headless Chromium để sniff stream URLs ẩn"""

    PLAYWRIGHT_TIMEOUT = 20_000  # 20 giây
    WAIT_AFTER_LOAD = 5_000      # 5 giây sau khi trang load xong để bắt requests

    def __init__(self):
        super().__init__()
        self._playwright_available = self._check_playwright()

    @staticmethod
    def _check_playwright() -> bool:
        try:
            import playwright  # noqa
            return True
        except ImportError:
            return False

    def is_available(self) -> bool:
        return self._playwright_available

    def sniff_streams(
        self,
        page_url: str,
        wait_ms: int = 5000,
        follow_iframes: bool = True,
    ) -> Dict[str, Any]:
        """
        Mở trang bằng Playwright, bắt tất cả network requests liên quan đến media.

        Returns:
            {
                "streams": [StreamFormat, ...],
                "iframe_urls": [str, ...],
                "raw_urls": [str, ...],
                "found": bool,
                "method": "playwright" | "none",
                "error": str | None,
            }
        """
        if not self._playwright_available:
            return {
                "streams": [],
                "iframe_urls": [],
                "raw_urls": [],
                "found": False,
                "method": "none",
                "error": "Playwright chưa được cài đặt. Chạy: pip install playwright && playwright install chromium",
            }

        try:
            from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout
        except ImportError:
            return {
                "streams": [],
                "iframe_urls": [],
                "raw_urls": [],
                "found": False,
                "method": "none",
                "error": "Không thể import playwright",
            }

        captured_urls: List[str] = []
        iframe_urls: List[str] = []
        captured_images: List[MediaImage] = []

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=[
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                        "--disable-web-security",  # cho phép cross-origin iframe
                        "--disable-features=IsolateOrigins,site-per-process",
                    ],
                )
                context = browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1280, "height": 720},
                    ignore_https_errors=True,
                    java_script_enabled=True,
                )

                # Nạp cookies từ trình duyệt nếu có
                try:
                    import urllib.parse
                    domain = urllib.parse.urlparse(page_url).netloc
                    cfile = get_browser_cookies_txt("auto", domain=domain)
                    if cfile and os.path.exists(cfile):
                        cj = http.cookiejar.MozillaCookieJar(cfile)
                        cj.load(ignore_discard=True, ignore_expires=True)
                        pw_cookies = []
                        for c in cj:
                            d = c.domain
                            if not d.startswith(".") and domain in d:
                                d = "." + d
                            pw_cookies.append({
                                "name": c.name,
                                "value": c.value,
                                "domain": d,
                                "path": c.path,
                            })
                        if pw_cookies:
                            context.add_cookies(pw_cookies)
                except Exception:
                    pass

                # Bắt tất cả requests
                page = context.new_page()

                def on_request(request):
                    url = request.url
                    rtype = request.resource_type
                    # Media requests trực tiếp
                    if rtype in ("media", "xhr", "fetch", "document", "other"):
                        if self._is_media_url(url):
                            if url not in captured_urls:
                                captured_urls.append(url)
                                self.log(f"[Sniffer] Bắt được: [{rtype}] {url[:120]}")
                        # Iframe player
                        if rtype == "document" and self._is_embed_player(url) and url != page_url:
                            if url not in iframe_urls:
                                iframe_urls.append(url)

                def on_response(response):
                    url = response.url
                    ctype = (response.headers.get("content-type") or "").lower()
                    # HLS manifest / DASH
                    if "mpegurl" in ctype or "dash+xml" in ctype:
                        if url not in captured_urls:
                            captured_urls.append(url)
                            self.log(f"[Sniffer] Bắt qua Content-Type: {ctype[:50]} — {url[:100]}")
                    # JSON có thể chứa stream URL
                    elif "application/json" in ctype and self._is_media_url(url):
                        if url not in captured_urls:
                            captured_urls.append(url)

                page.on("request", on_request)
                page.on("response", on_response)

                # Điều hướng đến trang
                try:
                    page.goto(
                        page_url,
                        timeout=self.PLAYWRIGHT_TIMEOUT,
                        wait_until="domcontentloaded",
                    )
                except Exception as nav_err:
                    self.warn(f"[Sniffer] Lỗi navigate: {nav_err}")

                # Đợi để JS chạy và requests được phát
                actual_wait = min(wait_ms, 12000)
                page.wait_for_timeout(actual_wait)

                # Bóc tách ảnh từ nội dung trang (ảnh chất lượng cao bài viết)
                try:
                    page_html = page.content()
                    raw_imgs = re.findall(r'https:[^"\'<>\s]+(?:fbcdn\.net|cdninstagram\.com|twimg\.com|pinimg\.com|scontent)[^"\'<>\s]+', page_html)
                    cleaned_imgs = []
                    for m in raw_imgs:
                        u = m.replace(r"\u0025", "%").replace(r"\/", "/").replace("&amp;", "&").replace(r"\\", "")
                        if any(ext in u.lower() for ext in (".jpg", ".png", ".webp", ".jpeg")):
                            if not any(skip in u for skip in ("rsrc.php", "p50x50", "s150x150", "emoji")):
                                cleaned_imgs.append(u)
                    deduped_imgs = list(dict.fromkeys(cleaned_imgs))
                    for idx, img_u in enumerate(deduped_imgs[:60], 1):
                        captured_images.append(
                            MediaImage(
                                id=f"sniff_img_{idx}",
                                url=img_u,
                                title=f"Ảnh {idx}",
                                type="image",
                                thumb=img_u,
                            )
                        )
                except Exception:
                    pass

                # Nếu chưa tìm được và có iframes, thử từng iframe
                if not captured_urls and follow_iframes:
                    frames = page.frames
                    for frame in frames[1:]:  # bỏ main frame
                        frame_url = frame.url
                        if frame_url and frame_url != "about:blank":
                            if frame_url not in iframe_urls:
                                iframe_urls.append(frame_url)
                            self.log(f"[Sniffer] Phát hiện iframe: {frame_url[:100]}")

                browser.close()

        except Exception as e:
            self.warn(f"[Sniffer] Playwright error: {e}")
            return {
                "streams": [],
                "images": [],
                "iframe_urls": iframe_urls,
                "raw_urls": captured_urls,
                "found": False,
                "method": "playwright",
                "error": str(e),
            }

        # Nếu chưa tìm được stream từ main page, thử navigate từng iframe embed
        if not captured_urls and iframe_urls and follow_iframes:
            for iframe_url in iframe_urls[:3]:  # Giới hạn 3 iframes
                self.log(f"[Sniffer] Thử sniff iframe: {iframe_url[:100]}")
                iframe_result = self.sniff_streams(
                    iframe_url,
                    wait_ms=min(wait_ms, 8000),
                    follow_iframes=False,  # Không đệ quy thêm
                )
                if iframe_result.get("raw_urls"):
                    captured_urls.extend(iframe_result["raw_urls"])
                    break

        # Lọc và sắp xếp: ưu tiên m3u8 > mpd > mp4 > ts
        deduped = list(dict.fromkeys(captured_urls))  # giữ thứ tự, loại trùng
        streams = self._build_streams(deduped)

        return {
            "streams": streams,
            "images": captured_images,
            "iframe_urls": iframe_urls,
            "raw_urls": deduped,
            "found": bool(streams or captured_images),
            "method": "playwright",
            "error": None,
        }

    def _is_media_url(self, url: str) -> bool:
        """Kiểm tra URL có phải media stream không"""
        lower = url.lower()
        # Bỏ qua tracking pixels và analytics
        skip_patterns = [
            "google-analytics", "gtag", "pixel.gif", "beacon",
            "analytics", "telemetry", "stats.", "/ping",
        ]
        if any(s in lower for s in skip_patterns):
            return False
        return any(re.search(pat, lower) for pat in MEDIA_URL_PATTERNS)

    def _is_embed_player(self, url: str) -> bool:
        """Kiểm tra URL có phải iframe embed player không"""
        lower = url.lower()
        return any(re.search(pat, lower) for pat in EMBED_PLAYER_PATTERNS)

    def _build_streams(self, urls: List[str]) -> List[StreamFormat]:
        """Chuyển danh sách URL raw thành StreamFormat objects"""
        streams: List[StreamFormat] = []
        seen_types: set = set()

        # Sắp xếp ưu tiên
        def priority(u: str) -> int:
            l = u.lower()
            if ".m3u8" in l or "mpegurl" in l:
                return 0
            if ".mpd" in l or "dash" in l:
                return 1
            if ".mp4" in l:
                return 2
            if ".ts" in l or ".m4s" in l:
                return 3
            if ".webm" in l:
                return 4
            if ".mp3" in l or ".aac" in l or ".ogg" in l:
                return 5
            return 6

        sorted_urls = sorted(urls, key=priority)

        for url in sorted_urls:
            lower = url.lower()
            if ".m3u8" in lower or "mpegurl" in lower:
                fmt = "HLS"
                quality = "HLS Stream (Chất lượng tốt nhất)"
                stype = "full"
                key = "hls"
            elif ".mpd" in lower or "dash" in lower:
                fmt = "DASH"
                quality = "DASH Stream (Adaptive)"
                stype = "full"
                key = "dash"
            elif ".mp4" in lower:
                fmt = "MP4"
                quality = "MP4 Video (Nguồn gốc)"
                stype = "full"
                key = "mp4"
            elif ".ts" in lower:
                fmt = "TS"
                quality = "TS Segment Stream"
                stype = "stream"
                key = "ts"
            elif ".m4s" in lower:
                fmt = "M4S"
                quality = "DASH Segment (M4S)"
                stype = "stream"
                key = "m4s"
            elif ".webm" in lower:
                fmt = "WEBM"
                quality = "WebM Video"
                stype = "full"
                key = "webm"
            elif ".mp3" in lower:
                fmt = "MP3"
                quality = "MP3 Audio"
                stype = "audio"
                key = "mp3"
            elif ".aac" in lower:
                fmt = "AAC"
                quality = "AAC Audio"
                stype = "audio"
                key = "aac"
            else:
                fmt = "STREAM"
                quality = "Media Stream (Phát hiện tự động)"
                stype = "full"
                key = "other"

            # Chỉ lấy 1 stream mỗi loại chính (hls, mp4, dash...)
            if key in seen_types and key not in ("ts", "m4s", "other"):
                continue
            seen_types.add(key)

            streams.append(
                StreamFormat(
                    format_id=f"sniff_{key}_{len(streams)+1}",
                    quality=f"🔍 {quality}",
                    format=fmt,
                    size="Tự động (Stream)",
                    stream_type=stype,
                    has_audio=stype != "mute",
                    has_video=stype not in ("audio",),
                    url=url,
                )
            )

        return streams


# Singleton lazy-loaded
_sniffer: Optional[PlaywrightStreamSniffer] = None


def get_sniffer() -> PlaywrightStreamSniffer:
    global _sniffer
    if _sniffer is None:
        _sniffer = PlaywrightStreamSniffer()
    return _sniffer
