#!/usr/bin/env python3
"""
Kiểm thử thay đổi nền tảng giữa 11/2026:

  X tách trang Media thành hai timeline riêng (UserPhotoTimeline / UserVideoTimeline,
  ứng với /media?filter=photo|video). gallery-dl 1.32.20 đọc "/media" thành toàn bộ
  ảnh rồi mới tới video, nên giới hạn số bài của app chỉ còn thấy ảnh: chọn "Video"
  ra rỗng, "Tất cả" mất hết video. App phải gọi đúng timeline và trộn theo thời gian.

Chạy offline:  python3 -m pytest -q scripts/test_platform_changes_2026_11.py
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.dispatcher import MediaDispatcher
from core.extractors.gallery import GalleryDlExtractor


def _file(tweet_id, url, ext):
    return [3, url, {"category": "twitter", "tweet_id": tweet_id, "extension": ext,
                     "author": {"name": "natgeo"}, "num": 1, "count": 1}]


PHOTOS = [_file(900, "https://pbs.twimg.com/media/p900.jpg", "jpg"),
          _file(700, "https://pbs.twimg.com/media/p700.jpg", "jpg"),
          # tweet có cả ảnh lẫn video: xuất hiện ở CẢ HAI timeline
          _file(500, "https://pbs.twimg.com/media/p500.jpg", "jpg"),
          _file(500, "https://video.twimg.com/v500.mp4", "mp4")]
VIDEOS = [_file(950, "https://video.twimg.com/v950.mp4", "mp4"),
          _file(800, "https://video.twimg.com/v800.mp4", "mp4"),
          _file(500, "https://pbs.twimg.com/media/p500.jpg", "jpg"),
          _file(500, "https://video.twimg.com/v500.mp4", "mp4")]


def _fake_extractor(monkeypatch, calls):
    ext = GalleryDlExtractor()
    monkeypatch.setattr(ext, "is_available", lambda: True)
    monkeypatch.setattr(ext, "_expand_incomplete_posts", lambda groups, browser: groups)

    def crawl(url, browser, last_post, timeout, _depth=0):
        calls.append(url)
        if url.endswith("filter=photo"):
            return list(PHOTOS), 0, "", False
        if url.endswith("filter=video"):
            return list(VIDEOS), 0, "", False
        return list(PHOTOS) + list(VIDEOS), 0, "", False  # /media: ảnh trước, video sau

    monkeypatch.setattr(ext, "_crawl_entries", crawl)
    return ext


def test_x_media_timelines_by_media_type():
    base = "https://x.com/natgeo/media"
    assert GalleryDlExtractor._x_media_timelines(base, "video") == [base + "?filter=video"]
    assert GalleryDlExtractor._x_media_timelines(base + "/", "image") == [base + "?filter=photo"]
    assert GalleryDlExtractor._x_media_timelines(base, "all") == [base + "?filter=photo", base + "?filter=video"]
    # Người dùng đã chọn sẵn bộ lọc, hoặc không phải trang Media → không đụng tới
    assert GalleryDlExtractor._x_media_timelines(base + "?filter=video", "all") is None
    assert GalleryDlExtractor._x_media_timelines("https://x.com/natgeo/likes", "all") is None
    assert GalleryDlExtractor._x_media_timelines("https://x.com/natgeo/status/1", "all") is None
    assert GalleryDlExtractor._x_media_timelines("https://www.instagram.com/natgeo/media", "all") is None


def test_x_all_merges_both_timelines_newest_first(monkeypatch):
    calls = []
    ext = _fake_extractor(monkeypatch, calls)
    res = ext.crawl_profile("https://x.com/natgeo/media", limit=4, media_type="all", browser="none")

    assert sorted(calls) == ["https://x.com/natgeo/media?filter=photo", "https://x.com/natgeo/media?filter=video"]
    urls = [m.url for m in res.media]
    # mới nhất trước, đủ cả video lẫn ảnh, tweet 500 không bị lặp tệp
    assert urls == [
        "https://video.twimg.com/v950.mp4",
        "https://pbs.twimg.com/media/p900.jpg",
        "https://video.twimg.com/v800.mp4",
        "https://pbs.twimg.com/media/p700.jpg",
    ]
    res = ext.crawl_profile("https://x.com/natgeo/media", limit=10, media_type="all", browser="none")
    assert len(res.media) == len({m.url for m in res.media}) == 6


def test_x_video_only_uses_video_timeline(monkeypatch):
    calls = []
    ext = _fake_extractor(monkeypatch, calls)
    res = ext.crawl_profile("https://x.com/natgeo/media", limit=2, media_type="video", browser="none")
    assert calls == ["https://x.com/natgeo/media?filter=video"]
    assert [m.url for m in res.media] == ["https://video.twimg.com/v950.mp4", "https://video.twimg.com/v800.mp4"]


def test_x_old_gallery_dl_ignores_filter_and_still_dedupes(monkeypatch):
    """gallery-dl cũ bỏ qua ?filter= và trả timeline gộp ở cả hai lượt."""
    ext = GalleryDlExtractor()
    monkeypatch.setattr(ext, "is_available", lambda: True)
    monkeypatch.setattr(ext, "_expand_incomplete_posts", lambda groups, browser: groups)
    mixed = [_file(950, "https://video.twimg.com/v950.mp4", "mp4"),
             _file(900, "https://pbs.twimg.com/media/p900.jpg", "jpg")]
    monkeypatch.setattr(ext, "_crawl_entries", lambda *a, **k: (list(mixed), 0, "", False))
    res = ext.crawl_profile("https://x.com/natgeo/media", limit=5, media_type="all", browser="none")
    assert [m.url for m in res.media] == [mixed[0][1], mixed[1][1]]


def test_normalize_x_url_keeps_media_filter():
    n = MediaDispatcher._normalize_x_url
    assert n("https://x.com/natgeo/media?filter=videos") == "https://x.com/natgeo/media?filter=video"
    assert n("https://x.com/natgeo/media?filter=photo&s=20") == "https://x.com/natgeo/media?filter=photo"
    assert n("https://x.com/natgeo/media?s=20") == "https://x.com/natgeo/media"
    assert n("https://x.com/natgeo?s=20") == "https://x.com/natgeo/media"
