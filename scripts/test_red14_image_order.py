#!/usr/bin/env python3
"""
Verification script for RED 14 — Image Order không ổn định do sử dụng Set.

Tests:
- TEST #1:
  1. Deduplicate pattern test với input: ["A", "B", "A", "C", "B"] -> Output: ["A", "B", "C"].
  2. Kiểm tra actual scraper output trên trang web thực tế (qua Mock HTTP Server)
     với các thẻ chứa URL A, B, A, C, B -> đảm bảo kết quả thu được là [A, B, C].

- TEST #2:
  Chạy scraper trên media list có nhiều duplicates và kiểm tra insertion order:
  - Danh sách ảnh phong phú: [photo1, photo2, photo3, photo1, photo4, photo2, photo5]
  - Đảm bảo output chính xác [photo1, photo2, photo3, photo4, photo5].
  - Kiểm tra tính ổn định lặp lại (10 lần liên tiếp với clean instances) để chứng minh
    không còn bị ảnh hưởng bởi tính ngẫu nhiên của set hash table.
"""

import os
import sys
import socket
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Tuple, List

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.extractors.web_scraper import WebScraperExtractor


class MockScraperHandler(BaseHTTPRequestHandler):
    """Giả lập máy chủ HTTP phục vụ các trang web kiểm thử order hình ảnh."""

    def log_message(self, format, *args):
        pass  # Tắt log stdout

    def do_GET(self):
        if self.path == "/test_abacb":
            # Kịch bản Test #1: og:image và img tags theo thứ tự A, B, A, C, B
            html = """<!DOCTYPE html>
            <html>
            <head>
                <title>Test ABACB Order</title>
                <meta property="og:image" content="https://example.com/images/A.jpg" />
            </head>
            <body>
                <h1>Deduplication Test</h1>
                <img src="https://example.com/images/B.jpg" alt="B" />
                <img src="https://example.com/images/A.jpg" alt="A Duplicate" />
                <img src="https://example.com/images/C.jpg" alt="C" />
                <img src="https://example.com/images/B.jpg" alt="B Duplicate" />
            </body>
            </html>"""
            self._send_html(html)

        elif self.path == "/test_photo_gallery":
            # Kịch bản Test #2: Album gallery với nhiều ảnh và trùng lặp
            html = """<!DOCTYPE html>
            <html>
            <head>
                <title>Photo Gallery Album</title>
                <meta property="og:image" content="/uploads/photo1.jpg" />
            </head>
            <body>
                <h1>Photo Album</h1>
                <div class="gallery">
                    <img src="/uploads/photo2.jpg" />
                    <img src="/uploads/photo3.jpg" />
                    <img src="/uploads/photo1.jpg" /> <!-- Duplicate of og:image -->
                    <img src="/uploads/photo4.jpg" />
                    <img src="/uploads/photo2.jpg" /> <!-- Duplicate of photo2 -->
                    <img src="/uploads/photo5.jpg" />
                </div>
            </body>
            </html>"""
            self._send_html(html)

        elif self.path == "/test_pure_img_tags":
            # Không có og:image, chỉ có các thẻ img theo thứ tự 1, 2, 1, 3, 2, 4
            html = """<!DOCTYPE html>
            <html>
            <head><title>Pure Img Tags</title></head>
            <body>
                <img src="/images/item1.png" />
                <img src="/images/item2.png" />
                <img src="/images/item1.png" />
                <img src="/images/item3.png" />
                <img src="/images/item2.png" />
                <img src="/images/item4.png" />
            </body>
            </html>"""
            self._send_html(html)

        else:
            self.send_response(404)
            self.end_headers()

    def _send_html(self, html: str):
        content = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)


def get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run_mock_server() -> Tuple[HTTPServer, threading.Thread, str]:
    port = get_free_port()
    server = HTTPServer(("127.0.0.1", port), MockScraperHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{port}"
    return server, thread, base_url


def test_1(base_url: str) -> bool:
    print("\n" + "=" * 70)
    print("  🚀 CHẠY TEST #1 — Kiểm thử Deduplication Pattern và Insertion Order")
    print("=" * 70)

    # 1. Kiểm tra unit pattern: ["A", "B", "A", "C", "B"] -> ["A", "B", "C"]
    print("\n  [1/2] Kiểm thử thuật toán Deduplication giữ nguyên Insertion Order:")
    input_data = ["A", "B", "A", "C", "B"]
    expected_data = ["A", "B", "C"]
    actual_data = WebScraperExtractor.deduplicate_urls(input_data)
    print(f"        Input   : {input_data}")
    print(f"        Expected: {expected_data}")
    print(f"        Actual  : {actual_data}")
    assert actual_data == expected_data, f"LỖI: Kết quả dedup {actual_data} != {expected_data}"
    print("  ✅ [PASS] Deduplication giữ nguyên insertion order chính xác!")

    # 2. Kiểm tra actual scraper output trên trang web thực tế
    print("\n  [2/2] Kiểm thử Actual Scraper Output trên trang web:")
    extractor = WebScraperExtractor()
    test_url = f"{base_url}/test_abacb"
    metadata = extractor.extract(test_url)

    assert metadata.images is not None, "LỖI: metadata.images không được None"
    extracted_urls = [img.url for img in metadata.images]
    expected_urls = [
        "https://example.com/images/A.jpg",
        "https://example.com/images/B.jpg",
        "https://example.com/images/C.jpg",
    ]

    print(f"        Trang test: {test_url}")
    print(f"        Extracted : {extracted_urls}")
    print(f"        Expected  : {expected_urls}")

    assert extracted_urls == expected_urls, (
        f"LỖI: Thứ tự ảnh bóc tách không đúng!\n"
        f"Nhận được: {extracted_urls}\n"
        f"Kỳ vọng  : {expected_urls}"
    )

    # Kiểm tra thêm thumbnail logic không bị ảnh hưởng và trỏ đúng ảnh đầu tiên
    assert metadata.thumbnail == "https://example.com/images/A.jpg", (
        f"LỖI: Thumbnail không đúng ảnh đầu tiên: {metadata.thumbnail}"
    )

    print("  ✅ [PASS] Scraper bóc tách đúng thứ tự [A, B, C] và thumbnail chính xác!")
    print("\n  🎉 KẾT QUẢ TEST #1: PASS")
    return True


def test_2(base_url: str) -> bool:
    print("\n" + "=" * 70)
    print("  🚀 CHẠY TEST #2 — Kiểm thử Scraper trên Media List có Duplicate & Order Stability")
    print("=" * 70)

    extractor = WebScraperExtractor()

    # Kịch bản 1: Album ảnh có chứa duplicate và đường dẫn tương đối
    print("\n  [1/3] Kiểm tra album gallery có duplicate:")
    test_url_gallery = f"{base_url}/test_photo_gallery"
    res_gallery = extractor.extract(test_url_gallery)

    expected_gallery_urls = [
        f"{base_url}/uploads/photo1.jpg",
        f"{base_url}/uploads/photo2.jpg",
        f"{base_url}/uploads/photo3.jpg",
        f"{base_url}/uploads/photo4.jpg",
        f"{base_url}/uploads/photo5.jpg",
    ]
    actual_gallery_urls = [img.url for img in res_gallery.images]

    print(f"        Extracted : {actual_gallery_urls}")
    print(f"        Expected  : {expected_gallery_urls}")

    assert actual_gallery_urls == expected_gallery_urls, (
        f"LỖI: Danh sách ảnh gallery không khớp thứ tự kỳ vọng!\n"
        f"Nhận được: {actual_gallery_urls}\n"
        f"Kỳ vọng  : {expected_gallery_urls}"
    )

    # Kiểm tra ID đánh số liên tục 1..N
    ids = [img.id for img in res_gallery.images]
    assert ids == [1, 2, 3, 4, 5], f"LỖI: Đánh số ID không liên tục: {ids}"
    print("  ✅ [PASS] Album gallery bóc tách chính xác 5 ảnh theo đúng insertion order!")

    # Kịch bản 2: Pure <img> tags không có thẻ meta
    print("\n  [2/3] Kiểm tra trang chỉ có thẻ <img> với duplicate:")
    test_url_pure = f"{base_url}/test_pure_img_tags"
    res_pure = extractor.extract(test_url_pure)

    expected_pure_urls = [
        f"{base_url}/images/item1.png",
        f"{base_url}/images/item2.png",
        f"{base_url}/images/item3.png",
        f"{base_url}/images/item4.png",
    ]
    actual_pure_urls = [img.url for img in res_pure.images]

    assert actual_pure_urls == expected_pure_urls, (
        f"LỖI: Thứ tự pure img tags không đúng!\n"
        f"Nhận được: {actual_pure_urls}\n"
        f"Kỳ vọng  : {expected_pure_urls}"
    )
    print("  ✅ [PASS] Thứ tự pure <img> tags được bảo toàn tuyệt đối!")

    # Kịch bản 3: Độ ổn định lặp lại (Repeatability & Determinism)
    print("\n  [3/3] Kiểm tra độ ổn định lặp lại qua 10 lần chạy với clean instances:")
    for i in range(10):
        fresh_scraper = WebScraperExtractor()
        meta = fresh_scraper.extract(test_url_gallery)
        urls = [img.url for img in meta.images]
        assert urls == expected_gallery_urls, (
            f"LỖI: Bất ổn định thứ tự tại lần chạy {i+1}!\n"
            f"Nhận được: {urls}"
        )
    print("  ✅ [PASS] 10/10 lần chạy cho kết quả hoàn toàn đồng nhất 100%!")

    print("\n  🎉 KẾT QUẢ TEST #2: PASS")
    return True


def main():
    server, thread, base_url = run_mock_server()
    print(f"Đã khởi động Mock Web Server tại: {base_url}")

    try:
        t1_ok = test_1(base_url)
        t2_ok = test_2(base_url)

        print("\n" + "=" * 70)
        print("TỔNG KẾT:")
        print(f"TEST #1: {'PASS' if t1_ok else 'FAIL'}")
        print(f"TEST #2: {'PASS' if t2_ok else 'FAIL'}")
        print("=" * 70 + "\n")

        if not (t1_ok and t2_ok):
            sys.exit(1)
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
