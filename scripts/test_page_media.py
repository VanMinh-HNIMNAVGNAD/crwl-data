#!/usr/bin/env python3
"""
Kiểm thử "lấy HẾT ảnh/video của một bài viết trên trang web bất kỳ" (core/extractors/page_media.py).

Trước đây với trang không thuộc nền tảng quen thuộc:
  - bài chỉ có ảnh → "Không tìm được nguồn video", 0 ảnh;
  - bài nhiều video → chỉ video đầu tiên; bài nhúng YouTube → chỉ video, mất hết ảnh;
  - bài nhúng 2 video → lỗi "liên kết là một danh sách";
  - trang dựng bằng JavaScript → chỉ bắt được luồng video, mất ảnh;
  - trang cần đăng nhập → không gửi cookie nên chỉ thấy trang đăng nhập.

Chạy offline với máy chủ HTTP cục bộ. Các test cần Chromium thật (Playwright) tự bỏ
qua nếu máy chưa cài.
"""

import http.server
import json
import os
import struct
import sys
import threading
import time
import urllib.parse
import zlib

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.extractors import page_media, stream_sniffer
from core.extractors.page_media import (
    MAX_ITEMS,
    NoPostMediaError,
    PageMediaExtractor,
    embed_info,
    parse_srcset,
    scan_html,
    select_post_media,
    variant_key,
)
from core.extractors.stream_sniffer import PlaywrightStreamSniffer, _PlaywrightWorkerThread


# ─────────────────────────────────────────────────────────────────────────────
# Trang mẫu
# ─────────────────────────────────────────────────────────────────────────────

UP = "/wp-content/uploads/2026/10"

WORDPRESS_POST = f"""<!doctype html><html><head><title>Chuyến đi Đà Lạt – Blog</title>
<meta property="og:title" content="Chuyến đi Đà Lạt">
<meta property="og:image" content="{UP}/cover.jpg">
<meta property="og:site_name" content="Blog Du Lịch">
</head><body>
<header class="site-header"><img src="/logo.png" alt="logo"><nav class="menu"><img src="/icons/home.png"></nav></header>
<div class="post-thumbnail"><img src="{UP}/cover-1024x683.jpg" srcset="{UP}/cover-300x200.jpg 300w, {UP}/cover-1024x683.jpg 1024w, {UP}/cover.jpg 2048w"></div>
<div class="site-content content-sidebar-wrap">
<article id="post-1" class="post type-post">
  <h1>Chuyến đi Đà Lạt</h1>
  <div class="entry-content">
    <p>{"Hồ Xuân Hương buổi sớm. " * 20}</p>
    <img src="{UP}/a-300x225.jpg" srcset="{UP}/a-300x225.jpg 300w, {UP}/a-1024x768.jpg 1024w, {UP}/a.jpg 1600w" width="300" height="225">
    <p>Ảnh hai bấm vào xem bản gốc</p>
    <a href="{UP}/b.jpg"><img src="{UP}/b-300x225.jpg" width="300" height="225"></a>
    <p>Ảnh lazy-load</p>
    <img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" data-src="{UP}/c.jpg" class="lazyload">
    <noscript><img src="{UP}/c.jpg"></noscript>
    <picture><source srcset="{UP}/d.webp" type="image/webp"><img src="{UP}/d-300x225.jpg"></picture>
    <img src="https://s.w.org/images/core/emoji/15.0.3/svg/1f600.svg" class="emoji" width="16" height="16">
    <img src="https://stats.example.com/pixel.gif" width="1" height="1">
    <img src="{UP}/e.png">
    <div class="sharedaddy sd-sharing"><img src="/share-facebook.png"></div>
  </div>
  <section class="jp-relatedposts"><img src="{UP}/related-1.jpg"><img src="{UP}/related-2.jpg"></section>
</article>
<aside class="sidebar widget-area"><img src="{UP}/ad-banner.jpg"><img src="{UP}/sidebar-photo.jpg"></aside>
</div>
<div id="comments" class="comments-area"><img src="https://secure.gravatar.com/avatar/abc?s=48"><img src="{UP}/comment-upload.jpg"></div>
<footer class="site-footer"><img src="/footer-logo.png"></footer>
</body></html>"""

VIDEO_POST = f"""<!doctype html><html><head><title>Bài video</title></head><body>
<main><article><h1>Ba video và một ảnh</h1><p>{"Nội dung. " * 30}</p>
<video controls src="/clip1.mp4" poster="{UP}/poster1.jpg"></video>
<video controls><source src="/clip2.webm" type="video/webm"><source src="/clip2.mp4" type="video/mp4"></video>
<iframe width="560" height="315" src="https://www.youtube.com/embed/dQw4w9WgXcQ?rel=0"></iframe>
<iframe src="https://player.vimeo.com/video/76979871"></iframe>
<a href="/clip3.mov"><img src="{UP}/clip3-thumb.jpg"></a>
<video controls src="/hls/master.m3u8"></video>
<img src="{UP}/photo.jpg">
</article></main></body></html>"""

JSONLD_ONLY = """<!doctype html><html><head><title>Bài JSON-LD</title>
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[
 {"@type":"Organization","logo":"https://example.com/org-logo.png"},
 {"@type":"NewsArticle","headline":"Tin","image":["https://cdn.example.com/news/hero.jpg",{"@type":"ImageObject","url":"https://cdn.example.com/news/second.jpg"}],
  "author":{"@type":"Person","image":"https://cdn.example.com/author.jpg"},
  "video":{"@type":"VideoObject","contentUrl":"https://cdn.example.com/news/clip.mp4","thumbnailUrl":"https://cdn.example.com/news/clip.jpg"}}
]}</script></head><body><article><p>Chỉ có chữ.</p></article></body></html>"""

FEED_PAGE = "<!doctype html><html><head><title>Trang chủ</title></head><body><main>" + "".join(
    f'<article><h2>Bài {i}</h2><p>{"Tóm tắt. " * 10}</p><img src="/feed/{i}.jpg"></article>' for i in range(1, 7)
) + "</main></body></html>"

LOGIN_PAGE = """<!doctype html><html><head><title>Đăng nhập</title></head><body>
<form action="/login" method="post"><input name="user"><input type="password" name="pass"><button>Đăng nhập</button></form>
</body></html>"""

PRIVATE_POST = f"""<!doctype html><html><head><title>Bài riêng tư</title></head><body><article><h1>Chỉ thành viên</h1>
<p>{"Nội dung riêng. " * 20}</p><img src="{UP}/private-1.jpg"><img src="{UP}/private-2.jpg"><img src="{UP}/private-3.jpg">
</article></body></html>"""

SPA_PAGE = f"""<!doctype html><html><head><title>SPA</title></head><body><div id="root"></div>
<script>
const a = document.createElement('article');
a.innerHTML = '<h1>Bài dựng bằng JS</h1><p>' + 'Nội dung. '.repeat(40) + '</p>';
['{UP}/spa-1.jpg', '{UP}/spa-2.jpg'].forEach((u) => {{ const i = new Image(); i.src = u; a.appendChild(i); }});
const v = document.createElement('video'); v.src = '/clip1.mp4'; a.appendChild(v);
document.getElementById('root').appendChild(a);
</script></body></html>"""

LAZY_SCROLL_PAGE = f"""<!doctype html><html><head><title>Lazy</title></head><body><article><h1>Ảnh nạp khi cuộn</h1>
{"".join(f'<div style="height:1200px"></div><img class="lz" data-src="{UP}/lazy-{i}.jpg" src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" width="800" height="600">' for i in range(1, 6))}
</article>
<script>
const io = new IntersectionObserver((entries) => entries.forEach((e) => {{
  if (e.isIntersecting) {{ e.target.src = e.target.dataset.src; e.target.removeAttribute('data-src'); io.unobserve(e.target); }}
}}));
document.querySelectorAll('img.lz').forEach((img) => io.observe(img));
</script></body></html>"""

MSE_PAGE = """<!doctype html><html><head><title>Player</title></head><body><article><h1>Video phát qua blob</h1>
<p>Player tự tải manifest rồi gắn blob: vào thẻ video.</p><video id="v" controls></video></article>
<script>
fetch('/hls/master.m3u8').then((r) => r.text()).then(() => {
  document.getElementById('v').src = URL.createObjectURL(new Blob(['x'], { type: 'video/mp4' }));
});
</script></body></html>"""

SPA_PRIVATE = """<!doctype html><html><head><title>SPA riêng tư</title></head><body><div id="app"></div>
<script>
fetch('/api/private-post', { credentials: 'include' }).then((r) => r.json()).then((d) => {
  const a = document.createElement('article');
  a.innerHTML = '<h1>' + d.title + '</h1><p>' + 'Nội dung riêng. '.repeat(30) + '</p>';
  d.images.forEach((u) => { const i = new Image(); i.src = u; a.appendChild(i); });
  document.getElementById('app').appendChild(a);
});
</script></body></html>"""

def _png(width, height):
    """Ảnh PNG thật (một màu). Ảnh 1x1 sẽ bị loại đúng như pixel theo dõi."""
    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\x80\x40\x20" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


PHOTO = _png(400, 300)


class _Handler(http.server.BaseHTTPRequestHandler):
    session_cookie = "session=dung-roi"
    seen_cookies = []

    def log_message(self, *a):
        pass

    def _send(self, body, ctype="text/html; charset=utf-8", status=200, length=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length if length is not None else len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        cookie = self.headers.get("Cookie") or ""
        _Handler.seen_cookies.append((path, cookie))
        logged_in = self.session_cookie in cookie
        pages = {
            "/wp-post": WORDPRESS_POST,
            "/video-post": VIDEO_POST,
            "/jsonld": JSONLD_ONLY,
            "/feed": FEED_PAGE,
            "/spa": SPA_PAGE,
            "/lazy": LAZY_SCROLL_PAGE,
            "/mse": MSE_PAGE,
            "/spa-private": SPA_PRIVATE,
        }
        if path in pages:
            return self._send(pages[path])
        if path == "/members/post":
            return self._send(PRIVATE_POST if logged_in else LOGIN_PAGE)
        if path == "/api/private-post":
            if not logged_in:
                return self._send(json.dumps({"error": "login"}), "application/json", 401)
            return self._send(json.dumps({"title": "Riêng tư", "images": [f"{UP}/vip-1.jpg", f"{UP}/vip-2.jpg"]}), "application/json")
        if path == "/huge-post":
            imgs = "".join(f'<img src="{UP}/huge-{i}.jpg">' for i in range(3000))
            return self._send(f"<html><body><article><p>{'chữ ' * 100}</p>{imgs}</article></body></html>")
        if path == "/direct/photo.jpg":
            return self._send(PHOTO, "image/jpeg")
        if path == "/direct/clip.mp4":
            return self._send(b"\x00\x00\x00\x18ftypmp42", "application/octet-stream", length=200 * 1024 * 1024)
        if path == "/direct/video":
            # Khai báo 200 MB nhưng chỉ gửi vài byte: nếu bộ bóc tách cố đọc thân tệp
            # thì nó sẽ treo tới hết thời gian chờ.
            return self._send(b"\x00\x00\x00\x18ftypmp42", "video/mp4", length=200 * 1024 * 1024)
        if path == "/hls/master.m3u8":
            return self._send("#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1\nlow.m3u8\n", "application/vnd.apple.mpegurl")
        if path.endswith((".jpg", ".png", ".webp")):
            return self._send(PHOTO, "image/png")
        if path.endswith((".mp4", ".webm", ".mov")):
            return self._send(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64, "video/mp4")
        self._send("not found", "text/plain", 404)


@pytest.fixture(scope="module")
def site():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture(autouse=True)
def no_real_browser_cookies(monkeypatch):
    """Không bao giờ đọc cookie thật của trình duyệt trên máy chạy test."""
    monkeypatch.setattr(page_media, "get_browser_cookies_txt", lambda *a, **k: None)
    monkeypatch.setattr(stream_sniffer, "get_browser_cookies_txt", lambda *a, **k: None)


def _static(url):
    """Chỉ đọc HTML tĩnh (không mở trình duyệt)."""
    return PageMediaExtractor(sniffer=None).extract(url, browser="none")


def _paths(meta):
    return [urllib.parse.urlsplit(i.url).path for i in (meta.images or [])]


# ─────────────────────────────────────────────────────────────────────────────
# 1. Bài viết tĩnh
# ─────────────────────────────────────────────────────────────────────────────

def test_wordpress_post_returns_every_post_image_full_size_and_nothing_else(site):
    meta = _static(f"{site}/wp-post")
    assert meta.type == "album"
    assert meta.title == "Chuyến đi Đà Lạt"
    assert _paths(meta) == [
        f"{UP}/cover.jpg",    # ảnh đại diện nằm ngoài <article> nhưng chính là og:image
        f"{UP}/a.jpg",        # srcset → bản 1600w
        f"{UP}/b.jpg",        # link "xem ảnh gốc" bọc ảnh thu nhỏ
        f"{UP}/c.jpg",        # lazy-load data-src (bản <noscript> trùng được gộp)
        f"{UP}/d.webp",       # <picture><source> bản gốc thay vì -300x225
        f"{UP}/e.png",
    ]
    by_path = {urllib.parse.urlsplit(i.url).path: i for i in meta.images}
    # Xem trước bằng bản NHỎ để lưới album không giải mã ảnh gốc
    assert by_path[f"{UP}/a.jpg"].thumb.endswith("a-300x225.jpg")
    assert by_path[f"{UP}/b.jpg"].thumb.endswith("b-300x225.jpg")
    assert all(i.via is None for i in meta.images)
    assert meta.referer and meta.referer.endswith("/wp-post")


def test_videos_embeds_and_streams_of_a_post(site):
    meta = _static(f"{site}/video-post")
    assert meta.type == "album"
    items = [(i.type, i.url.replace(site, ""), i.via) for i in meta.images]
    assert items == [
        ("video", "/clip1.mp4", None),
        ("video", "/clip2.mp4", None),   # các <source> của một <video> là MỘT video, ưu tiên mp4
        ("video", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "ytdlp"),
        ("video", "https://vimeo.com/76979871", "ytdlp"),
        ("video", "/clip3.mov", None),   # <a href=video><img> → ảnh chỉ là ảnh bìa
        ("video", "/hls/master.m3u8", "ytdlp"),
        ("image", f"{UP}/photo.jpg", None),
    ]
    thumbs = {i.url.replace(site, ""): i.thumb for i in meta.images}
    assert thumbs["/clip1.mp4"].endswith("poster1.jpg")
    assert thumbs["https://www.youtube.com/watch?v=dQw4w9WgXcQ"] == "https://i.ytimg.com/vi/dQw4w9WgXcQ/hqdefault.jpg"
    assert thumbs["/clip3.mov"].endswith("clip3-thumb.jpg")
    # Ô video không bao giờ lấy URL video làm ảnh xem trước (WebKitGTK ngốn vài GB RAM)
    assert not any(t and t.endswith((".mp4", ".mov", ".m3u8")) for t in thumbs.values())


def test_jsonld_media_is_used_when_the_post_body_has_none(site):
    meta = _static(f"{site}/jsonld")
    urls = [(i.type, i.url) for i in meta.images]
    assert ("video", "https://cdn.example.com/news/clip.mp4") in urls
    # Logo tổ chức và ảnh tác giả không phải media của bài
    assert all("org-logo" not in u and "author" not in u for _, u in urls)


def test_feed_page_keeps_every_article_instead_of_picking_one(site):
    meta = _static(f"{site}/feed")
    assert _paths(meta) == [f"/feed/{i}.jpg" for i in range(1, 7)]


def test_huge_page_is_capped_and_fast(site):
    started = time.monotonic()
    meta = _static(f"{site}/huge-post")
    assert time.monotonic() - started < 10
    assert len(meta.images) == MAX_ITEMS
    assert "chỉ lấy" in meta.description


def test_direct_file_links_never_download_the_body(site):
    photo = _static(f"{site}/direct/photo.jpg")
    assert photo.type == "album" and photo.images[0].url.endswith("/direct/photo.jpg")

    started = time.monotonic()
    video = _static(f"{site}/direct/video")
    assert time.monotonic() - started < 5, "Không được đọc thân tệp video 200 MB"
    assert video.type == "video" and video.streams[0].url.endswith("/direct/video")

    started = time.monotonic()
    generic = _static(f"{site}/direct/clip.mp4")
    assert time.monotonic() - started < 5
    assert generic.type == "video" and generic.streams[0].url.endswith("/direct/clip.mp4")


# ─────────────────────────────────────────────────────────────────────────────
# 2. Trang cần đăng nhập
# ─────────────────────────────────────────────────────────────────────────────

def _cookie_file(tmp_path, host, line_value="dung-roi", expires="0"):
    path = tmp_path / "cookies_export.txt"
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        f"{host}\tFALSE\t/\tFALSE\t{expires}\tsession\t{line_value}\n"
        f".other-site.com\tTRUE\t/\tFALSE\t0\tsession\tKHONG-DUOC-GUI\n",
        encoding="utf-8",
    )
    return path


def test_logged_in_page_uses_session_cookie_and_cleans_up(site, tmp_path, monkeypatch):
    host = urllib.parse.urlsplit(site).hostname
    exported = _cookie_file(tmp_path, host)
    asked = []

    def fake_export(browser, domain=None):
        asked.append((browser, domain))
        copy = tmp_path / f"tmp_{len(asked)}.txt"
        copy.write_text(exported.read_text(encoding="utf-8"), encoding="utf-8")
        return str(copy)

    monkeypatch.setattr(page_media, "get_browser_cookies_txt", fake_export)
    _Handler.seen_cookies.clear()
    meta = PageMediaExtractor(sniffer=None).extract(f"{site}/members/post", browser="firefox")

    assert _paths(meta) == [f"{UP}/private-{i}.jpg" for i in (1, 2, 3)]
    assert asked == [("firefox", host)], "Chỉ xin cookie của ĐÚNG tên miền trang"
    sent = [c for p, c in _Handler.seen_cookies if p == "/members/post"]
    # Cookie phiên (hạn 0) vẫn phải được gửi; cookie của tên miền khác thì không
    assert sent and "session=dung-roi" in sent[0] and "KHONG-DUOC-GUI" not in sent[0]
    assert not list(tmp_path.glob("tmp_*.txt")), "Tệp cookie tạm phải bị xoá"


def test_login_wall_without_cookies_gives_an_actionable_error(site):
    with pytest.raises(RuntimeError) as info:
        _static(f"{site}/members/post")
    assert "đăng nhập" in str(info.value)
    assert "Cookie Manager" in str(info.value)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Hàm thuần
# ─────────────────────────────────────────────────────────────────────────────

def test_srcset_parsing_handles_commas_inside_urls_and_densities():
    entries = parse_srcset(
        "https://res.cloudinary.com/x/image/upload/w_300,h_200/a.jpg 300w,"
        " https://res.cloudinary.com/x/image/upload/w_900,h_600/a.jpg 900w"
    )
    assert [e[1] for e in entries] == [300, 900]
    assert entries[1][0].endswith("w_900,h_600/a.jpg")
    assert parse_srcset("a.jpg 1x, b.jpg 2x") == [("a.jpg", 0.0, 1.0), ("b.jpg", 0.0, 2.0)]
    assert parse_srcset("") == []


def test_size_variants_share_one_key():
    assert variant_key("https://x.com/wp-content/uploads/a-300x200.jpg") == variant_key("https://x.com/wp-content/uploads/a.jpg")
    assert variant_key("https://x.com/a-scaled.jpg") == variant_key("https://x.com/a.jpg")
    assert variant_key("https://cdn.x.com/i.jpg?w=300&q=80") == variant_key("https://cdn.x.com/i.jpg?w=1200")
    assert variant_key("https://x.com/a.jpg") != variant_key("https://x.com/b.jpg")


def test_embed_urls_become_downloadable_video_pages():
    assert embed_info("https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ")[0] == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert embed_info("https://player.vimeo.com/video/123?h=abc")[0] == "https://vimeo.com/123"
    fb = embed_info("https://www.facebook.com/plugins/video.php?href=https%3A%2F%2Fwww.facebook.com%2Fx%2Fvideos%2F9%2F")
    assert fb[0] == "https://www.facebook.com/x/videos/9/"
    assert embed_info("https://ads.example.com/frame.html") is None


def test_hostile_html_is_parsed_in_linear_time():
    """Thẻ không đóng, lồng sâu, thuộc tính khổng lồ: không treo, không vỡ."""
    nasty = (
        "<html><body>"
        + "<meta " * 200_000
        + "<div>" * 50_000
        + "<article>" * 5_000
        + '<img src="/x.jpg" srcset="' + "a.jpg 1w, " * 20_000 + '">'
        + "</p>" * 20_000
        + '<img data-src="' + "/" * 300_000 + 'y.jpg">'
    )
    started = time.monotonic()
    scan = scan_html(nasty, "https://example.com/p")
    items, _ = select_post_media(scan)
    assert time.monotonic() - started < 15
    assert any(i.url.endswith("/x.jpg") or i.url.endswith("a.jpg") for i in items)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Trang dựng bằng JavaScript (Chromium thật)
# ─────────────────────────────────────────────────────────────────────────────

def _playwright_ready() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False
    try:
        with sync_playwright() as pw:
            return os.path.exists(pw.chromium.executable_path)
    except Exception:
        return False


needs_chromium = pytest.mark.skipif(not _playwright_ready(), reason="cần Playwright + Chromium")


@pytest.fixture
def sniffer(monkeypatch):
    monkeypatch.setattr(_PlaywrightWorkerThread, "IDLE_SHUTDOWN_SEC", 2.0)
    monkeypatch.setattr(PlaywrightStreamSniffer, "COLLECT_MAX_SCROLL_STEPS", 25)
    sn = PlaywrightStreamSniffer()
    sn.close()
    yield sn
    sn.close()


@needs_chromium
def test_spa_post_is_rendered_and_every_media_found(site, sniffer):
    meta = PageMediaExtractor(sniffer).extract(f"{site}/spa", browser="none")
    assert [(i.type, i.url.replace(site, "")) for i in meta.images] == [
        ("image", f"{UP}/spa-1.jpg"), ("image", f"{UP}/spa-2.jpg"), ("video", "/clip1.mp4"),
    ]
    assert "trình duyệt" in meta.description


@needs_chromium
def test_lazy_images_loaded_on_scroll_are_all_collected(site, sniffer):
    meta = PageMediaExtractor(sniffer).extract(f"{site}/lazy", browser="none")
    assert _paths(meta) == [f"{UP}/lazy-{i}.jpg" for i in range(1, 6)]


@needs_chromium
def test_blob_video_is_recovered_from_the_network_manifest(site, sniffer):
    meta = PageMediaExtractor(sniffer).extract(f"{site}/mse", browser="none")
    assert meta.type == "video"
    assert meta.streams[0].url.endswith("/hls/master.m3u8")
    assert meta.streams[0].format == "HLS"


@needs_chromium
def test_logged_in_spa_loads_private_post_with_browser_cookies(site, sniffer, tmp_path, monkeypatch):
    host = urllib.parse.urlsplit(site).hostname
    exported = _cookie_file(tmp_path, host)
    created = []

    def fake_export(browser, domain=None):
        copy = tmp_path / f"pw_{len(created)}.txt"
        copy.write_text(exported.read_text(encoding="utf-8"), encoding="utf-8")
        created.append(copy)
        return str(copy)

    monkeypatch.setattr(stream_sniffer, "get_browser_cookies_txt", fake_export)
    meta = PageMediaExtractor(sniffer).extract(f"{site}/spa-private", browser="auto")
    assert _paths(meta) == [f"{UP}/vip-1.jpg", f"{UP}/vip-2.jpg"]
    assert created and not any(p.exists() for p in created), "Tệp cookie tạm phải bị xoá"

    # Người dùng chọn KHÔNG dùng cookie: không được nạp cookie vào trình duyệt
    created.clear()
    with pytest.raises(NoPostMediaError) as info:
        PageMediaExtractor(sniffer).extract(f"{site}/spa-private", browser="none")
    assert info.value.dynamic_ran
    assert created == []


def test_text_only_post_does_not_return_the_sites_default_share_image():
    """Bài chỉ có chữ: og:image là thẻ chia sẻ chung của cả trang, không phải media của bài."""
    page = (
        '<html><head><meta property="og:image" content="https://s.w.org/images/home/wordpress-default-ogimage.png">'
        '</head><body><main><article><p>' + "Chỉ có chữ. " * 50 + "</p></article></main></body></html>"
    )
    items, _ = select_post_media(scan_html(page, "https://wordpress.org/news/x/"))
    assert items == []
    # og:image riêng của bài (vd. bài mạng xã hội chỉ hiện ảnh bằng JS) vẫn được dùng
    page2 = page.replace("images/home/wordpress-default-ogimage.png", "uploads/2026/10/anh-bai-viet.jpg")
    items2, _ = select_post_media(scan_html(page2, "https://example.com/p/"))
    assert [i.url for i in items2] == ["https://s.w.org/uploads/2026/10/anh-bai-viet.jpg"]


def test_forum_thread_skips_inline_emoji_avatars_and_site_ui_video():
    """Thread XenForo: ảnh đính kèm của các bài, không lấy emoji 72x72, avatar, video giao diện."""
    page = (
        '<html><body><div class="p-body"><div class="block-body">'
        + "".join(
            f'<article class="message message--post"><div class="message-avatar">'
            f'<img src="/data/avatars/m/1/{i}.jpg" width="96" height="96" class="avatar avatar--m"></div>'
            f'<div class="message-content"><div class="bbWrapper">Bài {i} '
            f'<img class="smilie smilie--emoji" src="https://cdn.jsdelivr.net/gh/twitter/twemoji@14.0.2/assets/72x72/2705.png" width="72" height="72">'
            f'<img src="/attachments/anh-{i}-webp.{1000 + i}/" class="bbImage"></div></div>'
            f'<aside class="message-signature"><img src="/sig/{i}.png"></aside></article>'
            for i in range(1, 5)
        )
        + '</div></div></div><div class="overlay-container"><div class="overlay">'
        '<div class="block-row"><video src="/styles/default/xenforo/add_to_home.mp4" autoplay muted></video></div></div></div>'
        "</body></html>"
    )
    items, scope = select_post_media(scan_html(page, "https://forum.example.com/t/x.1/"))
    assert scope == "feed"
    assert [i.url for i in items] == [f"https://forum.example.com/attachments/anh-{i}-webp.{1000 + i}/" for i in range(1, 5)]


def test_browser_is_only_opened_for_lazy_images_or_players_inside_the_post(site, monkeypatch):
    """Mở Chromium tốn 10-25 giây: ảnh lazy ở sidebar hay bản đồ trong bài không đáng."""
    calls = []

    class Spy:
        def is_available(self):
            return True

        def collect_page_media(self, url, browser=None):
            calls.append(url)
            return {"dom": None, "frames": [], "network": [], "error": "spy"}

    def scan_of(body):
        scan = scan_html(f"<html><body>{body}</body></html>", "https://example.com/p")
        items, scope = select_post_media(scan)
        return scan, items, scope

    ext = PageMediaExtractor(Spy())
    post = '<article><p>' + "chữ " * 150 + '</p><img src="/a.jpg">{}</article>'
    sidebar_lazy = '<aside class="sidebar"><img src="data:image/gif;base64,R0l" data-x="1"></aside>'
    maps = '<iframe src="https://www.google.com/maps/embed?pb=1"></iframe>'
    player = '<iframe src="https://player.example-video.net/e/abc"></iframe>'
    lazy_in_post = '<img src="data:image/gif;base64,R0l">'

    scan, items, scope = scan_of(post.format(maps) + sidebar_lazy)
    assert not ext._needs_dynamic(scan, items, scope, False)
    scan, items, scope = scan_of(post.format(player))
    assert ext._needs_dynamic(scan, items, scope, False)
    scan, items, scope = scan_of(post.format(lazy_in_post))
    assert ext._needs_dynamic(scan, items, scope, False)
    assert calls == []


def test_text_only_post_never_returns_sidebar_or_footer_images():
    page = (
        '<html><body><div class="sidebar-left"><img src="/about-me.jpg"></div>'
        '<div class="post"><h1>Chỉ có chữ</h1><p>' + "Nội dung. " * 80 + "</p></div>"
        '<footer><img src="/partner.jpg"></footer></body></html>'
    )
    items, _ = select_post_media(scan_html(page, "https://blog.example.com/p"))
    assert items == []


def test_layout_wrapper_named_like_a_sidebar_still_counts_as_content():
    """Khung bọc cả trang mang class "has-sidebar" (không có article/main) vẫn là nội dung."""
    page = (
        '<html><body><div id="wrapper" class="layout has-sidebar"><div class="content"><h1>Ảnh</h1><p>'
        + "Nội dung. " * 80 + '</p><img src="/photo-1.jpg"><img src="/photo-2.jpg"></div>'
        '<div class="widget"><img src="/ad.jpg"></div></div></body></html>'
    )
    items, _ = select_post_media(scan_html(page, "https://blog.example.com/p"))
    assert [i.url for i in items] == ["https://blog.example.com/photo-1.jpg", "https://blog.example.com/photo-2.jpg"]
