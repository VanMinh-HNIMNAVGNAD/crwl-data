#!/usr/bin/env python3
"""
Kiểm thử các link / trang từng lọt khỏi mọi nhánh bóc tách (bản 3.7.6):

  1. Trang ghi thẳng tên tệp có dấu cách / tiếng Việt (`<img src="/ảnh đẹp 1.jpg">`):
     engine trả URL thô mà curl từ chối ("URL rejected: Malformed input") — bài viết
     phân tích ra đủ ảnh nhưng tải tệp nào cũng hỏng.
  2. Trang AMP (bản báo mobile mà Google chia sẻ): <amp-img> / <amp-video> /
     <amp-youtube> bị bỏ qua — link AMP của một bài báo chỉ ra video, mất hết ảnh.
  3. Định tuyến link chia sẻ: bài ảnh TikTok (/photo/) đi qua yt-dlp vốn không hỗ trợ;
     Instagram /share/; Douyin v.douyin.com / iesdouyin.com (yt-dlp chỉ nhận
     douyin.com/video/<id>); nút "Chia sẻ" của app Reddit (/r/<sub>/s/<mã>).

Chạy offline:  python3 -m pytest -q scripts/test_share_links_and_page_urls.py
"""

import functools
import http.server
import os
import shutil
import subprocess
import sys
import threading

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.dispatcher import MediaDispatcher
from core.extractors.page_media import PageMediaExtractor, _clean_url, scan_html, select_post_media
from core.models import MediaImage, MediaMetadata
from core.resolver.url_resolver import UrlResolver

JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00" + b"\x00" * 64 + b"\xff\xd9"


def _post_items(html, url="https://bao.vn/tin/bai-viet.html"):
    items, _ = select_post_media(scan_html(html, url))
    return [(c.kind, c.url, c.via) for c in items]


# ─────────────────────────────────────────────────────────────────────────────
# 1. Tên tệp có dấu cách / tiếng Việt
# ─────────────────────────────────────────────────────────────────────────────

def test_urls_are_encoded_like_a_browser_does():
    base = "https://bao.vn/tin/bai.html"
    assert _clean_url("/up/ảnh đẹp 1.jpg", base) == "https://bao.vn/up/%E1%BA%A3nh%20%C4%91%E1%BA%B9p%201.jpg"
    assert _clean_url("/a b.jpg?chu thich=có dấu#xem", base) == "https://bao.vn/a%20b.jpg?chu%20thich=c%C3%B3%20d%E1%BA%A5u"
    # Đã mã hoá sẵn / ký tự hợp lệ: giữ nguyên, không mã hoá hai lần
    for ok in (
        "https://cdn.vn/a/%E1%BA%A3nh%20x.jpg?w=1080&q=75",
        "https://cdn.vn/_next/image?url=%2Fa.jpg&w=640&q=75",
        "https://cdn.vn/anh[1].jpg?size[]=l&x=a,b;c",
    ):
        assert _clean_url(ok, base) == ok
    assert _clean_url("https://bóngđá.vn/a.jpg", base).startswith("https://xn--")


def test_post_with_accented_file_names_gives_one_encoded_url_per_image():
    html = """<html><body><article><h1>Bài</h1><p>chữ</p>
      <img src="/up/ảnh đẹp 1.jpg" width="800" height="600">
      <img src="/up/%E1%BA%A3nh%20%C4%91%E1%BA%B9p%201.jpg" width="800" height="600">
      <img src="/up/hình 2.png" width="800" height="600">
    </article></body></html>"""
    urls = [u for _, u, _ in _post_items(html)]
    assert urls == [
        "https://bao.vn/up/%E1%BA%A3nh%20%C4%91%E1%BA%B9p%201.jpg",
        "https://bao.vn/up/h%C3%ACnh%202.png",
    ], "bản thô và bản đã mã hoá là CÙNG một ảnh"


@pytest.fixture()
def site(tmp_path):
    (tmp_path / "up").mkdir()
    (tmp_path / "up" / "ảnh đẹp 1.jpg").write_bytes(JPEG)
    (tmp_path / "bai.html").write_text(
        '<html><head><title>Bài</title></head><body><article><h1>Bài</h1><p>chữ</p>'
        '<img src="/up/ảnh đẹp 1.jpg" width="800" height="600"></article></body></html>',
        encoding="utf-8",
    )
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    handler.log_message = lambda *a: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.mark.skipif(not shutil.which("curl"), reason="cần curl")
def test_image_url_from_the_page_downloads_with_curl(site, tmp_path):
    res = PageMediaExtractor(None).extract(f"{site}/bai.html", browser="none", allow_dynamic=False)
    assert [img.type for img in res.images] == ["image"]
    dest = tmp_path / "tai-ve.jpg"
    # Cùng cách Rust gọi curl (-g: không coi [ ] { } là mẫu)
    proc = subprocess.run(["curl", "-sSfg", "-o", str(dest), res.images[0].url], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert dest.read_bytes() == JPEG


# ─────────────────────────────────────────────────────────────────────────────
# 2. Trang AMP
# ─────────────────────────────────────────────────────────────────────────────

AMP_PAGE = """<html amp><head><title>Tin AMP</title></head><body><article><h1>Tin</h1><p>nội dung</p>
  <amp-img src="/img/a.jpg" width="800" height="600" layout="responsive">
    <noscript><img src="/img/a.jpg" width="800" height="600"></noscript></amp-img>
  <amp-img src="/img/b.jpg" srcset="/img/b.jpg 800w, /img/b-lon.jpg 1600w" width="800" height="600"></amp-img>
  <amp-anim src="/img/dong.gif" width="400" height="300"></amp-anim>
  <amp-video poster="/img/poster.jpg" width="640" height="360" layout="responsive" controls>
    <source src="/v/clip.mp4" type="video/mp4"></amp-video>
  <amp-iframe src="https://www.youtube.com/embed/dQw4w9WgXcQ" width="640" height="360" sandbox="allow-scripts">
    <amp-img placeholder src="/img/cho.jpg" layout="fill"></amp-img></amp-iframe>
  <amp-youtube data-videoid="jNQXAC9IVRw" layout="responsive" width="480" height="270"></amp-youtube>
</article></body></html>"""


def test_amp_article_keeps_every_image_and_video():
    assert _post_items(AMP_PAGE) == [
        ("image", "https://bao.vn/img/a.jpg", None),
        ("image", "https://bao.vn/img/b-lon.jpg", None),
        ("image", "https://bao.vn/img/dong.gif", None),
        ("video", "https://bao.vn/v/clip.mp4", None),
        ("video", "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "ytdlp"),
        ("video", "https://www.youtube.com/watch?v=jNQXAC9IVRw", "ytdlp"),
    ]


# ─────────────────────────────────────────────────────────────────────────────
# 3. Định tuyến link chia sẻ
# ─────────────────────────────────────────────────────────────────────────────

def test_share_links_are_routed_to_the_engine_that_supports_them():
    d = MediaDispatcher()
    photo = "https://www.tiktok.com/@user/photo/7400000000000000000"
    assert not d.is_video_audio_platform(photo), "yt-dlp chỉ nhận /video/"
    assert d.is_gallery_platform(photo)
    assert d.is_video_audio_platform("https://www.tiktok.com/@user/video/7400000000000000000")
    for share in ("https://www.instagram.com/share/reel/BAvqZ1x2y3", "https://www.instagram.com/share/BAvqZ1x2y3"):
        assert d.is_gallery_platform(share), share
    assert d.is_video_audio_platform("https://www.douyin.com/video/7300000000000000000")
    assert not d.movie.is_movie_or_stream_url("https://www.douyin.com/video/7300000000000000000")


def test_douyin_and_reddit_share_links_are_expanded():
    is_short = UrlResolver.is_shortened_url
    assert is_short("https://v.douyin.com/iAbCdEf/")
    assert is_short("https://www.reddit.com/r/pics/s/AbCdEf1234")
    assert is_short("https://reddit.com/u/someone/s/AbCdEf1234/")
    assert is_short("www.reddit.com/r/pics/s/AbCdEf1234"), "dán thiếu https://"
    assert not is_short("https://www.reddit.com/r/pics/comments/1abcde/tieu_de/")
    assert not is_short("https://www.reddit.com/r/s/")
    # Khách chưa đăng nhập bị Instagram chuyển sang trang đăng nhập: không giải mã trước
    assert not is_short("https://www.instagram.com/share/reel/BAvqZ1x2y3")

    norm = UrlResolver.normalize_douyin_url
    for raw in (
        "https://www.iesdouyin.com/share/video/7300000000000000000/?region=CN&mid=1",
        "https://m.douyin.com/share/video/7300000000000000000",
    ):
        assert norm(raw) == "https://www.douyin.com/video/7300000000000000000", raw
    assert norm("https://www.douyin.com/video/7300000000000000000") == "https://www.douyin.com/video/7300000000000000000"
    assert norm("https://example.com/share/video/7300000000000000000") == (
        "https://example.com/share/video/7300000000000000000"
    )
    assert UrlResolver.detect_platform("https://www.douyin.com/video/1") == ("douyin", "Douyin")


def _offline(monkeypatch, d):
    monkeypatch.setattr(d.resolver, "resolve_url", lambda url, expected_platform=None: type(
        "R", (), {"is_shortened": False, "resolved_url": url})())
    monkeypatch.setattr("core.dispatcher.dns_blocked_host", lambda url: None)


def test_tiktok_photo_post_goes_straight_to_gallery_dl(monkeypatch):
    d = MediaDispatcher()
    _offline(monkeypatch, d)

    # Ghi lại thay vì ném lỗi: nhánh dự phòng của dispatcher nuốt mọi lỗi của yt-dlp
    ytdlp_calls = []

    def ytdlp(url, *a, **k):
        ytdlp_calls.append(url)
        raise RuntimeError("ERROR: Unsupported URL")

    monkeypatch.setattr(d.ytdlp, "extract_metadata", ytdlp)
    album = MediaMetadata(id="7400", platform="tiktok", type="album", images=[
        MediaImage(id=1, url="https://p16-sign.tiktokcdn.com/a.jpeg", type="image"),
        MediaImage(id=2, url="https://p16-sign.tiktokcdn.com/b.jpeg", type="image"),
    ])
    calls = []
    monkeypatch.setattr(d.gallery, "extract_gallery", lambda url, browser=None, timeout=30: calls.append(url) or album)
    res = d.extract("https://www.tiktok.com/@user/photo/7400000000000000000", browser="none")
    assert ytdlp_calls == [], "yt-dlp chỉ nhận /video/ — chạy trước gallery-dl chỉ tốn thời gian"
    assert calls == ["https://www.tiktok.com/@user/photo/7400000000000000000"]
    assert [img.url for img in res.images] == [img.url for img in album.images]


def test_pasted_iesdouyin_link_reaches_ytdlp_in_its_supported_form(monkeypatch):
    d = MediaDispatcher()
    _offline(monkeypatch, d)
    seen = []

    def ytdlp(url, browser=None, **k):
        seen.append(url)
        return MediaMetadata(id="7300", platform="douyin", type="video", title="clip")

    monkeypatch.setattr(d.ytdlp, "extract_metadata", ytdlp)
    d.extract("https://www.iesdouyin.com/share/video/7300000000000000000/?region=CN", browser="none")
    assert seen == ["https://www.douyin.com/video/7300000000000000000"]
