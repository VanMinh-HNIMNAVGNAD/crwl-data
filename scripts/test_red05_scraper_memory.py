#!/usr/bin/env python3
"""
Verification script for RED 05 — Generic Web Scraper đọc toàn bộ HTTP Response vào RAM.
Tests:
- TEST #1:
  1. Small response (5 KB): HTML page with title, og tags, images, videos.
  2. Normal response (250 KB): Realistic blog post HTML with multiple images and streams.
  3. Large response (5 MB): Valid large page under 50 MB limit, read completely in chunks.
  4. Response vượt giới hạn (Content-Length): 60 MB > 50 MB limit -> Aborts immediately on header check.
  5. Response vượt giới hạn (Chunked Streaming): Stream exceeding 50 MB without Content-Length -> Aborts safely mid-stream.
  6. Custom limit test: Demonstrates configurable limit (e.g., 100 KB limit against 250 KB response).
  Checks:
  - small response works
  - scraper has no regression
  - oversized response safely aborts with controlled error (no crash, no OOM)
- TEST #2:
  - Repeat full scraper test suite in clean state.
  - Re-test oversized responses (both Content-Length and chunked stream) to ensure 100% repeatability.
"""

import os
import sys
import time
import socket
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Optional, Tuple

# Ensure project root in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.extractors.web_scraper import WebScraperExtractor, OversizedResponseError


class MockWebScraperServer(BaseHTTPRequestHandler):
    """Giả lập máy chủ HTTP phục vụ các kịch bản kích thước phản hồi khác nhau."""

    # Tắt log mặc định ra stderr để không làm rối terminal
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        path = self.path

        if path == "/small":
            # 1. Phản hồi nhỏ (~5 KB)
            html = """<!DOCTYPE html>
            <html>
            <head>
                <title>Trang Web Nhỏ</title>
                <meta property="og:title" content="Trang Web Nhỏ Tiêu Đề" />
                <meta property="og:description" content="Mô tả trang web nhỏ" />
                <meta property="og:site_name" content="SmallSite" />
                <meta property="og:video" content="https://example.com/video1.mp4" />
            </head>
            <body>
                <h1>Tiêu đề bài viết</h1>
                <img src="https://example.com/images/photo1.jpg" />
                <img src="/upload/photo2.png" />
                <video src="/videos/clip1.mp4"></video>
                <p>Nội dung văn bản ngắn gọn.</p>
            </body>
            </html>
            """
            # Đệm thêm cho đủ ~5 KB
            padding = "<!-- " + ("A" * 4000) + " -->\n"
            body = (html + padding).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif path == "/normal":
            # 2. Phản hồi trung bình (~250 KB)
            html = """<!DOCTYPE html>
            <html>
            <head>
                <title>Bài Viết Blog Bình Thường</title>
                <meta property="og:title" content="Bài Viết Blog Bình Thường" />
                <meta property="og:description" content="Mô tả chi tiết bài viết với nhiều hình ảnh và luồng stream" />
                <meta property="og:site_name" content="TechBlog" />
            </head>
            <body>
                <h1>Bộ Sưu Tập Công Nghệ</h1>
                <video src="https://example.com/streams/live.m3u8"></video>
            """
            for i in range(1, 20):
                html += f'<img src="https://example.com/uploads/gallery_item_{i}.webp" alt="Hình {i}" />\n'
            html += "</body></html>\n"
            # Đệm cho đủ ~250 KB
            padding = "<!-- " + ("B" * (250 * 1024 - len(html))) + " -->\n"
            body = (html + padding).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif path == "/large":
            # 3. Phản hồi lớn hợp lệ (5 MB, < 50 MB limit)
            html = """<!DOCTYPE html>
            <html>
            <head>
                <title>Tài Liệu Cực Lớn</title>
                <meta property="og:title" content="Tài Liệu Kỹ Thuật Dung Lượng 5MB" />
                <meta property="og:site_name" content="LargeDocs" />
            </head>
            <body>
                <h1>Tài liệu kỹ thuật chuyên sâu</h1>
                <img src="https://example.com/uploads/large_diagram.png" />
                <video src="https://example.com/media/overview.mp4"></video>
            """
            target_size = 5 * 1024 * 1024  # 5 MB
            initial_body = html.encode("utf-8")
            remaining = target_size - len(initial_body) - 30
            padding = ("<!-- " + ("C" * max(0, remaining)) + " --></body></html>").encode("utf-8")
            full_body = initial_body + padding
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(full_body)))
            self.end_headers()
            self.wfile.write(full_body)

        elif path == "/oversized_content_length":
            # 4. Phản hồi vượt giới hạn qua Content-Length header (60 MB > 50 MB)
            fake_size = 60 * 1024 * 1024  # 60 MB
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(fake_size))
            self.end_headers()
            # Server không cần ghi dữ liệu vì client phải abort ngay khi đọc header!
            # Nếu client không abort và cố đọc, server ghi 1 ít rồi đóng socket
            try:
                self.wfile.write(b"<!DOCTYPE html><html><body>Oversized</body></html>")
            except Exception:
                pass

        elif path == "/oversized_chunked":
            # 5. Phản hồi vượt giới hạn qua stream/chunk (không có Content-Length, streaming > 50 MB)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            # Không gửi Content-Length (streaming / chunked mô phỏng)
            self.end_headers()
            chunk_block = b"D" * (128 * 1024)  # 128 KB mỗi lần ghi
            total_sent = 0
            max_to_send = 60 * 1024 * 1024  # Gửi tối đa 60 MB nếu client không ngắt
            try:
                while total_sent < max_to_send:
                    self.wfile.write(chunk_block)
                    self.wfile.flush()
                    total_sent += len(chunk_block)
            except (BrokenPipeError, ConnectionResetError, socket.error):
                # Client đã ngắt kết nối an toàn đúng như mong đợi
                pass
            except Exception:
                pass

        else:
            self.send_response(404)
            self.end_headers()


def start_test_server() -> Tuple[HTTPServer, str]:
    server = HTTPServer(("127.0.0.1", 0), MockWebScraperServer)
    port = server.server_port
    base_url = f"http://127.0.0.1:{port}"
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    return server, base_url


def run_test_1(base_url: str) -> bool:
    print("\n" + "=" * 70)
    print("  🚀 CHẠY TEST #1 — Kiểm thử an toàn bộ nhớ HTTP Response")
    print("=" * 70)

    extractor = WebScraperExtractor()
    print(f"  ℹ️  Default limit: {extractor.max_response_size / (1024 * 1024):.1f} MB ({extractor.max_response_size} bytes)")

    # 1. Kiểm tra Small response (~5 KB)
    print("\n  [1/6] Kiểm tra small response (~5 KB)...")
    try:
        meta_small = extractor.extract(f"{base_url}/small")
        assert meta_small.title == "Trang Web Nhỏ Tiêu Đề", f"Tiêu đề không khớp: {meta_small.title}"
        assert meta_small.images and len(meta_small.images) >= 2, "Thiếu hình ảnh"
        assert meta_small.streams and len(meta_small.streams) >= 2, "Thiếu video stream"
        print(f"  ✅ [PASS] Small response trích xuất thành công: '{meta_small.title}' ({len(meta_small.images)} ảnh, {len(meta_small.streams)} video)")
    except Exception as e:
        print(f"  ❌ [FAIL] Small response thất bại: {e}")
        return False

    # 2. Kiểm tra Normal response (~250 KB)
    print("\n  [2/6] Kiểm tra normal response (~250 KB)...")
    try:
        meta_normal = extractor.extract(f"{base_url}/normal")
        assert meta_normal.title == "Bài Viết Blog Bình Thường", f"Tiêu đề không khớp: {meta_normal.title}"
        assert meta_normal.images and len(meta_normal.images) == 19, f"Số lượng ảnh không đúng: {len(meta_normal.images)}"
        assert meta_normal.streams and any(s.format == "HLS" for s in meta_normal.streams), "Thiếu HLS stream"
        print(f"  ✅ [PASS] Normal response trích xuất thành công: '{meta_normal.title}' ({len(meta_normal.images)} ảnh)")
    except Exception as e:
        print(f"  ❌ [FAIL] Normal response thất bại: {e}")
        return False

    # 3. Kiểm tra Large response (5 MB, < 50 MB limit)
    print("\n  [3/6] Kiểm tra large response (5 MB, dưới ngưỡng 50 MB)...")
    try:
        meta_large = extractor.extract(f"{base_url}/large")
        assert meta_large.title == "Tài Liệu Kỹ Thuật Dung Lượng 5MB", f"Tiêu đề không khớp: {meta_large.title}"
        assert meta_large.images and len(meta_large.images) >= 1, "Thiếu hình ảnh từ large page"
        assert meta_large.streams and len(meta_large.streams) >= 1, "Thiếu video từ large page"
        print(f"  ✅ [PASS] Large response (5 MB) đọc thành công bằng chunking an toàn: '{meta_large.title}'")
    except Exception as e:
        print(f"  ❌ [FAIL] Large response thất bại: {e}")
        return False

    # 4. Kiểm tra Oversized qua Content-Length (60 MB > 50 MB limit)
    print("\n  [4/6] Kiểm tra response vượt giới hạn qua Content-Length (60 MB > 50 MB)...")
    aborted_by_content_length = False
    try:
        extractor.extract(f"{base_url}/oversized_content_length")
        print("  ❌ [FAIL] Phải ném ngoại lệ khi Content-Length vượt quá 50 MB nhưng lại thành công!")
        return False
    except OversizedResponseError as oe:
        aborted_by_content_length = True
        print(f"  ✅ [PASS] Đã abort an toàn bằng Content-Length validation trước khi đọc bytes: {oe}")
    except RuntimeError as re:
        if "vượt quá giới hạn" in str(re):
            aborted_by_content_length = True
            print(f"  ✅ [PASS] Đã bắt RuntimeError có kiểm soát: {re}")
        else:
            print(f"  ❌ [FAIL] Ngoại lệ không đúng mong đợi: {re}")
            return False
    except Exception as e:
        print(f"  ❌ [FAIL] Lỗi không mong đợi: {e}")
        return False

    if not aborted_by_content_length:
        return False

    # 5. Kiểm tra Oversized qua Streaming/Chunked (không có Content-Length, luồng gửi > 50 MB)
    print("\n  [5/6] Kiểm tra response vượt giới hạn qua streaming/chunked (> 50 MB)...")
    aborted_by_streaming = False
    try:
        extractor.extract(f"{base_url}/oversized_chunked")
        print("  ❌ [FAIL] Phải ngắt luồng streaming khi vượt 50 MB nhưng lại thành công!")
        return False
    except OversizedResponseError as oe:
        aborted_by_streaming = True
        print(f"  ✅ [PASS] Đã ngắt luồng streaming an toàn khi vượt ngưỡng: {oe}")
    except RuntimeError as re:
        if "vượt quá giới hạn" in str(re):
            aborted_by_streaming = True
            print(f"  ✅ [PASS] Đã bắt RuntimeError có kiểm soát từ stream: {re}")
        else:
            print(f"  ❌ [FAIL] Ngoại lệ không đúng: {re}")
            return False
    except Exception as e:
        print(f"  ❌ [FAIL] Lỗi không mong đợi: {e}")
        return False

    if not aborted_by_streaming:
        return False

    # 6. Kiểm tra Custom Limit linh hoạt (100 KB limit đối với trang 250 KB)
    print("\n  [6/6] Kiểm tra giới hạn tùy biến (custom limit 100 KB đối với response normal 250 KB)...")
    custom_extractor = WebScraperExtractor(max_response_size=100 * 1024)  # 100 KB
    try:
        custom_extractor.extract(f"{base_url}/normal")
        print("  ❌ [FAIL] Phải abort trang 250 KB khi giới hạn là 100 KB!")
        return False
    except OversizedResponseError as oe:
        print(f"  ✅ [PASS] Custom limit 100 KB hoạt động chính xác, ngắt an toàn trang 250 KB: {oe}")
    except RuntimeError as re:
        if "vượt quá giới hạn" in str(re):
            print(f"  ✅ [PASS] Custom limit bắt lỗi có kiểm soát: {re}")
        else:
            print(f"  ❌ [FAIL] Lỗi không khớp: {re}")
            return False

    print("\n  🎉 KẾT QUẢ TEST #1: TẤT CẢ CÁC KIỂM THỬ ĐẠT YÊU CẦU!")
    return True


def run_test_2(base_url: str) -> bool:
    print("\n" + "=" * 70)
    print("  🚀 CHẠY TEST #2 — Lặp lại toàn bộ test với Clean State & Kiểm tra Oversized kỹ lưỡng")
    print("=" * 70)

    # Khởi tạo instance mới hoàn toàn để đảm bảo tính độc lập trạng thái (clean state)
    fresh_extractor = WebScraperExtractor()

    scenarios = [
        ("small response", f"{base_url}/small", True),
        ("normal response", f"{base_url}/normal", True),
        ("large response (5 MB)", f"{base_url}/large", True),
        ("oversized (Content-Length 60 MB)", f"{base_url}/oversized_content_length", False),
        ("oversized (Chunked stream 60 MB)", f"{base_url}/oversized_chunked", False),
    ]

    for name, url, should_succeed in scenarios:
        print(f"\n  [Repeat Test] Đang kiểm tra: {name}...")
        try:
            res = fresh_extractor.extract(url)
            if not should_succeed:
                print(f"  ❌ [FAIL] {name} lẽ ra phải bị abort nhưng lại chạy thành công!")
                return False
            print(f"  ✅ [PASS] {name}: Thành công như kỳ vọng ('{res.title}')")
        except (OversizedResponseError, RuntimeError) as e:
            if should_succeed:
                print(f"  ❌ [FAIL] {name} thất bại ngoài ý muốn: {e}")
                return False
            print(f"  ✅ [PASS] {name}: Bị abort an toàn với lỗi có kiểm soát: {e}")
        except Exception as e:
            print(f"  ❌ [FAIL] {name}: Gặp lỗi không xác định: {e}")
            return False

    # Kiểm tra kiểm soát lỗi: Worker/Caller không bị crash
    print("\n  [Repeat Test] Kiểm tra worker crash prevention khi gọi liên tiếp nhiều oversized URLs...")
    for i in range(3):
        try:
            fresh_extractor.extract(f"{base_url}/oversized_content_length")
        except (OversizedResponseError, RuntimeError):
            pass
        try:
            fresh_extractor.extract(f"{base_url}/oversized_chunked")
        except (OversizedResponseError, RuntimeError):
            pass

    # Sau các lỗi oversized, scraper vẫn hoạt động bình thường với link hợp lệ
    meta_after = fresh_extractor.extract(f"{base_url}/small")
    assert meta_after.title == "Trang Web Nhỏ Tiêu Đề", "Scraper bị hỏng trạng thái sau các lỗi oversized"
    print("  ✅ [PASS] Scraper không bị regression và phục hồi bình thường sau nhiều lỗi oversized liên tiếp!")

    print("\n  🎉 KẾT QUẢ TEST #2: TẤT CẢ CÁC BÀI KIỂM THỬ LẶP LẠI ĐỀU ĐẠT CHUẨN 100%!")
    return True


def test_red05_memory_safety():
    """Entrypoint cho pytest tự động phát hiện và kiểm thử."""
    server, base_url = start_test_server()
    try:
        assert run_test_1(base_url) is True
        assert run_test_2(base_url) is True
    finally:
        server.shutdown()
        server.server_close()


def main():
    server, base_url = start_test_server()
    print(f"Đã khởi động Mock Web Server tại: {base_url}")

    try:
        t1_ok = run_test_1(base_url)
        t2_ok = run_test_2(base_url)

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
