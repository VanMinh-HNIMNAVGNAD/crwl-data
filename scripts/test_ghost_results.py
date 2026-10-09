#!/usr/bin/env python3
"""
Kiểm thử các trường hợp engine bóc tách từng trả KẾT QUẢ GIẢ hoặc kết quả tải không được.

  1. Link playlist / kênh dán vào "Tải theo liên kết": yt-dlp bóc tách từng video tới
     khi quá 30 giây, rồi web_scraper vớt ảnh og:image → "thành công" một album chỉ
     có ảnh bìa.
  2. Video giới hạn tuổi / cần đăng nhập trên nền tảng video: cũng ra album ảnh bìa,
     che mất lỗi thật là cần cookie.
  3. Trang phim mà luồng HLS chỉ tìm được bằng cách dò HTML: kết quả trỏ về URL
     trang (yt-dlp không đọc được) nên mọi định dạng hiển thị đều tải lỗi.
  4. TikTok embed resolver dùng link CDN `playAddr` (cần cookie + Referer) → tải 403.
  5. Tệp cookie thủ công thiếu dòng đầu Netscape → yt-dlp từ chối cả tệp.
  6. yt-dlp cũ không có `--js-runtimes` → thêm cờ đó là mọi lượt bóc tách thất bại.

Các test dùng yt-dlp GIẢ (script in sẵn kết quả) nên chạy offline. Riêng test luồng
HLS cục bộ cần ffmpeg + yt-dlp thật và tự bỏ qua nếu máy không có.
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import threading
import http.server
import functools

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.cookies import browser_cookies
from core.dispatcher import MediaDispatcher
from core.extractors import tiktok as tiktok_module
from core.extractors.movie import MovieExtractor
from core.extractors.tiktok import TikTokExtractor
from core.extractors.ytdlp import PlaylistUrlError, YtDlpExtractor
from core.models import MediaImage, MediaMetadata, StreamFormat


def _fake_ytdlp(tmp_path, name, stdout_lines=(), help_text="", exit_code=0):
    """Script đóng vai yt-dlp: `--help` in help_text, còn lại in stdout_lines."""
    script = tmp_path / name
    payload = "\n".join(stdout_lines)
    script.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"if '--help' in sys.argv:\n    print({help_text!r}); sys.exit(0)\n"
        f"print({payload!r})\n"
        f"sys.exit({exit_code})\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script)


def _ytdlp_with(binary):
    ext = YtDlpExtractor()
    ext.binary_path = binary
    return ext


# ─────────────────────────────────────────────────────────────────────────────
# 1. Playlist / kênh
# ─────────────────────────────────────────────────────────────────────────────

FLAT_ENTRY = {
    "_type": "url",
    "ie_key": "Youtube",
    "id": "fOT0BUpITw8",
    "url": "https://www.youtube.com/watch?v=fOT0BUpITw8",
    "playlist_count": 182,
    "playlist_title": "Popular Music Videos",
}


def test_playlist_url_is_reported_instead_of_extracting_every_video(tmp_path):
    binary = _fake_ytdlp(tmp_path, "yt-dlp", [json.dumps(FLAT_ENTRY), json.dumps(FLAT_ENTRY)])
    with pytest.raises(PlaylistUrlError) as info:
        _ytdlp_with(binary).extract_metadata("https://www.youtube.com/playlist?list=PL1", browser="none")
    assert "182" in str(info.value)
    assert "Tải theo tài khoản" in str(info.value)


def test_single_video_still_extracts_normally(tmp_path):
    video = {"_type": "video", "id": "abc", "title": "Clip", "formats": [
        {"format_id": "18", "vcodec": "avc1", "acodec": "mp4a", "height": 360, "ext": "mp4"},
    ]}
    binary = _fake_ytdlp(tmp_path, "yt-dlp", [json.dumps(video)])
    meta = _ytdlp_with(binary).extract_metadata("https://www.youtube.com/watch?v=abc", browser="none")
    assert meta.title == "Clip"
    assert any(s.stream_type == "full" for s in meta.streams)


def _dispatcher_without_fallbacks(monkeypatch, ytdlp_error):
    """Dispatcher có yt-dlp ném `ytdlp_error`; gallery-dl lỗi; web_scraper trả ảnh bìa."""
    d = MediaDispatcher()
    monkeypatch.setattr(d.resolver, "resolve_url", lambda url, expected_platform=None: type(
        "R", (), {"is_shortened": False, "resolved_url": url})())

    def fail_ytdlp(url, browser=None, timeout=30):
        raise ytdlp_error

    def fail_gallery(url, browser=None, timeout=30):
        raise RuntimeError("[gallery-dl][error] Unsupported URL")

    def thumbnail_only(url, timeout=25, max_size=None):
        return MediaMetadata(
            id="1", platform="generic", type="album", original_url=url,
            images=[MediaImage(id=1, url="https://i.ytimg.com/vi/x/hqdefault.jpg")],
            streams=[StreamFormat(format_id="web_video_1", url="https://www.youtube.com/embed/x")],
        )

    monkeypatch.setattr(d.ytdlp, "extract_metadata", fail_ytdlp)
    monkeypatch.setattr(d.gallery, "extract_gallery", fail_gallery)
    monkeypatch.setattr(d.web_scraper, "extract", thumbnail_only)
    return d


def test_dispatcher_does_not_turn_a_playlist_into_a_thumbnail_album(monkeypatch):
    d = _dispatcher_without_fallbacks(monkeypatch, PlaylistUrlError("danh sách 182 mục"))
    with pytest.raises(PlaylistUrlError):
        d.extract("https://www.youtube.com/playlist?list=PL1", browser="none")


# ─────────────────────────────────────────────────────────────────────────────
# 2. Video cần đăng nhập / giới hạn tuổi
# ─────────────────────────────────────────────────────────────────────────────

def test_age_restricted_video_reports_login_instead_of_fake_album(monkeypatch):
    err = RuntimeError(
        "yt-dlp extract thất bại: ERROR: [youtube] Tq92D6wQ1mg: Sign in to confirm your age. "
        "Use --cookies-from-browser or --cookies for the authentication."
    )
    d = _dispatcher_without_fallbacks(monkeypatch, err)
    with pytest.raises(RuntimeError) as info:
        d.extract("https://www.youtube.com/watch?v=Tq92D6wQ1mg", browser="none")
    msg = str(info.value)
    assert "YouTube yêu cầu đăng nhập" in msg
    assert "Sign in to confirm your age" in msg


def test_video_platform_ignores_thumbnail_only_scraper_result(monkeypatch):
    err = RuntimeError("yt-dlp extract thất bại: ERROR: [youtube] x: Some unexpected failure")
    d = _dispatcher_without_fallbacks(monkeypatch, err)
    with pytest.raises(RuntimeError) as info:
        d.extract("https://www.youtube.com/watch?v=xxxxxxxxxxx", browser="none")
    assert "unexpected failure" in str(info.value)


def test_video_platform_still_accepts_a_real_video_file_from_the_scraper(monkeypatch):
    err = RuntimeError("yt-dlp extract thất bại: ERROR: [facebook] x: Unable to extract")
    d = _dispatcher_without_fallbacks(monkeypatch, err)
    real = MediaMetadata(
        id="1", platform="generic", type="video", original_url="u",
        streams=[StreamFormat(format_id="web_video_1", url="https://video.fbcdn.net/v/t42/clip.mp4?x=1")],
    )
    monkeypatch.setattr(d.web_scraper, "extract", lambda url, timeout=25, max_size=None: real)
    res = d.extract("https://www.facebook.com/watch/?v=123", browser="none")
    assert res.streams[0].url.endswith("clip.mp4?x=1")


# ─────────────────────────────────────────────────────────────────────────────
# 3. Trang phim: kết quả phải trỏ vào luồng đã tìm được
# ─────────────────────────────────────────────────────────────────────────────

def test_movie_page_result_points_at_the_scraped_stream(monkeypatch):
    movie = MovieExtractor(YtDlpExtractor())
    page = "https://phim.example/xem-phim/tap-1"
    stream = "https://cdn.example/hls/master.m3u8"
    monkeypatch.setattr(movie, "_scrape_stream_url", lambda url, timeout=15: (stream, []))
    seen = []

    def fake_extract(url, browser=None, timeout=45):
        seen.append(url)
        return MediaMetadata(id="1", platform="generic", original_url=url, streams=[])

    monkeypatch.setattr(movie.ytdlp, "extract_metadata", fake_extract)
    meta = movie.extract(page)
    assert seen == [stream]
    assert meta.original_url == stream, "UI tải theo originalUrl — phải là luồng, không phải trang"
    assert meta.referer == page
    assert meta.to_dict()["referer"] == page


def _serve(directory):
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=directory)
    handler.log_message = lambda *a, **k: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


@pytest.mark.skipif(not (shutil.which("ffmpeg") and YtDlpExtractor().is_available()),
                    reason="cần ffmpeg và yt-dlp thật")
def test_movie_page_stream_is_actually_downloadable(tmp_path):
    """Luồng HLS thật phục vụ cục bộ: bóc tách xong phải TẢI được bằng chính originalUrl."""
    site = tmp_path / "site"
    (site / "hls").mkdir(parents=True)
    (site / "xem-phim").mkdir()
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=duration=2:size=160x120:rate=10",
         "-f", "lavfi", "-i", "sine=duration=2", "-c:v", "libx264", "-c:a", "aac", "-shortest",
         "-hls_time", "2", "-hls_playlist_type", "vod", str(site / "hls" / "index.m3u8")],
        check=True, timeout=120,
    )
    server = _serve(str(site))
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        (site / "xem-phim" / "tap-1.html").write_text(
            f'<html><body><div id="player"></div><script>var cfg = {{"file": "{base}/hls/index.m3u8"}};</script></body></html>',
            encoding="utf-8",
        )
        page = f"{base}/xem-phim/tap-1.html"
        meta = MovieExtractor(YtDlpExtractor()).extract(page, browser="none")
        assert meta.original_url == f"{base}/hls/index.m3u8"
        assert meta.referer == page

        # Đúng lệnh mà Rust chạy khi người dùng bấm tải định dạng đầu tiên
        fmt = meta.streams[0].format_id
        out_dir = tmp_path / "out"
        done = subprocess.run(
            [YtDlpExtractor().binary_path, meta.original_url, "-f", fmt, "--referer", meta.referer,
             "-P", str(out_dir), "-o", "%(title).60s [%(id).50s].%(ext)s", "--no-playlist"],
            capture_output=True, text=True, timeout=180,
        )
        assert done.returncode == 0, done.stderr[-500:]
        assert any(out_dir.iterdir())
    finally:
        server.shutdown()


# ─────────────────────────────────────────────────────────────────────────────
# 4. TikTok embed resolver
# ─────────────────────────────────────────────────────────────────────────────

def test_tiktok_embed_items_link_to_the_video_page_not_the_signed_cdn(monkeypatch):
    state = {"source": {"data": {"x": {"videoList": [
        {"id": "7300000000000000001", "desc": "a", "playAddr": "https://v16-webapp.tiktok.com/signed.mp4?tk=1"},
        {"desc": "không có id", "playAddr": "https://v16-webapp.tiktok.com/other.mp4?tk=2"},
    ]}}}}
    html = f'<html><script id="__FRONTITY_CONNECT_STATE__" type="application/json">{json.dumps(state)}</script></html>'

    class FakeResp:
        status_code = 200
        text = html

    class FakeCffi:
        @staticmethod
        def get(url, impersonate=None, timeout=None):
            return FakeResp()

    monkeypatch.setattr(tiktok_module, "cffi_requests", FakeCffi)
    ext = TikTokExtractor()
    monkeypatch.setattr(ext, "run_process", lambda *a, **k: (1, "", ""))
    res = ext.resolve_profile("@someone", limit=10)
    urls = [m.url for m in res.media]
    assert urls[0] == "https://www.tiktok.com/@someone/video/7300000000000000001"
    # Mục không có id: không bịa link trang từ số thứ tự
    assert urls[1] == "https://v16-webapp.tiktok.com/other.mp4?tk=2"


# ─────────────────────────────────────────────────────────────────────────────
# 5. Cookie thủ công thiếu dòng đầu Netscape
# ─────────────────────────────────────────────────────────────────────────────

def test_manual_cookie_copy_always_has_the_netscape_header(tmp_path, monkeypatch):
    cookies_dir = tmp_path / "crwl" / "cookies"
    cookies_dir.mkdir(parents=True)
    lines = ".youtube.com\tTRUE\t/\tTRUE\t1890000000\tPREF\tf6=4\n"
    (cookies_dir / "youtube.txt").write_text(lines, encoding="utf-8")
    monkeypatch.setenv("CRWL_CONFIG_DIR", str(tmp_path / "crwl"))

    copy = browser_cookies.get_browser_cookies_txt("auto", domain="www.youtube.com")
    try:
        with open(copy, encoding="utf-8") as f:
            content = f.read()
        assert content.startswith("# Netscape HTTP Cookie File\n")
        assert content.endswith(lines)
        # Tệp gốc của người dùng giữ nguyên (chỉ bản sao tạm được sửa)
        assert (cookies_dir / "youtube.txt").read_text(encoding="utf-8") == lines

        import http.cookiejar
        jar = http.cookiejar.MozillaCookieJar(copy)
        jar.load(ignore_discard=True, ignore_expires=True)  # yt-dlp dùng đúng bộ đọc này
        assert [c.name for c in jar] == ["PREF"]
    finally:
        os.remove(copy)


# ─────────────────────────────────────────────────────────────────────────────
# 6. `--js-runtimes` chỉ dùng khi yt-dlp hiểu
# ─────────────────────────────────────────────────────────────────────────────

def test_js_runtimes_flag_follows_ytdlp_support(tmp_path, monkeypatch):
    old = _ytdlp_with(_fake_ytdlp(tmp_path, "yt-dlp-old", help_text="  --cookies FILE"))
    new = _ytdlp_with(_fake_ytdlp(tmp_path, "yt-dlp-new", help_text="  --js-runtimes RUNTIME[:PATH]"))
    node = sys.executable  # chỉ cần một tệp có thật đóng vai Node.js
    monkeypatch.setattr(YtDlpExtractor, "find_binary",
                        lambda self, name, env_var=None: node if name == "node" else None)

    old_args, _ = old.get_base_args(browser="none")
    new_args, _ = new.get_base_args(browser="none")
    assert "--js-runtimes" not in old_args, "yt-dlp cũ sẽ thoát với 'no such option'"
    assert new_args[new_args.index("--js-runtimes") + 1] == f"node:{node}"


def test_stream_without_resolution_still_offers_a_video_option(tmp_path):
    """HLS .m3u8 không khai báo độ phân giải từng chỉ có lựa chọn âm thanh."""
    hls = {"_type": "video", "id": "index", "title": "index", "formats": [
        {"format_id": "0", "url": "http://x/index.m3u8", "ext": "mp4", "protocol": "m3u8_native"},
    ]}
    binary = _fake_ytdlp(tmp_path, "yt-dlp", [json.dumps(hls)])
    meta = _ytdlp_with(binary).extract_metadata("http://x/index.m3u8", browser="none")
    video = [s for s in meta.streams if s.stream_type == "full"]
    assert video and video[0].format_id == "bestvideo+bestaudio/best"

    audio_only = {"_type": "video", "id": "t", "title": "Track", "formats": [
        {"format_id": "mp3", "url": "http://x/t.mp3", "ext": "mp3", "vcodec": "none", "acodec": "mp3"},
    ]}
    binary = _fake_ytdlp(tmp_path, "yt-dlp-audio", [json.dumps(audio_only)])
    meta = _ytdlp_with(binary).extract_metadata("https://soundcloud.com/a/t", browser="none")
    assert all(s.stream_type == "audio" for s in meta.streams), "nguồn chỉ có tiếng không được bịa lựa chọn video"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
