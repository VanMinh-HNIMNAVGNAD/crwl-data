"""
Thông tin THẬT của một tệp / luồng media dán thẳng (.m3u8, .mp4, mảnh .ts/.m4s...).

Trước đây link tệp trực tiếp mà máy chủ trả "application/octet-stream" (mặc định của
nginx, S3, nhiều CDN) và danh sách phát HLS cấp thấp (không khai báo codec / độ phân
giải) chỉ cho yt-dlp một URL trần: engine coi như "không có định dạng video nào" nên
chỉ còn các lựa chọn MP3 / âm thanh. Module này:

  1. Hỏi ffprobe xem link chứa HÌNH, TIẾNG hay cả hai, độ phân giải và thời lượng —
     nhờ vậy danh sách định dạng nói đúng sự thật (tệp chỉ có hình của DASH thì ghi
     "Không tiếng" thay vì hứa "Có âm thanh", luồng âm thanh thì nói rõ là âm thanh).
  2. Với MẢNH luồng (.ts / .m4s — chỉ vài giây, mảnh .m4s còn không tự phát được),
     dò danh sách phát đầy đủ (.m3u8 / .mpd) nằm cùng thư mục hoặc thư mục cha để
     tải được nguyên video.
"""

import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .base import BaseExtractor
from ..cancellation import (
    attach_request,
    cap_timeout,
    current_request_id,
    detach_request,
    raise_if_cancelled,
)

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

MANIFEST_EXTS = frozenset({"m3u8", "mpd"})
# Mảnh của luồng HLS / DASH / CMAF
SEGMENT_EXTS = frozenset({"ts", "m4s", "cmfv", "cmfa"})
VIDEO_FILE_EXTS = frozenset({
    "mp4", "m4v", "mov", "webm", "mkv", "flv", "avi", "3gp", "ogv", "wmv", "mpg", "mpeg", "m2ts", "mts",
})
AUDIO_FILE_EXTS = frozenset({"mp3", "m4a", "aac", "ogg", "oga", "opus", "flac", "wav", "weba", "wma", "aiff", "alac"})
DIRECT_MEDIA_EXTS = MANIFEST_EXTS | SEGMENT_EXTS | VIDEO_FILE_EXTS | AUDIO_FILE_EXTS

# Mảnh ngắn hơn mốc này (giây) được coi là một mảnh của luồng, không phải cả video.
SEGMENT_MAX_SECONDS = 60.0
# Mỗi lần ffprobe: ffprobe chỉ đọc phần đầu (playlist + mảnh đầu tiên).
PROBE_TIMEOUT = 15
# Mỗi yêu cầu dò danh sách phát.
MANIFEST_PEEK_TIMEOUT = 4
MAX_PLAYLIST_BYTES = 2 * 1024 * 1024

_HLS_NAMES = ("master.m3u8", "index.m3u8", "playlist.m3u8", "prog_index.m3u8")
_DASH_NAMES = ("manifest.mpd", "stream.mpd")
# Số yêu cầu dò chạy cùng lúc: mỗi yêu cầu là một kết nối TLS mới (~1-2 giây với máy
# chủ ở xa), nên phải xong trong 1-2 lượt thay vì xếp hàng 4-5 lượt.
_PEEK_WORKERS = 16


def path_ext(url: str) -> str:
    """Đuôi tệp trong PATH của URL (chữ thường, không có dấu chấm)."""
    try:
        path = urllib.parse.urlsplit(url or "").path
    except ValueError:
        return ""
    name = path.rsplit("/", 1)[-1]
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1].lower()


def is_direct_media_url(url: str) -> bool:
    """URL http(s) trỏ thẳng tới tệp / luồng media (theo đuôi tệp trong PATH)."""
    try:
        scheme = urllib.parse.urlsplit((url or "").strip()).scheme.lower()
    except ValueError:
        return False
    return scheme in ("http", "https") and path_ext(url) in DIRECT_MEDIA_EXTS


def is_segment_url(url: str) -> bool:
    return path_ext(url) in SEGMENT_EXTS


@dataclass
class ProbeInfo:
    has_video: bool
    has_audio: bool
    width: Optional[int] = None
    height: Optional[int] = None
    duration: Optional[float] = None
    vcodec: Optional[str] = None
    acodec: Optional[str] = None
    container: Optional[str] = None

    @property
    def is_short(self) -> bool:
        """Chỉ vài giây (một mảnh của luồng) — hoặc không đọc được thời lượng."""
        return not self.duration or self.duration < SEGMENT_MAX_SECONDS


def _to_int(value) -> Optional[int]:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _to_float(value) -> Optional[float]:
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def parse_ffprobe_json(text: str) -> Optional[ProbeInfo]:
    """Kết quả `ffprobe -print_format json` → ProbeInfo (None nếu không có luồng nào)."""
    try:
        data = json.loads(text or "{}")
    except (json.JSONDecodeError, RecursionError):
        return None
    if not isinstance(data, dict):
        return None
    streams = data.get("streams")
    if not isinstance(streams, list) or not streams:
        # HLS: ffprobe liệt kê luồng theo từng "program"
        streams = []
        for program in data.get("programs") or []:
            if isinstance(program, dict):
                streams.extend(s for s in program.get("streams") or [] if isinstance(s, dict))
    streams = [s for s in streams if isinstance(s, dict)]
    # Ảnh bìa nhúng trong MP3/M4A cũng là một luồng "video" (mjpeg/png) — không tính.
    videos = [
        s for s in streams
        if s.get("codec_type") == "video" and s.get("codec_name") not in ("mjpeg", "png", "bmp", "gif", "webp")
    ]
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    if not videos and not audios:
        return None
    best_video = max(videos, key=lambda s: _to_int(s.get("height")) or 0, default=None)
    fmt = data.get("format") if isinstance(data.get("format"), dict) else {}
    duration = _to_float(fmt.get("duration"))
    if duration is None:
        duration = max((_to_float(s.get("duration")) or 0.0 for s in streams), default=0.0) or None
    return ProbeInfo(
        has_video=bool(videos),
        has_audio=bool(audios),
        width=_to_int(best_video.get("width")) if best_video else None,
        height=_to_int(best_video.get("height")) if best_video else None,
        duration=duration,
        vcodec=(best_video or {}).get("codec_name"),
        acodec=audios[0].get("codec_name") if audios else None,
        container=fmt.get("format_name"),
    )


def _classify_manifest(head: str) -> Optional[str]:
    text = head.lstrip("﻿ \t\r\n")
    if text.startswith("#EXTM3U"):
        if "#EXT-X-STREAM-INF" in text:
            return "hls_master"
        return "hls_media"
    if "<MPD" in text[:4096]:
        return "dash"
    return None


class MediaProbe(BaseExtractor):
    """ffprobe + dò danh sách phát cho mảnh luồng."""

    _CACHE_TTL = 120.0

    def __init__(self):
        super().__init__()
        self.binary_path = self.find_binary("ffprobe", "FFPROBE_PATH")
        self._cache: Dict[Tuple[str, Optional[str]], Tuple[float, Optional[ProbeInfo]]] = {}
        self._cache_lock = threading.Lock()
        # ffprobe cũ (trước 7.1) không có -extension_picky: gặp là thoát ngay.
        self._picky_supported: Optional[bool] = None

    def is_available(self) -> bool:
        if not (self.binary_path and os.path.exists(self.binary_path)):
            self.binary_path = self.find_binary("ffprobe", "FFPROBE_PATH")
        return bool(self.binary_path) and os.path.exists(self.binary_path)

    # ── ffprobe ───────────────────────────────────────────────────────────
    def probe(self, url: str, referer: Optional[str] = None, timeout: float = PROBE_TIMEOUT) -> Optional[ProbeInfo]:
        """Đọc luồng hình / tiếng của URL. None = không đọc được (mảnh .m4s thiếu phần
        đầu, bị chặn, chưa cài ffprobe...)."""
        try:
            scheme = urllib.parse.urlsplit(url).scheme.lower()
        except ValueError:
            return None
        if scheme not in ("http", "https") or not self.is_available():
            return None

        key = (url, referer or None)
        with self._cache_lock:
            hit = self._cache.get(key)
            if hit and time.monotonic() - hit[0] < self._CACHE_TTL:
                return hit[1]

        io_timeout_us = str(int(min(10.0, timeout) * 1_000_000))
        cmd = [
            self.binary_path, "-v", "error", "-hide_banner",
            "-print_format", "json",
            "-show_entries", "stream=codec_type,codec_name,width,height,duration:format=format_name,duration",
            # Chỉ cho đọc qua mạng: danh sách phát lạ không được trỏ ffprobe vào tệp trên máy.
            "-protocol_whitelist", "http,https,tcp,tls,crypto,httpproxy",
            "-rw_timeout", io_timeout_us,
            "-user_agent", USER_AGENT,
        ]
        if referer:
            cmd += ["-headers", f"Referer: {referer}\r\n"]
        # Trang phim hay nguỵ trang mảnh HLS thành .jpg/.png/.html: ffmpeg mới từ chối
        # đọc nếu không nới quy tắc đuôi tệp.
        lenient = ["-allowed_extensions", "ALL", "-extension_picky", "0"]
        use_lenient = self._picky_supported is not False
        code, out, err = self.run_process(cmd + (lenient if use_lenient else []) + [url], timeout=timeout)
        if code != 0 and use_lenient and ("Unrecognized option" in err or "Option not found" in err):
            self._picky_supported = False
            code, out, err = self.run_process(cmd + [url], timeout=timeout)
        elif use_lenient and code == 0:
            self._picky_supported = True

        info = parse_ffprobe_json(out) if out else None
        if info is None and code != 0:
            last = ((err or "").strip().splitlines() or [""])[-1]
            self.log(f"ffprobe không đọc được luồng: {last[:200]}")
        with self._cache_lock:
            if len(self._cache) > 64:
                self._cache.clear()
            self._cache[key] = (time.monotonic(), info)
        return info

    # ── dò danh sách phát của một mảnh ────────────────────────────────────
    @staticmethod
    def manifest_candidates(segment_url: str) -> List[Tuple[int, str, str]]:
        """(độ sâu thư mục, tên tệp, URL) của các danh sách phát có thể chứa mảnh này.

        Cùng thư mục, thư mục cha và ông — bố cục thường gặp:
          .../video/master.m3u8 + .../video/720p/index.m3u8 + .../video/720p/seg-12.ts
        Giữ nguyên query (?token=...) vì CDN ký URL thường ký theo cả thư mục.
        """
        try:
            parts = urllib.parse.urlsplit(segment_url)
        except ValueError:
            return []
        ext = path_ext(segment_url)
        stem = parts.path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
        # Tên đặt theo chính mảnh: index0.ts ↔ index.m3u8 (ffmpeg), hoặc
        # .../url_462/video_7.ts ↔ .../video_7.m3u8 (mỗi mảnh một thư mục con)
        named = [f"{n}.m3u8" for n in dict.fromkeys((re.sub(r"[-_.]?\d+$", "", stem), stem)) if n]
        common = list(_HLS_NAMES) + ([] if ext == "ts" else list(_DASH_NAMES))  # mảnh .ts chỉ có ở HLS

        out: List[Tuple[int, str, str]] = []
        directory = parts.path.rsplit("/", 1)[0]
        for depth in range(3):
            if depth and not directory:
                break
            names = list(dict.fromkeys((named if depth < 2 else []) + common))
            for name in names:
                path = f"{directory}/{name}"
                out.append((depth, name, urllib.parse.urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))))
            directory = directory.rsplit("/", 1)[0] if "/" in directory else ""
        return out

    def _peek(self, url: str, referer: Optional[str]) -> Tuple[Optional[str], str]:
        """(loại danh sách phát hoặc None, nội dung) — chỉ đọc hết khi đúng là danh sách phát."""
        headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
        if referer:
            headers["Referer"] = referer
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=cap_timeout(MANIFEST_PEEK_TIMEOUT)) as resp:
                ctype = (resp.headers.get("Content-Type") or "").lower()
                # Playlist HLS hay được trả với kiểu "audio/mpegurl" — chỉ loại tệp media thật / trang web
                is_playlist_type = "mpegurl" in ctype or "dash" in ctype
                if not is_playlist_type and (ctype.startswith(("image/", "video/", "audio/")) or "html" in ctype):
                    return None, ""
                head = resp.read(4096)
                kind = _classify_manifest(head.decode("utf-8", errors="replace"))
                if not kind:
                    return None, ""
                rest = resp.read(MAX_PLAYLIST_BYTES - len(head))
                return kind, (head + rest).decode("utf-8", errors="replace")
        except (urllib.error.URLError, OSError, ValueError):
            return None, ""

    @staticmethod
    def _references(text: str, needle: str) -> bool:
        return bool(needle) and needle in text

    def find_manifest(self, segment_url: str, referer: Optional[str] = None) -> Optional[str]:
        """URL danh sách phát (.m3u8 / .mpd) chứa mảnh này, hoặc None."""
        candidates = self.manifest_candidates(segment_url)
        if not candidates:
            return None
        parts = urllib.parse.urlsplit(segment_url)
        segment_name = parts.path.rsplit("/", 1)[-1]
        dirs = [d for d in parts.path.split("/")[:-1] if d]

        req_id = current_request_id()

        def check(item: Tuple[int, str, str]) -> Optional[Tuple[int, str]]:
            depth, _name, url = item
            previous = attach_request(req_id)
            try:
                kind, text = self._peek(url, referer)
            finally:
                detach_request(previous)
            if not kind:
                return None
            # Đường dẫn từ thư mục của danh sách phát tới thư mục chứa mảnh: "720p"
            sub = "/".join(dirs[-depth:]) if depth and len(dirs) >= depth else ""
            # Mảnh phải thật sự thuộc danh sách phát này (tránh nhận nhầm luồng khác cùng thư mục cha)
            if kind == "hls_media":
                rel = f"{sub}/{segment_name}" if sub else segment_name
                return (2, url) if self._references(text, rel) else None
            if kind == "hls_master":
                if depth == 0:
                    return (0, url)
                # Biến thể trỏ vào đúng thư mục chứa mảnh: "720p/index.m3u8"
                return (0, url) if self._references(text, sub + "/") else None
            if kind == "dash":
                if depth == 0 or self._references(text, "$RepresentationID$") or self._references(text, dirs[-1] if dirs else ""):
                    return (1, url)
            return None

        found: List[Tuple[int, int, str]] = []
        pool = ThreadPoolExecutor(max_workers=min(_PEEK_WORKERS, len(candidates)), thread_name_prefix="crwl-manifest")
        try:
            pending = {pool.submit(check, c): c for c in candidates}
            deadline = time.monotonic() + MANIFEST_PEEK_TIMEOUT * 3
            while pending:
                raise_if_cancelled()
                left = deadline - time.monotonic()
                if left <= 0:
                    break
                done, _ = wait(list(pending), timeout=min(0.5, left), return_when=FIRST_COMPLETED)
                for fut in done:
                    depth = pending.pop(fut)[0]
                    try:
                        hit = fut.result()
                    except Exception:
                        hit = None
                    if hit:
                        found.append((hit[0], depth, hit[1]))
                # Đã có danh sách tổng (master) thì không cần chờ thêm
                if any(f[0] == 0 for f in found):
                    break
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        if not found:
            return None
        # master > dash > danh sách phát của một chất lượng; cùng loại thì thư mục gần nhất
        found.sort(key=lambda f: (f[0], f[1]))
        return found[0][2]
