#!/usr/bin/env python3
"""
Dán link bài viết của MỘT TRANG WEB BẤT KỲ → phải lấy được HẾT ảnh/video của bài.

Kiểm thử ở mức dispatcher (đúng đường đi khi bấm "Phân tích & Tải") với yt-dlp và
gallery-dl GIẢ — chạy offline, kết quả không phụ thuộc phiên bản công cụ. Các lỗi
từng xảy ra trước bản này:

  1. Bài chỉ có ảnh: yt-dlp/gallery-dl báo "Unsupported URL", web_scraper tìm được ảnh
     nhưng bị bỏ đi vì "không có stream" → "Không tìm được nguồn video", 0 ảnh.
  2. Bài có 2 thẻ <video> + ảnh: yt-dlp chỉ trả video ĐẦU TIÊN, mất video 2 và ảnh.
  3. Bài nhúng 1 video YouTube + ảnh: chỉ trả video YouTube, mất ảnh.
  4. Bài nhúng 2 video YouTube: yt-dlp coi là "danh sách" → báo lỗi, không lấy gì.

Đồng thời giữ nguyên những gì đang đúng: playlist / kênh thật vẫn báo là danh sách,
trang video của nền tảng yt-dlp hỗ trợ vẫn trả đủ lựa chọn chất lượng của yt-dlp.
"""

import http.server
import json
import os
import stat
import sys
import threading
import urllib.parse

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.dispatcher import MediaDispatcher
from core.extractors.ytdlp import PlaylistUrlError

UP = "/wp-content/uploads/2026/10"

PAGES = {
    "/blog-images": f"""<html><head><title>Ảnh Đà Lạt</title></head><body><article><h1>Ảnh Đà Lạt</h1>
        <p>{"Chữ. " * 40}</p><img src="{UP}/1.jpg"><img src="{UP}/2.jpg"><img src="{UP}/3.jpg"><img src="{UP}/4.jpg">
        </article></body></html>""",
    "/two-videos": f"""<html><head><title>Hai clip</title></head><body><article><h1>Hai clip</h1>
        <video src="/clip1.mp4"></video><p>giữa</p><video><source src="/clip2.mp4" type="video/mp4"></video>
        <img src="{UP}/5.jpg"></article></body></html>""",
    "/one-embed": f"""<html><head><title>Một video nhúng</title></head><body><article><h1>Review</h1>
        <iframe src="https://www.youtube.com/embed/dQw4w9WgXcQ"></iframe><img src="{UP}/6.jpg"><img src="{UP}/7.jpg">
        </article></body></html>""",
    "/two-embeds": f"""<html><head><title>Hai video nhúng</title></head><body><article><h1>Tổng hợp</h1>
        <iframe src="https://www.youtube.com/embed/dQw4w9WgXcQ"></iframe><img src="{UP}/8.jpg">
        <iframe src="https://www.youtube.com/embed/9bZkp7q19f0"></iframe></article></body></html>""",
    "/channel": f"""<html><head><title>Kênh</title></head><body><main>
        <a href="/v/1"><img src="{UP}/t1.jpg"></a><a href="/v/2"><img src="{UP}/t2.jpg"></a></main></body></html>""",
    "/video-site": f"""<html><head><title>Trang video</title><meta property="og:image" content="{UP}/poster.jpg">
        </head><body><main><div id="player"></div><img src="{UP}/related.jpg"></main></body></html>""",
}


class _Site(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        body = PAGES.get(path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture(scope="module")
def site():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


# yt-dlp GIẢ: in đúng thứ yt-dlp thật in ra cho từng loại trang (đã đối chiếu với
# yt-dlp 2026.08 chạy trên các trang mẫu này).
FAKE_YTDLP = r'''#!/usr/bin/env python3
import json, sys, urllib.parse
if "--help" in sys.argv:
    print("Usage: yt-dlp [OPTIONS] URL"); sys.exit(0)
url = sys.argv[-1]
base = url.split("/", 3)
origin = base[0] + "//" + base[2]
path = urllib.parse.urlsplit(url).path
def fmt(h):
    return {"format_id": str(h), "vcodec": "avc1", "acodec": "mp4a", "height": h, "ext": "mp4"}
if path == "/two-videos":
    for i in (1, 2):
        print(json.dumps({"_type": "video", "id": f"two-videos-{i}", "title": f"Hai clip ({i})",
                          "extractor_key": "HTML5MediaEmbed", "extractor": "html5", "webpage_url": url,
                          "url": f"{origin}/clip{i}.mp4", "playlist_count": 2, "formats": [fmt(360)]}))
elif path == "/one-embed":
    print(json.dumps({"_type": "video", "id": "dQw4w9WgXcQ", "title": "Never Gonna Give You Up",
                      "extractor_key": "Youtube", "extractor": "youtube",
                      "webpage_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
                      "thumbnail": "https://i.ytimg.com/vi/dQw4w9WgXcQ/maxresdefault.jpg", "formats": [fmt(1080)]}))
elif path == "/two-embeds":
    for vid in ("dQw4w9WgXcQ", "9bZkp7q19f0"):
        print(json.dumps({"_type": "url", "ie_key": "Youtube", "extractor_key": "Youtube", "id": vid,
                          "url": f"https://www.youtube.com/embed/{vid}", "webpage_url": f"https://www.youtube.com/embed/{vid}",
                          "playlist_count": 2}))
elif path == "/channel":
    for i in (1, 2):
        print(json.dumps({"_type": "url", "ie_key": "SomeSite", "id": str(i), "url": f"{origin}/v/{i}",
                          "playlist_count": 2, "playlist_title": "Kênh"}))
elif path == "/video-site":
    print(json.dumps({"_type": "video", "id": "vs1", "title": "Video của trang", "extractor_key": "SomeSite",
                      "extractor": "somesite", "webpage_url": url, "formats": [fmt(720), fmt(1080)]}))
else:
    sys.stderr.write(f"ERROR: Unsupported URL: {url}\n"); sys.exit(1)
'''

FAKE_GALLERY_DL = r'''#!/usr/bin/env python3
import sys
sys.stderr.write(f"[gallery-dl][error] Unsupported URL '{sys.argv[-1]}'\n"); sys.exit(1)
'''


class _NoBrowser:
    """Máy chạy test có thể không có Chromium: các ca dưới đây đều phải giải được bằng HTML tĩnh."""

    def is_available(self):
        return False

    def sniff_streams(self, url, **kwargs):
        return {"streams": [], "images": [], "error": "không có trình duyệt"}


def _write(tmp_path, name, body):
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return str(path)


@pytest.fixture
def dispatcher(tmp_path):
    d = MediaDispatcher()
    d.ytdlp.binary_path = _write(tmp_path, "yt-dlp", FAKE_YTDLP)
    d.gallery.binary_path = _write(tmp_path, "gallery-dl", FAKE_GALLERY_DL)
    d.sniffer = _NoBrowser()
    if hasattr(d, "page_media"):
        d.page_media.sniffer = d.sniffer
    return d


def _items(meta, site):
    return [(i.type, (i.url or "").replace(site, "")) for i in (meta.images or [])]


def test_image_only_post_returns_every_image(dispatcher, site):
    meta = dispatcher.extract(f"{site}/blog-images", browser="none")
    assert meta.type == "album"
    assert _items(meta, site) == [("image", f"{UP}/{i}.jpg") for i in (1, 2, 3, 4)]


def test_post_with_two_video_tags_returns_both_videos_and_the_image(dispatcher, site):
    meta = dispatcher.extract(f"{site}/two-videos", browser="none")
    assert meta.type == "album"
    assert _items(meta, site) == [("video", "/clip1.mp4"), ("video", "/clip2.mp4"), ("image", f"{UP}/5.jpg")]


def test_post_embedding_one_youtube_video_keeps_its_images(dispatcher, site):
    meta = dispatcher.extract(f"{site}/one-embed", browser="none")
    assert meta.type == "album"
    assert _items(meta, site) == [
        ("video", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
        ("image", f"{UP}/6.jpg"),
        ("image", f"{UP}/7.jpg"),
    ]
    # Video nhúng phải được tải qua yt-dlp (curl chỉ tải được TỆP)
    assert meta.images[0].to_dict().get("via") == "ytdlp"


def test_post_embedding_two_videos_is_not_reported_as_a_playlist(dispatcher, site):
    meta = dispatcher.extract(f"{site}/two-embeds", browser="none")
    assert _items(meta, site) == [
        ("video", "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),
        ("image", f"{UP}/8.jpg"),
        ("video", "https://www.youtube.com/watch?v=9bZkp7q19f0"),
    ]


def test_a_real_channel_is_still_reported_as_a_list(dispatcher, site):
    with pytest.raises(PlaylistUrlError):
        dispatcher.extract(f"{site}/channel", browser="none")


def test_video_page_of_a_supported_site_keeps_ytdlp_quality_options(dispatcher, site):
    meta = dispatcher.extract(f"{site}/video-site", browser="none")
    assert meta.type == "video"
    assert meta.title == "Video của trang"
    assert any("1080" in (s.quality or "") for s in meta.streams)
    assert not meta.images


def test_ipc_payload_of_a_post_is_plain_json(dispatcher, site):
    """Kết quả gửi lên giao diện không mang trường nội bộ của engine."""
    data = dispatcher.extract(f"{site}/one-embed", browser="none").to_dict()
    text = json.dumps(data, ensure_ascii=False)
    assert "extractor_key" not in text and "webpage_url" not in text and "page_video_count" not in text
    assert data["images"][0]["via"] == "ytdlp"
    assert "via" not in data["images"][1]
