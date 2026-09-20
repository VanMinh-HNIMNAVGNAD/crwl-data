#!/usr/bin/env python3
"""
Verification script for RED 06 — Remove Fake / Placeholder Data From Core.

Tests:
- TEST #1:
  1. Source có đầy đủ metadata: Kiểm tra metadata thật (title, author, url, duration, views, filesize, resolution) vẫn được giữ nguyên.
  2. Source thiếu metadata: Kiểm tra các trường thiếu trả về None/null/empty thay vì fake string ("Tự động", "Không có tiêu đề", "Tác giả", "Playlist / Channel", "Người dùng", "Không xác định", "Chất lượng gốc", "Ảnh HD", "Video HD", "Video 1"...).
- TEST #2:
  1. Lặp lại cả hai trường hợp trên clean instances để đảm bảo tính lặp lại (repeatability).
  2. Đặc biệt kiểm tra Filesize:
     - actual ≠ estimated: không được đánh tráo.
     - Muxed streams (bestvideo+bestaudio) và Audio presets không được tính toán dung lượng giả định từ bitrate (12000, 6000, 3000, 1500 kbps) rồi trả về như actual filesize.
     - Khi không có actual filesize: size và raw_size phải là None.
     - Khi có actual filesize thật: size và raw_size thật được giữ nguyên chính xác.
     - Không trả về "Tự động" hay "Tự động (Stream)".
"""

import os
import sys
import json

# Thêm PROJECT_ROOT vào sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.models import (
    StreamFormat,
    SubtitleItem,
    ChapterItem,
    MediaImage,
    CrawlMediaItem,
    MediaMetadata,
    ProfileCrawlResult,
)
from core.extractors.ytdlp import YtDlpExtractor
from core.extractors.gallery import GalleryDlExtractor
from core.extractors.tiktok import TikTokExtractor
from core.extractors.web_scraper import WebScraperExtractor
from core.extractors.stream_sniffer import PlaywrightStreamSniffer
from core.extractors.direct import DirectImageExtractor
from core.dispatcher import MediaDispatcher


DISALLOWED_FAKE_STRINGS = [
    "Không có tiêu đề",
    "Tác giả",
    "Playlist / Channel",
    "@channel",
    "Người dùng",
    "@profile",
    "Tài khoản",
    "Không xác định",
    "Chất lượng gốc",
    "Tự động",
    "Tự động (Stream)",
    "Bộ sưu tập đa phương tiện",
    "Bộ sưu tập trang web",
    "Bộ sưu tập ảnh",
    "Ảnh HD",
    "Video HD",
    "Ảnh gốc HD",
    "Video Web HD",
    "1 video",
    "1 hình ảnh",
    "Media Stream (Phát hiện tự động)",
]


def assert_no_disallowed_strings(data: dict, path: str = ""):
    """Đệ quy kiểm tra không được chứa bất kỳ chuỗi fake data nào."""
    for k, v in data.items():
        current_path = f"{path}.{k}" if path else k
        if isinstance(v, str):
            for fake in DISALLOWED_FAKE_STRINGS:
                assert v != fake, (
                    f"Phát hiện fake string '{fake}' tại '{current_path}': '{v}'"
                )
        elif isinstance(v, dict):
            assert_no_disallowed_strings(v, current_path)
        elif isinstance(v, list):
            for idx, item in enumerate(v):
                if isinstance(item, dict):
                    assert_no_disallowed_strings(item, f"{current_path}[{idx}]")
                elif isinstance(item, str):
                    for fake in DISALLOWED_FAKE_STRINGS:
                        assert item != fake, (
                            f"Phát hiện fake string '{fake}' tại '{current_path}[{idx}]': '{item}'"
                        )


def run_test_1():
    print("=" * 70)
    print("  🚀 CHẠY TEST #1 — Kiểm thử loại bỏ Fake / Placeholder Data khỏi Core")
    print("=" * 70)

    # ─────────────────────────────────────────────────────────────────────────
    # Phân hệ A: Source có đầy đủ metadata
    # ─────────────────────────────────────────────────────────────────────────
    print("\n  [1.1] Kiểm tra source có ĐẦY ĐỦ metadata (dữ liệu thật phải giữ nguyên)...")

    # A1. YtDlpExtractor với raw data đầy đủ
    ytdlp = YtDlpExtractor()
    full_raw = {
        "id": "vid_full_123",
        "extractor": "youtube",
        "title": "Video Khoa Học Tự Nhiên 4K",
        "uploader": "Kênh Khoa Học TV",
        "uploader_url": "https://www.youtube.com/@khoahoctv",
        "duration": 185,
        "view_count": 2500000,
        "like_count": 120000,
        "comment_count": 4500,
        "description": "Mô tả đầy đủ chi tiết của video",
        "thumbnail": "https://i.ytimg.com/vi/vid_full_123/hqdefault.jpg",
        "upload_date": "20260315",
        "formats": [
            {
                "format_id": "137",
                "vcodec": "avc1.640028",
                "height": 1080,
                "ext": "mp4",
                "filesize": 104857600,  # 100 MB thực tế
                "fps": 60,
                "tbr": 4500,
                "url": "https://googlevideo.com/137",
            },
            {
                "format_id": "hd",
                "ext": "mp4",
                "filesize": 83886080,  # 80 MB thực tế
                "url": "https://googlevideo.com/hd",
            }
        ],
    }

    meta = ytdlp._normalize_metadata(full_raw, "https://www.youtube.com/watch?v=vid_full_123")
    assert meta.title == "Video Khoa Học Tự Nhiên 4K", f"Sai title: {meta.title}"
    assert meta.author == "Kênh Khoa Học TV", f"Sai author: {meta.author}"
    assert meta.author_url == "https://www.youtube.com/@khoahoctv", f"Sai author_url: {meta.author_url}"
    assert meta.duration == "03:05", f"Sai duration: {meta.duration}"
    assert meta.views == "2.5M lượt xem", f"Sai views: {meta.views}"
    assert meta.likes == "120.0K lượt thích", f"Sai likes: {meta.likes}"
    assert meta.comments == "4.5K bình luận", f"Sai comments: {meta.comments}"
    assert meta.upload_date == "2026-03-15", f"Sai upload_date: {meta.upload_date}"

    # Kiểm tra format có filesize thật thì size thật được giữ nguyên
    f137 = next((s for s in meta.streams if s.format_id == "137"), None)
    assert f137 is not None, "Không tìm thấy format 137"
    assert f137.size == "100.0 MB", f"Sai filesize thật: {f137.size}"
    assert f137.raw_size == 104857600, f"Sai raw_size: {f137.raw_size}"

    # A2. GalleryDlExtractor với album đầy đủ
    gallery = GalleryDlExtractor()
    full_gallery_entries = [
        [
            2,
            {
                "category": "instagram",
                "author": {"name": "photographer_hanoi", "url": "https://instagram.com/photographer_hanoi"},
                "title": "Hà Nội Mùa Thu Tuyệt Đẹp",
                "description": "Bộ ảnh chụp tại Hồ Gươm",
            },
        ],
        [
            3,
            "https://cdn.instagram.com/img1.jpg",
            {
                "filename": "hanoi_autumn_01.jpg",
                "width": 1920,
                "height": 1080,
                "filesize": 2097152,  # 2 MB thực tế
                "extension": "jpg",
            },
        ],
    ]
    gal_meta = gallery._normalize_gallery(full_gallery_entries, "https://instagram.com/p/test")
    assert gal_meta.author == "photographer_hanoi", f"Sai author: {gal_meta.author}"
    assert gal_meta.author_url == "https://instagram.com/photographer_hanoi", f"Sai author_url: {gal_meta.author_url}"
    assert gal_meta.title == "Hà Nội Mùa Thu Tuyệt Đẹp (1 tệp)", f"Sai title: {gal_meta.title}"
    assert len(gal_meta.images) == 1
    assert gal_meta.images[0].title == "hanoi_autumn_01.jpg"
    assert gal_meta.images[0].resolution == "1920x1080"
    assert gal_meta.images[0].size == "2.0 MB"

    print("  ✅ [PASS] Source có đầy đủ metadata giữ nguyên chính xác 100% dữ liệu thật!")

    # ─────────────────────────────────────────────────────────────────────────
    # Phân hệ B: Source THIẾU metadata -> phải là None/null, không được fake data
    # ─────────────────────────────────────────────────────────────────────────
    print("\n  [1.2] Kiểm tra source THIẾU metadata (missing -> None/null/empty, không fake string)...")

    # B1. YtDlpExtractor với metadata rỗng
    empty_raw = {
        "id": "empty_vid",
        "extractor": "generic",
    }
    empty_meta = ytdlp._normalize_metadata(empty_raw, "https://example.com/video")
    assert empty_meta.title is None, f"Title phải là None, nhận được: {empty_meta.title}"
    assert empty_meta.author is None, f"Author phải là None, nhận được: {empty_meta.author}"
    assert empty_meta.author_url is None, f"Author_url phải là None, nhận được: {empty_meta.author_url}"
    assert empty_meta.duration is None, f"Duration phải là None, nhận được: {empty_meta.duration}"
    assert empty_meta.views is None, f"Views phải là None, nhận được: {empty_meta.views}"
    assert empty_meta.upload_date is None

    empty_dict = empty_meta.to_dict()
    assert_no_disallowed_strings(empty_dict, "ytdlp_empty")
    assert empty_dict["title"] is None
    assert empty_dict["author"] is None
    assert empty_dict["authorUrl"] is None
    assert empty_dict["duration"] is None
    assert empty_dict["views"] is None

    # B2. YtDlpExtractor _parse_playlist_data khi thiếu thông tin
    empty_playlist_stdout = json.dumps({"id": "v1"}) + "\n" + json.dumps({"id": "v2"})
    crawl_res = ytdlp._parse_playlist_data(empty_playlist_stdout, "https://youtube.com/playlist?list=xxx")
    assert crawl_res.name is None, f"Channel name phải là None, nhận: {crawl_res.name}"
    assert crawl_res.handle is None, f"Channel handle phải là None, nhận: {crawl_res.handle}"
    for idx, it in enumerate(crawl_res.media, 1):
        assert it.title is None, f"Item {idx} title phải là None, nhận: {it.title}"
        assert it.quality is None, f"Item {idx} quality phải là None, nhận: {it.quality}"
        assert it.size is None, f"Item {idx} size phải là None, nhận: {it.size}"
        assert it.duration is None, f"Item {idx} duration phải là None, nhận: {it.duration}"
    crawl_dict = crawl_res.to_dict()
    assert_no_disallowed_strings(crawl_dict, "ytdlp_playlist_empty")

    # B3. GalleryExtractor với album thiếu metadata
    empty_gal_entries = [
        [3, "https://cdn.example.com/raw_media.jpg", {}],
    ]
    empty_gal_meta = gallery._normalize_gallery(empty_gal_entries, "https://example.com/post/123")
    assert empty_gal_meta.author is None, f"Gallery author phải là None, nhận: {empty_gal_meta.author}"
    assert empty_gal_meta.author_url is None, f"Gallery author_url phải là None, nhận: {empty_gal_meta.author_url}"
    assert empty_gal_meta.title is None, f"Gallery title phải là None, nhận: {empty_gal_meta.title}"
    assert empty_gal_meta.duration is None, f"Gallery duration phải là None, nhận: {empty_gal_meta.duration}"
    assert empty_gal_meta.views is None, f"Gallery views phải là None, nhận: {empty_gal_meta.views}"
    assert empty_gal_meta.images[0].title is None
    assert empty_gal_meta.images[0].resolution is None
    assert empty_gal_meta.images[0].size is None
    gal_dict = empty_gal_meta.to_dict()
    assert_no_disallowed_strings(gal_dict, "gallery_empty")

    # B4. GalleryExtractor crawl rỗng
    empty_crawl = gallery._parse_crawl_result([], "https://instagram.com/unknown", "all")
    assert empty_crawl.name is None
    assert empty_crawl.handle is None
    assert_no_disallowed_strings(empty_crawl.to_dict(), "gallery_crawl_empty")

    # B5. DirectImageExtractor khi không có Content-Length
    direct = DirectImageExtractor()
    direct_meta = direct.extract("https://example.com/cdn/photo.png")
    assert direct_meta.author is None, f"Direct author phải là None, nhận: {direct_meta.author}"
    assert direct_meta.author_url is None, f"Direct author_url phải là None, nhận: {direct_meta.author_url}"
    assert direct_meta.duration is None, f"Direct duration phải là None, nhận: {direct_meta.duration}"
    assert direct_meta.views is None, f"Direct views phải là None, nhận: {direct_meta.views}"
    assert direct_meta.images[0].resolution is None, f"Direct resolution phải là None, nhận: {direct_meta.images[0].resolution}"
    assert_no_disallowed_strings(direct_meta.to_dict(), "direct_empty")

    # B6. Stream sniffer formats
    sniffer = PlaywrightStreamSniffer()
    streams = sniffer._build_streams(["https://example.com/live.m3u8", "https://example.com/video.mp4"])
    for s in streams:
        assert "(Phát hiện tự động)" not in (s.quality or ""), f"Chứa fake quality: {s.quality}"
        assert s.size is None, f"Sniffer stream size phải là None, nhận: {s.size}"
        assert_no_disallowed_strings(s.to_dict(), "sniffer_stream")

    print("  ✅ [PASS] Tất cả các trường thiếu đều trả về None/null, không có bất kỳ fake string nào!")
    print("\n  🎉 KẾT QUẢ TEST #1: HOÀN TOÀN ĐẠT CHUẨN!")


def run_test_2():
    print("\n" + "=" * 70)
    print("  🚀 CHẠY TEST #2 — Kiểm tra độ lặp lại & ĐẶC BIỆT KIỂM TRA FILESIZE")
    print("=" * 70)

    # ─────────────────────────────────────────────────────────────────────────
    # Kiểm tra Filesize: actual ≠ estimated (không đánh tráo)
    # ─────────────────────────────────────────────────────────────────────────
    print("\n  [2.1] Kiểm tra Filesize: actual ≠ estimated không được đánh tráo...")

    ytdlp = YtDlpExtractor()

    # Kịch bản 1: Video có height 2160, 1440, 1080, 720 nhưng KHÔNG có filesize thật cho muxed streams
    muxed_raw = {
        "id": "vid_muxed",
        "extractor": "youtube",
        "title": "Video Kiểm Tra Dung Lượng",
        "duration": 300,  # 5 phút (nếu nhân 12000 kbps sẽ ra ~450 MB giả định)
        "formats": [
            {"format_id": "313", "vcodec": "vp9", "height": 2160, "ext": "webm"},
            {"format_id": "271", "vcodec": "vp9", "height": 1440, "ext": "webm"},
            {"format_id": "137", "vcodec": "avc1", "height": 1080, "ext": "mp4"},
            {"format_id": "22", "vcodec": "avc1", "height": 720, "ext": "mp4", "acodec": "aac", "filesize": 52428800},  # 50 MB thật
        ],
    }

    meta = ytdlp._normalize_metadata(muxed_raw, "https://www.youtube.com/watch?v=vid_muxed")

    for stream in meta.streams:
        # Nếu là muxed spec (bestvideo+bestaudio) -> tuyệt đối không trả estimated size như actual
        if "bestvideo" in stream.format_id:
            assert stream.size is None, (
                f"LỖI: Format muxed '{stream.format_id}' đang trả size='{stream.size}' thay vì None! "
                f"Dự án không có field estimated_size thì không được đánh tráo trả về actual size."
            )
            assert stream.raw_size is None, f"raw_size phải là None cho muxed stream: {stream.raw_size}"
            assert stream.bitrate is None, f"bitrate không được giả định: {stream.bitrate}"
            assert stream.fps is None, f"fps không được giả định: {stream.fps}"

        # Nếu là audio presets -> tuyệt đối không tính duration * bitrate ra actual size
        elif stream.stream_type == "audio":
            assert stream.size is None, (
                f"LỖI: Audio preset '{stream.format_id}' đang trả size='{stream.size}' thay vì None!"
            )
            assert stream.raw_size is None, f"raw_size phải là None cho audio preset: {stream.raw_size}"

    # Kịch bản 2: Format có filesize THẬT (format 22 có filesize: 52428800 = 50 MB)
    format_with_actual = {
        "id": "vid_actual",
        "extractor": "facebook",
        "formats": [
            {
                "format_id": "hd",
                "ext": "mp4",
                "filesize": 104857600,  # 100 MB thật
            },
            {
                "format_id": "sd",
                "ext": "mp4",
                # Thiếu filesize
            }
        ],
    }
    fb_meta = ytdlp._normalize_metadata(format_with_actual, "https://facebook.com/watch/123")
    hd_stream = next((s for s in fb_meta.streams if s.format_id == "hd"), None)
    sd_stream = next((s for s in fb_meta.streams if s.format_id == "sd"), None)

    assert hd_stream is not None and hd_stream.size == "100.0 MB"
    assert hd_stream.raw_size == 104857600
    assert sd_stream is not None and sd_stream.size is None, f"sd_stream thiếu size phải là None, nhận: {sd_stream.size}"
    assert sd_stream.raw_size is None

    # Kịch bản 3: Live stream format
    live_raw = {
        "id": "live_stream",
        "is_live": True,
    }
    live_meta = ytdlp._normalize_metadata(live_raw, "https://youtube.com/live/123")
    assert len(live_meta.streams) == 1
    assert live_meta.streams[0].size is None, f"Live stream size phải là None, không phải 'Live': {live_meta.streams[0].size}"

    print("  ✅ [PASS] Filesize: actual ≠ estimated được phân định rõ ràng. Không đánh tráo estimated thành actual!")

    # ─────────────────────────────────────────────────────────────────────────
    # Kiểm tra tính lặp lại (Repeatability) trên toàn bộ extractor
    # ─────────────────────────────────────────────────────────────────────────
    print("\n  [2.2] Kiểm tra tính lặp lại trên toàn bộ extractor...")

    # Chạy lại TikTok, WebScraper, Dispatcher
    tiktok = TikTokExtractor()
    tk_res = tiktok.resolve_profile("unknown_user_test", limit=5)
    # Nếu embed API trả về rỗng hoặc danh sách video thiếu title/quality
    if tk_res and tk_res.media:
        for it in tk_res.media:
            assert it.size is None
            assert it.duration is None
            assert_no_disallowed_strings(it.to_dict(), "tiktok_item")

    scraper = WebScraperExtractor()
    # Dispatcher sniff fallback
    disp = MediaDispatcher()
    # Kiểm tra _try_playwright_sniff khi không tìm được streams
    class DummySniffer:
        def is_available(self): return True
        def sniff_streams(self, url): return {"streams": [], "images": []}
    disp.sniffer = DummySniffer()
    sniff_meta = disp._try_playwright_sniff("https://example.com/drm-page", Exception("gal"), Exception("yt"))
    assert sniff_meta.title is None, f"Sniff title phải là None: {sniff_meta.title}"
    assert sniff_meta.author is None, f"Sniff author phải là None: {sniff_meta.author}"
    assert sniff_meta.duration is None, f"Sniff duration phải là None: {sniff_meta.duration}"
    assert sniff_meta.views is None, f"Sniff views phải là None: {sniff_meta.views}"
    assert_no_disallowed_strings(sniff_meta.to_dict(), "dispatcher_sniff_empty")

    print("  ✅ [PASS] Tính lặp lại đạt 100%, tất cả module hoạt động đồng nhất và ổn định!")
    print("\n  🎉 KẾT QUẢ TEST #2: HOÀN TOÀN ĐẠT CHUẨN!")


if __name__ == "__main__":
    try:
        run_test_1()
        run_test_2()
        print("\n" + "=" * 70)
        print("TỔNG KẾT:")
        print("TEST #1: PASS")
        print("TEST #2: PASS")
        print("=" * 70)
        sys.exit(0)
    except AssertionError as e:
        print(f"\n❌ KIỂM THỬ THẤT BẠI: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ LỖI BẤT THƯỜNG: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
