#!/usr/bin/env python3
"""
Test Suite for Linux Desktop App (Tauri 2 + Python Core + Rust Native).
Replaces the old backend/scripts/full_system_test.py.
Tests URL resolution, handle expansion, sidecar IPC protocol, and metadata extraction.
"""

import sys
import os
import json
import time
import subprocess

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.dispatcher import MediaDispatcher
from core.resolver.url_resolver import UrlResolver

def log_section(title: str):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)

def log_ok(msg: str):
    print(f"  ✅ [PASS] {msg}")

def log_info(msg: str):
    print(f"  ℹ️  {msg}")

def log_skip(msg: str):
    print(f"  ⏭️  [SKIP] {msg}")


SKIPPED = []


def network_available(timeout: float = 3.0) -> bool:
    """Có ra Internet được không.

    Bộ test từng gọi thẳng `unshorten_url` (curl thật) nên hỏng mạng là đỏ toàn
    bộ suite dù logic chẳng sai gì. Nay phần logic chạy offline tất định, còn
    phần cần mạng thì bỏ qua có thông báo. Đặt CRWL_SKIP_NETWORK_TESTS=1 để ép bỏ qua.
    """
    if os.environ.get("CRWL_SKIP_NETWORK_TESTS") == "1":
        return False
    try:
        import socket
        socket.create_connection(("1.1.1.1", 443), timeout=timeout).close()
        return True
    except OSError:
        return False


HAS_NETWORK = network_available()

def test_url_resolver_and_handles():
    log_section("1. KIỂM THỬ GIẢI MÃ LIÊN KẾT & CHUẨN HOÁ @USERNAME")
    resolver = UrlResolver()

    # 1.1a Logic giải mã link rút gọn — CHẠY OFFLINE.
    # Thay tầng mạng bằng stub để kiểm thử đúng phần logic của chúng ta:
    # nhận diện link rút gọn, làm sạch tracking param, nhận diện nền tảng.
    original_unshorten = UrlResolver.unshorten_url
    try:
        UrlResolver.unshorten_url = classmethod(
            lambda cls, url, max_hops=5, timeout=10: "https://www.youtube.com/watch?v=dQw4w9WgXcQ&si=AbCdEf"
        )
        res = resolver.resolve_url("https://youtu.be/dQw4w9WgXcQ")
    finally:
        UrlResolver.unshorten_url = original_unshorten

    assert res.is_shortened is True, "Phải nhận diện được link rút gọn"
    assert "watch?v=dQw4w9WgXcQ" in res.resolved_url, "Phải giải mã ra watch?v="
    assert "si=" not in res.resolved_url, "Phải loại bỏ tracking param 'si'"
    assert res.platform == "youtube", f"Kỳ vọng platform youtube, nhận được {res.platform}"
    log_ok(f"1.1a [offline] Logic giải mã youtu.be -> {res.resolved_url} (Platform: {res.platform})")

    # 1.1b Giải mã thật qua mạng — bỏ qua khi offline
    if HAS_NETWORK:
        res_net = resolver.resolve_url("https://youtu.be/dQw4w9WgXcQ")
        assert "dQw4w9WgXcQ" in res_net.resolved_url, "Redirect thật phải giữ được video id"
        assert res_net.platform == "youtube"
        log_ok(f"1.1b [mạng] Giải mã thật youtu.be -> {res_net.resolved_url}")
    else:
        SKIPPED.append("1.1b Giải mã link rút gọn qua mạng thật")
        log_skip("1.1b Giải mã link rút gọn qua mạng thật (không có kết nối)")

    # 1.2 Resolve @handle with expected platform (TikTok)
    res_tt = resolver.resolve_url("@mrbeast", expected_platform="tiktok")
    assert res_tt.resolved_url == "https://www.tiktok.com/@mrbeast"
    assert res_tt.platform == "tiktok"
    log_ok(f"1.2 Chuẩn hoá @mrbeast (tiktok) -> {res_tt.resolved_url}")

    # 1.3 Resolve @handle with expected platform (YouTube)
    res_yt = resolver.resolve_url("@mrbeast", expected_platform="youtube")
    assert res_yt.resolved_url == "https://www.youtube.com/@mrbeast"
    assert res_yt.platform == "youtube"
    log_ok(f"1.3 Chuẩn hoá @mrbeast (youtube) -> {res_yt.resolved_url}")

    # 1.4 Resolve @handle with expected platform (Instagram)
    res_ig = resolver.resolve_url("@nasa", expected_platform="instagram")
    assert res_ig.resolved_url == "https://www.instagram.com/nasa/"
    assert res_ig.platform == "instagram"
    log_ok(f"1.4 Chuẩn hoá @nasa (instagram) -> {res_ig.resolved_url}")

    # 1.5 Clean tracking parameters (fbclid, utm_source, etc.)
    dirty_url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&si=abcdef123456&utm_source=share"
    clean = resolver.clean_tracking_params(dirty_url)
    assert "si=" not in clean and "utm_source" not in clean, "Phải lọc sạch tracking params"
    log_ok(f"1.5 Làm sạch tracking query parameters: {clean}")

def test_sidecar_ipc_worker():
    log_section("2. KIỂM THỬ GIAO THỨC IPC SIDECAR (STDIN / STDOUT)")
    cli_path = os.path.join(PROJECT_ROOT, "core", "extractor_cli.py")

    proc = subprocess.Popen(
        [sys.executable, cli_path, "--stdin"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        # Gửi request resolve
        # URL đầy đủ (không rút gọn) → resolver không gọi mạng, nên bài kiểm thử
        # giao thức IPC này tất định kể cả khi offline.
        req = json.dumps({"id": "test_req_1", "action": "resolve", "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}) + "\n"
        proc.stdin.write(req)
        proc.stdin.flush()

        resp_line = proc.stdout.readline().strip()
        resp = json.loads(resp_line)
        assert resp.get("id") == "test_req_1", "ID phản hồi phải khớp"
        assert resp.get("success") is True, f"Request thất bại: {resp}"
        assert resp.get("data", {}).get("platform") == "youtube"
        log_ok(f"2.1 IPC Resolve Request thành công: {resp.get('data', {}).get('resolvedUrl')}")

        # Gửi request resolve handle
        req2 = json.dumps({"id": "test_req_2", "action": "resolve", "url": "@nasa", "expected": "x"}) + "\n"
        proc.stdin.write(req2)
        proc.stdin.flush()

        resp_line2 = proc.stdout.readline().strip()
        resp2 = json.loads(resp_line2)
        assert resp2.get("id") == "test_req_2"
        assert resp2.get("success") is True
        assert resp2.get("data", {}).get("resolvedUrl") == "https://x.com/nasa"
        log_ok(f"2.2 IPC Handle Resolution thành công: {resp2.get('data', {}).get('resolvedUrl')}")

    finally:
        proc.stdin.close()
        proc.terminate()
        proc.wait()

def test_media_extraction():
    log_section("3. KIỂM THỬ TRÍCH XUẤT THÔNG TIN MEDIA (PYTHON DISPATCHER)")
    dispatcher = MediaDispatcher()

    # 3.0 Định tuyến nền tảng — thuần logic, chạy được offline
    routing_cases = [
        ("https://www.youtube.com/watch?v=abc", True, False),
        ("https://x.com/nasa/status/1", False, True),
        ("https://www.instagram.com/p/Cxyz/", False, True),
        # Từng khớp NHẦM vì so khớp bằng substring "x.com/"
        ("https://www.vox.com/article/1", False, False),
        ("https://netflix.com/watch/1", False, False),
    ]
    for url, want_video, want_gallery in routing_cases:
        got_video = dispatcher.is_video_audio_platform(url)
        got_gallery = dispatcher.is_gallery_platform(url)
        assert got_video == want_video and got_gallery == want_gallery, (
            f"Định tuyến sai cho {url}: video={got_video} gallery={got_gallery}"
        )
    log_ok(f"3.0 [offline] Định tuyến nền tảng đúng trên {len(routing_cases)} trường hợp")

    if not HAS_NETWORK:
        SKIPPED.append("3.1 Trích xuất video thực tế")
        log_skip("3.1 Trích xuất video thực tế (không có kết nối)")
        return

    # 3.1 Trích xuất thật. Có mạng mà vẫn hỏng thì phải BÁO LỖI, không được nuốt
    # thành "bỏ qua" — trước đây `except Exception` giấu luôn cả lỗi thật.
    test_url = "https://www.youtube.com/watch?v=jNQXAC9IVRw" # "Me at the zoo" - first YouTube video
    t0 = time.time()
    meta = dispatcher.extract(test_url, browser="none")
    elapsed = time.time() - t0
    assert meta.title, "Thiếu tiêu đề media"
    assert meta.platform == "youtube", f"Kỳ vọng platform youtube, nhận {meta.platform}"
    assert len(meta.streams) > 0, "Phải có danh sách stream formats"
    log_ok(f"3.1 Trích xuất YouTube thành công trong {elapsed:.2f}s: '{meta.title}'")
    log_ok(f"    Tác giả: {meta.author} | Lượt xem: {meta.views} | Số formats: {len(meta.streams)}")

def main():
    start = time.time()
    print("\n🚀 BẮT ĐẦU KIỂM THỬ HỆ THỐNG DESKTOP NATIVE (CORE + RUST + CLI)")
    test_url_resolver_and_handles()
    test_sidecar_ipc_worker()
    test_media_extraction()
    total = time.time() - start
    print("\n" + "=" * 70)
    if SKIPPED:
        print(f"  ✅ CÁC BÀI KIỂM THỬ ĐÃ CHẠY ĐỀU ĐẠT ({total:.2f}s)")
        print(f"  ⏭️  Bỏ qua {len(SKIPPED)} bài cần mạng: " + "; ".join(SKIPPED))
    else:
        print(f"  🎉 TẤT CẢ CÁC BÀI KIỂM THỬ ĐỀU ĐẠT CHUẨN 100% ({total:.2f}s)")
    print("=" * 70 + "\n")

if __name__ == "__main__":
    main()
