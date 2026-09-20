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

import atexit
import http.cookiejar
import os
import queue
import re
import sys
import threading
import time
from typing import Optional, List, Dict, Any, Tuple, Callable
from urllib.parse import urljoin
from .base import BaseExtractor
from ..models import StreamFormat, MediaImage
from ..cookies.browser_cookies import get_browser_cookies_txt

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


class _PlaywrightWorkerThread(threading.Thread):
    """
    Dedicated background worker thread for Playwright sync API.
    Guarantees thread affinity so that greenlet / Playwright sync calls
    always execute on the exact same thread, preventing:
    'greenlet.error: Cannot switch to a different thread'.
    """

    def __init__(self):
        super().__init__(daemon=True, name="pw_sniffer_worker")
        self._tasks: queue.Queue = queue.Queue()
        self._pw = None
        self._browser = None
        self._started_event = threading.Event()
        self.start()

    def run(self):
        self._started_event.set()
        while True:
            item = self._tasks.get()
            if item is None:
                self._cleanup()
                self._tasks.task_done()
                break
            fn, args, kwargs, result_holder, done_event = item
            try:
                result_holder["result"] = fn(*args, **kwargs)
            except Exception as e:
                result_holder["error"] = e
            finally:
                done_event.set()
                self._tasks.task_done()

    def submit(self, fn: Callable, *args, timeout: Optional[float] = None, **kwargs) -> Any:
        done_event = threading.Event()
        result_holder = {"result": None, "error": None}
        self._tasks.put((fn, args, kwargs, result_holder, done_event))
        if not done_event.wait(timeout=timeout):
            raise TimeoutError(f"Playwright worker task timed out after {timeout}s")
        if result_holder["error"] is not None:
            raise result_holder["error"]
        return result_holder["result"]

    def _ensure_browser(self):
        """Khởi tạo hoặc trả về instance browser Chromium duy nhất còn kết nối"""
        if self._browser is not None:
            try:
                if self._browser.is_connected():
                    return self._browser
            except Exception:
                pass
            self._cleanup()

        from playwright.sync_api import sync_playwright
        if self._pw is None:
            self._pw = sync_playwright().start()

        self._browser = self._pw.chromium.launch(
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
        return self._browser

    def _cleanup(self):
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:
                pass
            self._pw = None

    def close(self):
        if self.is_alive():
            self._tasks.put(None)
            self.join(timeout=5)


class PlaywrightStreamSniffer(BaseExtractor):
    """
    Dùng Playwright headless Chromium để sniff stream URLs ẩn.
    Áp dụng Singleton pattern giữ một instance Chromium duy nhất hoạt động ngầm.
    Mỗi request tạo context/page mới và đóng ngay sau khi thu thập xong stream.
    """

    _instance: Optional["PlaywrightStreamSniffer"] = None
    _singleton_lock = threading.Lock()

    PLAYWRIGHT_TIMEOUT = 20_000  # 20 giây
    WAIT_AFTER_LOAD = 5_000      # 5 giây sau khi trang load xong để bắt requests

    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            with cls._singleton_lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        super().__init__()
        self._playwright_available = self._check_playwright()
        self._worker: Optional[_PlaywrightWorkerThread] = None
        self._worker_lock = threading.Lock()
        if self._playwright_available:
            atexit.register(self.close)
        self._initialized = True

    @staticmethod
    def _check_playwright() -> bool:
        try:
            import playwright  # noqa
            return True
        except ImportError:
            return False

    def is_available(self) -> bool:
        return self._playwright_available

    def _get_worker(self) -> _PlaywrightWorkerThread:
        if self._worker is None or not self._worker.is_alive():
            with self._worker_lock:
                if self._worker is None or not self._worker.is_alive():
                    self._worker = _PlaywrightWorkerThread()
        return self._worker

    def close(self) -> None:
        """Đóng Chromium browser và dọn dẹp background worker thread"""
        with self._worker_lock:
            if self._worker is not None:
                try:
                    self._worker.close()
                except Exception:
                    pass
                self._worker = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def sniff_streams(
        self,
        page_url: str,
        wait_ms: int = 5000,
        follow_iframes: bool = True,
    ) -> Dict[str, Any]:
        """
        Mở trang bằng Playwright qua instance Chromium ngầm, bắt tất cả network requests liên quan đến media.
        Tạo new_context/new_page và đóng context sau khi lấy được stream để tối ưu RAM.

        Returns:
            {
                "streams": [StreamFormat, ...],
                "images": [MediaImage, ...],
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
                "images": [],
                "iframe_urls": [],
                "raw_urls": [],
                "found": False,
                "method": "none",
                "error": "Playwright chưa được cài đặt. Chạy: pip install playwright && playwright install chromium",
            }

        try:
            worker = self._get_worker()
            timeout_sec = (self.PLAYWRIGHT_TIMEOUT / 1000.0) + (wait_ms / 1000.0) + 15.0
            if follow_iframes:
                timeout_sec += 30.0
            return worker.submit(
                self._do_sniff_streams,
                page_url,
                wait_ms,
                follow_iframes,
                timeout=timeout_sec,
            )
        except Exception as e:
            self.warn(f"[Sniffer] Playwright error: {e}")
            return {
                "streams": [],
                "images": [],
                "iframe_urls": [],
                "raw_urls": [],
                "found": False,
                "method": "playwright",
                "error": str(e),
            }

    def _do_sniff_streams(
        self,
        page_url: str,
        wait_ms: int = 5000,
        follow_iframes: bool = True,
    ) -> Dict[str, Any]:
        """Thực thi sniffing trên luồng chuyên biệt của Playwright worker"""
        worker = self._get_worker()
        browser = worker._ensure_browser()

        captured_urls, iframe_urls, captured_images = self._sniff_page(browser, page_url, wait_ms)

        # Nếu chưa tìm được stream từ main page, thử navigate từng iframe embed
        if not captured_urls and iframe_urls and follow_iframes:
            for iframe_url in iframe_urls[:3]:  # Giới hạn 3 iframes
                self.log(f"[Sniffer] Thử sniff iframe: {iframe_url[:100]}")
                try:
                    i_urls, _, _ = self._sniff_page(browser, iframe_url, min(wait_ms, 8000))
                    if i_urls:
                        captured_urls.extend(i_urls)
                        break
                except Exception as iframe_err:
                    self.warn(f"[Sniffer] Lỗi sniff iframe {iframe_url[:60]}: {iframe_err}")

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

    def _sniff_page(
        self,
        browser,
        target_url: str,
        wait_ms: int,
    ) -> Tuple[List[str], List[str], List[MediaImage]]:
        """Mở một context/page mới trên instance browser hiện có, sniff và đóng context khi xong"""
        captured_urls: List[str] = []
        iframe_urls: List[str] = []
        captured_images: List[MediaImage] = []

        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 720},
            ignore_https_errors=True,
            java_script_enabled=True,
        )

        try:
            # Nạp cookies từ trình duyệt nếu có
            try:
                import urllib.parse
                domain = urllib.parse.urlparse(target_url).netloc
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

            def add_candidate(url: str, source: str) -> None:
                if not url or url.startswith(("blob:", "data:", "javascript:")):
                    return
                if self._is_media_url(url) and url not in captured_urls:
                    captured_urls.append(url)
                    self.log(f"[Sniffer] Bắt qua {source}: {url[:120]}")

            def inspect_payload(payload: str, base_url: str, source: str) -> None:
                if not payload or len(payload) > 4_000_000:
                    return
                # JSON/HTML thường giữ URL tuyệt đối hoặc URL tương đối của
                # manifest trong các thuộc tính file/src/source/playlist.
                absolute_urls = re.findall(r'https?://[^\s"\'<>\\]+', payload)
                relative_urls = re.findall(
                    r'["\']([^"\']+(?:\.m3u8|\.mpd|/manifest|/playlist|/hls/|/dash/)[^"\']*)["\']',
                    payload,
                    re.IGNORECASE,
                )
                for candidate in absolute_urls + relative_urls:
                    add_candidate(urljoin(base_url, candidate).rstrip('\\'), source)

                if '#EXTM3U' in payload or '<MPD' in payload:
                    add_candidate(base_url, f'{source} manifest')

            def on_request(request):
                url = request.url
                rtype = request.resource_type
                # Media requests trực tiếp
                if rtype in ("media", "xhr", "fetch", "document", "other"):
                    if self._is_media_url(url):
                        if url not in captured_urls:
                            captured_urls.append(url)
                            self.log(f"[Sniffer] Bắt được: [{rtype}] {url[:120]}")
                    # Thu mọi document iframe; nhiều player không dùng URL
                    # có chữ embed/player nên không thể lọc bằng hostname.
                    if rtype == "document" and url != target_url:
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
                # Manifest có thể không có đuôi mở rộng nhưng vẫn khai báo
                # đúng Content-Type.
                elif "application/json" in ctype or "text/html" in ctype or "javascript" in ctype:
                    try:
                        inspect_payload(response.text(), url, f"response {ctype.split(';')[0]}")
                    except Exception:
                        pass

            page.on("request", on_request)
            page.on("response", on_response)

            # Điều hướng đến trang
            try:
                page.goto(
                    target_url,
                    timeout=self.PLAYWRIGHT_TIMEOUT,
                    wait_until="domcontentloaded",
                )
            except Exception as nav_err:
                self.warn(f"[Sniffer] Lỗi navigate: {nav_err}")

            # Đợi để JS chạy và requests được phát
            actual_wait = min(wait_ms, 12000)
            page.wait_for_timeout(actual_wait)

            # Player có thể được tạo động trong iframe và không xuất hiện
            # trong HTML ban đầu. Đọc cả DOM media elements và resource
            # timing để tìm manifest không có tên file quen thuộc.
            for frame in page.frames:
                frame_url = frame.url
                if frame_url and frame_url != "about:blank" and frame_url != target_url:
                    if frame_url not in iframe_urls:
                        iframe_urls.append(frame_url)
                try:
                    discovered = frame.evaluate(
                        """() => ({
                            media: Array.from(document.querySelectorAll('video, audio, source')).flatMap((el) => [el.src, el.currentSrc]).filter(Boolean),
                            resources: performance.getEntriesByType('resource').map((entry) => entry.name)
                        })"""
                    )
                    for media_url in (discovered or {}).get("media", []):
                        add_candidate(media_url, "DOM media")
                    for resource_url in (discovered or {}).get("resources", []):
                        add_candidate(resource_url, "performance")
                except Exception:
                    pass

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

            for iframe_url in iframe_urls:
                self.log(f"[Sniffer] Phát hiện iframe: {iframe_url[:100]}")

        finally:
            # Đóng context/page ngay lập tức để giải phóng RAM, browser vẫn giữ nguyên
            try:
                context.close()
            except Exception:
                pass

        return captured_urls, iframe_urls, captured_images

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

    # Phân loại theo phần mở rộng THẬT. Dùng substring trần (`".ts" in url`,
    # `"dash" in url`) khiến một URL .mp4 trên host "x.ts.cdn.com" hay bất kỳ URL
    # nào chứa chữ "dash" bị gắn nhãn sai loại.
    _KIND_RULES = [
        ("hls", r"\.m3u8(?:[?#]|$)|mpegurl", "HLS", "HLS Stream (Chất lượng tốt nhất)", "full", 0),
        ("dash", r"\.mpd(?:[?#]|$)|/dash/", "DASH", "DASH Stream (Adaptive)", "full", 1),
        ("mp4", r"\.mp4(?:[?#]|$)", "MP4", "MP4 Video (Nguồn gốc)", "full", 2),
        ("ts", r"\.ts(?:[?#]|$)", "TS", "TS Segment Stream", "stream", 3),
        ("m4s", r"\.m4s(?:[?#]|$)", "M4S", "DASH Segment (M4S)", "stream", 3),
        ("webm", r"\.webm(?:[?#]|$)", "WEBM", "WebM Video", "full", 4),
        ("mp3", r"\.mp3(?:[?#]|$)", "MP3", "MP3 Audio", "audio", 5),
        ("aac", r"\.aac(?:[?#]|$)", "AAC", "AAC Audio", "audio", 5),
    ]

    @classmethod
    def _classify(cls, url: str) -> Tuple[str, str, str, str, int]:
        """(key, fmt, quality, stream_type, priority) cho một URL media."""
        lower = url.lower()
        for key, pattern, fmt, quality, stype, prio in cls._KIND_RULES:
            if re.search(pattern, lower):
                return key, fmt, quality, stype, prio
        return "other", "STREAM", "Media Stream", "full", 6

    def _build_streams(self, urls: List[str]) -> List[StreamFormat]:
        """Chuyển danh sách URL raw thành StreamFormat objects"""
        streams: List[StreamFormat] = []
        seen_types: set = set()

        sorted_urls = sorted(urls, key=lambda u: self._classify(u)[4])

        for url in sorted_urls:
            key, fmt, quality, stype, _ = self._classify(url)

            # Chỉ lấy 1 stream mỗi loại chính (hls, mp4, dash...)
            if key in seen_types and key not in ("ts", "m4s", "other"):
                continue
            seen_types.add(key)

            streams.append(
                StreamFormat(
                    format_id=f"sniff_{key}_{len(streams)+1}",
                    quality=quality,
                    format=fmt,
                    size=None,
                    raw_size=None,
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

