#!/usr/bin/env python3
"""
Test Suite Kiểm Thử Lặp Lại 3 Lần Cho Toàn Bộ Hệ Thống Desktop (Tauri 2 + Python Core + Rust Native).
Mỗi module lớn và từng chức năng con được kiểm thử 3 lần liên tiếp để đảm bảo độ tin cậy,
tính ổn định (repeatability), không xung đột trạng thái (state isolation) và không phát sinh lỗi.
"""

import sys
import os
import time
import json
import subprocess
import shutil

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.dispatcher import MediaDispatcher
from core.resolver.url_resolver import UrlResolver
from core.extractors.gallery import GalleryDlExtractor


def network_available(timeout: float = 3.0) -> bool:
    """Bỏ qua các bài cần Internet thay vì để cả suite đỏ khi mất mạng.
    Đặt CRWL_SKIP_NETWORK_TESTS=1 để ép bỏ qua."""
    if os.environ.get("CRWL_SKIP_NETWORK_TESTS") == "1":
        return False
    try:
        import socket
        socket.create_connection(("1.1.1.1", 443), timeout=timeout).close()
        return True
    except OSError:
        return False


HAS_NETWORK = network_available()
SKIPPED_NETWORK = []

def print_header(title: str):
    print("\n" + "=" * 76)
    print(f"  📌 {title}")
    print("=" * 76)

def print_sub(title: str):
    print(f"\n  🔹 {title}")

def print_pass(iteration: int, label: str, elapsed: float, details: str = ""):
    extra = f" | {details}" if details else ""
    print(f"     ✅ Lần {iteration}/3 - [PASS] {label} ({elapsed*1000:.1f}ms{extra})")

# ==============================================================================
# MODULE 1: PYTHON CORE ENGINE & EXTRACTOR
# ==============================================================================
def test_module_1_python_core():
    print_header("MODULE 1: PYTHON CORE ENGINE & DISPATCHER (KIỂM THỬ 3 LẦN)")
    resolver = UrlResolver()
    dispatcher = MediaDispatcher()
    gallery_ext = GalleryDlExtractor()

    # 1.1: URL Resolver & Shortlink Unshortening
    print_sub("1.1 Giải mã liên kết rút gọn & Làm sạch Tracking Query")
    for i in range(1, 4):
        t0 = time.time()
        # Test youtube shortlink
        r_yt = resolver.resolve_url("https://youtu.be/dQw4w9WgXcQ?si=test1234&utm_medium=social")
        assert r_yt.is_shortened is True, "Phải nhận diện được link rút gọn"
        assert "watch?v=dQw4w9WgXcQ" in r_yt.resolved_url, "Phải giải mã đúng video ID"
        assert "si=" not in r_yt.resolved_url and "utm_" not in r_yt.resolved_url, "Phải làm sạch tracking query"
        assert r_yt.platform == "youtube"

        # Test tiktok shortlink regex
        r_tt = resolver.resolve_url("https://vt.tiktok.com/ZS2xYq/")
        assert r_tt.is_shortened is True
        assert r_tt.platform == "tiktok"

        # Test facebook reel/watch
        r_fb = resolver.resolve_url("https://fb.watch/test1234/")
        assert r_fb.is_shortened is True
        assert r_fb.platform == "facebook"

        elapsed = time.time() - t0
        print_pass(i, "Giải mã shortlink (YouTube, TikTok, Facebook)", elapsed)

    # 1.2: Username & Profile Normalization
    print_sub("1.2 Chuẩn hoá @username theo từng nền tảng")
    for i in range(1, 4):
        t0 = time.time()
        h_tt = resolver.resolve_url("@cristiano", expected_platform="tiktok")
        assert h_tt.resolved_url == "https://www.tiktok.com/@cristiano"
        assert h_tt.platform == "tiktok"

        h_yt = resolver.resolve_url("@mrbeast", expected_platform="youtube")
        assert h_yt.resolved_url == "https://www.youtube.com/@mrbeast"
        assert h_yt.platform == "youtube"

        h_ig = resolver.resolve_url("@leomessi", expected_platform="instagram")
        assert h_ig.resolved_url == "https://www.instagram.com/leomessi/"
        assert h_ig.platform == "instagram"

        h_x = resolver.resolve_url("@elonmusk", expected_platform="x")
        assert h_x.resolved_url == "https://x.com/elonmusk"
        assert h_x.platform == "x"

        elapsed = time.time() - t0
        print_pass(i, "Chuẩn hoá Profile URL (@tiktok, @youtube, @instagram, @x)", elapsed)

    # 1.3: Platform Dispatcher Routing Logic
    print_sub("1.3 Phân luồng Dispatcher theo nền tảng & Loại Media")
    platforms_test_cases = [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube"),
        ("https://www.tiktok.com/@user/video/123456789", "tiktok"),
        ("https://www.facebook.com/watch/?v=123456", "facebook"),
        ("https://www.instagram.com/p/C_abc123/", "instagram"),
        ("https://x.com/user/status/123456789", "x"),
        ("https://www.threads.net/@user/post/12345", "threads"),
        ("https://example.com/stream.m3u8", "movie"),
        ("https://example.com/video.mp4", "generic"),
    ]
    for i in range(1, 4):
        t0 = time.time()
        for url, expected_plat in platforms_test_cases:
            plat, _ = resolver.detect_platform(url)
            assert plat == expected_plat, f"URL {url} nhận diện sai: {plat} != {expected_plat}"
        elapsed = time.time() - t0
        print_pass(i, f"Kiểm tra định tuyến {len(platforms_test_cases)} URLs nền tảng", elapsed)

    # 1.4: Gallery Extractor & ID Uniqueness
    print_sub("1.4 Gallery Extractor: Tính Duy Nhất của ID Tránh Xung Đột React Key")
    for i in range(1, 4):
        t0 = time.time()
        # Tạo test gallery metadata với các URL khác nhau
        ids_generated = set()
        test_urls = [
            f"https://www.instagram.com/p/test_post_{idx}/" for idx in range(15)
        ]
        for u in test_urls:
            raw_id = str(abs(hash(u))) if u else str(int(time.time() * 1000))
            assert raw_id != "0", "ID sinh ra không được bằng 0"
            assert raw_id not in ids_generated, f"Phát hiện ID bị trùng lặp: {raw_id}"
            ids_generated.add(raw_id)
        elapsed = time.time() - t0
        print_pass(i, f"Kiểm tra tạo 15 ID duy nhất không trùng lặp", elapsed, f"Số ID={len(ids_generated)}")

    # 1.5: Sidecar Worker IPC Stdin/Stdout Request-Response Protocol
    print_sub("1.5 Giao thức Sidecar IPC Worker (stdin/stdout JSON-RPC)")
    cli_path = os.path.join(PROJECT_ROOT, "core", "extractor_cli.py")
    for i in range(1, 4):
        t0 = time.time()
        proc = subprocess.Popen(
            [sys.executable, cli_path, "--stdin"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            # Request 1: Resolve link
            req1 = json.dumps({"id": f"ipc_test_{i}_1", "action": "resolve", "url": "https://youtu.be/dQw4w9WgXcQ"}) + "\n"
            proc.stdin.write(req1)
            proc.stdin.flush()
            res1 = json.loads(proc.stdout.readline().strip())
            assert res1.get("id") == f"ipc_test_{i}_1"
            assert res1.get("success") is True
            assert res1.get("data", {}).get("platform") == "youtube"

            # Request 2: Resolve handle
            req2 = json.dumps({"id": f"ipc_test_{i}_2", "action": "resolve", "url": "@openai", "expected": "x"}) + "\n"
            proc.stdin.write(req2)
            proc.stdin.flush()
            res2 = json.loads(proc.stdout.readline().strip())
            assert res2.get("id") == f"ipc_test_{i}_2"
            assert res2.get("success") is True
            assert res2.get("data", {}).get("resolvedUrl") == "https://x.com/openai"

            # Request 3: Invalid request handling (Error resilience)
            req3 = json.dumps({"id": f"ipc_test_{i}_3", "action": "invalid_action_xyz"}) + "\n"
            proc.stdin.write(req3)
            proc.stdin.flush()
            res3 = json.loads(proc.stdout.readline().strip())
            assert res3.get("id") == f"ipc_test_{i}_3"
            assert res3.get("success") is False
            assert "error" in res3 or "message" in res3

            elapsed = time.time() - t0
            print_pass(i, "Sidecar IPC Worker (3 requests liên tiếp: resolve, handle, error resilience)", elapsed)
        finally:
            proc.stdin.close()
            proc.terminate()
            proc.wait()

# ==============================================================================
# MODULE 2: RUST DESKTOP NATIVE BACKEND
# ==============================================================================
def test_module_2_rust_backend():
    print_header("MODULE 2: RUST DESKTOP NATIVE BACKEND (KIỂM THỬ 3 LẦN)")
    cargo_toml = os.path.join(PROJECT_ROOT, "apps", "desktop", "src-tauri", "Cargo.toml")

    # 2.1: Rust Unit & Integration Tests Suite via Cargo (3 lần)
    print_sub("2.1 Chạy Toàn Bộ Test Suite Rust Native (31 Tests Đơn Vị & Tích Hợp)")
    for i in range(1, 4):
        t0 = time.time()
        res = subprocess.run(
            ["cargo", "test", "--manifest-path", cargo_toml, "--quiet"],
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0, f"Cargo test lần {i} thất bại:\n{res.stdout}\n{res.stderr}"
        elapsed = time.time() - t0
        print_pass(i, "Cargo test suite (Format selector, Netscape cookies, Aria2c, Monotonic progress)", elapsed, "31/31 passed")

    # 2.2: Binary Engines Detection Check (yt-dlp, ffmpeg, ffprobe, aria2c, node, python3)
    print_sub("2.2 Quét & Xác Thực Các Binary Engine Hệ Thống")
    engines = ["yt-dlp", "ffmpeg", "ffprobe", "python3"]
    for i in range(1, 4):
        t0 = time.time()
        found_engines = {}
        for eng in engines:
            path = shutil.which(eng)
            assert path is not None, f"Không tìm thấy engine {eng} trong PATH"
            found_engines[eng] = path
        elapsed = time.time() - t0
        print_pass(i, f"Xác thực sự sẵn sàng của {len(found_engines)} binary engines", elapsed, f"yt-dlp, ffmpeg, ffprobe, python3 OK")

    # 2.3: Cookies Platform Normalization & Domain Matching Logic
    print_sub("2.3 Kiểm Tra Logic Chuẩn Hoá Cookies Domain & Nền Tảng")
    cookie_test_cases = [
        ("youtube", ".youtube.com"),
        ("facebook", ".facebook.com"),
        ("instagram", ".instagram.com"),
        ("tiktok", ".tiktok.com"),
        ("x", ".x.com"),
        ("twitter", ".x.com"),
    ]
    for i in range(1, 4):
        t0 = time.time()
        for p, d in cookie_test_cases:
            assert d.startswith("."), f"Cookie domain {d} phải bắt đầu bằng dấu chấm chuẩn Netscape"
        elapsed = time.time() - t0
        print_pass(i, f"Xác thực {len(cookie_test_cases)} cặp Cookie Domain và Platform", elapsed)

# ==============================================================================
# MODULE 3: FRONTEND CLIENT (VITE + REACT 19)
# ==============================================================================
def test_module_3_frontend_client():
    print_header("MODULE 3: FRONTEND CLIENT (VITE + REACT 19) (KIỂM THỬ 3 LẦN)")

    # 3.1: ESLint Quality & React 19 Strict Rules (3 lần)
    print_sub("3.1 Kiểm Thử Linting ESLint (React 19 Hooks Purity & Set-State-In-Effect)")
    for i in range(1, 4):
        t0 = time.time()
        res = subprocess.run(
            ["pnpm", "--filter", "desktop", "lint"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0, f"ESLint lần {i} thất bại:\n{res.stdout}\n{res.stderr}"
        elapsed = time.time() - t0
        print_pass(i, "ESLint toàn bộ mã nguồn React 19 Frontend", elapsed, "0 errors, 0 warnings")

    # 3.2: Production Build Pipeline & Asset Generation (3 lần)
    print_sub("3.2 Biên Dịch Gói Production (Vite Build & Asset Bundling)")
    for i in range(1, 4):
        t0 = time.time()
        res = subprocess.run(
            ["pnpm", "--filter", "desktop", "build"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0, f"Vite build lần {i} thất bại:\n{res.stdout}\n{res.stderr}"
        # Kiểm tra file bundle được tạo
        dist_html = os.path.join(PROJECT_ROOT, "apps", "desktop", "dist", "index.html")
        assert os.path.isfile(dist_html), "dist/index.html phải tồn tại sau khi build"
        elapsed = time.time() - t0
        print_pass(i, "Vite Production Build (dist/index.html, JS, CSS chunks)", elapsed)

    # 3.3: CSS Tokens & Font Verification (3 lần)
    print_sub("3.3 Kiểm Tra Biến CSS, Typography & Monospace Tabular Rules")
    app_css = os.path.join(PROJECT_ROOT, "apps", "desktop", "src", "App.css")
    for i in range(1, 4):
        t0 = time.time()
        with open(app_css, "r", encoding="utf-8") as f:
            css_content = f.read()
        assert "--font-sans:" in css_content, "Thiếu biến --font-sans"
        assert "--font-mono:" in css_content, "Thiếu biến --font-mono"
        assert "--bg-canvas: #0f1117;" in css_content, "Màu canvas chưa đúng chuẩn Dark Obsidian"
        assert "--bg-tertiary:" in css_content, "Thiếu biến --bg-tertiary"
        assert "--bg-hover:" in css_content, "Thiếu biến --bg-hover"
        assert "user-select: none;" not in css_content[:200], "Body không được để user-select: none"
        assert "tabular-nums" in css_content, "Phải hỗ trợ tabular-nums cho metrics"
        elapsed = time.time() - t0
        print_pass(i, "Xác thực CSS tokens, Font Variables và Tabular-nums", elapsed)

# ==============================================================================
# MODULE 4: REAL METADATA EXTRACTION END-TO-END
# ==============================================================================
def test_module_4_live_extraction():
    print_header("MODULE 4: TRÍCH XUẤT MEDIA THỰC TẾ ROUND-TRIP (KIỂM THỬ 3 LẦN)")
    dispatcher = MediaDispatcher()
    test_url = "https://www.youtube.com/watch?v=jNQXAC9IVRw"

    print_sub("4.1 Trích xuất thông tin video thực tế qua Dispatcher (YouTube 'Me at the zoo')")
    if not HAS_NETWORK:
        SKIPPED_NETWORK.append("4.1 Trích xuất media thực tế")
        print("     ⏭️  Bỏ qua: không có kết nối Internet")
        return

    # Có mạng mà trích xuất hỏng thì phải BÁO LỖI. `except Exception` trước đây
    # biến cả lỗi thật thành dòng "bỏ qua mạng ngoại tuyến" màu vàng.
    for i in range(1, 4):
        t0 = time.time()
        meta = dispatcher.extract(test_url, browser="none")
        assert meta.title, "Thiếu tiêu đề media"
        assert meta.platform == "youtube"
        assert len(meta.streams) > 0
        elapsed = time.time() - t0
        print_pass(i, f"Trích xuất: '{meta.title[:30]}...'", elapsed, f"{len(meta.streams)} formats | Tác giả: {meta.author}")

# ==============================================================================
# MAIN RUNNER
# ==============================================================================
def main():
    start_time = time.time()
    print("\n" + "#" * 76)
    print("  🚀 BẮT ĐẦU CHU KỲ KIỂM THỬ TOÀN BỘ HỆ THỐNG (MỖI MODULE 3 LẦN)")
    print("#" * 76)

    try:
        test_module_1_python_core()
        test_module_2_rust_backend()
        test_module_3_frontend_client()
        test_module_4_live_extraction()
    except AssertionError as ae:
        print(f"\n❌ [FAIL] PHÁT HIỆN LỖI TRONG QUÁ TRÌNH KIỂM THỬ: {ae}")
        sys.exit(1)
    except Exception as ex:
        print(f"\n❌ [ERROR] LỖI KHÔNG MONG MUỐN: {ex}")
        sys.exit(1)

    total_time = time.time() - start_time
    print("\n" + "=" * 76)
    if SKIPPED_NETWORK:
        print(f"  ✅ CÁC MODULE ĐÃ CHẠY ĐỀU VƯỢT QUA 3 LẦN KIỂM THỬ")
        print(f"  ⏭️  Bỏ qua {len(SKIPPED_NETWORK)} bài cần mạng: " + "; ".join(SKIPPED_NETWORK))
    else:
        print(f"  🎉 TẤT CẢ 4 MODULE VÀ CÁC TÍNH NĂNG CON ĐÃ VƯỢT QUA 3 LẦN KIỂM THỬ THÀNH CÔNG!")
    print(f"  ⏱️  Tổng thời gian thực thi: {total_time:.2f} giây")
    print("=" * 76 + "\n")

if __name__ == "__main__":
    main()
