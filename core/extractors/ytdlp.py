"""
yt-dlp Extractor Engine.
Wraps yt-dlp binary to extract rich video/audio metadata and playlist crawl data.
Ported from YtDlpService into standalone Python.
"""

import json
import math
import os
import re
import subprocess
import threading
from typing import Optional, List, Dict, Any, Tuple
from .base import BaseExtractor
from .media_probe import AUDIO_FILE_EXTS, DIRECT_MEDIA_EXTS, MediaProbe, ProbeInfo, path_ext
from . import ytdlp_runtime
from ..models import (
    MediaMetadata,
    StreamFormat,
    SubtitleItem,
    ChapterItem,
    CrawlMediaItem,
    ProfileCrawlResult,
)
from ..cookies.browser_cookies import browser_store_readable, get_browser_cookies_txt


YOUTUBE_VIDEO_ID_REGEX = re.compile(r"^[a-zA-Z0-9_-]{11}$")

# Lỗi không thể do cookie gây ra: chạy lại yt-dlp không cookie chỉ lặp lại đúng lỗi đó
# (mỗi lần thêm 1-5 giây). Trang web thường không có video → "Unsupported URL" là lỗi
# gặp nhiều nhất ở bước dò trang bất kỳ.
_COOKIE_INDEPENDENT_ERROR_RE = re.compile(
    r"Unsupported URL|is not a valid URL|Name or service not known|Temporary failure in name resolution|"
    r"No address associated with hostname|nodename nor servname|getaddrinfo failed|Failed to resolve|"
    r"HTTP Error 404|HTTP Error 410",
    re.IGNORECASE,
)

AUDIO_PRESETS = (
    ("opus_best", "OPUS Chuẩn gốc (160 kbps, không suy hao)", "OPUS"),
    ("mp3_320k", "MP3 Chất lượng cao (320 kbps)", "MP3"),
    ("mp3_192k", "MP3 Chuẩn phổ biến (192 kbps)", "MP3"),
    ("m4a_aac", "M4A / AAC Gốc (256 kbps)", "M4A"),
    ("ogg_vorbis", "OGG Vorbis (192 kbps)", "OGG"),
    ("flac_lossless", "FLAC Âm thanh lossless (phòng thu)", "FLAC"),
    ("wav_lossless", "WAV Bản ghi không nén (Uncompressed)", "WAV"),
    ("alac_lossless", "ALAC Chuẩn Apple Lossless", "ALAC"),
)


def audio_preset_streams() -> List[StreamFormat]:
    return [
        StreamFormat(
            format_id=fid,
            quality=label,
            format=ext,
            size=None,
            raw_size=None,
            stream_type="audio",
            has_audio=True,
            has_video=False,
        )
        for fid, label, ext in AUDIO_PRESETS
    ]


def height_label(h: int) -> str:
    if h >= 2160:
        return "4K (2160p Ultra HD)"
    if h >= 1440:
        return "2K (1440p Quad HD)"
    if h >= 1080:
        return "Full HD (1080p)"
    if h >= 720:
        return "HD (720p)"
    if h >= 480:
        return "Chuẩn SD (480p)"
    if h >= 360:
        return "Tiết kiệm (360p)"
    return f"Thấp ({h}p)"


class PlaylistUrlError(RuntimeError):
    """Liên kết là một DANH SÁCH (playlist, kênh, trang cá nhân), không phải một video.

    Trước đây `--dump-json` bóc tách đầy đủ TỪNG video của danh sách: playlist lớn
    luôn quá 30 giây, rồi chuỗi fallback để web_scraper vớt ảnh og:image và trả về
    "thành công" một album giả chỉ có ảnh bìa. Playlist nhỏ thì UI hiện thông tin
    video đầu tiên nhưng bấm tải lại kéo về cả danh sách. Dispatcher phải ném lỗi
    này thẳng ra ngoài, không thử engine khác.

    `entry_urls`: URL các mục yt-dlp liệt kê. Một bài viết nhúng 2 video YouTube cũng bị
    yt-dlp coi là "danh sách" — các mục khi đó trỏ sang tên miền KHÁC trang gốc, nhờ
    vậy dispatcher phân biệt được với playlist/kênh thật.
    """

    def __init__(self, message: str, entry_urls: Optional[List[str]] = None):
        super().__init__(message)
        self.entry_urls = list(entry_urls or [])


class YtDlpExtractor(BaseExtractor):
    """Wrapper cho yt-dlp binary"""

    # (đường dẫn yt-dlp, mtime) -> bản yt-dlp có hiểu `--js-runtimes` không
    _js_runtimes_support: Dict[Tuple[str, float], bool] = {}
    _js_runtimes_lock = threading.Lock()

    def __init__(self):
        super().__init__()
        self.binary_path = self.find_binary("yt-dlp", "YT_DLP_PATH")
        self.prober = MediaProbe()

    def supports_js_runtimes(self) -> bool:
        """Bản yt-dlp đang dùng có tuỳ chọn `--js-runtimes` không.

        Tuỳ chọn này chỉ có từ các bản yt-dlp cuối 2025. Bản cũ (vd. gói apt của
        Ubuntu/Debian) gặp nó là thoát ngay với "no such option: --js-runtimes":
        trước đây máy nào có Node.js là MỌI lượt bóc tách đều thất bại. Kết quả được
        nhớ theo mtime để lần cập nhật yt-dlp sau được kiểm tra lại.
        """
        path = self.binary_path or ""
        try:
            key = (path, os.path.getmtime(path))
        except OSError:
            return False
        with self._js_runtimes_lock:
            cached = self._js_runtimes_support.get(key)
        if cached is not None:
            return cached
        # Bản giải nén đã đọc sẵn mã nguồn: khỏi chạy `--help` (0,25-0,75 giây)
        runtime = ytdlp_runtime.lookup(path)
        if runtime is not None:
            with self._js_runtimes_lock:
                self._js_runtimes_support[key] = runtime.js_runtimes
            return runtime.js_runtimes
        try:
            out = subprocess.run(
                [*self.command(), "--help"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
            )
            supported = "--js-runtimes" in (out.stdout or "")
        except (OSError, subprocess.SubprocessError):
            # Không đọc được trợ giúp thì đừng thêm cờ: thiếu cờ chỉ làm YouTube kém
            # đi, còn thêm nhầm thì yt-dlp cũ thất bại hoàn toàn.
            supported = False
        with self._js_runtimes_lock:
            self._js_runtimes_support[key] = supported
        return supported

    def command(self) -> List[str]:
        """Lệnh chạy yt-dlp (bản zipapp chạy từ bản giải nén — xem ytdlp_runtime)."""
        return ytdlp_runtime.command_for(self.binary_path) if self.binary_path else [""]

    def is_available(self) -> bool:
        # Cài yt-dlp SAU khi engine đã khởi động thì lần dò lúc khởi động không thấy:
        # dò lại thay vì báo "không tìm thấy" mãi tới khi khởi động lại engine.
        if not (self.binary_path and os.path.exists(self.binary_path)):
            self.binary_path = self.find_binary("yt-dlp", "YT_DLP_PATH")
        return self.binary_path is not None and os.path.exists(self.binary_path)

    def get_base_args(self, allow_playlist: bool = False, browser: Optional[str] = None, target_url: str = "") -> Tuple[List[str], Optional[str]]:
        """Xây dựng arguments cơ bản cho yt-dlp"""
        args = [
            "--no-warnings",
            "--ignore-errors",
            "--socket-timeout", "15",   # Ngắt kết nối sau 15s idle (tránh treo vô thời hạn)
            "--retries", "2",           # Chỉ thử lại 2 lần
            "--fragment-retries", "2",
        ]


        # Sử dụng Node.js runtime cho YouTube n-sig solver nếu có — và chỉ khi bản
        # yt-dlp hiểu tuỳ chọn này (xem supports_js_runtimes).
        node_bin = self.find_binary("node")
        if node_bin and os.path.exists(node_bin) and self.supports_js_runtimes():
            args.extend(["--js-runtimes", f"node:{node_bin}"])

        if not allow_playlist:
            args.append("--no-playlist")

        tmp_cookie_file = None
        if browser != "none":
            # Thử tự động xuất cookies từ trình duyệt Linux
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
            elif browser and browser not in ("auto", "") and not browser_store_readable(browser):
                # Chỉ nhờ yt-dlp tự đọc trình duyệt khi engine KHÔNG đọc được kho cookie đó
                # (khoá, mã hoá lạ...). Đọc được mà không có cookie nào của tên miền này thì
                # yt-dlp cũng chẳng tìm thấy gì — trước đây nó vẫn giải mã lại toàn bộ kho
                # cookie trong MỖI lần chạy với mọi trang không cần đăng nhập.
                args.extend(["--cookies-from-browser", browser])

        return args, tmp_cookie_file

    @staticmethod
    def _uses_cookies(args: List[str]) -> bool:
        return "--cookies" in args or "--cookies-from-browser" in args

    @classmethod
    def _should_retry_without_cookies(cls, args: List[str], stderr: str) -> bool:
        """Chạy lại không cookie chỉ khi lần đầu CÓ dùng cookie và lỗi có thể do cookie.

        Trước đây mọi lỗi đều chạy lại yt-dlp thêm một lần — kể cả khi không có cookie
        nào được gửi (lệnh y hệt) hoặc lỗi là "Unsupported URL" / 404 / sai tên miền.
        """
        return cls._uses_cookies(args) and not _COOKIE_INDEPENDENT_ERROR_RE.search(stderr or "")

    def extract_metadata(
        self,
        url: str,
        browser: Optional[str] = None,
        timeout: int = 30,
        referer: Optional[str] = None,
    ) -> MediaMetadata:
        """Trích xuất chi tiết metadata của 1 URL video/audio.

        `referer`: trang chứa luồng (luồng tìm thấy bên trong trang phim) — máy chủ
        luồng hay chặn yêu cầu thiếu Referer đúng trang.
        """
        if not self.is_available():
            raise RuntimeError("yt-dlp binary không được tìm thấy trên hệ thống.")

        args, tmp_cookie = self.get_base_args(allow_playlist=False, browser=browser, target_url=url)
        if referer:
            args += ["--referer", referer]
        # `--flat-playlist` không đổi gì với một video đơn (vẫn bóc tách đầy đủ), nhưng
        # với playlist/kênh thì chỉ liệt kê nhanh các mục thay vì bóc tách từng video
        # tới khi quá giờ — nhờ đó nhận ra ngay đây là danh sách (PlaylistUrlError).
        cmd = self.command() + args + ["--flat-playlist", "--dump-json", url]

        self.log(f"yt-dlp extract: {url}")
        try:
            code, stdout, stderr = self.run_process(cmd, timeout=timeout)
        finally:
            if tmp_cookie and os.path.exists(tmp_cookie):
                try:
                    os.remove(tmp_cookie)
                except Exception:
                    pass

        retry_plain = browser != "none" and self._should_retry_without_cookies(args, stderr)
        if code != 0 or not stdout.strip():
            # Quá giờ thì chạy lại lần nữa (không cookie) chỉ tốn gấp đôi thời gian.
            if self.is_timeout(code, stderr):
                raise RuntimeError(f"yt-dlp quá thời gian {timeout}s khi đọc thông tin liên kết.")
            if retry_plain:
                self.warn("yt-dlp extract lỗi với cookies, đang thử lại không dùng cookies ('none')...")
                return self.extract_metadata(url, browser="none", timeout=timeout, referer=referer)
            raise RuntimeError(f"yt-dlp extract thất bại: {stderr.strip() or f'Exit code {code}'}")

        # Lấy dòng JSON đầu tiên
        json_line = None
        for line in stdout.strip().split("\n"):
            if line.strip().startswith("{"):
                json_line = line.strip()
                break

        if not json_line:
            if retry_plain:
                self.warn("yt-dlp không trả metadata hợp lệ với cookies, thử lại không cookie...")
                return self.extract_metadata(url, browser="none", timeout=timeout, referer=referer)
            raise ValueError("Không tìm thấy dữ liệu JSON hợp lệ từ kết quả yt-dlp.")

        try:
            raw_data = json.loads(json_line)
        except json.JSONDecodeError as e:
            if retry_plain:
                self.warn("yt-dlp trả JSON lỗi với cookies, thử lại không cookie...")
                return self.extract_metadata(url, browser="none", timeout=timeout, referer=referer)
            raise ValueError(f"Lỗi parse JSON yt-dlp: {e}")

        # Mục "phẳng" (chỉ là liên kết tới video khác) chỉ xuất hiện khi URL là một
        # danh sách — xem chú thích của PlaylistUrlError.
        if raw_data.get("_type") in ("url", "url_transparent"):
            raise PlaylistUrlError(self._playlist_message(raw_data, stdout), self._entry_urls(stdout))

        meta = self._normalize_metadata(raw_data, url)
        probe_target = self._probe_target(raw_data)
        if probe_target:
            self._refine_with_probe(meta, raw_data, probe_target, referer)
        meta.extractor_key = str(raw_data.get("extractor_key") or raw_data.get("extractor") or "") or None
        meta.webpage_url = raw_data.get("webpage_url") if isinstance(raw_data.get("webpage_url"), str) else None
        meta.is_direct_media = bool(raw_data.get("direct"))
        count = raw_data.get("playlist_count") or raw_data.get("n_entries")
        meta.page_video_count = count if isinstance(count, int) and count > 0 else None
        return meta

    # ─────────────────────────────────────────────────────────────────────────
    # Link tệp / luồng không khai báo codec
    # ─────────────────────────────────────────────────────────────────────────

    @staticmethod
    def _formats_of(raw: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Danh sách định dạng như yt-dlp hiểu (YoutubeDL._get_formats).

        Link tệp trực tiếp ("URL could be a direct video link") chỉ có `url` ở cấp
        ngoài, KHÔNG có `formats`. Trước đây danh sách rỗng đó khiến kết quả chỉ còn
        các lựa chọn âm thanh: dán link .mp4/.ts/.m4s mà máy chủ trả
        "application/octet-stream" là chỉ thấy MP3.
        """
        formats = raw.get("formats")
        if isinstance(formats, list) and formats:
            return [f for f in formats if isinstance(f, dict)]
        if isinstance(raw.get("url"), str) and raw.get("url"):
            return [raw]
        return []

    @classmethod
    def _probe_target(cls, raw: Dict[str, Any]) -> Optional[str]:
        """URL tệp / luồng cần hỏi ffprobe vì yt-dlp không biết nó có hình / tiếng gì
        (tệp trực tiếp, danh sách phát HLS của một chất lượng), hoặc None.

        Chỉ dò khi đó đúng là tệp media — không bao giờ dò địa chỉ một TRANG web mà
        extractor của nền tảng trả về một định dạng thiếu thông tin.
        """
        if raw.get("is_live") or raw.get("live_status") == "is_live":
            return None
        formats = cls._formats_of(raw)
        if len(formats) != 1:
            return None
        f = formats[0]
        target = f.get("url")
        if not isinstance(target, str) or not target.startswith(("http://", "https://")):
            return None
        if raw.get("direct"):
            return target
        if f.get("height") or f.get("width") or f.get("vcodec") or f.get("acodec"):
            return None
        is_manifest = str(f.get("protocol") or "").startswith(("m3u8", "http_dash_segments"))
        return target if is_manifest or path_ext(target) in DIRECT_MEDIA_EXTS else None

    def _refine_with_probe(self, meta: MediaMetadata, raw: Dict[str, Any], target: str, referer: Optional[str]) -> None:
        """Dựng lại danh sách định dạng theo luồng hình / tiếng THẬT mà ffprobe đọc được."""
        info = self.prober.probe(target, referer=referer)
        if info is None:
            return
        ext = path_ext(target) or str(raw.get("ext") or "")
        if not meta.duration and info.duration:
            meta.duration = self.format_duration(info.duration)
        streams, note = self.streams_from_probe(info, ext)
        meta.streams = streams
        if note and not meta.description:
            meta.description = note

    @staticmethod
    def streams_from_probe(info: ProbeInfo, ext: str) -> Tuple[List[StreamFormat], Optional[str]]:
        """Định dạng tải được của MỘT tệp / luồng theo kết quả ffprobe, kèm ghi chú (ℹ️)."""
        # Luồng HLS/DASH và mảnh fMP4 được ghép / đổi sang MP4 khi tải
        container = "MP4" if ext in ("", "m3u8", "mpd", "m4s", "cmfv") else ext.upper()
        label = height_label(info.height) if info.height else None
        if info.has_video and info.has_audio:
            return [
                StreamFormat(
                    format_id="bestvideo+bestaudio/best",
                    quality=f"{label} — Có âm thanh" if label else "Video tốt nhất hiện có — Có âm thanh",
                    format=container,
                    stream_type="full",
                    has_audio=True,
                    has_video=True,
                    vcodec=info.vcodec,
                    acodec=info.acodec,
                ),
                *audio_preset_streams(),
            ], None
        if info.has_video:
            # Không có tiếng thì cũng không có gì để tách ra MP3: bỏ các lựa chọn âm thanh.
            return [
                StreamFormat(
                    format_id="best",
                    quality=f"{info.height}p (Chỉ video / Không tiếng)" if info.height else "Video (Không tiếng)",
                    format=container,
                    stream_type="mute",
                    has_audio=False,
                    has_video=True,
                    vcodec=info.vcodec,
                ),
            ], (
                "ℹ️ Liên kết này chỉ có HÌNH, không có tiếng — nhiều trang tách hình và tiếng thành "
                "hai luồng riêng. Muốn có tiếng, hãy dán link danh sách phát tổng (.m3u8/.mpd) "
                "hoặc link trang chứa video."
            )
        return audio_preset_streams(), (
            "ℹ️ Liên kết này là luồng ÂM THANH (không có hình) nên chỉ tải được âm thanh. "
            "Hình thường nằm ở một luồng khác: hãy dán link danh sách phát tổng (.m3u8/.mpd) "
            "hoặc link trang chứa video."
        )

    @staticmethod
    def _entry_urls(stdout: str, limit: int = 50) -> List[str]:
        urls: List[str] = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            value = entry.get("url") or entry.get("webpage_url")
            if isinstance(value, str) and value.startswith(("http://", "https://")):
                urls.append(value)
            if len(urls) >= limit:
                break
        return urls

    @staticmethod
    def _playlist_message(entry: Dict[str, Any], stdout: str) -> str:
        count = entry.get("playlist_count") or entry.get("n_entries")
        if not isinstance(count, int) or count <= 0:
            count = sum(1 for line in stdout.splitlines() if line.strip().startswith("{"))
        title = entry.get("playlist_title") or entry.get("playlist")
        name = f" «{title}»" if title else ""
        return (
            f"Liên kết này là một danh sách{name} gồm {count} mục (playlist, kênh hoặc trang "
            f"nhiều video), không phải một video đơn. Hãy dán liên kết vào khung "
            f"'Tải theo tài khoản' để quét và chọn video cần tải, hoặc dán liên kết của từng video."
        )

    def extract_video_posters(self, url: str, browser: Optional[str] = None, timeout: int = 25) -> List[Tuple[str, str]]:
        """Danh sách (media_id, ảnh poster) của mọi video trong một bài đăng.

        gallery-dl không trả ảnh bìa cho video (vd. X/Twitter) trong khi yt-dlp thì có,
        nên dùng để bổ sung poster cho từng video của bài nhiều video.
        """
        if not self.is_available():
            return []
        args, tmp_cookie = self.get_base_args(allow_playlist=True, browser=browser, target_url=url)
        cmd = [*self.command(), *args, "--dump-json", url]
        try:
            code, stdout, _ = self.run_process(cmd, timeout=timeout)
        finally:
            if tmp_cookie and os.path.exists(tmp_cookie):
                try:
                    os.remove(tmp_cookie)
                except Exception:
                    pass
        posters: List[Tuple[str, str]] = []
        for line in stdout.splitlines():
            if not line.startswith("{"):
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                continue
            thumb = raw.get("thumbnail")
            if isinstance(thumb, str) and thumb.startswith("http"):
                posters.append((str(raw.get("id") or ""), thumb))
        return posters

    def extract_playlist(
        self,
        url: str,
        limit: int = 50,
        browser: Optional[str] = None,
        from_item: Optional[int] = None,
        to_item: Optional[int] = None,
        timeout: Optional[int] = None,
    ) -> ProfileCrawlResult:
        """Quét playlist / channel / profile bằng --flat-playlist"""
        if not self.is_available():
            raise RuntimeError("yt-dlp binary không được tìm thấy trên hệ thống.")

        # Kênh/playlist lớn cần nhiều thời gian hơn mốc cố định 45s trước đây
        if timeout is None:
            if from_item and to_item and to_item >= from_item:
                count = to_item - from_item + 1
            else:
                count = limit
            timeout = 540 if not count or count <= 0 else max(90, min(540, 60 + int(count * 1.2)))

        args, tmp_cookie = self.get_base_args(allow_playlist=True, browser=browser, target_url=url)
        if from_item and to_item and to_item >= from_item:
            range_spec = f"{from_item}-{to_item}"
        elif limit and limit > 0:
            range_spec = f"1-{limit}"
        else:
            range_spec = None

        cmd = [
            *self.command(),
            *args,
            "--flat-playlist",
            "--dump-json",
        ]
        if range_spec:
            cmd.extend(["--playlist-items", range_spec])
        cmd.append(url)

        self.log(f"yt-dlp playlist ({range_spec or 'all'}): {url}")
        try:
            code, stdout, stderr = self.run_process(cmd, timeout=timeout)
        finally:
            if tmp_cookie and os.path.exists(tmp_cookie):
                try:
                    os.remove(tmp_cookie)
                except Exception:
                    pass

        timed_out = self.is_timeout(code, stderr)
        if code != 0 and not stdout.strip():
            if timed_out:
                raise RuntimeError(
                    f"Quét quá thời gian {timeout}s. Hãy giảm số lượng cần quét hoặc chọn 'Khoảng' nhỏ hơn."
                )
            if browser != "none" and self._should_retry_without_cookies(args, stderr):
                self.warn("yt-dlp playlist lỗi với cookies, đang thử lại không dùng cookies ('none')...")
                return self.extract_playlist(url, limit, browser="none", from_item=from_item, to_item=to_item, timeout=timeout)
            raise RuntimeError(f"yt-dlp playlist thất bại: {stderr.strip() or f'Exit code {code}'}")

        result = self._parse_playlist_data(stdout, url)
        # URL kênh gốc (youtube.com/@kenh) là danh sách các TAB (Videos, Shorts, Live):
        # yt-dlp áp `--playlist-items` cho TỪNG tab, nên chọn 20 bài lại nhận về tới 60.
        wanted = (to_item - from_item + 1) if (from_item and to_item and to_item >= from_item) else limit
        if wanted and wanted > 0 and len(result.media) > wanted:
            result.media = result.media[:wanted]
            result.total_count = len(result.media)
            result.stats = f"Đã quét {len(result.media)} tệp phương tiện"
        if timed_out and result.media:
            # --flat-playlist in từng mục ngay khi có: trả về phần đã quét được
            result.stats = f"{result.stats} (dừng sớm vì hết thời gian)"
        return result

    # ─────────────────────────────────────────────────────────────────────────
    # Normalizers
    # ─────────────────────────────────────────────────────────────────────────

    def _normalize_metadata(self, raw: Dict[str, Any], original_url: str) -> MediaMetadata:
        raw_platform = (raw.get("extractor_key") or raw.get("extractor") or "media").lower()
        platform = self._detect_platform_string(raw_platform, original_url)

        title = raw.get("title") or None
        author = raw.get("uploader") or raw.get("channel") or raw.get("creator") or None
        author_url = raw.get("uploader_url") or raw.get("channel_url") or None
        duration_sec = raw.get("duration")
        duration = self.format_duration(duration_sec) if duration_sec else None

        view_count = raw.get("view_count")
        views = f"{self.format_number(view_count)} lượt xem" if view_count is not None else None

        like_count = raw.get("like_count")
        likes = f"{self.format_number(like_count)} lượt thích" if like_count is not None else None

        comment_count = raw.get("comment_count")
        comments = f"{self.format_number(comment_count)} bình luận" if comment_count is not None else None

        description = raw.get("description") or ""
        tags = raw.get("tags")[:10] if isinstance(raw.get("tags"), list) else None

        upload_date_raw = raw.get("upload_date")
        upload_date = (
            f"{upload_date_raw[:4]}-{upload_date_raw[4:6]}-{upload_date_raw[6:8]}"
            if upload_date_raw and len(upload_date_raw) >= 8
            else None
        )

        is_live = bool(raw.get("is_live") or raw.get("live_status") == "is_live")

        # Thumbnails
        thumbnail = raw.get("thumbnail") or ""
        high_res_thumbnail = thumbnail
        thumbnails = raw.get("thumbnails")
        if isinstance(thumbnails, list) and thumbnails:
            sorted_thumbs = sorted(thumbnails, key=lambda t: t.get("width") or 0, reverse=True)
            if sorted_thumbs:
                high_res_thumbnail = sorted_thumbs[0].get("url") or thumbnail
                good_thumb = next((t.get("url") for t in sorted_thumbs if (t.get("width") or 0) >= 480), None)
                thumbnail = good_thumb or high_res_thumbnail

        # Subtitles
        subtitles: List[SubtitleItem] = []
        seen_langs = set()
        for sub_type, is_auto in [("subtitles", False), ("automatic_captions", True)]:
            sub_dict = raw.get(sub_type)
            if isinstance(sub_dict, dict):
                for lang, s_list in sub_dict.items():
                    if lang not in seen_langs:
                        seen_langs.add(lang)
                        first = s_list[0] if isinstance(s_list, list) and s_list else {}
                        s_name = first.get("name") or lang.upper()
                        subtitles.append(
                            SubtitleItem(
                                lang=lang,
                                name=s_name,
                                ext=first.get("ext") or "vtt",
                                url=first.get("url"),
                                is_auto_generated=is_auto,
                            )
                        )

        # Chapters
        chapters: List[ChapterItem] = []
        raw_chapters = raw.get("chapters")
        if isinstance(raw_chapters, list):
            for ch in raw_chapters:
                st = ch.get("start_time") or 0.0
                et = ch.get("end_time") or 0.0
                chapters.append(
                    ChapterItem(
                        title=ch.get("title") or "Chương",
                        start_time=float(st),
                        end_time=float(et),
                        start_formatted=self.format_duration(st),
                    )
                )

        # Streams
        streams: List[StreamFormat] = []
        formats = self._formats_of(raw)

        if is_live:
            streams.append(
                StreamFormat(
                    format_id="live_best",
                    quality="Live Stream (Chất lượng tốt nhất)",
                    format="HLS",
                    size=None,
                    stream_type="full",
                    has_audio=True,
                    has_video=True,
                )
            )
        else:
            video_formats = [
                f for f in formats
                if (f.get("vcodec") and f.get("vcodec") != "none")
                or (f.get("video_ext") and f.get("video_ext") != "none")
                or (not f.get("acodec") and f.get("ext") == "mp4")
                or str(f.get("format_id", "")).lower() in ("hd", "sd", "best", "default")
            ]

            available_heights = sorted(
                list({f["height"] for f in video_formats if f.get("height")}),
                reverse=True,
            )

            target_heights = [2160, 1440, 1080, 720, 480, 360]
            matched_heights = [
                h for h in target_heights
                if any(ah >= h * 0.95 for ah in available_heights)
            ]
            # Video thấp hơn cả mốc 360p (ví dụ 240p) vẫn phải có ít nhất một lựa
            # chọn — dùng chính độ phân giải thật thay vì gắn nhãn 720p không có thật.
            if not matched_heights and available_heights:
                matched_heights = [available_heights[0]]

            for h in matched_heights:
                label = height_label(h)
                muxed_spec = f"bestvideo[height<={h}]+bestaudio/best[height<={h}]"

                streams.append(
                    StreamFormat(
                        format_id=muxed_spec,
                        quality=f"{label} — Có âm thanh đầy đủ",
                        format="MP4",
                        size=None,
                        raw_size=None,
                        stream_type="full",
                        has_audio=True,
                        has_video=True,
                    )
                )

            # Trường hợp không có thông tin height (như Facebook có hd/sd)
            if not available_heights and video_formats:
                hd = next((f for f in video_formats if str(f.get("format_id", "")).lower() == "hd" or str(f.get("format_note", "")).lower() == "hd"), None)
                sd = next((f for f in video_formats if str(f.get("format_id", "")).lower() == "sd" or str(f.get("format_note", "")).lower() == "sd"), None)
                if hd:
                    sz = hd.get("filesize") or hd.get("filesize_approx")
                    streams.append(
                        StreamFormat(
                            format_id="hd",
                            quality="HD (Độ phân giải cao) — Có âm thanh đầy đủ",
                            format=(hd.get("ext") or "mp4").upper(),
                            size=self.format_bytes(sz) if sz else None,
                            raw_size=sz or None,
                            stream_type="full",
                            has_audio=True,
                            has_video=True,
                            url=hd.get("url"),
                        )
                    )
                if sd:
                    sz = sd.get("filesize") or sd.get("filesize_approx")
                    streams.append(
                        StreamFormat(
                            format_id="sd",
                            quality="SD (Tiêu chuẩn) — Có âm thanh đầy đủ",
                            format=(sd.get("ext") or "mp4").upper(),
                            size=self.format_bytes(sz) if sz else None,
                            raw_size=sz or None,
                            stream_type="full",
                            has_audio=True,
                            has_video=True,
                            url=sd.get("url"),
                        )
                    )

            # Mute streams (chỉ video không tiếng)
            seen_mute = set()
            for f in video_formats:
                h = f.get("height")
                acodec = f.get("acodec")
                if (not acodec or acodec == "none") and h and h not in seen_mute:
                    seen_mute.add(h)
                    sz = f.get("filesize") or f.get("filesize_approx")
                    streams.append(
                        StreamFormat(
                            format_id=str(f.get("format_id")),
                            quality=f"{h}p (Chỉ video / Không tiếng)",
                            format=(f.get("ext") or "mp4").upper(),
                            size=self.format_bytes(sz) if sz else None,
                            raw_size=sz or None,
                            stream_type="mute",
                            has_audio=False,
                            has_video=True,
                            fps=f"{round(f['fps'])}fps" if f.get("fps") else None,
                            bitrate=f"{round(f['tbr'])}kbps" if f.get("tbr") else None,
                            vcodec=f.get("vcodec"),
                            url=f.get("url"),
                        )
                    )

            # Nguồn không khai báo độ phân giải (HLS "media playlist" .m3u8, link
            # .mp4/.webm trực tiếp...) không lọt vào nhóm nào ở trên, nên trước đây
            # chỉ còn các lựa chọn ÂM THANH — không có cách nào tải video. Chỉ bỏ qua
            # khi mọi định dạng đều khai báo rõ là không có hình (vd. SoundCloud).
            # Tệp nhạc trực tiếp (.mp3/.m4a...) yt-dlp không gắn vcodec="none" — nhận theo đuôi.
            has_video_option = any(s.stream_type in ("full", "mute") for s in streams)
            might_have_video = any(
                (f.get("vcodec") or "") != "none"
                and not (not f.get("vcodec") and str(f.get("ext") or "").lower() in AUDIO_FILE_EXTS)
                for f in formats
            )
            if not has_video_option and might_have_video:
                best = formats[-1] if formats else {}
                streams.append(
                    StreamFormat(
                        format_id="bestvideo+bestaudio/best",
                        quality="Video tốt nhất hiện có — Có âm thanh",
                        format=(best.get("ext") or "mp4").upper(),
                        size=None,
                        raw_size=None,
                        stream_type="full",
                        has_audio=True,
                        has_video=True,
                    )
                )

            # Audio formats chuẩn
            streams.extend(audio_preset_streams())

        # Reels / Shorts detection
        url_lower = original_url.lower()
        is_short = (
            "/shorts/" in url_lower
            or "/shorts/" in str(raw.get("webpage_url", "")).lower()
            or (platform == "youtube" and duration_sec is not None and 0 < duration_sec <= 65 and (raw.get("height") or 0) > (raw.get("width") or 0))
        )
        is_reel = (
            "/reel/" in url_lower
            or "/reels/" in url_lower
            or "/share/r/" in url_lower
            or "/reel/" in str(raw.get("webpage_url", "")).lower()
        )

        return MediaMetadata(
            id=str(raw.get("id") or "media"),
            platform=platform,
            title=title,
            author=author,
            author_url=author_url,
            duration=duration,
            views=views,
            likes=likes,
            comments=comments,
            thumbnail=thumbnail,
            high_res_thumbnail=high_res_thumbnail,
            type="live" if is_live else "video",
            original_url=original_url,
            description=description,
            tags=tags,
            upload_date=upload_date,
            subtitles=subtitles if subtitles else None,
            chapters=chapters if chapters else None,
            streams=streams,
            is_live=is_live,
            live_stream_url=raw.get("url") if is_live else None,
            is_reel=is_reel or None,
            is_short=is_short or None,
        )

    def _parse_playlist_data(self, stdout_data: str, url: str) -> ProfileCrawlResult:
        lines = [l.strip() for l in stdout_data.strip().split("\n") if l.strip().startswith("{")]
        media: List[CrawlMediaItem] = []
        channel_name = None
        channel_handle = None
        avatar = ""
        platform = self._detect_platform_string("", url)

        for idx, line in enumerate(lines, 1):
            try:
                item = json.loads(line)
                if item.get("channel") or item.get("uploader"):
                    channel_name = item.get("channel") or item.get("uploader")
                    handle_id = item.get("channel_id") or item.get("uploader_id") or (channel_name.lower().replace(" ", "") if channel_name else None)
                    channel_handle = f"@{handle_id}" if handle_id else None

                thumbs = item.get("thumbnails")
                best_thumb = thumbs[-1].get("url") if isinstance(thumbs, list) and thumbs else (item.get("thumbnail") or "")

                # Trích xuất URL thực từ source
                raw_url = item.get("url") or item.get("webpage_url") or item.get("original_url")
                if isinstance(raw_url, str) and (raw_url.startswith("http://") or raw_url.startswith("https://")):
                    video_url = raw_url
                else:
                    # Nếu source không có URL thật:
                    # Chỉ construct YouTube URL nếu:
                    # 1. Platform chắc chắn là YouTube
                    # 2. ID chắc chắn hợp lệ (11 ký tự chuẩn base64url của YouTube)
                    # 3. URL format được xác định chính xác
                    item_ie = str(item.get("ie_key") or item.get("extractor_key") or item.get("extractor") or "").strip().lower()
                    is_definitely_youtube = (platform == "youtube") or (item_ie in ("youtube", "youtubetab", "youtubeplaylist", "youtubechannel"))

                    candidate_id = str(item.get("id") or "").strip()
                    if not YOUTUBE_VIDEO_ID_REGEX.match(candidate_id):
                        alt_id = str(item.get("url") or "").strip()
                        candidate_id = alt_id if YOUTUBE_VIDEO_ID_REGEX.match(alt_id) else ""

                    if is_definitely_youtube and candidate_id:
                        video_url = f"https://www.youtube.com/watch?v={candidate_id}"
                    else:
                        video_url = None

                v_lower = (video_url or "").lower()
                is_short = "/shorts/" in v_lower
                is_reel = "/reel/" in v_lower or "/reels/" in v_lower

                dur = item.get("duration")
                duration_str = self.format_duration(dur) if dur else None

                vc = item.get("view_count")
                views_str = f"{self.format_number(vc)} lượt xem" if vc is not None else None

                media.append(
                    CrawlMediaItem(
                        id=idx,
                        type="video",
                        title=item.get("title") or None,
                        thumb=best_thumb,
                        url=video_url,
                        duration=duration_str,
                        quality=None,
                        size=None,
                        author=item.get("uploader") or item.get("channel") or channel_name or None,
                        views=views_str,
                        is_reel=is_reel or None,
                        is_short=is_short or None,
                    )
                )
            except Exception:
                continue

        return ProfileCrawlResult(
            platform=platform,
            name=channel_name,
            handle=channel_handle,
            url=url,
            avatar=avatar,
            stats=f"Đã quét {len(media)} tệp phương tiện",
            media=media,
            total_count=len(media),
        )

    def _detect_platform_string(self, raw_platform: str, url: str) -> str:
        s = f"{raw_platform} {url}".lower()
        if "youtube" in s or "youtu.be" in s:
            return "youtube"
        if "tiktok" in s:
            return "tiktok"
        if "instagram" in s:
            return "instagram"
        if "facebook" in s or "fb.watch" in s or "fb.com" in s:
            return "facebook"
        if "twitter" in s or "x.com" in s:
            return "x"
        if "pinterest" in s or "pin.it" in s:
            return "pinterest"
        if "reddit" in s or "redd.it" in s:
            return "reddit"
        if "soundcloud" in s:
            return "soundcloud"
        if "twitch" in s:
            return "twitch"
        if "dailymotion" in s:
            return "dailymotion"
        if "bilibili" in s:
            return "bilibili"
        if "bluesky" in s or "bsky" in s:
            return "bluesky"
        if "movie" in s or ".m3u8" in s or ".mpd" in s:
            return "movie"
        return "generic"

    @staticmethod
    def format_duration(seconds: Optional[float]) -> Optional[str]:
        if not seconds or math.isnan(seconds) or seconds <= 0:
            return None
        s = int(seconds)
        secs = s % 60
        mins = (s // 60) % 60
        hrs = s // 3600
        if hrs > 0:
            return f"{hrs:02d}:{mins:02d}:{secs:02d}"
        return f"{mins:02d}:{secs:02d}"

    @staticmethod
    def format_bytes(b: Any) -> str:
        try:
            b = int(b)
        except (TypeError, ValueError):
            return "0 B"
        if b <= 0:
            return "0 B"
        sizes = ["B", "KB", "MB", "GB", "TB", "PB"]
        # Kẹp chỉ số trong phạm vi `sizes` để không IndexError với tệp rất lớn
        i = max(0, min(int(math.floor(math.log(b, 1024))), len(sizes) - 1))
        p = math.pow(1024, i)
        s = round(b / p, 1)
        return f"{s} {sizes[i]}"

    @staticmethod
    def format_number(n: Optional[int]) -> str:
        if n is None:
            return ""
        if n >= 1_000_000_000:
            return f"{n / 1_000_000_000:.1f}B"
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n / 1_000:.1f}K"
        return str(n)
