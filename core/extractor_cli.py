#!/usr/bin/env python3
"""
Social Media Extractor CLI & Sidecar Worker.
Command-line interface and JSON IPC protocol worker for Tauri Desktop App.

Usage:
  # Quick extract (defaults to extract action):
  python3 core/extractor_cli.py "https://www.youtube.com/watch?v=..."
  
  # Explicit extract:
  python3 core/extractor_cli.py extract "https://..." --browser edge --pretty
  
  # Crawl Profile / Channel:
  python3 core/extractor_cli.py crawl "https://www.tiktok.com/@tiktok" --limit 20
  
  # Resolve Shortened URL:
  python3 core/extractor_cli.py resolve "https://vt.tiktok.com/..."
  
  # Sidecar IPC Mode (for Tauri Rust subprocess):
  python3 core/extractor_cli.py --stdin
"""

import sys
import json
import argparse
import os
import threading
from concurrent.futures import ThreadPoolExecutor

# Ensure package root is in sys.path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(SCRIPT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.cancellation import (
    RequestCancelled,
    RequestTimedOut,
    begin_request,
    cancel_request,
    end_request,
)
from core.cookies.browser_cookies import export_cookies_for_domains
from core.dispatcher import MediaDispatcher
from core.extractors import ytdlp_runtime
from core.resolver.url_resolver import UrlResolver


# Nhiều luồng cùng ghi stdout sẽ làm hỏng giao thức JSON-per-line, nên cần khóa.
_STDOUT_LOCK = threading.Lock()

# Số request bóc tách chạy song song tối đa. Mỗi request tự sinh tiến trình
# yt-dlp/gallery-dl riêng nên cần chặn trên để không làm nghẽn máy.
MAX_CONCURRENT_REQUESTS = int(os.environ.get("CRWL_MAX_WORKERS", "4"))

# Yêu cầu ngắn mà người dùng đang chờ trực tiếp (giải mã link rút gọn khi gõ, cookie
# cho lượt tải vừa bấm) chạy ở hàng đợi riêng: nếu xếp chung với 4 lượt bóc tách dài
# (mỗi lượt tới vài phút), bấm "Tải" phải chờ cả phút mới bắt đầu.
QUICK_ACTIONS = frozenset({"resolve", "cookies"})


def output_json(data: dict, pretty: bool = False) -> None:
    indent = 2 if pretty else None
    payload = json.dumps(data, ensure_ascii=False, indent=indent)
    with _STDOUT_LOCK:
        sys.stdout.write(payload + "\n")
        sys.stdout.flush()


def _ipc_id(value):
    """Mã request dùng làm khoá trong sổ huỷ: phải hash được (Rust luôn gửi chuỗi)."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def handle_request(dispatcher: MediaDispatcher, resolver: UrlResolver, req: dict) -> dict:
    """Xử lý một request IPC, luôn trả về dict response (không ném exception)."""
    req_id = _ipc_id(req.get("id"))
    action = str(req.get("action") or "extract").lower()
    url = req.get("url")
    url = url.strip() if isinstance(url, str) else ""

    if not url:
        return {"id": req_id, "success": False, "error": "Missing 'url' parameter"}

    try:
        budget = float(req.get("budget_sec") or 0) or None
    except (TypeError, ValueError):
        budget = None
    begin_request(req_id, budget)
    try:
        if action == "extract":
            res = dispatcher.extract(url, browser=req.get("browser"))
            return {"id": req_id, "success": True, "data": res.to_dict()}

        if action == "crawl":
            limit = int(req.get("limit", 50) or 0)
            media_type = req.get("media_type") or req.get("mediaType") or "all"
            range_start = req.get("range_start") or req.get("rangeStart")
            range_end = req.get("range_end") or req.get("rangeEnd")
            res = dispatcher.crawl_profile(
                url,
                limit=limit,
                media_type=media_type,
                platform_hint=req.get("platform"),
                browser=req.get("browser"),
                range_start=int(range_start) if range_start else None,
                range_end=int(range_end) if range_end else None,
            )
            return {"id": req_id, "success": True, "data": res.to_dict()}

        if action == "resolve":
            expected = req.get("expected") or req.get("expectedPlatform")
            res = resolver.resolve_url(url, expected_platform=expected)
            return {"id": req_id, "success": True, "data": res.to_dict()}

        if action == "cookies":
            # Tệp cookie tạm cho bước tải bằng curl (Rust xoá sau khi tải xong)
            domains = req.get("domains")
            domains = [d for d in domains if isinstance(d, str)] if isinstance(domains, list) else [url]
            path, count = export_cookies_for_domains(domains, req.get("browser"))
            return {"id": req_id, "success": True, "data": {"path": path, "count": count}}

        return {"id": req_id, "success": False, "error": f"Unknown action: '{action}'"}
    except RequestTimedOut:
        sys.stderr.write(f"[Sidecar] Request {req_id} dừng vì hết ngân sách {budget}s\n")
        sys.stderr.flush()
        hint = " Hãy giảm số lượng cần quét rồi thử lại." if action == "crawl" else ""
        return {
            "id": req_id,
            "success": False,
            "error": f"Timeout: quá {int(budget or 0)} giây mà chưa bóc tách xong.{hint}",
        }
    except RequestCancelled:
        sys.stderr.write(f"[Sidecar] Request {req_id} đã bị huỷ theo yêu cầu người dùng\n")
        sys.stderr.flush()
        return {
            "id": req_id,
            "success": False,
            "cancelled": True,
            "error": "Đã huỷ theo yêu cầu.",
        }
    except Exception as e:
        sys.stderr.write(f"[Sidecar:ERROR] Request {req_id} failed: {e}\n")
        sys.stderr.flush()
        return {"id": req_id, "success": False, "error": str(e)}
    finally:
        end_request(req_id)


def _warm_up(dispatcher: MediaDispatcher) -> None:
    try:
        ytdlp = dispatcher.ytdlp
        if ytdlp.is_available():
            ytdlp_runtime.prepare(ytdlp.binary_path)
            ytdlp.supports_js_runtimes()
    except Exception as e:  # chỉ là tối ưu — lỗi ở đây không được ảnh hưởng engine
        sys.stderr.write(f"[Sidecar] Bỏ qua bước chuẩn bị sẵn: {e}\n")
        sys.stderr.flush()


def run_stdin_worker(dispatcher: MediaDispatcher, resolver: UrlResolver) -> None:
    """Chế độ Sidecar IPC: đọc JSON qua stdin, xử lý SONG SONG, in JSON qua stdout.

    Trước đây mỗi request chạy tuần tự trong vòng lặp đọc stdin, nên khi UI gửi
    nhiều liên kết cùng lúc thì các request sau phải xếp hàng và bị Rust timeout.
    """
    sys.stderr.write(
        f"[Sidecar] Extractor CLI worker started (IPC mode, {MAX_CONCURRENT_REQUESTS} luồng song song)\n"
    )
    sys.stderr.flush()

    pool = ThreadPoolExecutor(
        max_workers=MAX_CONCURRENT_REQUESTS,
        thread_name_prefix="crwl-req",
    )
    quick_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="crwl-quick")

    # Chuẩn bị sẵn ở nền để lượt phân tích / tải ĐẦU TIÊN không phải chờ: bản giải nén
    # của yt-dlp zipapp và phép thử `--js-runtimes` (trước đây ~0,75 giây cộng vào lượt
    # phân tích đầu tiên sau mỗi lần mở app).
    ytdlp_runtime.enable_background_prepare()
    threading.Thread(target=_warm_up, args=(dispatcher,), name="crwl-warmup", daemon=True).start()

    def process(raw_req: dict) -> None:
        # Luôn phải trả lời: một request rơi mất (lỗi ngoài handle_request, dữ liệu
        # không serialize được...) khiến Rust chờ tới hết timeout mới biết.
        try:
            output_json(handle_request(dispatcher, resolver, raw_req))
        except BaseException as e:  # noqa: BLE001 — luồng pool, không được chết im lặng
            req_id = _ipc_id(raw_req.get("id"))
            sys.stderr.write(f"[Sidecar:ERROR] Không gửi được phản hồi cho {req_id}: {e}\n")
            sys.stderr.flush()
            try:
                output_json({"id": req_id, "success": False, "error": f"Lỗi nội bộ engine bóc tách: {e}"})
            except Exception:
                pass  # stdout đã đóng (Rust thoát) — không còn ai để báo

    try:
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue

            try:
                req = json.loads(line)
            except (json.JSONDecodeError, RecursionError) as e:
                output_json({"success": False, "error": f"Invalid JSON input: {e}"})
                continue

            # Một dòng JSON hợp lệ nhưng không phải object (mảng, chuỗi, số) hay có
            # "action" không phải chuỗi từng làm vòng đọc ném AttributeError → CẢ engine
            # thoát, mọi request đang chờ phía sau mất phản hồi.
            if not isinstance(req, dict):
                output_json({"success": False, "error": "Yêu cầu IPC phải là một JSON object"})
                continue

            # Lệnh huỷ phải xử lý NGAY tại vòng đọc. Nếu đẩy vào pool, nó sẽ xếp
            # hàng sau đúng những request đang bận mà nó cần huỷ → không bao giờ chạy.
            if str(req.get("action") or "").lower() == "cancel":
                target = str(req.get("target") or req.get("target_id") or "").strip()
                killed = cancel_request(target) if target else 0
                sys.stderr.write(f"[Sidecar] Huỷ request '{target}': đã kill {killed} tiến trình con\n")
                sys.stderr.flush()
                output_json({
                    "id": req.get("id"),
                    "success": True,
                    "data": {"target": target, "killedProcesses": killed},
                })
                continue

            action = str(req.get("action") or "extract").lower()
            (quick_pool if action in QUICK_ACTIONS else pool).submit(process, req)
    finally:
        pool.shutdown(wait=True)
        quick_pool.shutdown(wait=True)


def main() -> None:
    # Trường hợp gọi trực tiếp không có cờ subcommand: python3 extractor_cli.py "https://..."
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-") and sys.argv[1] not in ("extract", "crawl", "resolve"):
        url = sys.argv[1]
        browser = None
        pretty = "--pretty" in sys.argv or "-p" in sys.argv
        if "--browser" in sys.argv:
            b_idx = sys.argv.index("--browser")
            if b_idx + 1 < len(sys.argv):
                browser = sys.argv[b_idx + 1]

        dispatcher = MediaDispatcher()
        try:
            res = dispatcher.extract(url, browser=browser)
            output_json(res.to_dict(), pretty=pretty)
            sys.exit(0)
        except Exception as e:
            output_json({"success": False, "error": str(e)}, pretty=pretty)
            sys.exit(1)

    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument("--pretty", "-p", action="store_true", help="In JSON có định dạng thụt đầu dòng (indent 2)")

    parser = argparse.ArgumentParser(
        description="Social Media Extractor CLI Engine (Tauri / Terminal)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[common_parser],
    )

    parser.add_argument("--stdin", action="store_true", help="Chạy ở chế độ IPC Sidecar (đọc stdin, in stdout)")

    subparsers = parser.add_subparsers(dest="command", help="Lệnh thực hiện")

    # Command: extract
    extract_parser = subparsers.add_parser("extract", parents=[common_parser], help="Trích xuất thông tin media từ 1 URL")
    extract_parser.add_argument("url", type=str, help="URL bài viết / video / album")
    extract_parser.add_argument("--browser", "-b", type=str, default=None, help="Tên trình duyệt để lấy cookie (edge, chrome, firefox, none)")

    # Command: crawl
    crawl_parser = subparsers.add_parser("crawl", parents=[common_parser], help="Quét toàn bộ bài viết từ hồ sơ/kênh/playlist")
    crawl_parser.add_argument("url", type=str, help="URL tài khoản, playlist hoặc @username")
    crawl_parser.add_argument("--limit", "-l", type=int, default=50, help="Số lượng tối đa cần quét")
    crawl_parser.add_argument("--type", "-t", choices=["all", "video", "image"], default="all", help="Loại media cần lọc")
    crawl_parser.add_argument("--platform", type=str, default=None, help="Gợi ý nền tảng (tiktok, instagram, youtube...)")
    crawl_parser.add_argument("--browser", "-b", type=str, default=None, help="Tên trình duyệt lấy cookie")
    crawl_parser.add_argument("--range-start", type=int, default=None, help="Chỉ số bắt đầu")
    crawl_parser.add_argument("--range-end", type=int, default=None, help="Chỉ số kết thúc")

    # Command: resolve
    resolve_parser = subparsers.add_parser("resolve", parents=[common_parser], help="Giải mã URL rút gọn và chuẩn hóa platform")
    resolve_parser.add_argument("url", type=str, help="URL rút gọn hoặc cần phân tích")
    resolve_parser.add_argument("--expected", "-e", type=str, default=None, help="Nền tảng kỳ vọng")

    args = parser.parse_args()

    dispatcher = MediaDispatcher()
    resolver = UrlResolver()

    if args.stdin:
        run_stdin_worker(dispatcher, resolver)
        return

    if not args.command:
        parser.print_help()
        sys.exit(0)

    try:
        if args.command == "extract":
            res = dispatcher.extract(args.url, browser=args.browser)
            output_json(res.to_dict(), pretty=args.pretty)
        elif args.command == "crawl":
            res = dispatcher.crawl_profile(
                args.url,
                limit=args.limit,
                media_type=args.type,
                platform_hint=args.platform,
                browser=args.browser,
                range_start=args.range_start,
                range_end=args.range_end,
            )
            output_json(res.to_dict(), pretty=args.pretty)
        elif args.command == "resolve":
            res = resolver.resolve_url(args.url, expected_platform=args.expected)
            output_json(res.to_dict(), pretty=args.pretty)
    except Exception as e:
        output_json({"success": False, "error": str(e)}, pretty=args.pretty)
        sys.exit(1)


if __name__ == "__main__":
    main()
