#!/usr/bin/env python3
"""
Kiểm thử các trường hợp làm TREO, CRASH hoặc RÒ RỈ tài nguyên của engine bóc tách.

  1. Regex quay lui thảm hoạ: trang 1-2 MB kiểu React/Next.js (JSON trong thuộc tính,
     dấu nháy mã hoá &quot;), thẻ thiếu ">" hay object thiếu "}" làm từng regex chạy
     hơn 60 giây. Regex của Python giữ GIL nên CẢ engine đứng: các yêu cầu khác và
     cả lệnh Huỷ đều không được xử lý.
  2. Một dòng IPC hợp lệ JSON nhưng không phải object (vd. `[1,2,3]`) hay có "action"
     không phải chuỗi làm vòng đọc stdin ném lỗi → engine thoát, mọi yêu cầu đang chờ
     mất phản hồi.
  3. Chạy engine thật (tiến trình con, đúng chế độ `--stdin` mà Rust dùng) qua nhiều
     vòng yêu cầu: RAM, file descriptor, số luồng, tiến trình con, tệp tạm không được
     tăng dần; yêu cầu bị huỷ phải kill được tiến trình con đang chạy.
  4. Chromium headless của bộ đọc trang không được giữ context / tiến trình sau khi xong.

Mỗi ca dễ treo chạy trong tiến trình riêng có giới hạn thời gian để bản lỗi không làm
treo luôn pytest. Chạy offline (yt-dlp / gallery-dl giả, máy chủ HTTP cục bộ).
"""

import http.server
import json
import os
import queue
import stat
import subprocess
import sys
import threading
import time
import urllib.parse

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

CLI = os.path.join(PROJECT_ROOT, "core", "extractor_cli.py")

# Thời gian tối đa cho mỗi ca: bản đã sửa xong trong < 1 giây, bản lỗi cần hơn 60 giây.
HANG_LIMIT_SEC = 10

# Trang kiểu React/Next.js: JSON escape trong thuộc tính nên không có dấu nháy thật.
# Giữ dạng mã nguồn để dựng lại được trong tiến trình con (trang 1,6 MB không truyền
# qua dòng lệnh được).
ESCAPED_JSON_PAGE_SRC = (
    "ESCAPED_JSON_PAGE = ('<html><head><title>SSR</title></head><body><div data-props=\"{' + ''.join("
    "f'&quot;url&quot;:&quot;https://cdn.example.com/a/b/c/img_{i}.jpg?x=1&amp;y=2&quot;,' for i in range(20000))"
    " + '}\"></div><article><p>Bài viết</p><img src=\"/real.jpg\"></article></body></html>')\n"
)
_ns: dict = {}
exec(ESCAPED_JSON_PAGE_SRC, _ns)
ESCAPED_JSON_PAGE: str = _ns["ESCAPED_JSON_PAGE"]


def _run_isolated(code: str, timeout: float = HANG_LIMIT_SEC) -> float:
    """Chạy `code` trong tiến trình Python riêng; trả về số giây, ném lỗi nếu treo."""
    started = time.monotonic()
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            cwd=PROJECT_ROOT,
            env={**os.environ, "PYTHONPATH": PROJECT_ROOT},
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"Treo quá {timeout:.0f} giây (regex quay lui / vòng lặp không giới hạn)")
    assert proc.returncode == 0, proc.stderr[-2000:]
    return time.monotonic() - started


# ─────────────────────────────────────────────────────────────────────────────
# 1. Regex / parser không được treo trên trang lớn hay HTML độc
# ─────────────────────────────────────────────────────────────────────────────

class _OnePage(http.server.BaseHTTPRequestHandler):
    body = b""

    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(self.body)))
        self.end_headers()
        try:
            self.wfile.write(self.body)
        except (BrokenPipeError, ConnectionResetError):
            pass


def _serve(body: str):
    handler = type("H", (_OnePage,), {"body": body.encode("utf-8")})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/page"


def test_movie_page_scan_is_linear_on_ssr_pages():
    server, url = _serve(ESCAPED_JSON_PAGE)
    try:
        _run_isolated(
            "from core.extractors.movie import MovieExtractor\n"
            f"MovieExtractor()._scrape_stream_url({url!r})\n"
        )
    finally:
        server.shutdown()


def test_movie_page_still_finds_streams_after_the_rewrite():
    page = (
        '<html><body><div data-cfg="{&quot;file&quot;:&quot;https://cdn.example.com/v/master.m3u8?t=1&amp;s=2&quot;}"></div>'
        '<script>var p = {"src":"https:\\/\\/cdn.example.com\\/v\\/clip.mp4"};</script>'
        '<iframe width="1" src="//player.example.net/embed/abc"></iframe></body></html>'
    )
    server, url = _serve(page)
    try:
        from core.extractors.movie import MovieExtractor
        stream, embeds = MovieExtractor()._scrape_stream_url(url)
    finally:
        server.shutdown()
    # Trước đây URL lấy ra còn dính "&amp;" và cả đuôi "&quot;}" của thuộc tính
    assert stream == "https://cdn.example.com/v/master.m3u8?t=1&s=2"
    assert embeds == ["https://player.example.net/embed/abc"]


def test_sniffer_social_cdn_scan_is_linear():
    _run_isolated(
        "from core.extractors.stream_sniffer import social_cdn_images\n"
        + ESCAPED_JSON_PAGE_SRC
        + "page = ESCAPED_JSON_PAGE\n"
        "assert social_cdn_images(page) == []\n"
        "assert social_cdn_images('<img src=\"https://scontent.fbcdn.net/v/a_n.jpg?x=1&amp;y=2\">') == "
        "['https://scontent.fbcdn.net/v/a_n.jpg?x=1&y=2']\n"
    )


def test_story_html_scan_is_linear():
    _run_isolated(
        "from core.extractors.story import StoryExtractor as S\n"
        "html = '<html>' + ('require([\"a\"],function(){x=1;' * 3000) + ('<script>{' + 'a' * 200 + '\\n') * 5000 + '</html>'\n"
        "assert S._extract_from_embedded_json(html) == ([], [])\n"
        "bad = ('<source data-x=1 ' * 100000) + ('\"image\": {\"a\": 1, ' * 100000)\n"
        "assert S._extract_urls_from_html(bad, True) == []\n"
        "assert S._extract_urls_from_html(bad, False) == []\n"
        "ok = '<script>' + '{\"x\": 1, \"playable_url\": \"https:\\\\/\\\\/v.fbcdn.net\\\\/a.mp4\", \"pad\": \"' + 'p' * 120 + '\"}' + '</script>'\n"
        "v, _ = S._extract_from_embedded_json(ok)\n"
        "assert v == ['https://v.fbcdn.net/a.mp4'], v\n"
    )


def test_web_scraper_is_linear_on_tags_without_closing_bracket():
    server, url = _serve("<html><head>" + "<meta " * 200_000 + "<video " * 100_000 + "</head></html>")
    try:
        _run_isolated(
            "from core.extractors.web_scraper import WebScraperExtractor\n"
            "try:\n"
            f"    WebScraperExtractor().extract({url!r})\n"
            "except RuntimeError:\n"
            "    pass  # không có media là đúng, miễn là không treo\n"
        )
    finally:
        server.shutdown()


def test_page_media_scan_is_linear_on_ssr_and_hostile_pages():
    _run_isolated(
        "from core.extractors.page_media import scan_html, select_post_media\n"
        + ESCAPED_JSON_PAGE_SRC
        + "page = ESCAPED_JSON_PAGE\n"
        "items, _ = select_post_media(scan_html(page, 'https://example.com/p'))\n"
        "assert [i.url for i in items] == ['https://example.com/real.jpg'], items\n"
        "nasty = '<html>' + '<meta ' * 200000 + '<div>' * 50000 + '<article>' * 5000 + '<a href=\"x.jpg\">' * 20000\n"
        "select_post_media(scan_html(nasty, 'https://example.com/p'))\n"
    )


def test_big_page_scan_does_not_freeze_other_engine_threads():
    """Engine phục vụ 4 yêu cầu song song: một trang nặng không được chặn các luồng khác.

    Regex cũ chạy một mạch trong C và giữ GIL: luồng đọc stdin (nơi xử lý lệnh Huỷ)
    đứng im suốt vài giây tới vài phút.
    """
    from core.extractors.page_media import scan_html, select_post_media
    from core.extractors.stream_sniffer import social_cdn_images

    page = ESCAPED_JSON_PAGE * 2
    ticks = []
    stop = threading.Event()

    def ticker():
        while not stop.is_set():
            ticks.append(time.monotonic())
            time.sleep(0.01)

    t = threading.Thread(target=ticker, daemon=True)
    t.start()
    time.sleep(0.05)
    started = time.monotonic()
    select_post_media(scan_html(page, "https://example.com/p"))
    social_cdn_images(page)
    finished = time.monotonic()
    stop.set()
    t.join()
    during = [b - a for a, b in zip(ticks, ticks[1:]) if started <= a <= finished]
    assert during, "luồng phụ không chạy được chút nào trong lúc quét trang"
    assert max(during) < 1.0, f"luồng phụ bị chặn {max(during):.2f}s"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Engine thật: IPC sai định dạng, nhiều vòng yêu cầu, huỷ
# ─────────────────────────────────────────────────────────────────────────────

FAKE_YTDLP = r'''#!/usr/bin/env python3
import sys, time
if "--help" in sys.argv:
    print("Usage: yt-dlp"); sys.exit(0)
url = sys.argv[-1]
if "/slow" in url:
    time.sleep(60)
sys.stderr.write(f"ERROR: Unsupported URL: {url}\n"); sys.exit(1)
'''

FAKE_GALLERY_DL = r'''#!/usr/bin/env python3
import sys
sys.stderr.write(f"[gallery-dl][error] Unsupported URL '{sys.argv[-1]}'\n"); sys.exit(1)
'''

SITE_PAGES = {
    "/post": "<html><head><title>Bài</title></head><body><article><p>" + "chữ " * 80 + "</p>"
             + "".join(f'<img src="/img/{i}.jpg">' for i in range(12)) + "</article></body></html>",
    "/videos": "<html><body><article><p>" + "chữ " * 80 + '</p><video src="/a.mp4"></video>'
               '<iframe src="https://www.youtube.com/embed/dQw4w9WgXcQ"></iframe></article></body></html>',
    "/huge": "<html><body><article>" + "".join(f'<img src="/h/{i}.jpg">' for i in range(4000)) + "</article></body></html>",
    "/ssr": ESCAPED_JSON_PAGE,
}


class _Site(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        path = urllib.parse.urlsplit(self.path).path
        body = SITE_PAGES.get(path)
        status = 200 if body is not None else 404
        data = (body or "không có").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass


class _Engine:
    """Engine bóc tách thật ở chế độ `--stdin`, như Rust khởi động."""

    def __init__(self, tmp_path):
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir()
        self.tmpdir = tmp_path / "tmp"
        self.tmpdir.mkdir()
        env = {
            **os.environ,
            "PYTHONUNBUFFERED": "1",
            "YT_DLP_PATH": self._script(bin_dir / "yt-dlp", FAKE_YTDLP),
            "GALLERY_DL_PATH": self._script(bin_dir / "gallery-dl", FAKE_GALLERY_DL),
            # Mọi tệp tạm (cookie...) của engine nằm ở đây để đếm phần còn sót
            "TMPDIR": str(self.tmpdir),
            # Không đọc cookie thật của người chạy test
            "CRWL_CONFIG_DIR": str(tmp_path / "config"),
            "CRWL_SNIFFER_IDLE_SEC": "1",
        }
        self.proc = subprocess.Popen(
            [sys.executable, CLI, "--stdin"],
            cwd=PROJECT_ROOT,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self.responses: "queue.Queue[dict]" = queue.Queue()
        threading.Thread(target=self._read, daemon=True).start()

    @staticmethod
    def _script(path, body):
        path.write_text(body, encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return str(path)

    def _read(self):
        for line in self.proc.stdout:
            line = line.strip()
            if line:
                try:
                    self.responses.put(json.loads(line))
                except ValueError:
                    self.responses.put({"_raw": line})

    def send(self, obj):
        line = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()

    def wait_ids(self, ids, timeout=60):
        pending, got = set(ids), {}
        deadline = time.monotonic() + timeout
        while pending and time.monotonic() < deadline:
            try:
                resp = self.responses.get(timeout=0.5)
            except queue.Empty:
                assert self.proc.poll() is None, f"Engine đã thoát (mã {self.proc.returncode})"
                continue
            rid = resp.get("id")
            if rid in pending:
                pending.discard(rid)
                got[rid] = resp
        assert not pending, f"{len(pending)} yêu cầu không được trả lời: {sorted(pending)[:5]}"
        return got

    # ── số đo tài nguyên ─────────────────────────────────────────────────
    def rss_mb(self):
        with open(f"/proc/{self.proc.pid}/status") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024
        return 0.0

    def fds(self):
        return len(os.listdir(f"/proc/{self.proc.pid}/fd"))

    def threads(self):
        return len(os.listdir(f"/proc/{self.proc.pid}/task"))

    def children(self):
        out = []
        for pid in os.listdir("/proc"):
            if not pid.isdigit():
                continue
            try:
                with open(f"/proc/{pid}/stat") as fh:
                    fields = fh.read().rsplit(")", 1)[1].split()
            except OSError:
                continue
            if int(fields[1]) == self.proc.pid:
                out.append((int(pid), fields[0]))
        return out

    def close(self):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()


linux_only = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="đọc /proc của Linux")


@pytest.fixture
def engine(tmp_path):
    eng = _Engine(tmp_path)
    yield eng
    eng.close()


@pytest.fixture(scope="module")
def site():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


def test_malformed_ipc_lines_do_not_kill_the_engine(engine):
    for bad in ("[1,2,3]", '"chuoi"', "123", "null", "{khong-phai-json",
                '{"id":"a","action":5,"url":"x"}', '{"id":"b","action":"resolve","url":123}',
                '{"id":{"x":1},"action":"resolve","url":"https://example.com"}',
                '{"action":"cancel","target":{"a":1}}', "[" * 100_000 + "]" * 100_000,
                '{"id":"big","action":"resolve","url":"https://example.com/' + "a" * 2_000_000 + '"}'):
        engine.send(bad)
    engine.send({"id": "ok", "action": "resolve", "url": "https://example.com"})
    got = engine.wait_ids({"a", "b", "big", "ok"})
    assert got["ok"]["success"] is True
    assert got["a"]["success"] is False and got["b"]["success"] is False
    assert engine.proc.poll() is None


@linux_only
def test_engine_resources_stay_flat_over_many_requests(engine, site):
    def one_round(round_no):
        ids = []
        for i in range(12):
            rid = f"r{round_no}_{i}"
            path = ("/post", "/videos", "/huge", "/ssr", "/missing")[i % 5]
            engine.send({"id": rid, "action": "extract", "url": site + path, "browser": "none", "budget_sec": 60})
            ids.append(rid)
        for i in range(10):
            rid = f"q{round_no}_{i}"
            engine.send({"id": rid, "action": "resolve", "url": f"https://example.com/{round_no}/{i}"})
            ids.append(rid)
            # Huỷ một request không tồn tại: không được để lại gì trong sổ huỷ mãi mãi
            engine.send({"id": f"c{round_no}_{i}", "action": "cancel", "target": f"khong-co-{round_no}-{i}"})
        engine.send("không phải json")
        got = engine.wait_ids(ids, timeout=120)
        post = got[f"r{round_no}_0"]
        assert post["success"] and len(post["data"]["images"]) == 12, post
        huge = got[f"r{round_no}_2"]
        assert huge["success"] and len(huge["data"]["images"]) <= 500
        return got

    one_round(0)  # khởi động: pool luồng, import, cache
    time.sleep(0.5)
    fds0, thr0 = engine.fds(), engine.threads()
    for r in range(1, 13):
        one_round(r)
    time.sleep(0.5)
    rss1, fds1, thr1 = engine.rss_mb(), engine.fds(), engine.threads()

    assert fds1 <= fds0 + 2, f"File descriptor tăng {fds0} → {fds1}"
    assert thr1 <= thr0 + 1, f"Số luồng tăng {thr0} → {thr1}"
    # RSS của glibc chững ở đỉnh bộ nhớ khi 4 luồng cùng xử lý trang nặng (~120 MB),
    # không trả về hệ điều hành nên không so được theo từng vòng. Rò rỉ thật (giữ lại
    # HTML 1-3 MB mỗi lượt × 156 lượt) sẽ vượt xa mốc này; rò rỉ nhỏ do test
    # tracemalloc bên dưới bắt.
    assert rss1 < 250, f"RAM engine lên {rss1:.0f} MB sau 156 lượt bóc tách"
    zombies = [c for c in engine.children() if c[1] == "Z"]
    assert not zombies, f"Tiến trình con zombie: {zombies}"
    # Trang lỗi 404 được thử lại bằng Chromium (trang chặn bot hay trả lỗi giả): hồ sơ
    # tạm của Chromium chỉ được dọn khi nó tự đóng lúc rảnh (CRWL_SNIFFER_IDLE_SEC=1).
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and (engine.children() or any(engine.tmpdir.iterdir())):
        time.sleep(0.25)
    assert not engine.children(), f"Tiến trình con vẫn chạy khi engine rảnh: {engine.children()}"
    leftovers = sorted(p.name for p in engine.tmpdir.iterdir())
    assert not leftovers, f"Tệp tạm còn sót: {leftovers[:5]}"


def test_repeated_extractions_do_not_accumulate_python_objects(site):
    """Đếm chính xác đối tượng Python còn sống sau 80 lượt (không bị nhiễu bởi glibc)."""
    import gc
    import tracemalloc

    from core.extractors.page_media import PageMediaExtractor

    extractor = PageMediaExtractor(sniffer=None)

    def one_round():
        for path in ("/post", "/videos", "/huge", "/ssr", "/missing"):
            try:
                extractor.extract(site + path, browser="none")
            except Exception:
                pass

    for _ in range(3):
        one_round()
    gc.collect()
    tracemalloc.start()
    try:
        before = tracemalloc.take_snapshot()
        for _ in range(16):
            one_round()
        gc.collect()
        after = tracemalloc.take_snapshot()
    finally:
        tracemalloc.stop()
    grown = sum(stat.size_diff for stat in after.compare_to(before, "filename"))
    assert grown < 512 * 1024, f"Bộ nhớ Python tăng {grown / 1024:.0f} KB sau 80 lượt bóc tách"


@linux_only
def test_cancel_kills_the_running_child_and_answers_immediately(engine, site):
    engine.send({"id": "slow", "action": "extract", "url": site + "/slow", "browser": "none", "budget_sec": 120})

    def extracting():
        # Tiến trình con đầu tiên có thể là `yt-dlp --help` (dò tính năng) — huỷ lúc đó
        # thì chưa có gì để kill. Chờ đúng lượt bóc tách URL /slow.
        for pid, state in engine.children():
            try:
                with open(f"/proc/{pid}/cmdline", "rb") as fh:
                    if state != "Z" and b"/slow" in fh.read():
                        return True
            except OSError:
                continue
        return False

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not extracting():
        time.sleep(0.1)
    assert extracting(), "yt-dlp giả phải đang bóc tách /slow"
    started = time.monotonic()
    engine.send({"id": "cancel-1", "action": "cancel", "target": "slow"})
    got = engine.wait_ids({"slow", "cancel-1"}, timeout=20)
    assert time.monotonic() - started < 10
    assert got["slow"].get("cancelled") is True
    assert got["cancel-1"]["data"]["killedProcesses"] >= 1
    time.sleep(0.5)
    alive = [c for c in engine.children() if c[1] != "Z"]
    assert not alive, f"Tiến trình con vẫn chạy sau khi huỷ: {alive}"
    # Engine vẫn phục vụ tiếp
    engine.send({"id": "after", "action": "resolve", "url": "https://example.com"})
    assert engine.wait_ids({"after"})["after"]["success"]


@linux_only
def test_quick_requests_are_not_stuck_behind_long_extractions(engine, site):
    """4 lượt bóc tách dài chiếm hết hàng đợi: giải mã link / lấy cookie cho lượt tải
    người dùng vừa bấm vẫn phải trả lời ngay, không chờ tới khi chúng xong."""
    for i in range(4):
        engine.send({"id": f"busy{i}", "action": "extract", "url": f"{site}/slow?{i}", "browser": "none", "budget_sec": 120})
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and len([c for c in engine.children() if c[1] != "Z"]) < 4:
        time.sleep(0.1)
    started = time.monotonic()
    engine.send({"id": "res", "action": "resolve", "url": "https://example.com/a"})
    engine.send({"id": "ck", "action": "cookies", "url": "example.com", "domains": ["example.com"], "browser": "none"})
    got = engine.wait_ids({"res", "ck"}, timeout=20)
    assert time.monotonic() - started < 5, "Yêu cầu nhanh bị kẹt sau các lượt bóc tách dài"
    assert got["ck"]["data"] == {"path": None, "count": 0}
    for i in range(4):
        engine.send({"id": f"stop{i}", "action": "cancel", "target": f"busy{i}"})
    engine.wait_ids({f"busy{i}" for i in range(4)}, timeout=20)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Chromium headless không giữ context / tiến trình sau khi đọc trang
# ─────────────────────────────────────────────────────────────────────────────

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


def _chromium_descendants():
    me = os.getpid()
    parents = {}
    names = {}
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/stat") as fh:
                raw = fh.read()
            fields = raw.rsplit(")", 1)[1].split()
            parents[int(pid)] = int(fields[1])
            names[int(pid)] = raw[raw.find("(") + 1:raw.rfind(")")]
        except (OSError, ValueError):
            continue
    found = []
    for pid, name in names.items():
        p = parents.get(pid)
        depth = 0
        while p and p != me and depth < 20:
            p = parents.get(p)
            depth += 1
        if p == me and ("chrom" in name.lower() or "headless" in name.lower()):
            found.append(pid)
    return found


@linux_only
@pytest.mark.skipif(not _playwright_ready(), reason="cần Playwright + Chromium")
def test_page_reader_closes_contexts_and_chromium_when_idle(site, monkeypatch):
    from core.extractors import stream_sniffer
    from core.extractors.page_media import PageMediaExtractor
    from core.extractors.stream_sniffer import PlaywrightStreamSniffer, _PlaywrightWorkerThread

    monkeypatch.setattr(stream_sniffer, "get_browser_cookies_txt", lambda *a, **k: None)
    monkeypatch.setattr(_PlaywrightWorkerThread, "IDLE_SHUTDOWN_SEC", 1.0)
    monkeypatch.setattr(PlaywrightStreamSniffer, "COLLECT_MAX_SCROLL_STEPS", 4)
    sniffer = PlaywrightStreamSniffer()
    sniffer.close()
    try:
        extractor = PageMediaExtractor(sniffer)
        for _ in range(4):
            meta = extractor.extract(site + "/post", browser="none", force_dynamic=True)
            assert len(meta.images) == 12
            open_contexts = sniffer._worker.submit(lambda should_stop: len(sniffer._worker._browser.contexts), timeout=10)
            assert open_contexts == 0, f"Còn {open_contexts} context Chromium chưa đóng"
        assert _chromium_descendants(), "Chromium phải đang chạy ngay sau khi dùng"
        private_tmp = sniffer._worker._tmpdir
        assert private_tmp and os.path.isdir(private_tmp)
        deadline = time.monotonic() + 20
        while _chromium_descendants() and time.monotonic() < deadline:
            time.sleep(0.25)
        assert not _chromium_descendants(), "Chromium vẫn chạy sau khi rảnh"
        deadline = time.monotonic() + 5
        while os.path.exists(private_tmp) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not os.path.exists(private_tmp), "Thư mục tạm của Chromium (tệp bộ nhớ chia sẻ) phải bị xoá"
    finally:
        sniffer.close()


def test_stale_chromium_tmpdirs_from_a_killed_run_are_removed(tmp_path, monkeypatch):
    """App bị tắt ngang thì không kịp dọn: lần khởi động Chromium sau phải dọn giúp."""
    import tempfile as tempfile_module

    from core.extractors.stream_sniffer import _PlaywrightWorkerThread

    monkeypatch.setattr(tempfile_module, "tempdir", str(tmp_path))
    old = tmp_path / f"{_PlaywrightWorkerThread.TMP_PREFIX}old"
    fresh = tmp_path / f"{_PlaywrightWorkerThread.TMP_PREFIX}dang-dung"
    other = tmp_path / "khong-phai-cua-app"
    for d in (old, fresh, other):
        d.mkdir()
        (d / ".org.chromium.Chromium.x").write_bytes(b"\0" * 1024)
    stale = time.time() - _PlaywrightWorkerThread.STALE_TMP_SEC - 60
    os.utime(old, (stale, stale))
    os.utime(other, (stale, stale))

    worker = _PlaywrightWorkerThread.__new__(_PlaywrightWorkerThread)
    _PlaywrightWorkerThread._remove_stale_tmpdirs(worker)
    assert not old.exists()
    assert fresh.exists(), "Thư mục của Chromium đang chạy không được xoá"
    assert other.exists(), "Chỉ xoá thư mục do app tạo"
