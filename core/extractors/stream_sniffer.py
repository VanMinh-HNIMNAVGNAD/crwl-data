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
import os
import queue
import re
import shutil
import sys
import tempfile
import threading
import time
from typing import Optional, List, Dict, Any, Tuple, Callable
from urllib.parse import urljoin
from .base import BaseExtractor
from ..models import StreamFormat, MediaImage
from ..cookies.browser_cookies import get_browser_cookies_txt
from ..cancellation import cap_timeout, current_request_id, is_cancelled, raise_if_cancelled

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

# Đoạn "https:..." liền mạch tới ký tự phân cách. Không có phần bắt buộc nào phía
# sau nên không bao giờ phải quay lui: thời gian tuyến tính theo độ dài trang.
_URL_TOKEN_RE = re.compile(r'https:[^"\'<>\s]+')
_SOCIAL_IMAGE_CDNS = ("fbcdn.net", "cdninstagram.com", "twimg.com", "pinimg.com", "scontent")


def social_cdn_images(page_html: str) -> List[str]:
    r"""Ảnh CDN của Facebook/Instagram/X/Pinterest xuất hiện trong mã trang (theo thứ tự, không trùng).

    Trước đây dùng một regex `https:[^"'<>\s]+(?:fbcdn...)[^"'<>\s]+`: với trang
    React/Next.js (JSON nằm trong thuộc tính, dấu nháy bị mã hoá &quot;) mỗi chuỗi
    "https:" quét lại tới cuối đoạn → hàng phút trên trang 1-2 MB, và regex giữ GIL
    nên CẢ engine đứng (kể cả lệnh Huỷ). Nay tách đoạn URL bằng regex không quay lui
    rồi lọc bằng phép so chuỗi.
    """
    cleaned: List[str] = []
    for token in _URL_TOKEN_RE.findall(page_html or ""):
        if not any(cdn in token for cdn in _SOCIAL_IMAGE_CDNS):
            continue
        u = token.replace(r"\u0025", "%").replace(r"\/", "/").replace("&amp;", "&").replace(r"\\", "")
        if any(ext in u.lower() for ext in (".jpg", ".png", ".webp", ".jpeg")):
            if not any(skip in u for skip in ("rsrc.php", "p50x50", "s150x150", "emoji")):
                cleaned.append(u)
    return list(dict.fromkeys(cleaned))

# JSON response keys thường chứa stream URL
STREAM_JSON_KEYS = [
    'file', 'src', 'url', 'source', 'stream', 'video', 'hls', 'dash',
    'mp4', 'stream_url', 'video_url', 'media_url', 'playback_url',
    'sources', 'tracks', 'playlist', 'embed', 'direct',
]


def _env_seconds(name: str, default: float) -> float:
    """Đọc số giây từ biến môi trường; giá trị sai không được làm engine chết lúc import."""
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


class _PlaywrightWorkerThread(threading.Thread):
    """
    Dedicated background worker thread for Playwright sync API.
    Guarantees thread affinity so that greenlet / Playwright sync calls
    always execute on the exact same thread, preventing:
    'greenlet.error: Cannot switch to a different thread'.
    """

    # Chromium headless + driver Playwright chiếm vài trăm MB RAM. Trước đây chúng
    # sống tới khi thoát app chỉ sau MỘT lần dò; nay tự đóng khi rảnh chừng này giây
    # (lần dò sau tự khởi động lại). <= 0 = không tự đóng.
    IDLE_SHUTDOWN_SEC = _env_seconds("CRWL_SNIFFER_IDLE_SEC", 180.0)

    # Thư mục tạm riêng của mỗi lần khởi động Chromium (xem _ensure_browser)
    TMP_PREFIX = "crwl-chromium-"
    # Thư mục tạm cũ hơn chừng này giây là của lần chạy đã chết (app bị tắt ngang)
    STALE_TMP_SEC = 6 * 60 * 60

    def __init__(self):
        super().__init__(daemon=True, name="pw_sniffer_worker")
        self._tasks: queue.Queue = queue.Queue()
        self._pw = None
        self._browser = None
        self._tmpdir: Optional[str] = None
        self._started_event = threading.Event()
        self.start()

    def _idle_timeout(self) -> Optional[float]:
        """Thời gian chờ việc trước khi đóng Chromium; None = chờ mãi (chưa mở gì)."""
        if self._browser is None and self._pw is None:
            return None
        return self.IDLE_SHUTDOWN_SEC if self.IDLE_SHUTDOWN_SEC > 0 else None

    def run(self):
        self._started_event.set()
        while True:
            try:
                item = self._tasks.get(timeout=self._idle_timeout())
            except queue.Empty:
                sys.stderr.write(
                    f"[Sniffer] Đóng Chromium headless sau {self.IDLE_SHUTDOWN_SEC:.0f}s không dùng để giải phóng RAM\n"
                )
                sys.stderr.flush()
                self._cleanup()
                continue
            if item is None:
                self._cleanup()
                self._tasks.task_done()
                break
            fn, args, kwargs, result_holder, done_event = item
            # Người gửi đã bỏ cuộc (quá giờ / huỷ) trong lúc task còn xếp hàng: bỏ qua,
            # nếu không các task "ma" cứ lần lượt chạy và làm task mới timeout theo.
            if result_holder.get("abandoned"):
                done_event.set()
                self._tasks.task_done()
                continue
            try:
                result_holder["result"] = fn(*args, **kwargs)
            except Exception as e:
                result_holder["error"] = e
            finally:
                done_event.set()
                self._tasks.task_done()

    def submit(
        self,
        fn: Callable,
        *args,
        timeout: Optional[float] = None,
        stop_when: Optional[Callable[[], bool]] = None,
        **kwargs,
    ) -> Any:
        """Chạy `fn` trên luồng Playwright và chờ kết quả.

        `fn` nhận thêm `should_stop()`: trả True khi người gửi đã bỏ cuộc (quá giờ)
        hoặc `stop_when()` báo dừng (vd. request bị huỷ) — để task đang chạy tự kết
        thúc sớm thay vì chiếm luồng duy nhất cho tới hết các mốc chờ của nó.
        """
        done_event = threading.Event()
        result_holder = {"result": None, "error": None, "abandoned": False}

        def should_stop() -> bool:
            return bool(result_holder["abandoned"] or (stop_when and stop_when()))

        kwargs["should_stop"] = should_stop
        self._tasks.put((fn, args, kwargs, result_holder, done_event))
        deadline = time.monotonic() + timeout if timeout else None
        # Chờ theo từng nhịp ngắn để phát hiện huỷ ngay cả khi task đang chạy
        while not done_event.wait(timeout=0.5):
            if stop_when and stop_when():
                result_holder["abandoned"] = True
                raise TimeoutError("Playwright task đã bị huỷ")
            if deadline is not None and time.monotonic() >= deadline:
                result_holder["abandoned"] = True
                raise TimeoutError(f"Playwright worker task timed out after {timeout:.0f}s")
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

        launch_kwargs: Dict[str, Any] = {}
        proxy = os.environ.get("CRWL_PROXY")
        if proxy:
            # Chromium không tự đọc HTTP(S)_PROXY như yt-dlp/gallery-dl
            launch_kwargs["proxy"] = {"server": proxy}
        # Với --disable-dev-shm-usage, Chromium tạo tệp bộ nhớ chia sẻ trong TMPDIR và
        # không phải lúc nào cũng xoá: /tmp tích dần các ".org.chromium.Chromium.*".
        # Cho mỗi lần khởi động một thư mục tạm riêng rồi xoá nguyên thư mục khi đóng.
        self._remove_stale_tmpdirs()
        self._tmpdir = tempfile.mkdtemp(prefix=self.TMP_PREFIX)
        launch_kwargs["env"] = {**os.environ, "TMPDIR": self._tmpdir}
        self._browser = self._pw.chromium.launch(
            **launch_kwargs,
            headless=True,
            # KHÔNG dùng --disable-web-security: trình duyệt này mở trang BẤT KỲ người
            # dùng dán vào, kèm cookie đăng nhập. Tắt same-origin cho phép script của
            # trang đó đọc phản hồi từ trang khác và từ mạng nội bộ (router, NAS...).
            # Playwright vẫn đọc được iframe khác nguồn mà không cần cờ này.
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
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
        if self._tmpdir:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
            self._tmpdir = None

    def _remove_stale_tmpdirs(self) -> None:
        """Dọn thư mục tạm của các lần chạy trước bị tắt ngang (không kịp _cleanup)."""
        base = tempfile.gettempdir()
        now = time.time()
        try:
            names = os.listdir(base)
        except OSError:
            return
        for name in names:
            if not name.startswith(self.TMP_PREFIX):
                continue
            path = os.path.join(base, name)
            try:
                if now - os.path.getmtime(path) > self.STALE_TMP_SEC:
                    shutil.rmtree(path, ignore_errors=True)
            except OSError:
                continue

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
        browser: Optional[str] = "auto",
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
            # Không chờ quá ngân sách thời gian còn lại của request
            timeout_sec = cap_timeout(timeout_sec, minimum=3.0)
            req_id = current_request_id()
            result = worker.submit(
                self._do_sniff_streams,
                page_url,
                wait_ms,
                follow_iframes,
                browser,
                timeout=timeout_sec,
                stop_when=(lambda: is_cancelled(req_id)) if req_id else None,
            )
            raise_if_cancelled()
            return result
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
        cookie_browser: Optional[str] = "auto",
        should_stop: Callable[[], bool] = lambda: False,
    ) -> Dict[str, Any]:
        """Thực thi sniffing trên luồng chuyên biệt của Playwright worker"""
        worker = self._get_worker()
        browser = worker._ensure_browser()

        captured_urls, iframe_urls, captured_images = self._sniff_page(
            browser, page_url, wait_ms, should_stop, cookie_browser
        )

        # Nếu chưa tìm được stream từ main page, thử navigate từng iframe embed
        if not captured_urls and iframe_urls and follow_iframes:
            for iframe_url in iframe_urls[:3]:  # Giới hạn 3 iframes
                if should_stop():
                    break
                self.log(f"[Sniffer] Thử sniff iframe: {iframe_url[:100]}")
                try:
                    i_urls, _, _ = self._sniff_page(
                        browser, iframe_url, min(wait_ms, 8000), should_stop, cookie_browser
                    )
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
        should_stop: Callable[[], bool] = lambda: False,
        cookie_browser: Optional[str] = "auto",
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
            self._apply_cookies(context, target_url, cookie_browser)

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

            # Đợi để JS chạy và requests được phát — theo từng nhịp ngắn để dừng
            # ngay khi người gửi đã bỏ cuộc hoặc request bị huỷ.
            waited = 0
            actual_wait = min(wait_ms, 12000)
            while waited < actual_wait and not should_stop():
                step = min(500, actual_wait - waited)
                page.wait_for_timeout(step)
                waited += step
            if should_stop():
                return captured_urls, iframe_urls, captured_images

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
                deduped_imgs = social_cdn_images(page.content())
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

    # ─────────────────────────────────────────────────────────────────────────
    # Cookie đăng nhập
    # ─────────────────────────────────────────────────────────────────────────

    def _apply_cookies(self, context, target_url: str, cookie_browser: Optional[str]) -> int:
        """Nạp cookie của ĐÚNG tên miền trang vào phiên trình duyệt; trả về số cookie.

        Trước đây luôn lấy cookie ("auto") kể cả khi người dùng chọn không dùng cookie.
        """
        if cookie_browser == "none":
            return 0
        from .page_media import load_cookie_jar

        cfile = None
        try:
            import urllib.parse
            host = urllib.parse.urlparse(target_url).hostname or ""
            cfile = get_browser_cookies_txt(cookie_browser or "auto", domain=host)
            jar = load_cookie_jar(cfile) if cfile and os.path.exists(cfile) else None
            pw_cookies = []
            for c in (jar or []):
                domain = c.domain
                if not domain.startswith(".") and c.domain_specified:
                    domain = "." + domain
                cookie = {
                    "name": c.name,
                    "value": c.value,
                    "domain": domain,
                    "path": c.path or "/",
                    "secure": bool(c.secure),
                }
                if c.expires:
                    cookie["expires"] = float(c.expires)
                pw_cookies.append(cookie)
            if pw_cookies:
                context.add_cookies(pw_cookies)
            return len(pw_cookies)
        except Exception as err:
            self.warn(f"[Sniffer] Không nạp được cookie: {err}")
            return 0
        finally:
            # get_browser_cookies_txt() luôn tạo một tệp tạm MỚI chứa cookie đăng
            # nhập. Trước đây tệp này không bao giờ bị xoá: mỗi lần dò (trang chính
            # + tối đa 3 iframe) lại để lại một bản sao phiên đăng nhập trong /tmp.
            if cfile and os.path.exists(cfile):
                try:
                    os.remove(cfile)
                except OSError:
                    pass

    # ─────────────────────────────────────────────────────────────────────────
    # Thu thập TOÀN BỘ media của một bài viết đã dựng xong (page_media dùng)
    # ─────────────────────────────────────────────────────────────────────────

    # Số bước cuộn tối đa để nạp ảnh lazy-load; trang cuộn vô tận dừng ở đây
    COLLECT_MAX_SCROLL_STEPS = 40
    COLLECT_MAX_ITEMS = 3000

    def collect_page_media(self, page_url: str, browser: Optional[str] = "auto") -> Dict[str, Any]:
        """Mở trang bằng Chromium (kèm cookie), cuộn hết trang rồi đọc mọi ảnh/video.

        Trả về dữ liệu thô {"dom", "frames", "network", "error"} để page_media chọn
        đúng phần của bài viết.
        """
        if not self._playwright_available:
            return {"dom": None, "frames": [], "network": [], "error": "Playwright chưa được cài đặt"}
        try:
            worker = self._get_worker()
            timeout_sec = cap_timeout(
                self.PLAYWRIGHT_TIMEOUT / 1000.0 + 45.0,
                minimum=5.0,
            )
            req_id = current_request_id()
            result = worker.submit(
                self._do_collect_page_media,
                page_url,
                browser,
                timeout=timeout_sec,
                stop_when=(lambda: is_cancelled(req_id)) if req_id else None,
            )
            raise_if_cancelled()
            return result
        except Exception as e:
            if type(e).__name__ in ("RequestCancelled", "RequestTimedOut"):
                raise
            self.warn(f"[Sniffer] Không đọc được trang bằng trình duyệt: {e}")
            return {"dom": None, "frames": [], "network": [], "error": str(e)}

    def _do_collect_page_media(
        self,
        page_url: str,
        cookie_browser: Optional[str] = "auto",
        should_stop: Callable[[], bool] = lambda: False,
    ) -> Dict[str, Any]:
        from .page_media import DOM_IMG_ATTRS, _ARTICLE_ITEMTYPES, _CHROME_WORDS, _MAIN_TOKENS

        worker = self._get_worker()
        browser = worker._ensure_browser()
        network: List[Dict[str, Any]] = []
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 900},
            ignore_https_errors=True,
            java_script_enabled=True,
            locale="vi-VN",
        )
        try:
            used = self._apply_cookies(context, page_url, cookie_browser)
            if used:
                self.log(f"[Sniffer] Mở trang kèm {used} cookie đăng nhập")
            page = context.new_page()

            def on_response(response):
                # Chỉ ghi URL + loại nội dung, KHÔNG đọc thân phản hồi (video vài GB)
                if len(network) >= 800:
                    return
                try:
                    url = response.url
                    ctype = (response.headers.get("content-type") or "").lower()
                    length = response.headers.get("content-length") or 0
                except Exception:
                    return
                if (
                    ctype.startswith(("video/", "audio/"))
                    or "mpegurl" in ctype
                    or "dash+xml" in ctype
                    or _COLLECT_MEDIA_URL_RE.search(url.lower())
                ):
                    network.append({"url": url, "ctype": ctype, "length": length})

            page.on("response", on_response)
            try:
                page.goto(page_url, timeout=self.PLAYWRIGHT_TIMEOUT, wait_until="domcontentloaded")
            except Exception as nav_err:
                self.warn(f"[Sniffer] Lỗi mở trang: {nav_err}")
            if should_stop():
                return {"dom": None, "frames": [], "network": network, "error": "Đã dừng"}
            try:
                page.wait_for_load_state("load", timeout=6000)
            except Exception:
                pass

            self._auto_scroll(page, should_stop)
            if should_stop():
                return {"dom": None, "frames": [], "network": network, "error": "Đã dừng"}

            # Video phát bằng blob: (MSE) chỉ tải manifest khi bắt đầu phát
            try:
                started = page.evaluate(_PLAY_HIDDEN_VIDEOS_JS) or 0
            except Exception:
                started = 0
            if started:
                self._wait(page, 2500, should_stop)

            opts = {
                "max": self.COLLECT_MAX_ITEMS,
                "chromeWords": sorted(_CHROME_WORDS),
                "mainTokens": sorted(_MAIN_TOKENS),
                "articleItemtypes": list(_ARTICLE_ITEMTYPES),
                "imgAttrs": list(DOM_IMG_ATTRS),
            }
            dom = page.evaluate(_COLLECT_PAGE_MEDIA_JS, opts)
            frames: List[Dict[str, Any]] = []
            for frame in page.frames[1:21]:
                if should_stop():
                    break
                try:
                    if not frame.url or frame.url == "about:blank":
                        continue
                    frames.append({"url": frame.url, "items": frame.evaluate(_COLLECT_FRAME_VIDEOS_JS)})
                except Exception:
                    continue
            return {"dom": dom, "frames": frames, "network": network, "error": None}
        finally:
            try:
                context.close()
            except Exception:
                pass

    def _wait(self, page, ms: int, should_stop: Callable[[], bool]) -> None:
        waited = 0
        while waited < ms and not should_stop():
            step = min(250, ms - waited)
            page.wait_for_timeout(step)
            waited += step

    def _auto_scroll(self, page, should_stop: Callable[[], bool]) -> None:
        """Cuộn tới cuối trang theo từng màn hình để ảnh lazy-load được nạp."""
        at_bottom = 0
        for _ in range(self.COLLECT_MAX_SCROLL_STEPS):
            if should_stop():
                return
            try:
                done = page.evaluate(_SCROLL_STEP_JS)
            except Exception:
                return
            self._wait(page, 300, should_stop)
            if done:
                at_bottom += 1
                if at_bottom >= 2:
                    break
            else:
                at_bottom = 0
        try:
            page.evaluate("() => window.scrollTo(0, 0)")
        except Exception:
            pass
        self._wait(page, 300, should_stop)

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
        # .ts / .m4s là MẢNH vài giây của một luồng HLS/DASH. Trước đây mỗi mảnh bắt
        # được thành một lựa chọn "TS Segment Stream" riêng — tải về chỉ được vài giây
        # video. Có manifest hoặc tệp video đầy đủ thì bỏ hẳn các mảnh; không có thì
        # giữ một mảnh mỗi loại như phương án cuối.
        has_full_source = any(
            self._classify(u)[0] in ("hls", "dash", "mp4", "webm") for u in sorted_urls
        )

        for url in sorted_urls:
            key, fmt, quality, stype, _ = self._classify(url)

            if key in ("ts", "m4s") and has_full_source:
                continue
            # Chỉ lấy 1 stream mỗi loại (hls, mp4, dash, mảnh ts...); riêng "other"
            # có thể là nhiều manifest khác nhau nên giữ cả.
            if key in seen_types and key != "other":
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


# ─────────────────────────────────────────────────────────────────────────────
# Script chạy trong trang (collect_page_media)
# ─────────────────────────────────────────────────────────────────────────────

_COLLECT_MEDIA_URL_RE = re.compile(r"\.(?:m3u8|mpd|mp4|webm|m4v|mov)(?:[?#]|$)")

_SCROLL_STEP_JS = """() => {
  window.scrollBy(0, Math.max(400, Math.floor(window.innerHeight * 0.85)));
  const el = document.scrollingElement || document.documentElement;
  return (window.innerHeight + window.scrollY) >= (el.scrollHeight - 4);
}"""

_PLAY_HIDDEN_VIDEOS_JS = """() => {
  let started = 0;
  for (const v of document.querySelectorAll('video')) {
    if (started >= 5) break;
    const src = v.currentSrc || v.getAttribute('src') || '';
    if (src && !src.startsWith('blob:')) continue;
    try {
      v.muted = true;
      const p = v.play();
      if (p && p.catch) p.catch(() => {});
      started++;
    } catch (e) {}
  }
  return started;
}"""

_COLLECT_FRAME_VIDEOS_JS = """() => Array.from(document.querySelectorAll('video')).slice(0, 50).map((v) => ({
  kind: 'video',
  src: v.currentSrc || v.getAttribute('src') || v.getAttribute('data-src') || '',
  sources: Array.from(v.querySelectorAll('source')).slice(0, 10).map((s) => ({ src: s.src || s.getAttribute('data-src') || '', type: s.type || '' })),
  poster: v.poster || v.getAttribute('data-poster') || '',
}))"""

# Cùng quy tắc phân vùng với page_media.container_level (bài viết / khung chính /
# vùng viền), để HTML tĩnh và DOM đã dựng được chọn bằng một thuật toán duy nhất.
_COLLECT_PAGE_MEDIA_JS = """(opts) => {
  const MAX = opts.max;
  const CHROME = new Set(opts.chromeWords);
  const MAIN = new Set(opts.mainTokens);
  const ITEMTYPES = opts.articleItemtypes;
  const IMG_ATTRS = opts.imgAttrs;
  const ids = new Map();
  const containers = [];
  const levelOf = (el) => {
    const tag = el.tagName.toLowerCase();
    const role = (el.getAttribute('role') || '').toLowerCase();
    const itemprop = (el.getAttribute('itemprop') || '').toLowerCase();
    const itemtype = (el.getAttribute('itemtype') || '').toLowerCase();
    if (tag === 'article' || role === 'article' || itemprop === 'blogpost' || itemprop === 'articlebody'
        || (itemtype && ITEMTYPES.some((t) => itemtype.includes(t)))) return 'article';
    if (tag === 'aside' || tag === 'nav' || tag === 'footer' || role === 'navigation'
        || role === 'complementary' || role === 'contentinfo') return 'chrome';
    const cls = typeof el.className === 'string' ? el.className : (el.getAttribute('class') || '');
    const tokens = (cls + ' ' + (el.id || '')).toLowerCase().split(/\\s+/).filter(Boolean).slice(0, 40)
      .map((t) => t.replace(/_/g, '-'));
    if (tag !== 'body' && tag !== 'html' && tag !== 'main') {
      for (const t of tokens) {
        if (CHROME.has(t)) return 'hint';
        for (const w of t.split('-')) if (w && CHROME.has(w)) return 'hint';
      }
    }
    if (tag === 'main' || role === 'main') return 'main';
    for (const t of tokens) if (MAIN.has(t)) return 'body';
    if (tag === 'header' || role === 'banner') return 'header';
    return null;
  };
  const pathOf = (el) => {
    const chain = [];
    for (let p = el.parentElement; p && chain.length < 400; p = p.parentElement) chain.push(p);
    chain.reverse();
    const path = [];
    let parent = null;
    for (const p of chain) {
      let id = ids.get(p);
      if (id === undefined) {
        const level = path.length < 64 ? levelOf(p) : null;
        if (level) {
          id = containers.length;
          containers.push({ id, level, parent, tag: p.tagName.toLowerCase(), text: (p.textContent || '').length });
        } else {
          id = null;
        }
        ids.set(p, id);
      }
      if (id !== null) { path.push(id); parent = id; }
    }
    return path;
  };
  const items = [];
  const push = (o) => { if (items.length < MAX) items.push(o); };
  const attrsOf = (el) => {
    const r = {};
    for (const n of IMG_ATTRS) {
      const v = el.getAttribute(n);
      if (v) r[n] = v.slice(0, 8192);
    }
    return r;
  };
  for (const img of Array.from(document.images).slice(0, MAX)) {
    const r = img.getBoundingClientRect();
    const a = img.closest('a[href]');
    const pic = img.parentElement && img.parentElement.tagName === 'PICTURE' ? img.parentElement : null;
    push({
      kind: 'img',
      src: img.currentSrc || img.getAttribute('src') || '',
      attrs: attrsOf(img),
      pictureSrcsets: pic ? Array.from(pic.querySelectorAll('source')).slice(0, 10)
        .map((s) => s.getAttribute('srcset') || s.getAttribute('data-srcset') || '').filter(Boolean) : [],
      link: a ? a.href : '',
      nw: img.naturalWidth || 0, nh: img.naturalHeight || 0,
      rw: Math.round(r.width), rh: Math.round(r.height),
      path: pathOf(img),
    });
  }
  for (const v of Array.from(document.querySelectorAll('video')).slice(0, 200)) {
    push({
      kind: 'video',
      src: v.currentSrc || v.getAttribute('src') || v.getAttribute('data-src') || '',
      sources: Array.from(v.querySelectorAll('source')).slice(0, 10)
        .map((s) => ({ src: s.src || s.getAttribute('data-src') || '', type: s.type || '' })),
      poster: v.poster || v.getAttribute('data-poster') || '',
      path: pathOf(v),
    });
  }
  for (const f of Array.from(document.querySelectorAll('iframe')).slice(0, 200)) {
    push({ kind: 'iframe', src: f.src || f.getAttribute('data-src') || '', path: pathOf(f) });
  }
  for (const a of Array.from(document.querySelectorAll('a[href]')).slice(0, 5000)) {
    if (items.length >= MAX) break;
    if (a.querySelector('img,video,picture')) continue;
    if (/\\.(jpe?g|png|gif|webp|avif|mp4|webm|mov|m4v|m3u8)$/i.test(a.pathname || '')) {
      push({ kind: 'a', src: a.href, path: pathOf(a) });
    }
  }
  let scanned = 0;
  const SKIP = new Set(['IMG', 'VIDEO', 'AUDIO', 'SOURCE', 'SCRIPT', 'STYLE', 'IFRAME', 'LINK', 'META', 'INPUT', 'svg']);
  if (document.body) {
    for (const el of document.body.getElementsByTagName('*')) {
      if (++scanned > 8000 || items.length >= MAX) break;
      const tag = el.tagName;
      if (SKIP.has(tag)) continue;
      // Ảnh nằm trong thuộc tính data-* (khung so sánh trước/sau, slider, nền lazy)
      let dataAttrs = null;
      for (const a of el.attributes) {
        if (!a.name.startsWith('data-') || !a.value || a.value.length > 4096) continue;
        if (!/\\.(jpe?g|png|gif|webp|avif|bmp|heic|jfif)(\\?|#|\\s|,|$)/i.test(a.value)) continue;
        (dataAttrs = dataAttrs || {})[a.name] = a.value;
      }
      if (dataAttrs) push({ kind: 'data', attrs: dataAttrs, path: pathOf(el) });
      const r = el.getBoundingClientRect();
      if (r.width < 200 || r.height < 150) continue;
      const bg = getComputedStyle(el).backgroundImage;
      if (!bg || bg === 'none' || bg.indexOf('url(') < 0) continue;
      const m = /url\\(\\s*["']?([^"')]+)["']?\\s*\\)/.exec(bg);
      if (m) push({ kind: 'bg', src: m[1], rw: Math.round(r.width), rh: Math.round(r.height), path: pathOf(el) });
    }
  }
  const meta = {};
  for (const m of document.querySelectorAll('meta[property],meta[name]')) {
    const k = (m.getAttribute('property') || m.getAttribute('name') || '').toLowerCase();
    if (k && !(k in meta)) meta[k] = (m.getAttribute('content') || '').slice(0, 2000);
  }
  const ld = Array.from(document.querySelectorAll('script[type="application/ld+json"]')).slice(0, 20)
    .map((s) => (s.textContent || '').slice(0, 500000));
  return {
    url: location.href,
    title: document.title || '',
    containers, items, meta, ld,
    hasPassword: !!document.querySelector('input[type="password"]'),
    textLen: document.body ? (document.body.textContent || '').length : 0,
  };
}"""


# Singleton lazy-loaded
_sniffer: Optional[PlaywrightStreamSniffer] = None


def get_sniffer() -> PlaywrightStreamSniffer:
    global _sniffer
    if _sniffer is None:
        _sniffer = PlaywrightStreamSniffer()
    return _sniffer

