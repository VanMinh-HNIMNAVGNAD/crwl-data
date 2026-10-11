#!/usr/bin/env python3
"""
Kiểm thử các lỗi rò rỉ tài nguyên / chức năng "ma" của engine bóc tách (bản 3.3.1).

  1. Kết quả "Quét tài khoản" giữ nguyên tiền tố `ytdl:` mà gallery-dl gắn cho video
     Reddit / Pinterest / Facebook... → bấm tải là yt-dlp báo
     "Unsupported url scheme: ytdl", không video nào tải được.
  2. Bộ dò luồng (Playwright) tạo một tệp cookie tạm chứa phiên đăng nhập cho MỖI
     trang/iframe nó mở và không bao giờ xoá.
  3. Chromium headless sống tới khi thoát app chỉ sau một lần dò (vài trăm MB RAM).
  4. Mỗi mảnh .ts/.m4s của luồng HLS/DASH hiện thành một "định dạng" riêng — tải về
     chỉ được vài giây video.

Chạy offline. Riêng test Chromium thật tự bỏ qua nếu máy chưa cài Playwright.
"""

import functools
import http.server
import os
import sys
import threading
import time

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.extractors import stream_sniffer
from core.extractors.gallery import GalleryDlExtractor
from core.extractors.stream_sniffer import PlaywrightStreamSniffer, _PlaywrightWorkerThread


# ─────────────────────────────────────────────────────────────────────────────
# 1. Tiền tố ytdl: trong kết quả quét tài khoản
# ─────────────────────────────────────────────────────────────────────────────

def _crawl(entries, media_type="all"):
    ext = GalleryDlExtractor()
    groups = ext._group_by_post(entries)
    return ext._parse_crawl_result(groups, entries, "https://www.reddit.com/user/someone/", media_type)


def test_crawled_reddit_and_pinterest_videos_have_downloadable_urls():
    entries = [
        [2, {"category": "reddit", "author": "someone"}],
        # Đúng như reddit.py của gallery-dl: "ytdl:" + submission["url"], extension = mp4
        [3, "ytdl:https://v.redd.it/abc123xyz", {
            "category": "reddit", "id": "1abcde", "extension": "mp4", "num": 0,
            "title": "Video Reddit", "thumbnail": "https://b.thumbs.redditmedia.com/t.jpg",
        }],
        # pinterest.py: luồng m3u8 → "ytdl:" + url, extension đổi thành mp4
        [3, "ytdl:https://v1.pinimg.com/videos/mc/hls/aa/bb/cc/video.m3u8", {
            "category": "pinterest", "id": "998877", "extension": "mp4", "num": 1,
        }],
        [3, "https://i.redd.it/photo.jpg", {"category": "reddit", "id": "2fghij", "extension": "jpg"}],
    ]
    result = _crawl(entries)
    urls = [m.url for m in result.media]
    assert urls == [
        "https://v.redd.it/abc123xyz",
        "https://v1.pinimg.com/videos/mc/hls/aa/bb/cc/video.m3u8",
        "https://i.redd.it/photo.jpg",
    ]
    assert [m.type for m in result.media] == ["video", "video", "image"]
    assert not any(u.startswith("ytdl:") for u in urls)
    # Ảnh bìa của video không bao giờ là chính URL video
    assert result.media[0].thumb == "https://b.thumbs.redditmedia.com/t.jpg"
    assert result.media[1].thumb is None

    # Lọc "Chỉ video" vẫn nhận ra video sau khi bỏ tiền tố
    only_videos = _crawl(entries, media_type="video")
    assert [m.url for m in only_videos.media] == urls[:2]


def test_single_post_with_null_reddit_video_does_not_crash():
    entries = [
        [2, {"category": "reddit", "title": "Bài có video"}],
        [3, "ytdl:https://v.redd.it/zzz999", {
            "category": "reddit", "extension": "mp4", "media": {"reddit_video": None},
        }],
    ]
    meta = GalleryDlExtractor()._normalize_gallery(entries, "https://www.reddit.com/r/x/comments/1/")
    assert meta.images[0].url == "https://v.redd.it/zzz999"
    assert meta.images[0].type == "video"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Tệp cookie tạm của bộ dò luồng
# ─────────────────────────────────────────────────────────────────────────────

class _FakePage:
    frames = []

    def on(self, *_args):
        pass

    def goto(self, *_args, **_kwargs):
        pass

    def wait_for_timeout(self, *_args):
        pass

    def content(self):
        return "<html></html>"


class _FakeContext:
    def __init__(self):
        self.cookies = []
        self.closed = False

    def add_cookies(self, cookies):
        self.cookies.extend(cookies)

    def new_page(self):
        return _FakePage()

    def close(self):
        self.closed = True


class _FakeBrowser:
    def __init__(self):
        self.contexts = []

    def new_context(self, **_kwargs):
        ctx = _FakeContext()
        self.contexts.append(ctx)
        return ctx


def test_sniffer_deletes_the_temporary_cookie_copy(tmp_path, monkeypatch):
    created = []

    def fake_cookies_txt(_browser, domain=None):
        path = tmp_path / f"cookies_{len(created)}.txt"
        path.write_text(
            "# Netscape HTTP Cookie File\n"
            f".{domain}\tTRUE\t/\tTRUE\t2147483647\tsessionid\tbi-mat\n",
            encoding="utf-8",
        )
        created.append(path)
        return str(path)

    monkeypatch.setattr(stream_sniffer, "get_browser_cookies_txt", fake_cookies_txt)
    browser = _FakeBrowser()
    sniffer = PlaywrightStreamSniffer()
    for page in ("https://example.com/watch/1", "https://player.example.com/embed/1"):
        sniffer._sniff_page(browser, page, wait_ms=0)

    assert len(created) == 2
    leftovers = [p for p in created if p.exists()]
    assert leftovers == [], f"Tệp cookie tạm còn sót lại: {leftovers}"
    # Cookie vẫn được nạp vào phiên trình duyệt trước khi tệp bị xoá
    assert browser.contexts[0].cookies and browser.contexts[0].cookies[0]["name"] == "sessionid"
    assert all(ctx.closed for ctx in browser.contexts)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Chromium headless phải tự đóng khi rảnh
# ─────────────────────────────────────────────────────────────────────────────

class _Closable:
    def __init__(self):
        self.closed = threading.Event()

    def is_connected(self):
        return not self.closed.is_set()

    def close(self):
        self.closed.set()

    def stop(self):
        self.closed.set()


def test_idle_sniffer_worker_releases_chromium(monkeypatch):
    monkeypatch.setattr(_PlaywrightWorkerThread, "IDLE_SHUTDOWN_SEC", 0.3)
    worker = _PlaywrightWorkerThread()
    browser, driver = _Closable(), _Closable()

    def pretend_launch(should_stop):
        worker._browser, worker._pw = browser, driver
        return "ok"

    try:
        assert worker.submit(pretend_launch, timeout=5) == "ok"
        assert browser.closed.wait(3), "Chromium không được đóng sau khi rảnh"
        assert driver.closed.wait(3), "Driver Playwright không được dừng sau khi rảnh"
        assert worker._browser is None and worker._pw is None
        # Không có gì để đóng thì luồng phải nằm chờ, không quay vòng tốn CPU
        assert worker._idle_timeout() is None
        # Luồng vẫn sống và nhận việc tiếp
        assert worker.is_alive()
        assert worker.submit(lambda should_stop: 42, timeout=5) == 42
    finally:
        worker.close()


def test_idle_shutdown_can_be_disabled(monkeypatch):
    monkeypatch.setattr(_PlaywrightWorkerThread, "IDLE_SHUTDOWN_SEC", 0)
    worker = _PlaywrightWorkerThread()
    try:
        worker._browser = _Closable()
        assert worker._idle_timeout() is None
    finally:
        worker._browser = None
        worker.close()


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


@pytest.mark.skipif(not _playwright_ready(), reason="cần Playwright + Chromium")
def test_real_chromium_is_closed_when_idle_and_relaunched_on_demand(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text(
        '<html><body><video src="/clip.mp4"></video></body></html>', encoding="utf-8"
    )
    (tmp_path / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 64)
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    handler.log_message = lambda *a, **k: None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    page_url = f"http://127.0.0.1:{server.server_address[1]}/index.html"

    monkeypatch.setattr(_PlaywrightWorkerThread, "IDLE_SHUTDOWN_SEC", 1.0)
    monkeypatch.setattr(stream_sniffer, "get_browser_cookies_txt", lambda *a, **k: None)
    sniffer = PlaywrightStreamSniffer()
    sniffer.close()  # bắt đầu từ một luồng worker mới với mốc rảnh 1 giây
    try:
        first = sniffer.sniff_streams(page_url, wait_ms=500, follow_iframes=False)
        assert first["error"] is None and first["raw_urls"], first
        worker = sniffer._worker
        deadline = time.monotonic() + 15
        while worker._browser is not None and time.monotonic() < deadline:
            time.sleep(0.2)
        assert worker._browser is None, "Chromium vẫn chạy sau khi rảnh"

        second = sniffer.sniff_streams(page_url, wait_ms=500, follow_iframes=False)
        assert second["error"] is None and second["raw_urls"], second
    finally:
        sniffer.close()
        server.shutdown()


# ─────────────────────────────────────────────────────────────────────────────
# 4. Mảnh HLS/DASH không phải là một "định dạng" tải được
# ─────────────────────────────────────────────────────────────────────────────

def test_segments_are_not_offered_when_a_manifest_or_file_exists():
    sniffer = PlaywrightStreamSniffer()
    streams = sniffer._build_streams([
        "https://cdn.example.com/hls/seg_0001.ts",
        "https://cdn.example.com/hls/master.m3u8",
        "https://cdn.example.com/hls/seg_0002.ts",
        "https://cdn.example.com/hls/seg_0003.ts",
    ])
    assert [s.format for s in streams] == ["HLS"]

    streams = sniffer._build_streams([
        "https://cdn.example.com/v/chunk-1.m4s",
        "https://cdn.example.com/v/full.mp4",
        "https://cdn.example.com/v/chunk-2.m4s",
    ])
    assert [s.format for s in streams] == ["MP4"]


def test_only_one_segment_is_kept_when_nothing_better_was_found():
    sniffer = PlaywrightStreamSniffer()
    streams = sniffer._build_streams([f"https://cdn.example.com/live/seg_{i}.ts" for i in range(30)])
    assert len(streams) == 1 and streams[0].format == "TS"
