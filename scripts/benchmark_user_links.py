#!/usr/bin/env python3
"""
Benchmark & Performance Test Script.
Tests each subsystem/tool 2 times on the specified user URLs:
1. https://www.youtube.com/live/nLdhKfuwfQM?si=9XmBlGXFK-oVLTP8
2. https://youtu.be/8qT4IxLpSBU?si=ioXYqYTfxESfTt6k
3. https://youtu.be/OeMmBMgqW-M?si=kE8Pxu-jZSYhFATr
4. https://www.youtube.com/@vinhxo69
"""

import sys
import os
import time
import json
import subprocess

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.resolver.url_resolver import UrlResolver
from core.extractors.ytdlp import YtDlpExtractor
from core.extractors.gallery import GalleryDlExtractor
from core.dispatcher import MediaDispatcher

TEST_URLS = [
    ("1. Live Stream", "https://www.youtube.com/live/nLdhKfuwfQM?si=9XmBlGXFK-oVLTP8"),
    ("2. Video 1 (Shortlink)", "https://youtu.be/8qT4IxLpSBU?si=ioXYqYTfxESfTt6k"),
    ("3. Video 2 (Shortlink)", "https://youtu.be/OeMmBMgqW-M?si=kE8Pxu-jZSYhFATr"),
    ("4. Channel Profile", "https://www.youtube.com/@vinhxo69"),
]

def format_time(t: float) -> str:
    if t < 1.0:
        return f"{t * 1000:.1f}ms"
    return f"{t:.2f}s"

def run_url_resolver_tests():
    print("\n" + "="*80)
    print("TOOL 1: URL RESOLVER (Giải mã redirect, tracking params, @handles)")
    print("="*80)
    resolver = UrlResolver()
    results = []

    for label, url in TEST_URLS:
        print(f"\n>> Testing: {label} ({url})")
        # Run 1
        t0 = time.time()
        res1 = resolver.resolve_url(url)
        t_run1 = time.time() - t0

        # Run 2
        t0 = time.time()
        res2 = resolver.resolve_url(url)
        t_run2 = time.time() - t0

        print(f"   [Lần 1]: {format_time(t_run1)} | Resolved: {res1.resolved_url} | Shortened: {res1.is_shortened}")
        print(f"   [Lần 2]: {format_time(t_run2)} | Resolved: {res2.resolved_url} | Shortened: {res2.is_shortened}")
        avg = (t_run1 + t_run2) / 2
        print(f"   -> Trung bình: {format_time(avg)}")
        results.append({
            "tool": "UrlResolver",
            "target": label,
            "run1": t_run1,
            "run2": t_run2,
            "avg": avg,
            "status": "PASS",
            "details": f"{res1.resolved_url} (shortened={res1.is_shortened})"
        })
    return results

def run_ytdlp_extractor_tests():
    print("\n" + "="*80)
    print("TOOL 2: YT-DLP EXTRACTOR (Trích xuất video metadata & danh sách kênh)")
    print("="*80)
    extractor = YtDlpExtractor()
    results = []

    for label, url in TEST_URLS:
        print(f"\n>> Testing: {label} ({url})")
        is_channel = "channel" in label.lower() or "@" in url

        # Run 1
        t0 = time.time()
        try:
            if is_channel:
                data1 = extractor.extract_playlist(url, limit=5, browser="none", timeout=45)
                details1 = f"Total: {data1.total_count}, Items: {len(data1.media)}, Name: {data1.name}"
            else:
                data1 = extractor.extract_metadata(url, browser="none", timeout=45)
                details1 = f"Title: '{data1.title[:45]}...' | Streams: {len(data1.streams)} | Duration: {data1.duration}"
            t_run1 = time.time() - t0
            status1 = "PASS"
        except Exception as e:
            t_run1 = time.time() - t0
            details1 = f"Error: {e}"
            status1 = "FAIL"

        # Run 2
        t0 = time.time()
        try:
            if is_channel:
                data2 = extractor.extract_playlist(url, limit=5, browser="none", timeout=45)
                details2 = f"Total: {data2.total_count}, Items: {len(data2.media)}, Name: {data2.name}"
            else:
                data2 = extractor.extract_metadata(url, browser="none", timeout=45)
                details2 = f"Title: '{data2.title[:45]}...' | Streams: {len(data2.streams)} | Duration: {data2.duration}"
            t_run2 = time.time() - t0
            status2 = "PASS"
        except Exception as e:
            t_run2 = time.time() - t0
            details2 = f"Error: {e}"
            status2 = "FAIL"

        print(f"   [Lần 1]: {format_time(t_run1)} | [{status1}] {details1}")
        print(f"   [Lần 2]: {format_time(t_run2)} | [{status2}] {details2}")
        avg = (t_run1 + t_run2) / 2
        print(f"   -> Trung bình: {format_time(avg)}")
        results.append({
            "tool": "YtDlpExtractor",
            "target": label,
            "run1": t_run1,
            "run2": t_run2,
            "avg": avg,
            "status": status1 if status1 == status2 else "PARTIAL",
            "details": details1
        })
    return results

def run_gallery_dl_tests():
    print("\n" + "="*80)
    print("TOOL 3: GALLERY-DL EXTRACTOR (Trích xuất gallery/album)")
    print("="*80)
    extractor = GalleryDlExtractor()
    results = []

    # Test how gallery-dl reacts to YouTube links (it should reject or only handle thumbnail)
    # Plus test on a native gallery target to verify its normal performance
    targets = [
        ("1. Live Stream", "https://www.youtube.com/live/nLdhKfuwfQM"),
        ("2. Video 1", "https://youtu.be/8qT4IxLpSBU"),
        ("3. Channel", "https://www.youtube.com/@vinhxo69"),
        ("4. Pinterest Pin (Gallery Target)", "https://www.pinterest.com/pin/123456789/"),
    ]

    for label, url in targets:
        print(f"\n>> Testing: {label} ({url})")
        # Run 1
        t0 = time.time()
        try:
            res1 = extractor.extract_gallery(url, browser="none", timeout=20)
            t_run1 = time.time() - t0
            details1 = f"Images: {len(res1.images)}, Title: {res1.title}"
            status1 = "PASS"
        except Exception as e:
            t_run1 = time.time() - t0
            details1 = f"Rejected (as expected for non-gallery): {str(e)[:60]}"
            status1 = "EXPECTED_REJECT" if "youtube" in url else "FAIL"

        # Run 2
        t0 = time.time()
        try:
            res2 = extractor.extract_gallery(url, browser="none", timeout=20)
            t_run2 = time.time() - t0
            details2 = f"Images: {len(res2.images)}, Title: {res2.title}"
            status2 = "PASS"
        except Exception as e:
            t_run2 = time.time() - t0
            details2 = f"Rejected: {str(e)[:60]}"
            status2 = "EXPECTED_REJECT" if "youtube" in url else "FAIL"

        print(f"   [Lần 1]: {format_time(t_run1)} | [{status1}] {details1}")
        print(f"   [Lần 2]: {format_time(t_run2)} | [{status2}] {details2}")
        avg = (t_run1 + t_run2) / 2
        print(f"   -> Trung bình: {format_time(avg)}")
        results.append({
            "tool": "GalleryDlExtractor",
            "target": label,
            "run1": t_run1,
            "run2": t_run2,
            "avg": avg,
            "status": status1,
            "details": details1
        })
    return results

def run_media_dispatcher_tests():
    print("\n" + "="*80)
    print("TOOL 4: MEDIA DISPATCHER (Phân luồng định tuyến thông minh & Fallback)")
    print("="*80)
    dispatcher = MediaDispatcher()
    results = []

    for label, url in TEST_URLS:
        print(f"\n>> Testing: {label} ({url})")
        is_channel = "channel" in label.lower() or "@" in url

        # Run 1
        t0 = time.time()
        try:
            if is_channel:
                data1 = dispatcher.crawl_profile(url, limit=5, browser="none")
                details1 = f"Crawl Items: {len(data1.media)}, Name: {data1.name}"
            else:
                data1 = dispatcher.extract(url, browser="none")
                details1 = f"Title: '{data1.title[:45]}...' | Platform: {data1.platform} | Type: {data1.type} | Streams: {len(data1.streams or [])}"
            t_run1 = time.time() - t0
            status1 = "PASS"
        except Exception as e:
            t_run1 = time.time() - t0
            details1 = f"Error: {e}"
            status1 = "FAIL"

        # Run 2
        t0 = time.time()
        try:
            if is_channel:
                data2 = dispatcher.crawl_profile(url, limit=5, browser="none")
                details2 = f"Crawl Items: {len(data2.media)}, Name: {data2.name}"
            else:
                data2 = dispatcher.extract(url, browser="none")
                details2 = f"Title: '{data2.title[:45]}...' | Platform: {data2.platform} | Type: {data2.type} | Streams: {len(data2.streams or [])}"
            t_run2 = time.time() - t0
            status2 = "PASS"
        except Exception as e:
            t_run2 = time.time() - t0
            details2 = f"Error: {e}"
            status2 = "FAIL"

        print(f"   [Lần 1]: {format_time(t_run1)} | [{status1}] {details1}")
        print(f"   [Lần 2]: {format_time(t_run2)} | [{status2}] {details2}")
        avg = (t_run1 + t_run2) / 2
        print(f"   -> Trung bình: {format_time(avg)}")
        results.append({
            "tool": "MediaDispatcher",
            "target": label,
            "run1": t_run1,
            "run2": t_run2,
            "avg": avg,
            "status": status1,
            "details": details1
        })
    return results

def run_ipc_sidecar_tests():
    print("\n" + "="*80)
    print("TOOL 5: IPC SIDECAR WORKER (Giao thức NDJSON qua stdin/stdout cho Desktop)")
    print("="*80)
    cli_path = os.path.join(PROJECT_ROOT, "core", "extractor_cli.py")
    proc = subprocess.Popen(
        [sys.executable, cli_path, "--stdin"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    results = []

    try:
        req_counter = 1
        for label, url in TEST_URLS:
            print(f"\n>> Testing IPC: {label}")
            is_channel = "channel" in label.lower() or "@" in url
            action = "crawl" if is_channel else "extract"

            # Run 1
            req_id_1 = f"bench_{req_counter}"
            req_counter += 1
            payload1 = {"id": req_id_1, "action": action, "url": url, "limit": 5, "browser": "none"}
            t0 = time.time()
            proc.stdin.write(json.dumps(payload1) + "\n")
            proc.stdin.flush()
            resp_line1 = proc.stdout.readline().strip()
            t_run1 = time.time() - t0
            r1 = json.loads(resp_line1) if resp_line1 else {}

            # Run 2
            req_id_2 = f"bench_{req_counter}"
            req_counter += 1
            payload2 = {"id": req_id_2, "action": action, "url": url, "limit": 5, "browser": "none"}
            t0 = time.time()
            proc.stdin.write(json.dumps(payload2) + "\n")
            proc.stdin.flush()
            resp_line2 = proc.stdout.readline().strip()
            t_run2 = time.time() - t0
            r2 = json.loads(resp_line2) if resp_line2 else {}

            status1 = "PASS" if r1.get("success") else "FAIL"
            status2 = "PASS" if r2.get("success") else "FAIL"
            details = f"IPC Response success={r1.get('success')}"
            print(f"   [Lần 1]: {format_time(t_run1)} | [{status1}]")
            print(f"   [Lần 2]: {format_time(t_run2)} | [{status2}]")
            avg = (t_run1 + t_run2) / 2
            print(f"   -> Trung bình: {format_time(avg)}")
            results.append({
                "tool": "IPCSidecarWorker",
                "target": label,
                "run1": t_run1,
                "run2": t_run2,
                "avg": avg,
                "status": status1,
                "details": details
            })
    finally:
        proc.stdin.close()
        proc.terminate()
        proc.wait()

    return results

def main():
    print("=" * 80)
    print("BẮT ĐẦU CHẠY TOÀN BỘ BENCHMARK HIỆU NĂNG CHO CÁC TOOLS & LINK CỦA USER")
    print("=" * 80)
    start_all = time.time()

    all_results = []
    all_results.extend(run_url_resolver_tests())
    all_results.extend(run_ytdlp_extractor_tests())
    all_results.extend(run_gallery_dl_tests())
    all_results.extend(run_media_dispatcher_tests())
    all_results.extend(run_ipc_sidecar_tests())

    total_time = time.time() - start_all
    print("\n" + "=" * 80)
    print(f"HOÀN THÀNH TẤT CẢ KIỂM THỬ TRONG {total_time:.2f}s")
    print("=" * 80)

    # Save results to json for report
    out_path = os.path.join(PROJECT_ROOT, "benchmark_results.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(all_results, f, ensure_ascii=False, indent=2)
    print(f"Đã lưu chi tiết kết quả benchmark vào: {out_path}")

if __name__ == "__main__":
    main()
