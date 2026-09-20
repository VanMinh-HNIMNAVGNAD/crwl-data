#!/usr/bin/env python3
"""
Verification script for RED 13 — Generic Web Scraper có thể dùng Video/M3U8 URL làm Image Thumbnail.

Tests:
- TEST #1:
  Kiểm tra 4 kịch bản trang web thông qua Mock HTTP Server:
  1. image only: Trang chỉ có ảnh -> thumbnail là URL ảnh, không bao giờ là .m3u8.
  2. video only:
     - Không có poster: thumbnail là None (không lấy arbitrary video URL .mp4 hay .m3u8).
     - Có poster: thumbnail là URL poster ảnh hợp lệ.
  3. image + video: Trang có cả ảnh và video -> thumbnail là URL ảnh, không phải video.
  4. video + m3u8:
     - Không poster: thumbnail là None (tuyệt đối KHÔNG bao giờ là .m3u8).
     - Có poster: thumbnail là URL poster ảnh hợp lệ.
     - Trang chứa m3u8 bẫy trong og:image / <img>: bị lọc bỏ, thumbnail không bao giờ là .m3u8.

- TEST #2:
  Kiểm thử phân loại phương tiện (Media Classification) toàn diện:
  - m3u8 -> video_manifest
  - m4s -> video_segment
  - ts -> video_segment
  - mp4 -> video
  - jpg, jpeg, png, webp, avif -> image
  - Kiểm tra query params, URL fragments, edge cases
  - Đảm bảo tính nhất quán và lặp lại 100%.
"""

import os
import sys
import socket
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Tuple

# Đảm bảo đường dẫn gốc của project nằm trong sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.extractors.web_scraper import WebScraperExtractor


class MockScraperHandler(BaseHTTPRequestHandler):
    """Giả lập máy chủ HTTP phục vụ các kịch bản trang web khác nhau."""

    def log_message(self, format, *args):
        pass  # Tắt log mặc định ra terminal

    def do_GET(self):
        path = self.path

        if path == "/image_only":
            html = """<!DOCTYPE html>
            <html>
            <head>
                <title>Trang Chỉ Có Ảnh</title>
                <meta property="og:title" content="Trang Chỉ Có Ảnh" />
                <meta property="og:image" content="https://example.com/uploads/og_banner.avif" />
            </head>
            <body>
                <h1>Nhiếp ảnh nghệ thuật</h1>
                <img src="/upload/gallery1.jpg" alt="Ảnh 1" />
                <img src="/images/gallery2.webp" alt="Ảnh 2" />
                <img src="https://example.com/photos/gallery3.png" alt="Ảnh 3" />
            </body>
            </html>"""
            self._send_html(html)

        elif path == "/video_only_no_poster":
            html = """<!DOCTYPE html>
            <html>
            <head>
                <title>Trang Chỉ Có Video Không Poster</title>
                <meta property="og:title" content="Video Tuyệt Đẹp" />
                <meta property="og:video" content="https://example.com/videos/nature.mp4" />
            </head>
            <body>
                <h1>Xem video thiên nhiên</h1>
                <video src="/media/nature_hd.mp4" controls></video>
                <source src="/media/nature_sd.mp4" type="video/mp4" />
            </body>
            </html>"""
            self._send_html(html)

        elif path == "/video_only_with_poster":
            html = """<!DOCTYPE html>
            <html>
            <head>
                <title>Trang Video Có Poster</title>
                <meta property="og:title" content="Trailer Phim Mới" />
            </head>
            <body>
                <h1>Trailer phim</h1>
                <video poster="/posters/movie_poster.jpg" src="/trailers/movie.mp4" controls>
                    <source src="/trailers/movie.mp4" type="video/mp4" />
                </video>
            </body>
            </html>"""
            self._send_html(html)

        elif path == "/image_and_video":
            html = """<!DOCTYPE html>
            <html>
            <head>
                <title>Trang Cả Ảnh Và Video</title>
                <meta property="og:title" content="Bài Đăng Hỗn Hợp" />
                <meta property="og:image" content="https://example.com/uploads/featured_image.webp" />
            </head>
            <body>
                <h1>Bài viết hướng dẫn</h1>
                <img src="/uploads/step1.png" alt="Bước 1" />
                <video src="/videos/tutorial.mp4" controls></video>
            </body>
            </html>"""
            self._send_html(html)

        elif path == "/video_and_m3u8_no_poster":
            html = """<!DOCTYPE html>
            <html>
            <head>
                <title>Trang HLS Stream Không Poster</title>
                <meta property="og:title" content="Trực Tiếp Luồng HLS" />
                <meta property="og:video" content="https://example.com/live/stream.m3u8" />
            </head>
            <body>
                <h1>Kênh truyền hình trực tiếp</h1>
                <video src="https://example.com/live/playlist.m3u8"></video>
                <source src="/hls/master.m3u8" type="application/x-mpegURL" />
            </body>
            </html>"""
            self._send_html(html)

        elif path == "/video_and_m3u8_with_poster":
            html = """<!DOCTYPE html>
            <html>
            <head>
                <title>Trang HLS Stream Kèm Poster</title>
                <meta property="og:title" content="Trực Tiếp Âm Nhạc" />
            </head>
            <body>
                <h1>Đại nhạc hội</h1>
                <video poster="/images/concert_poster.avif" src="https://example.com/live/concert.m3u8">
                    <source src="https://example.com/live/concert.m3u8" type="application/x-mpegURL" />
                </video>
            </body>
            </html>"""
            self._send_html(html)

        elif path == "/video_and_m3u8_adversarial":
            # Kịch bản trang web cố tình đưa URL m3u8 vào og:image và img src
            html = """<!DOCTYPE html>
            <html>
            <head>
                <title>Trang Bẫy M3U8 Trong Thẻ Ảnh</title>
                <meta property="og:title" content="Trang Bẫy M3U8" />
                <meta property="og:image" content="https://example.com/fake_image.m3u8" />
            </head>
            <body>
                <h1>Bẫy m3u8</h1>
                <img src="/uploads/fake_thumb.m3u8" />
                <img src="/media/segment.ts" />
                <img src="/media/chunk.m4s" />
                <video src="https://example.com/live/real_stream.m3u8"></video>
            </body>
            </html>"""
            self._send_html(html)

        else:
            self.send_response(404)
            self.end_headers()

    def _send_html(self, html: str):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_test_server() -> Tuple[HTTPServer, str]:
    """Khởi chạy HTTP server trên một cổng khả dụng ngẫu nhiên."""
    server = HTTPServer(("127.0.0.1", 0), MockScraperHandler)
    port = server.server_port
    base_url = f"http://127.0.0.1:{port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, base_url


def run_test_1(base_url: str) -> bool:
    """
    TEST #1:
    Kiểm tra 4 kịch bản trang web:
    - image only
    - video only
    - image + video
    - video + m3u8
    Đảm bảo thumbnail không bao giờ là .m3u8 hay arbitrary video URL.
    """
    print("\n" + "=" * 70)
    print("  🚀 CHẠY TEST #1 — Kiểm thử Thumbnail Selection trên các kịch bản trang web")
    print("=" * 70)

    extractor = WebScraperExtractor()

    # 1. Kịch bản: image only
    print("\n  [1/4] Kiểm tra trang 'image only'...")
    res_img = extractor.extract(f"{base_url}/image_only")
    assert res_img.thumbnail is not None, "Trang image only phải có thumbnail"
    assert not res_img.thumbnail.endswith(".m3u8"), f"Thumbnail không được là .m3u8: {res_img.thumbnail}"
    assert any(res_img.thumbnail.endswith(ext) for ext in (".avif", ".jpg", ".webp", ".png")), \
        f"Thumbnail phải là ảnh hợp lệ: {res_img.thumbnail}"
    assert res_img.type == "album", f"Loại trang phải là album: {res_img.type}"
    assert res_img.images and len(res_img.images) >= 1, "Phải trích xuất được danh sách ảnh"
    print(f"  ✅ [PASS] 'image only': thumbnail = '{res_img.thumbnail}' (hợp lệ, không phải .m3u8)")

    # 2. Kịch bản: video only
    print("\n  [2/4] Kiểm tra trang 'video only'...")
    # 2.1 Video only không có poster
    res_v_no_poster = extractor.extract(f"{base_url}/video_only_no_poster")
    assert res_v_no_poster.thumbnail is None, \
        f"Trang video không có poster/image phải có thumbnail=None, nhưng lại là: '{res_v_no_poster.thumbnail}'"
    assert res_v_no_poster.high_res_thumbnail is None, \
        f"high_res_thumbnail phải là None, nhưng lại là: '{res_v_no_poster.high_res_thumbnail}'"
    assert res_v_no_poster.type == "video", f"Loại trang phải là video: {res_v_no_poster.type}"
    assert res_v_no_poster.images is None, "Không được tự ý thêm video URL vào images"
    assert res_v_no_poster.streams and len(res_v_no_poster.streams) >= 1, "Phải tìm thấy video streams"
    print("  ✅ [PASS] 'video only (không poster)': thumbnail = None (không fallback sang video .mp4 hay .m3u8)")

    # 2.2 Video only có poster
    res_v_poster = extractor.extract(f"{base_url}/video_only_with_poster")
    assert res_v_poster.thumbnail is not None, "Trang video có poster phải trích xuất được poster làm thumbnail"
    assert res_v_poster.thumbnail.endswith("movie_poster.jpg"), \
        f"Thumbnail phải là poster: {res_v_poster.thumbnail}"
    assert not res_v_poster.thumbnail.endswith(".mp4"), "Thumbnail không được là .mp4"
    assert not res_v_poster.thumbnail.endswith(".m3u8"), "Thumbnail không được là .m3u8"
    print(f"  ✅ [PASS] 'video only (có poster)': thumbnail = '{res_v_poster.thumbnail}' (sử dụng đúng video poster)")

    # 3. Kịch bản: image + video
    print("\n  [3/4] Kiểm tra trang 'image + video'...")
    res_mix = extractor.extract(f"{base_url}/image_and_video")
    assert res_mix.thumbnail is not None, "Trang có ảnh phải có thumbnail"
    assert not res_mix.thumbnail.endswith(".mp4"), f"Thumbnail không được là video: {res_mix.thumbnail}"
    assert not res_mix.thumbnail.endswith(".m3u8"), f"Thumbnail không được là .m3u8: {res_mix.thumbnail}"
    assert res_mix.images and len(res_mix.images) >= 1, "Phải có danh sách ảnh"
    assert res_mix.streams and len(res_mix.streams) >= 1, "Phải có video stream"
    print(f"  ✅ [PASS] 'image + video': thumbnail = '{res_mix.thumbnail}' (lấy ảnh thực, không lấy video URL)")

    # 4. Kịch bản: video + m3u8
    print("\n  [4/4] Kiểm tra trang 'video + m3u8'...")
    # 4.1 M3U8 không có poster
    res_m3u8_no_poster = extractor.extract(f"{base_url}/video_and_m3u8_no_poster")
    assert res_m3u8_no_poster.thumbnail is None, \
        f"Trang M3U8 không poster phải có thumbnail=None, nhưng lại nhận: '{res_m3u8_no_poster.thumbnail}'"
    assert res_m3u8_no_poster.high_res_thumbnail is None
    assert res_m3u8_no_poster.streams and any(s.format == "HLS" for s in res_m3u8_no_poster.streams), \
        "Phải trích xuất được HLS stream"
    assert res_m3u8_no_poster.thumbnail != "https://example.com/live/stream.m3u8", "Thumbnail không được là .m3u8!"
    print("  ✅ [PASS] 'video + m3u8 (không poster)': thumbnail = None (TUYỆT ĐỐI không phải .m3u8)")

    # 4.2 M3U8 có poster
    res_m3u8_poster = extractor.extract(f"{base_url}/video_and_m3u8_with_poster")
    assert res_m3u8_poster.thumbnail is not None, "Phải có poster thumbnail"
    assert res_m3u8_poster.thumbnail.endswith("concert_poster.avif"), \
        f"Thumbnail phải là poster: {res_m3u8_poster.thumbnail}"
    assert ".m3u8" not in res_m3u8_poster.thumbnail, "Thumbnail không được chứa .m3u8"
    print(f"  ✅ [PASS] 'video + m3u8 (có poster)': thumbnail = '{res_m3u8_poster.thumbnail}' (lấy đúng poster)")

    # 4.3 M3U8 bẫy trong og:image và img tags
    res_adv = extractor.extract(f"{base_url}/video_and_m3u8_adversarial")
    assert res_adv.thumbnail is None, \
        f"Bẫy m3u8/ts/m4s trong thẻ img/og:image phải bị loại bỏ hoàn toàn, thumbnail phải là None: '{res_adv.thumbnail}'"
    if res_adv.images:
        for img in res_adv.images:
            assert not img.url.endswith(".m3u8"), f"Images không được chứa .m3u8: {img.url}"
            assert not img.url.endswith(".ts"), f"Images không được chứa .ts: {img.url}"
            assert not img.url.endswith(".m4s"), f"Images không được chứa .m4s: {img.url}"
    print("  ✅ [PASS] 'video + m3u8 (bẫy m3u8 trong thẻ img/og)': Bị lọc bỏ triệt để, thumbnail không bao giờ là .m3u8")

    print("\n  🎉 KẾT QUẢ TEST #1: TẤT CẢ 4 KỊCH BẢN ĐỀU ĐẠT CHUẨN!")
    return True


def run_test_2() -> bool:
    """
    TEST #2:
    Chạy lại scraper media classification trên tất cả các định dạng được yêu cầu:
    m3u8, m4s, ts, mp4, jpg, jpeg, png, webp, avif.
    """
    print("\n" + "=" * 70)
    print("  🚀 CHẠY TEST #2 — Kiểm thử Media Classification (Phân loại phương tiện)")
    print("=" * 70)

    cls = WebScraperExtractor

    # Bảng dữ liệu kiểm thử định dạng
    test_cases = [
        # (URL, expected_type, is_image, is_video, is_manifest, is_segment, is_thumb_candidate)
        # 1. m3u8 (Video Manifest)
        ("https://example.com/live/playlist.m3u8", "video_manifest", False, True, True, False, False),
        ("https://example.com/hls/master.m3u8?token=abc123#t=10", "video_manifest", False, True, True, False, False),
        ("http://stream.org/video?format=m3u8", "video_manifest", False, True, True, False, False),

        # 2. m4s (Video Segment)
        ("https://example.com/dash/chunk-001.m4s", "video_segment", False, False, False, True, False),
        ("https://example.com/stream/segment.m4s?range=0-1000", "video_segment", False, False, False, True, False),

        # 3. ts (Video Segment)
        ("https://example.com/hls/segment_0001.ts", "video_segment", False, False, False, True, False),
        ("https://example.com/media/part1.ts?seq=5", "video_segment", False, False, False, True, False),

        # 4. mp4 (Video)
        ("https://example.com/videos/clip.mp4", "video", False, True, False, False, False),
        ("https://example.com/download.mp4?auth=xyz", "video", False, True, False, False, False),

        # 5. jpg (Image / Thumbnail)
        ("https://example.com/images/banner.jpg", "image", True, False, False, False, True),
        ("https://example.com/photo.jpg?w=1080&h=720", "image", True, False, False, False, True),

        # 6. jpeg (Image / Thumbnail)
        ("https://example.com/img/item.jpeg", "image", True, False, False, False, True),

        # 7. png (Image / Thumbnail)
        ("https://example.com/assets/logo.png", "image", True, False, False, False, True),
        ("https://example.com/picture.png?quality=high", "image", True, False, False, False, True),

        # 8. webp (Image / Thumbnail)
        ("https://example.com/photos/modern.webp", "image", True, False, False, False, True),

        # 9. avif (Image / Thumbnail)
        ("https://example.com/gallery/nextgen.avif", "image", True, False, False, False, True),
        ("https://example.com/artwork.avif?token=99", "image", True, False, False, False, True),
    ]

    for url, exp_type, is_img, is_vid, is_man, is_seg, is_thumb in test_cases:
        actual_type = cls.classify_url(url)
        assert actual_type == exp_type, f"classify_url('{url}') = '{actual_type}', kỳ vọng: '{exp_type}'"
        assert cls.is_image_url(url) == is_img, f"is_image_url('{url}') kỳ vọng {is_img}"
        assert cls.is_video_url(url) == is_vid, f"is_video_url('{url}') kỳ vọng {is_vid}"
        assert cls.is_video_manifest_url(url) == is_man, f"is_video_manifest_url('{url}') kỳ vọng {is_man}"
        assert cls.is_video_segment_url(url) == is_seg, f"is_video_segment_url('{url}') kỳ vọng {is_seg}"
        assert cls.is_thumbnail_candidate(url) == is_thumb, f"is_thumbnail_candidate('{url}') kỳ vọng {is_thumb}"
        print(f"  ✅ [PASS] {exp_type.upper():<15}: {url}")

    # Kiểm tra tính lặp lại (repeatability) 5 lần trên clean instances
    print("\n  [Repeatability] Kiểm tra 5 lần liên tiếp với clean instances...")
    for run_idx in range(1, 6):
        inst = WebScraperExtractor()
        assert inst.classify_url("https://example.com/stream.m3u8") == "video_manifest"
        assert inst.is_thumbnail_candidate("https://example.com/stream.m3u8") is False
        assert inst.classify_url("https://example.com/photo.avif") == "image"
        assert inst.is_thumbnail_candidate("https://example.com/photo.avif") is True

    print("  ✅ [PASS] Tính lặp lại đạt 100%, phân loại media tuyệt đối ổn định và chính xác!")
    print("\n  🎉 KẾT QUẢ TEST #2: HOÀN TOÀN ĐẠT CHUẨN!")
    return True


def test_red13_web_scraper_thumbnail():
    """Entrypoint pytest tự động phát hiện."""
    server, base_url = start_test_server()
    try:
        assert run_test_1(base_url) is True
        assert run_test_2() is True
    finally:
        server.shutdown()
        server.server_close()


def main():
    server, base_url = start_test_server()
    print(f"Đã khởi động Mock Web Server tại: {base_url}")

    try:
        t1_ok = run_test_1(base_url)
        t2_ok = run_test_2()

        print("\n" + "=" * 70)
        print("TỔNG KẾT:")
        if t1_ok:
            print("TEST #1: PASS")
        else:
            print("TEST #1: FAIL")

        if t2_ok:
            print("TEST #2: PASS")
        else:
            print("TEST #2: FAIL")
        print("=" * 70)

        if not (t1_ok and t2_ok):
            sys.exit(1)

    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
