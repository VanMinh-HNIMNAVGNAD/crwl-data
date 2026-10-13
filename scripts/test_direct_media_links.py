#!/usr/bin/env python3
"""
Dán thẳng link luồng / tệp media (.m3u8, .m4s, .ts, .mp4...) phải nhận ra VIDEO.

Các lỗi từng xảy ra:

  1. Link tệp mà máy chủ trả "application/octet-stream" (mặc định của nginx, S3...):
     yt-dlp trả về một URL trần không có mảng `formats` → engine coi như "không có
     định dạng video" và CHỈ hiện các lựa chọn MP3 / âm thanh.
  2. Danh sách phát HLS của một chất lượng / một rendition: không biết có hình hay
     tiếng nên luồng chỉ có tiếng vẫn hứa "Video — Có âm thanh", luồng chỉ có hình vẫn
     hứa có tiếng và vẫn mời tải MP3.
  3. Mảnh luồng (.ts / .m4s): chỉ vài giây, mảnh .m4s còn không tự phát được — phải dò
     ra danh sách phát đầy đủ để tải nguyên video.
  4. Link luồng bị chặn (403, thiếu Referer) / hết hạn (404): dispatcher chạy tiếp
     gallery-dl, HTML tĩnh rồi Chromium (thêm 5-40 giây) và Chromium "thấy" chính link
     đó trong lưu lượng mạng → báo phân tích xong một video không bao giờ tải được.
  5. Khi yt-dlp lỗi, engine luôn chạy lại yt-dlp KHÔNG cookie — kể cả khi chẳng có
     cookie nào được gửi (lệnh y hệt) hay lỗi là "Unsupported URL".

yt-dlp / ffprobe giả (script in sẵn kết quả) để chạy offline; phần dò danh sách phát
dùng máy chủ HTTP cục bộ, phần cần ffmpeg thật tự bỏ qua nếu máy không có.
"""

import http.server
import json
import os
import shutil
import stat
import subprocess
import sys
import threading
import time
import urllib.parse

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.dispatcher import MediaDispatcher
from core.extractors import ytdlp as ytdlp_module
from core.extractors.media_probe import MediaProbe, is_direct_media_url, parse_ffprobe_json
from core.extractors.ytdlp import YtDlpExtractor
from core.models import MediaImage, MediaMetadata


# ─────────────────────────────────────────────────────────────────────────────
# Công cụ giả
# ─────────────────────────────────────────────────────────────────────────────

def _script(path, body):
    path.write_text("#!/usr/bin/env python3\nimport sys, os\n" + body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


def _fake_ytdlp(tmp_path, payload=None, stderr="", exit_code=0, name="yt-dlp"):
    """yt-dlp giả: ghi mỗi lần chạy vào <name>.calls, in `payload` (dict) dạng JSON."""
    calls = tmp_path / f"{name}.calls"
    out = json.dumps(payload) if payload is not None else ""
    return _script(tmp_path / name, (
        f"if '--help' in sys.argv:\n    print('  --cookies FILE'); sys.exit(0)\n"
        f"open({str(calls)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
        f"print({out!r})\n"
        f"sys.stderr.write({stderr!r})\n"
        f"sys.exit({exit_code})\n"
    )), calls


def _fake_ffprobe(tmp_path, result=None, name="ffprobe"):
    """ffprobe giả: in `result` (dict) hoặc báo lỗi đọc như mảnh .m4s thiếu phần đầu."""
    if result is None:
        body = "sys.stderr.write('x.m4s: Invalid data found when processing input\\n'); sys.exit(1)\n"
    else:
        body = f"print({json.dumps(result)!r})\n"
    return _script(tmp_path / name, body)


def _ytdlp(binary, ffprobe=None):
    ext = YtDlpExtractor()
    ext.binary_path = binary
    if ffprobe is not None:
        ext.prober.binary_path = ffprobe
    return ext


def _streams(meta):
    return [(s.stream_type, s.format_id) for s in meta.streams]


# JSON yt-dlp in ra cho tệp mà máy chủ trả "application/octet-stream"
# ("URL could be a direct video link, returning it as such.") — KHÔNG có "formats".
def _direct(url, ext):
    return {
        "_type": "video", "id": "index0", "title": "index0", "direct": True, "url": url, "ext": ext,
        "extractor": "generic", "extractor_key": "Generic", "webpage_url": url,
        "protocol": "http", "video_ext": ext, "audio_ext": "none", "format_id": "0",
    }


FF_VIDEO_AUDIO = {"streams": [
    {"codec_type": "video", "codec_name": "h264", "width": 1280, "height": 720},
    {"codec_type": "audio", "codec_name": "aac"},
], "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "640.5"}}
FF_VIDEO_ONLY = {"streams": [{"codec_type": "video", "codec_name": "h264", "width": 960, "height": 540}],
                 "format": {"format_name": "hls", "duration": "600"}}
FF_AUDIO_ONLY = {"streams": [{"codec_type": "audio", "codec_name": "aac"}], "format": {"format_name": "hls"}}


# ─────────────────────────────────────────────────────────────────────────────
# 1. Link tệp trực tiếp không có "formats"
# ─────────────────────────────────────────────────────────────────────────────

def test_octet_stream_video_file_is_offered_as_video_not_only_mp3(tmp_path, monkeypatch):
    """Kể cả khi máy không có ffprobe."""
    url = "http://cdn.example/v/clip.m4s"
    binary, _ = _fake_ytdlp(tmp_path, _direct(url, "m4s"))
    monkeypatch.setattr(MediaProbe, "is_available", lambda self: False)
    meta = _ytdlp(binary).extract_metadata(url, browser="none")
    kinds = {kind for kind, _ in _streams(meta)}
    assert "full" in kinds, f"chỉ có {kinds}: dán link .m4s/.mp4 octet-stream là chỉ thấy MP3"


def test_octet_stream_audio_file_is_still_audio_only(tmp_path, monkeypatch):
    url = "http://cdn.example/a/song.mp3"
    payload = _direct(url, "mp3") | {"acodec": "mp3"}
    binary, _ = _fake_ytdlp(tmp_path, payload)
    ext = _ytdlp(binary)
    monkeypatch.setattr(MediaProbe, "is_available", lambda self: False)
    meta = ext.extract_metadata(url, browser="none")
    assert {kind for kind, _ in _streams(meta)} == {"audio"}


# ─────────────────────────────────────────────────────────────────────────────
# 2. ffprobe nói thật: có hình / có tiếng / độ phân giải
# ─────────────────────────────────────────────────────────────────────────────

def _hls_media_playlist(url):
    return {"_type": "video", "id": "prog_index", "title": "prog_index", "extractor_key": "Generic",
            "formats": [{"format_id": "0", "url": url, "ext": "mp4", "protocol": "m3u8_native"}]}


def test_probe_labels_resolution_and_keeps_audio_presets(tmp_path):
    url = "https://cdn.example/v/index.m3u8"
    binary, _ = _fake_ytdlp(tmp_path, _hls_media_playlist(url))
    meta = _ytdlp(binary, _fake_ffprobe(tmp_path, FF_VIDEO_AUDIO)).extract_metadata(url, browser="none")
    full = [s for s in meta.streams if s.stream_type == "full"]
    assert len(full) == 1 and "720p" in full[0].quality and "Có âm thanh" in full[0].quality
    assert any(s.stream_type == "audio" for s in meta.streams)
    assert meta.duration == "10:40"


def test_video_only_rendition_is_not_promised_sound_or_mp3(tmp_path):
    url = "https://cdn.example/v5/prog_index.m3u8"
    binary, _ = _fake_ytdlp(tmp_path, _hls_media_playlist(url))
    meta = _ytdlp(binary, _fake_ffprobe(tmp_path, FF_VIDEO_ONLY)).extract_metadata(url, browser="none")
    assert _streams(meta) == [("mute", "best")], "luồng chỉ có hình không được hứa có tiếng / mời tải MP3"
    assert "540p" in meta.streams[0].quality
    assert meta.description.startswith("ℹ️") and "không có tiếng" in meta.description


def test_audio_only_rendition_is_explained(tmp_path):
    url = "https://cdn.example/a1/prog_index.m3u8"
    binary, _ = _fake_ytdlp(tmp_path, _hls_media_playlist(url))
    meta = _ytdlp(binary, _fake_ffprobe(tmp_path, FF_AUDIO_ONLY)).extract_metadata(url, browser="none")
    assert {kind for kind, _ in _streams(meta)} == {"audio"}
    assert meta.description.startswith("ℹ️") and "ÂM THANH" in meta.description


def test_known_formats_are_not_probed(tmp_path):
    """Kết quả yt-dlp đã có độ phân giải / codec thì không tốn thêm một lần ffprobe."""
    url = "https://cdn.example/master.m3u8"
    payload = {"_type": "video", "id": "m", "title": "m", "formats": [
        {"format_id": "720", "url": url, "ext": "mp4", "height": 720, "vcodec": "avc1", "acodec": "mp4a"}]}
    binary, _ = _fake_ytdlp(tmp_path, payload)
    marker = tmp_path / "probed"
    ffprobe = _script(tmp_path / "ffprobe", f"open({str(marker)!r}, 'w').write('1')\nprint('{{}}')\n")
    _ytdlp(binary, ffprobe).extract_metadata(url, browser="none")
    assert not marker.exists()


def test_page_urls_are_never_probed(tmp_path):
    """Extractor của nền tảng trả MỘT định dạng thiếu thông tin: không gửi ffprobe vào TRANG web."""
    page = "https://videosite.example/watch/123"
    payload = {"_type": "video", "id": "123", "title": "x", "extractor_key": "VideoSite",
               "formats": [{"format_id": "0", "url": page, "ext": "mp4", "protocol": "https"}]}
    binary, _ = _fake_ytdlp(tmp_path, payload)
    marker = tmp_path / "probed"
    ffprobe = _script(tmp_path / "ffprobe", f"open({str(marker)!r}, 'w').write('1')\nprint('{{}}')\n")
    _ytdlp(binary, ffprobe).extract_metadata(page, browser="none")
    assert not marker.exists()


def test_parse_ffprobe_ignores_cover_art_and_reads_hls_programs():
    mp3_with_cover = json.dumps({"streams": [
        {"codec_type": "audio", "codec_name": "mp3"},
        {"codec_type": "video", "codec_name": "mjpeg", "width": 500, "height": 500},
    ], "format": {"duration": "200"}})
    info = parse_ffprobe_json(mp3_with_cover)
    assert info.has_audio and not info.has_video, "ảnh bìa nhúng trong MP3 không phải luồng hình"

    hls = json.dumps({"programs": [{"streams": [
        {"codec_type": "video", "codec_name": "h264", "width": 640, "height": 360},
        {"codec_type": "audio", "codec_name": "aac"}]}], "streams": [], "format": {"format_name": "hls"}})
    info = parse_ffprobe_json(hls)
    assert info.has_video and info.has_audio and info.height == 360
    assert parse_ffprobe_json("{}") is None and parse_ffprobe_json("không phải json") is None


def test_direct_media_url_detection():
    assert is_direct_media_url("https://cdn.x/a/b/seg-12.m4s?token=1")
    assert is_direct_media_url("https://cdn.x/master.m3u8")
    assert is_direct_media_url("http://cdn.x/song.mp3")
    assert not is_direct_media_url("https://site.x/watch?file=video.m3u8"), "trang player, không phải tệp"
    assert not is_direct_media_url("https://site.x/video.mp4.html")
    assert not is_direct_media_url("file:///etc/passwd.mp4")


# ─────────────────────────────────────────────────────────────────────────────
# 3. Mảnh luồng → danh sách phát đầy đủ (máy chủ HTTP cục bộ)
# ─────────────────────────────────────────────────────────────────────────────

MASTER = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000,RESOLUTION=640x360\n360p/index.m3u8\n"
MEDIA_360 = "#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4,\nseg-1.ts\n#EXTINF:4,\nseg-2.ts\n#EXT-X-ENDLIST\n"
OTHER_MEDIA = "#EXTM3U\n#EXT-X-TARGETDURATION:4\n#EXTINF:4,\nother-1.ts\n#EXT-X-ENDLIST\n"
FILES = {
    # Bố cục kiểu CDN: master ở thư mục cha, mỗi chất lượng một thư mục con
    "/movie/master.m3u8": ("audio/mpegurl", MASTER),
    "/movie/360p/index.m3u8": ("application/vnd.apple.mpegurl", MEDIA_360),
    "/movie/360p/seg-1.ts": ("video/mp2t", "x"),
    # Bố cục ffmpeg: index.m3u8 + index0.m4s cùng thư mục
    "/ff/index.m3u8": ("application/vnd.apple.mpegurl",
                       '#EXTM3U\n#EXT-X-MAP:URI="init.mp4"\n#EXTINF:4,\nindex0.m4s\n#EXT-X-ENDLIST\n'),
    # Thư mục có playlist KHÁC, không chứa mảnh được dán
    "/mixed/index.m3u8": ("application/vnd.apple.mpegurl", OTHER_MEDIA),
    "/dash/manifest.mpd": ("application/dash+xml", '<?xml version="1.0"?><MPD xmlns="urn:mpeg:dash:schema:mpd:2011"></MPD>'),
}


class _Files(http.server.BaseHTTPRequestHandler):
    hits = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        self.hits.append(path)
        hit = FILES.get(path)
        if hit is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        ctype, body = hit
        data = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture(scope="module")
def files_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Files)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_segment_in_quality_subdir_leads_to_the_master_playlist(files_server):
    found = MediaProbe().find_manifest(f"{files_server}/movie/360p/seg-1.ts?token=abc")
    assert found == f"{files_server}/movie/master.m3u8?token=abc", "giữ token và ưu tiên master (đủ chất lượng)"


def test_ffmpeg_style_fragment_leads_to_its_playlist(files_server):
    assert MediaProbe().find_manifest(f"{files_server}/ff/index0.m4s") == f"{files_server}/ff/index.m3u8"


def test_dash_chunk_leads_to_the_mpd(files_server):
    assert MediaProbe().find_manifest(f"{files_server}/dash/chunk-stream0-00001.m4s") == f"{files_server}/dash/manifest.mpd"


def test_playlist_of_another_stream_is_not_taken(files_server):
    assert MediaProbe().find_manifest(f"{files_server}/mixed/seg-9.ts") is None


# ─────────────────────────────────────────────────────────────────────────────
# 4. Dispatcher: link luồng trực tiếp không đi qua chuỗi dự phòng dài
# ─────────────────────────────────────────────────────────────────────────────

def _dispatcher(binary, ffprobe=None):
    d = MediaDispatcher()
    d.ytdlp.binary_path = binary
    if ffprobe is not None:
        d.ytdlp.prober.binary_path = ffprobe

    def boom(*a, **k):
        raise AssertionError("link luồng trực tiếp không được chạy gallery-dl / đọc trang / Chromium")

    d.gallery.extract_gallery = boom
    d.page_media.extract = boom
    d._try_playwright_sniff = boom
    d.movie.extract = boom
    return d


@pytest.mark.parametrize("err,expect", [
    ("ERROR: [generic] index: Unable to download webpage: HTTP Error 403: Forbidden", "HTTP 403"),
    ("ERROR: [generic] index: Unable to download webpage: HTTP Error 404: Not Found", "HTTP 404"),
])
def test_blocked_or_expired_stream_fails_fast_with_advice(tmp_path, err, expect):
    binary, _ = _fake_ytdlp(tmp_path, None, stderr=err, exit_code=1)
    d = _dispatcher(binary)
    started = time.monotonic()
    with pytest.raises(RuntimeError) as exc:
        d.extract("https://cdn.example/hls/abc/index.m3u8?token=1", browser="none")
    assert time.monotonic() - started < 10
    msg = str(exc.value)
    assert expect in msg and "link TRANG" in msg and len(msg) <= 260


def test_html_page_with_media_extension_is_still_read_as_a_page(tmp_path, monkeypatch):
    """".../clip.mp4" mà máy chủ trả trang HTML: phải đọc như trang (như trước), và không
    chạy lại yt-dlp đúng URL vừa thất bại."""
    url = "https://videosite.example/watch/clip.mp4"
    binary, calls = _fake_ytdlp(tmp_path, None, stderr=f"ERROR: Unsupported URL: {url}", exit_code=1)
    d = MediaDispatcher()
    d.ytdlp.binary_path = binary

    def no_gallery(*a, **k):
        raise RuntimeError("gallery-dl: Unsupported URL")

    page = MediaMetadata(id="p", platform="generic", type="album", original_url=url,
                         images=[MediaImage(id=1, url="https://cdn.example/x.jpg")])
    monkeypatch.setattr(d.gallery, "extract_gallery", no_gallery)
    monkeypatch.setattr(d.page_media, "extract", lambda *a, **k: page)
    assert d.extract(url, browser="none") is page
    assert calls.read_text().count("\n") == 1, "không chạy lại yt-dlp đúng URL vừa thất bại"


def test_orphan_m4s_fragment_is_reported_not_offered(tmp_path, monkeypatch):
    url = "https://cdn.example/x/lonely.m4s"
    binary, _ = _fake_ytdlp(tmp_path, _direct(url, "m4s"))
    d = _dispatcher(binary, _fake_ffprobe(tmp_path, None))
    monkeypatch.setattr(MediaProbe, "find_manifest", lambda self, u, referer=None: None)
    res = d.extract(url, browser="none")
    assert res.streams == [], "mảnh .m4s thiếu phần đầu không phát được — không được mời tải"
    assert res.description.startswith("⚠️") and ".m3u8" in res.description


def test_fragment_with_found_playlist_downloads_the_whole_stream(tmp_path, monkeypatch):
    seg = "https://cdn.example/x/720p/seg-3.m4s?sig=1"
    master = "https://cdn.example/x/master.m3u8?sig=1"
    payload = {"_type": "video", "id": "master", "title": "master", "formats": [
        {"format_id": "720", "url": master, "ext": "mp4", "height": 720, "vcodec": "avc1", "acodec": "mp4a"}]}
    binary, calls = _fake_ytdlp(tmp_path, payload)
    d = _dispatcher(binary, _fake_ffprobe(tmp_path, None))
    monkeypatch.setattr(MediaProbe, "find_manifest", lambda self, u, referer=None: master)
    res = d.extract(seg, browser="none")
    assert res.original_url == master, "tải theo danh sách phát, không phải theo mảnh"
    assert any("720p" in (s.quality or "") for s in res.streams)
    assert calls.read_text().count("\n") == 1


def test_complete_m4s_file_is_not_called_a_fragment(tmp_path, monkeypatch):
    """.m4s đọc được (DASH một tệp, vd. Bilibili) là cả một luồng — không dò, không ghi chú."""
    url = "https://upos.example/v/123-1-30080.m4s"
    binary, _ = _fake_ytdlp(tmp_path, _direct(url, "m4s"))
    d = _dispatcher(binary, _fake_ffprobe(tmp_path, FF_VIDEO_ONLY))

    def no_lookup(self, u, referer=None):
        raise AssertionError("tệp hoàn chỉnh không cần dò danh sách phát")

    monkeypatch.setattr(MediaProbe, "find_manifest", no_lookup)
    res = d.extract(url, browser="none")
    assert _streams(res) == [("mute", "best")]
    assert "MẢNH" not in (res.description or "")


# ─────────────────────────────────────────────────────────────────────────────
# 5. Không chạy lại yt-dlp vô ích
# ─────────────────────────────────────────────────────────────────────────────

def test_no_identical_retry_when_no_cookie_was_sent(tmp_path, monkeypatch):
    binary, calls = _fake_ytdlp(tmp_path, None, stderr="ERROR: [generic] x: HTTP Error 403: Forbidden", exit_code=1)
    monkeypatch.setattr(ytdlp_module, "get_browser_cookies_txt", lambda *a, **k: None)
    monkeypatch.setattr(ytdlp_module, "browser_store_readable", lambda b: True)
    with pytest.raises(RuntimeError):
        _ytdlp(binary).extract_metadata("https://site.example/post/1", browser="auto")
    assert calls.read_text().count("\n") == 1, "không có cookie nào → chạy lại là lặp y hệt lệnh cũ"


def test_unsupported_url_is_not_retried_without_cookies(tmp_path, monkeypatch):
    cookie = tmp_path / "c.txt"
    binary, calls = _fake_ytdlp(tmp_path, None, stderr="ERROR: Unsupported URL: https://site.example/a", exit_code=1)
    monkeypatch.setattr(ytdlp_module, "get_browser_cookies_txt",
                        lambda *a, **k: (cookie.write_text("# Netscape HTTP Cookie File\n"), str(cookie))[1])
    with pytest.raises(RuntimeError):
        _ytdlp(binary).extract_metadata("https://site.example/a", browser="chrome")
    assert calls.read_text().count("\n") == 1


def test_cookie_failure_is_still_retried_without_cookies(tmp_path, monkeypatch):
    cookie = tmp_path / "c.txt"
    binary, calls = _fake_ytdlp(tmp_path, None, stderr="ERROR: [instagram] x: HTTP Error 400: Bad Request", exit_code=1)
    monkeypatch.setattr(ytdlp_module, "get_browser_cookies_txt",
                        lambda *a, **k: (cookie.write_text("# Netscape HTTP Cookie File\n"), str(cookie))[1])
    with pytest.raises(RuntimeError):
        _ytdlp(binary).extract_metadata("https://www.instagram.com/p/x/", browser="chrome")
    lines = calls.read_text().splitlines()
    assert len(lines) == 2 and "--cookies" in lines[0] and "--cookies" not in lines[1]


def test_readable_browser_without_site_cookies_skips_cookies_from_browser(monkeypatch):
    monkeypatch.setattr(ytdlp_module, "get_browser_cookies_txt", lambda *a, **k: None)
    ext = YtDlpExtractor()
    monkeypatch.setattr(ytdlp_module, "browser_store_readable", lambda b: True)
    args, _ = ext.get_base_args(browser="chrome", target_url="https://blog.example/post")
    assert "--cookies-from-browser" not in args, "yt-dlp không cần giải mã lại cả kho cookie"
    monkeypatch.setattr(ytdlp_module, "browser_store_readable", lambda b: False)
    args, _ = ext.get_base_args(browser="chrome", target_url="https://blog.example/post")
    assert args[args.index("--cookies-from-browser") + 1] == "chrome", "kho cookie engine không đọc được → nhờ yt-dlp"


def test_youtu_be_share_links_expand_without_network(monkeypatch):
    """Link chia sẻ YouTube từng tốn 1-2 giây giải mã qua mạng trước khi chạy yt-dlp."""
    from core.resolver.url_resolver import UrlResolver

    def no_network(*a, **k):
        raise AssertionError("youtu.be/<id> không cần giải mã qua mạng")

    monkeypatch.setattr(UrlResolver, "unshorten_url", classmethod(no_network))
    res = UrlResolver.resolve_url("https://youtu.be/jNQXAC9IVRw?si=AbC&t=42")
    assert res.resolved_url == "https://www.youtube.com/watch?v=jNQXAC9IVRw&t=42" and res.platform == "youtube"
    assert UrlResolver.resolve_url("youtu.be/jNQXAC9IVRw").resolved_url == "https://www.youtube.com/watch?v=jNQXAC9IVRw"


# ─────────────────────────────────────────────────────────────────────────────
# 6. Đầu-cuối với ffmpeg / yt-dlp thật (tự bỏ qua nếu thiếu)
# ─────────────────────────────────────────────────────────────────────────────

class _Octet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def guess_type(self, path):
        return "application/octet-stream"


@pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe") and shutil.which("yt-dlp")),
                    reason="cần ffmpeg, ffprobe và yt-dlp thật")
def test_real_octet_stream_files_and_fragments(tmp_path):
    root = tmp_path / "www"
    (root / "hls").mkdir(parents=True)
    src = root / "clip.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=6:size=320x240:rate=25",
                    "-f", "lavfi", "-i", "sine=duration=6", "-c:v", "libx264", "-preset", "ultrafast", "-g", "25", "-c:a", "aac",
                    "-shortest", str(src)], check=True)
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(src), "-c", "copy", "-f", "hls", "-hls_time", "2",
                    "-hls_segment_type", "fmp4", "-hls_playlist_type", "vod", str(root / "hls" / "index.m3u8")], check=True)
    handler = lambda *a, **k: _Octet(*a, directory=str(root), **k)  # noqa: E731
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        d = MediaDispatcher()
        res = d.extract(f"{base}/clip.mp4", browser="none")
        full = [s for s in res.streams if s.stream_type == "full"]
        assert full and "240p" in full[0].quality, [s.quality for s in res.streams]

        res = d.extract(f"{base}/hls/index1.m4s", browser="none")
        assert res.original_url == f"{base}/hls/index.m3u8"
        assert any(s.stream_type == "full" for s in res.streams)
    finally:
        server.shutdown()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
