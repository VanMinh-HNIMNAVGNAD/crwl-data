#!/usr/bin/env python3
"""
Kiểm thử các thay đổi chính sách / kỹ thuật của nền tảng (cuối 10/2026):

  1. Reddit chặn hẳn khách gọi API .json ("blocked by network security"); nhà mạng /
     router còn có thể chặn DNS www.reddit.com (trỏ về 127.0.0.1).
  2. Instagram: gallery-dl nhớ kết quả tra tài khoản vĩnh viễn — bản ghi cũ thiếu
     "username" làm MỌI lượt quét báo "Requested user could not be found"; truy vấn mục
     Bài viết thiếu biến bắt buộc mới (mục Reels vẫn chạy); khách không còn xem được
     danh sách bài; API web_profile_info trả 429 (Story Instagram).
  3. Facebook: dữ liệu video không còn nằm trong HTML ("Cannot parse data") — khi đã
     đăng nhập thì không được báo "cần đăng nhập". Từ 24/10 (yt-dlp #17906) yt-dlp chỉ
     đọc được khi giả dạng Firefox (`--impersonate firefox`, cần curl_cffi) — mục 7.
  4. Threads chuyển sang threads.com; Bilibili có đường dẫn /en/video/.
  5. Firefox 142+ lưu hạn cookie bằng mili-giây (Chromium chạy ngầm từ chối cả lô).
  6. Avatar / ảnh bìa trang cá nhân không bao giờ là "ảnh trong bài".

Chạy offline:  python3 -m pytest -q scripts/test_platform_policy_2026_10.py
"""

import json
import os
import socket
import sqlite3
import stat
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import core.dispatcher
from core.cookies import browser_cookies
from core.cookies.browser_cookies import BrowserCookieExporter, expiry_seconds
from core.dispatcher import MediaDispatcher
from core.extractors.gallery import GalleryDlExtractor, InstagramApiChanged, PlatformBlocked
from core.extractors.page_media import _decor_or_junk, load_cookie_jar
from core.extractors.story import StoryExtractor
from core.extractors.stream_sniffer import PlaywrightStreamSniffer
from core.extractors.web_scraper import WebScraperExtractor
from core.extractors.ytdlp import ImpersonationUnavailable, YtDlpExtractor, impersonation_target_for
from core.models import CrawlMediaItem, MediaImage, MediaMetadata, ProfileCrawlResult
from core.resolver import url_resolver
from core.resolver.url_resolver import UrlResolver, dns_blocked_host


def _row(host, name, value):
    return (host, "TRUE", "/", "TRUE", 0, name, value)


BROWSERS = {}


@pytest.fixture
def browsers(monkeypatch, tmp_path):
    """Kho cookie giả: mỗi bài kiểm thử tự đặt trình duyệt nào đăng nhập trang nào."""
    BROWSERS.clear()
    monkeypatch.setenv("CRWL_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(BrowserCookieExporter, "_raw_cookies_cached", lambda self, b: BROWSERS.get(b, []))
    browser_cookies.clear_cookie_cache()
    yield BROWSERS
    browser_cookies.clear_cookie_cache()


# ─────────────────────────────────────────────────────────────────────────────
# 1. DNS chặn tên miền / Reddit chặn khách
# ─────────────────────────────────────────────────────────────────────────────

def _fake_dns(monkeypatch, table):
    def getaddrinfo(host, port, *args, **kwargs):
        if host not in table:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return [(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port))
                for ip in table[host]]

    monkeypatch.setattr(url_resolver.socket, "getaddrinfo", getaddrinfo)
    for key in ("CRWL_PROXY", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(key, raising=False)


def test_domain_resolving_to_loopback_is_reported_as_dns_block(monkeypatch):
    # Đúng tình trạng mạng nhà người dùng: router trỏ www.reddit.com về ::1 / 127.0.0.1
    _fake_dns(monkeypatch, {
        "www.reddit.com": ["::1", "127.0.0.1"],
        "i.redd.it": ["151.101.77.140"],
        "blocked.example.com": ["0.0.0.0"],
    })
    assert dns_blocked_host("https://www.reddit.com/r/aww/") == "www.reddit.com"
    assert dns_blocked_host("https://blocked.example.com/a") == "blocked.example.com"
    assert dns_blocked_host("https://i.redd.it/abc.jpg") is None
    # Không phân giải được: để bước tải báo lỗi mạng như thường
    assert dns_blocked_host("https://nxdomain.example/x") is None
    # Máy nội bộ / IP gõ thẳng: người dùng cố ý
    assert dns_blocked_host("http://127.0.0.1:8080/video.mp4") is None
    assert dns_blocked_host("http://localhost:8080/a") is None
    assert dns_blocked_host("http://nas.local/a.mp4") is None


def test_dns_check_is_skipped_behind_a_proxy(monkeypatch):
    _fake_dns(monkeypatch, {"www.reddit.com": ["127.0.0.1"]})
    monkeypatch.setenv("CRWL_PROXY", "socks5://127.0.0.1:1080")
    assert dns_blocked_host("https://www.reddit.com/") is None


def test_dispatcher_stops_at_a_dns_blocked_domain(monkeypatch):
    d = MediaDispatcher()
    monkeypatch.setattr(core.dispatcher, "dns_blocked_host", lambda url: "www.reddit.com")
    for engine in (d.gallery.extract_gallery, d.ytdlp.extract_metadata, d.page_media.extract):
        monkeypatch.setattr(engine.__self__, engine.__name__, lambda *a, **k: pytest.fail("không được chạy engine"))
    with pytest.raises(RuntimeError) as info:
        d.extract("https://www.reddit.com/r/videos/comments/6rrwyj/x/", browser="none")
    assert "đang chặn www.reddit.com" in str(info.value) and "1.1.1.1" in str(info.value)
    with pytest.raises(RuntimeError) as info:
        d.crawl_profile("https://www.reddit.com/r/aww/", limit=3, browser="none")
    assert "đang chặn www.reddit.com" in str(info.value)


def _fake_gallery_dl(tmp_path, stdout, stderr="", code=0):
    script = tmp_path / "gallery-dl"
    script.write_text(
        "#!/usr/bin/env python3\nimport sys\n"
        f"print({stdout!r})\nsys.stderr.write({stderr!r})\nsys.exit({code})\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    ext = GalleryDlExtractor()
    ext.binary_path = str(script)
    return ext


REDDIT_POST = "https://www.reddit.com/r/videos/comments/6rrwyj/that_small_heart_attack/"
BLOCKED_JSON = '[[-1, {"error": "AbortExtraction", "message": "\\"You\'ve been blocked by network security.\\""}]]'


def test_reddit_guest_block_names_the_new_policy(tmp_path, browsers):
    ext = _fake_gallery_dl(tmp_path, BLOCKED_JSON)
    with pytest.raises(PlatformBlocked) as info:
        ext.extract_gallery(REDDIT_POST, browser="firefox")
    msg = str(info.value)
    assert "chặn truy cập khi chưa đăng nhập" in msg and "đăng nhập Reddit" in msg
    assert "network security" in msg  # lỗi gốc vẫn nằm trong phần "Chi tiết"


def test_reddit_block_with_a_session_points_to_an_expired_login(tmp_path, browsers):
    browsers["firefox"] = [_row(".reddit.com", "reddit_session", "r")]
    ext = _fake_gallery_dl(tmp_path, BLOCKED_JSON)
    with pytest.raises(RuntimeError) as info:
        ext.extract_gallery(REDDIT_POST, browser="firefox")
    assert "hết hạn" in str(info.value)
    # Có phiên đăng nhập thì dispatcher vẫn được thử đọc trang (Chromium kèm cookie)
    assert not isinstance(info.value, PlatformBlocked)


def test_reddit_crawl_keyerror_is_reported_as_a_block(tmp_path, browsers):
    # Quét r/aww khi bị chặn: gallery-dl nhận trang chặn thay cho JSON → KeyError 'data'
    ext = _fake_gallery_dl(tmp_path, '[[-1, {"error": "KeyError", "message": "\'data\'"}]]')
    with pytest.raises(PlatformBlocked) as info:
        ext.crawl_profile("https://www.reddit.com/r/aww/", limit=3, browser="none")
    assert "Reddit" in str(info.value) and "KeyError" in str(info.value)


def test_dispatcher_does_not_fall_back_after_a_platform_block(monkeypatch):
    d = MediaDispatcher()
    monkeypatch.setattr(d.resolver, "resolve_url", lambda url, expected_platform=None: type(
        "R", (), {"is_shortened": False, "resolved_url": url})())

    def blocked(url, browser=None, timeout=30):
        raise PlatformBlocked("Reddit đã chặn truy cập khi chưa đăng nhập (chính sách mới từ 10/2026).")

    monkeypatch.setattr(d.gallery, "extract_gallery", blocked)
    for name in ("ytdlp", "page_media", "web_scraper"):
        engine = getattr(d, name)
        attr = "extract_metadata" if name == "ytdlp" else "extract"
        monkeypatch.setattr(engine, attr, lambda *a, **k: pytest.fail("không được thử engine khác"))
    with pytest.raises(PlatformBlocked):
        d.extract(REDDIT_POST, browser="none")
    # Link video v.redd.it đi nhánh nền tảng video (yt-dlp trước) cũng dừng ở lỗi chặn
    monkeypatch.setattr(d.ytdlp, "extract_metadata", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("ERROR: [Reddit] x: Account authentication is required")))
    with pytest.raises(PlatformBlocked):
        d.extract("https://v.redd.it/abcdef123", browser="none")


# ─────────────────────────────────────────────────────────────────────────────
# 2. Instagram
# ─────────────────────────────────────────────────────────────────────────────

def test_instagram_runs_bypass_gallery_dl_persistent_user_cache(browsers):
    ext = GalleryDlExtractor()
    args, _ = ext.get_base_args("https://www.instagram.com/natgeo/posts/", browser="none")
    assert "user-cache=memory" in args
    args, _ = ext.get_base_args("https://x.com/natgeo/media", browser="none")
    assert "user-cache=memory" not in args


IG_POSTS = "https://www.instagram.com/natgeo/posts/"
IG_API_ERROR = "[instagram][error] Failed to load API data (KeyError: 'data')\n"


def test_instagram_api_change_while_logged_in_is_not_a_login_error(tmp_path, browsers):
    browsers["edge"] = [_row(".instagram.com", "sessionid", "s")]
    # gallery-dl mới thoát mã lỗi khi GraphQL từ chối truy vấn
    ext = _fake_gallery_dl(tmp_path, "", stderr=IG_API_ERROR, code=1)
    with pytest.raises(InstagramApiChanged) as info:
        ext.crawl_profile(IG_POSTS, limit=3, browser="edge")
    assert "Bản mới nhất" in str(info.value) and "Failed to load API data" in str(info.value)


def test_instagram_profile_without_login_asks_to_log_in(tmp_path, browsers):
    ext = _fake_gallery_dl(
        tmp_path, '[[-1, {"error": "NotFoundError", "message": "Requested user could not be found"}]]')
    with pytest.raises(PlatformBlocked) as info:
        ext._raise_probe_error(IG_POSTS, "none")
    assert "Instagram yêu cầu đăng nhập" in str(info.value)
    # Quét xong mà rỗng: cũng là lỗi đăng nhập khi chưa có phiên
    assert isinstance(ext.empty_crawl_error(IG_POSTS, "none"), PlatformBlocked)
    browsers["edge"] = [_row(".instagram.com", "sessionid", "s")]
    assert "Bản mới nhất" in str(ext.empty_crawl_error(IG_POSTS, "edge"))
    assert "không tìm thấy media" in str(ext.empty_crawl_error("https://x.com/a/media", "edge"))


def _crawl_dispatcher(monkeypatch, behaviours):
    d = MediaDispatcher()
    monkeypatch.setattr(d.resolver, "resolve_url", lambda url, expected_platform=None: type(
        "R", (), {"is_shortened": False, "resolved_url": url})())
    calls = []

    def crawl(url, **kwargs):
        calls.append(url)
        outcome = behaviours[url]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(d.gallery, "crawl_profile", crawl)
    return d, calls


def _reels_result():
    return ProfileCrawlResult(platform="instagram", name="natgeo", stats="Đã quét 1 tệp", media=[
        CrawlMediaItem(id=1, type="video", url="https://cdn/reel.mp4", post_url="https://www.instagram.com/reel/A/"),
    ])


def test_posts_tab_api_change_falls_back_to_reels_with_a_notice(monkeypatch):
    d, calls = _crawl_dispatcher(monkeypatch, {
        IG_POSTS: InstagramApiChanged("Không quét được tài khoản Instagram này."),
        "https://www.instagram.com/natgeo/reels/": _reels_result(),
    })
    res = d.crawl_profile("https://www.instagram.com/natgeo/", limit=3, media_type="all", browser="edge")
    assert calls == [IG_POSTS, "https://www.instagram.com/natgeo/reels/"]
    assert len(res.media) == 1 and "chỉ quét được Reels" in res.notice
    assert res.to_dict()["notice"] == res.notice


def test_photos_only_crawl_does_not_pretend_reels_are_photos(monkeypatch):
    photos = "https://www.instagram.com/natgeo/photos/"
    d, calls = _crawl_dispatcher(monkeypatch, {photos: InstagramApiChanged("Không quét được tài khoản Instagram này.")})
    with pytest.raises(InstagramApiChanged):
        d.crawl_profile("https://www.instagram.com/natgeo/", limit=3, media_type="image", browser="edge")
    assert calls == [photos]


def test_story_user_id_is_read_from_the_profile_page_first(monkeypatch):
    s = StoryExtractor()
    seen = []

    def http_get(url, cookies=None, headers=None):
        seen.append(url)
        if url == "https://www.instagram.com/natgeo/":
            return '<script>{"props":{"profile_id":"787132","params":{"username":"natgeo"}}}</script>'
        pytest.fail("web_profile_info trả 429 từ 10/2026 — không được gọi khi trang hồ sơ đã có ID")

    monkeypatch.setattr(s, "_http_get", http_get)
    assert s._ig_get_user_id("natgeo", {"sessionid": "s"}) == "787132"
    assert seen == ["https://www.instagram.com/natgeo/"]


def test_crawl_result_without_notice_keeps_the_old_shape():
    assert "notice" not in ProfileCrawlResult(platform="x").to_dict()


# ─────────────────────────────────────────────────────────────────────────────
# 3. Facebook / Bilibili: trang đổi cấu trúc ≠ cần đăng nhập
# ─────────────────────────────────────────────────────────────────────────────

FB_VIDEO = "https://www.facebook.com/radiokicksfm/videos/3676516585958356/"
CANNOT_PARSE = ("yt-dlp extract thất bại: ERROR: [facebook] 3676516585958356: Cannot parse data; please report "
                "this issue on  https://github.com/yt-dlp/yt-dlp/issues?q= , filling out the appropriate issue template")


def _video_dispatcher(monkeypatch, ytdlp_error):
    d = MediaDispatcher()
    monkeypatch.setattr(d.resolver, "resolve_url", lambda url, expected_platform=None: type(
        "R", (), {"is_shortened": False, "resolved_url": url})())
    monkeypatch.setattr(d.ytdlp, "extract_metadata", lambda *a, **k: (_ for _ in ()).throw(RuntimeError(ytdlp_error)))
    monkeypatch.setattr(d.gallery, "extract_gallery", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("gallery-dl extract thất bại: Unsupported URL")))
    monkeypatch.setattr(d.web_scraper, "extract", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("không có video")))
    return d


def test_facebook_cannot_parse_data_while_logged_in_is_a_site_change(monkeypatch, browsers):
    browsers["firefox"] = [_row(".facebook.com", "c_user", "1000")]
    d = _video_dispatcher(monkeypatch, CANNOT_PARSE)
    with pytest.raises(RuntimeError) as info:
        d.extract(FB_VIDEO, browser="auto")
    msg = str(info.value)
    assert msg.startswith("Facebook vừa thay đổi") and "Bản mới nhất" in msg
    assert "yêu cầu đăng nhập" not in msg


def test_facebook_cannot_parse_data_as_guest_still_asks_to_log_in(monkeypatch, browsers):
    d = _video_dispatcher(monkeypatch, CANNOT_PARSE)
    with pytest.raises(RuntimeError) as info:
        d.extract(FB_VIDEO, browser="auto")
    assert "Nội dung Facebook yêu cầu đăng nhập" in str(info.value)


def test_broken_extractor_on_other_sites_is_reported_as_a_site_change(monkeypatch, browsers):
    d = _video_dispatcher(monkeypatch, "ERROR: [BiliBili] 13x41117TL: Unable to extract initial state; please "
                                       "report this issue on  https://github.com/yt-dlp/yt-dlp/issues?q= ")
    with pytest.raises(RuntimeError) as info:
        d.extract("https://www.bilibili.com/video/BV13x41117TL", browser="none")
    assert str(info.value).startswith("Bilibili vừa thay đổi")


def test_bilibili_language_prefix_is_removed():
    norm = MediaDispatcher._normalize_bilibili_url
    assert norm("https://www.bilibili.com/en/video/BV1PHpX6EEEF/") == "https://www.bilibili.com/video/BV1PHpX6EEEF/"
    assert norm("https://m.bilibili.com/zh-cn/video/BV1x?p=2") == "https://m.bilibili.com/video/BV1x?p=2"
    assert norm("https://www.bilibili.com/video/BV13x41117TL") == "https://www.bilibili.com/video/BV13x41117TL"
    assert norm("https://example.com/en/video/1") == "https://example.com/en/video/1"


# ─────────────────────────────────────────────────────────────────────────────
# 4. Threads chuyển sang threads.com
# ─────────────────────────────────────────────────────────────────────────────

def test_threads_com_is_recognised_everywhere(browsers):
    url = "https://www.threads.com/@zuck/post/DGrIRjBvRk7"
    assert UrlResolver.detect_platform(url) == ("threads", "Threads")
    assert UrlResolver.detect_platform("https://www.threads.net/@zuck") == ("threads", "Threads")
    assert MediaDispatcher().is_gallery_platform(url)
    assert GalleryDlExtractor._category_of(url) == "threads"
    assert "Threads yêu cầu đăng nhập" in GalleryDlExtractor._login_required_message(url, "x")
    # Cookie dán tay trong Cookie Manager (threads.txt) dùng được cho threads.com
    cookies_dir = os.path.join(os.environ["CRWL_CONFIG_DIR"], "cookies")
    os.makedirs(cookies_dir)
    with open(os.path.join(cookies_dir, "threads.txt"), "w", encoding="utf-8") as fh:
        fh.write("# Netscape HTTP Cookie File\n.threads.com\tTRUE\t/\tTRUE\t0\tsessionid\tt\n")
    assert browser_cookies._find_manual_cookie_file("www.threads.com")


# ─────────────────────────────────────────────────────────────────────────────
# 5. Hạn cookie Firefox tính bằng mili-giây
# ─────────────────────────────────────────────────────────────────────────────

def test_expiry_in_milliseconds_is_converted_to_seconds():
    assert expiry_seconds(1_792_542_393_000) == 1_792_542_393
    assert expiry_seconds(1_792_542_393) == 1_792_542_393
    assert expiry_seconds("1792542393000") == 1_792_542_393
    for session_cookie in (0, None, "", -1, "abc"):
        assert expiry_seconds(session_cookie) == 0


def test_firefox_cookie_db_with_millisecond_expiry(tmp_path):
    db = tmp_path / "cookies.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE moz_cookies (host TEXT, path TEXT, isSecure INT, expiry INT, name TEXT, value TEXT)")
    conn.executemany("INSERT INTO moz_cookies VALUES (?,?,?,?,?,?)", [
        (".x.com", "/", 1, 1_792_542_393_000, "auth_token", "a"),  # Firefox 142+ (schema 16)
        (".x.com", "/", 1, 1_792_542_393, "ct0", "b"),             # hồ sơ cũ
        (".x.com", "/", 1, 0, "guest_id", "c"),                    # cookie phiên
    ])
    conn.commit()
    conn.close()
    rows = {r[5]: r[4] for r in BrowserCookieExporter()._read_firefox_db(str(db))}
    assert rows == {"auth_token": 1_792_542_393, "ct0": 1_792_542_393, "guest_id": 0}


def test_cookie_jar_for_headless_browser_never_has_millisecond_expiry(tmp_path):
    path = tmp_path / "c.txt"
    path.write_text(
        "# Netscape HTTP Cookie File\n"
        ".x.com\tTRUE\t/\tTRUE\t1792542393000\tauth_token\ta\n"
        ".x.com\tTRUE\t/\tTRUE\t0\tct0\tb\n",
        encoding="utf-8",
    )
    jar = {c.name: c for c in load_cookie_jar(str(path))}
    # Playwright chỉ nhận -1 hoặc mốc GIÂY hợp lệ — một cookie sai làm hỏng cả lô
    assert jar["auth_token"].expires == 1_792_542_393
    assert jar["ct0"].expires is None


# ─────────────────────────────────────────────────────────────────────────────
# 6. Avatar / ảnh bìa / tài nguyên giao diện không phải media của bài
# ─────────────────────────────────────────────────────────────────────────────

AVATARS = [
    "https://pbs.twimg.com/profile_images/2041959940777242624/TQnPe3LT_400x400.jpg",
    "https://pbs.twimg.com/profile_banners/11348282/1600000000/1500x500",
    "https://scontent.cdninstagram.com/v/t51.2885-19/44884218_345707102882519_n.jpg",
    "https://scontent.xx.fbcdn.net/v/t39.30808-1/123_n.jpg",
    "https://p16-sign-va.tiktokcdn.com/tos-maliva-avt-0068/7310abc~c5_100x100.jpeg",
    "https://cdn.bsky.app/img/avatar/plain/did:plc:z72i7hdynmk6r22z27h6tvur/bafkrei@jpeg",
    "https://styles.redditmedia.com/t5_2qh1o/styles/communityIcon_x.png",
]


def test_social_avatars_are_never_post_media():
    for url in AVATARS:
        assert _decor_or_junk(url) == (True, False), url
    # Ảnh thật của bài đăng vẫn giữ
    for url in ("https://pbs.twimg.com/media/HUv20aiacAAoxXt?format=jpg&name=orig",
                "https://i.redd.it/abc123.jpeg",
                "https://scontent.cdninstagram.com/v/t51.82787-15/847_n.jpg"):
        assert _decor_or_junk(url) == (False, False), url


class _AvatarPage(BaseHTTPRequestHandler):
    BODY = (
        '<html><head><meta property="og:image" content="https://pbs.twimg.com/profile_images/1/a_400x400.jpg">'
        "</head><body>"
        + "".join(f'<img src="{u}">' for u in AVATARS[:2])
        + "</body></html>"
    ).encode()

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(self.BODY)))
        self.end_headers()
        self.wfile.write(self.BODY)

    def log_message(self, *args):
        pass


def test_html_fallback_does_not_turn_avatars_into_an_album():
    # Bài X có video đã bị gỡ: trước đây ra "album 4 ảnh" toàn avatar
    server = HTTPServer(("127.0.0.1", 0), _AvatarPage)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/starwars/status/665052190608723968"
        try:
            res = WebScraperExtractor().extract(url)
        except Exception:
            return  # không có gì để trả về cũng là đúng
        assert not res.images, [i.url for i in res.images]
    finally:
        server.shutdown()
        server.server_close()


def test_sniffer_ignores_facebook_interface_files():
    sniffer = PlaywrightStreamSniffer.__new__(PlaywrightStreamSniffer)
    junk = [
        "https://static.xx.fbcdn.net/rsrc.php/yI/r/bg9JBgZmoaC.mp3?_nc_eui2=x",
        "https://www.facebook.com/data/manifest/",
        "https://static.xx.fbcdn.net/rsrc.php/v4/ye/r/oz-player/manifests/OzSegmentOptions",
        "https://static.xx.fbcdn.net/rsrc.php/v4/yj/r/^http(?:s)?:\\/\\/youtube\\.com\\/playlist\\?list\\=.+$",
    ]
    for url in junk:
        assert not sniffer._is_media_url(url), url
    assert sniffer._is_media_url("https://cdn.example.com/hls/master.m3u8?token=1")
    assert sniffer._is_media_url("https://video.xx.fbcdn.net/v/t42/abc.mp4?bytestart=0")


# ─────────────────────────────────────────────────────────────────────────────
# 7. Facebook 24/10/2026: yt-dlp chỉ đọc được trang khi giả dạng Firefox
# ─────────────────────────────────────────────────────────────────────────────

FB_REEL = "https://www.facebook.com/reel/1515445133963008"
FB_CANNOT_PARSE_STDERR = (
    "ERROR: [facebook] 1515445133963008: Cannot parse data; please report this issue on  "
    "https://github.com/yt-dlp/yt-dlp/issues?q= , filling out the appropriate issue template. "
    "Confirm you are on the latest version using  yt-dlp -U\n"
)
# stderr THẬT của yt-dlp 2026.10.21 khi Python chạy nó không có curl_cffi
IMPERSONATE_MISSING_STDERR = (
    "Traceback (most recent call last):\n"
    '  File "/usr/lib/python3/dist-packages/yt_dlp/YoutubeDL.py", line 768, in __init__\n'
    "    raise YoutubeDLError(\n"
    'yt_dlp.utils.YoutubeDLError: Impersonate target "firefox" is not available. Use '
    "--list-impersonate-targets to see available targets. You may be missing dependencies "
    "required to support this target.\n"
)
FB_INFO = {
    "_type": "video", "id": "1515445133963008", "title": "Reel", "extractor": "facebook",
    "extractor_key": "Facebook", "webpage_url": FB_REEL,
    "formats": [{"format_id": "hd", "url": "https://video.xx.fbcdn.net/v/t42/hd.mp4", "ext": "mp4",
                 "width": 1080, "height": 1920, "vcodec": "h264", "acodec": "aac"}],
}


def _fake_fb_ytdlp(tmp_path, impersonated):
    """yt-dlp giả: không giả dạng thì "Cannot parse data"; với `--impersonate firefox` thì
    `impersonated` = "ok" (đọc được video), "missing" (thiếu curl_cffi), "broken" (vẫn lỗi)."""
    calls = tmp_path / "yt-dlp.calls"
    script = tmp_path / "yt-dlp"
    script.write_text(
        "#!/usr/bin/env python3\nimport sys\n"
        "if '--help' in sys.argv:\n    print('  --cookies FILE'); sys.exit(0)\n"
        f"open({str(calls)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n"
        "args = sys.argv[1:]\n"
        "firefox = '--impersonate' in args and args[args.index('--impersonate') + 1] == 'firefox'\n"
        f"mode = {impersonated!r} if firefox else 'broken'\n"
        f"if mode == 'ok':\n    print({json.dumps(FB_INFO)!r}); sys.exit(0)\n"
        f"if mode == 'missing':\n    sys.stderr.write({IMPERSONATE_MISSING_STDERR!r}); sys.exit(1)\n"
        f"sys.stderr.write({FB_CANNOT_PARSE_STDERR!r}); sys.exit(1)\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return str(script), calls


def _ytdlp_at(binary):
    ext = YtDlpExtractor()
    ext.binary_path = binary
    return ext


def test_only_facebook_cannot_parse_data_is_retried_as_firefox():
    for url in (FB_REEL, "https://m.facebook.com/watch/?v=1", "https://fb.watch/abc/", "facebook.com/reel/1"):
        assert impersonation_target_for(url, FB_CANNOT_PARSE_STDERR) == "firefox", url
    assert impersonation_target_for(FB_REEL, "ERROR: [facebook] 1: This video is private") is None
    assert impersonation_target_for("https://notfacebook.com/reel/1", FB_CANNOT_PARSE_STDERR) is None
    assert impersonation_target_for("https://www.youtube.com/watch?v=x", FB_CANNOT_PARSE_STDERR) is None


def test_facebook_is_read_as_firefox_with_the_same_login_cookies(tmp_path, browsers):
    browsers["firefox"] = [_row(".facebook.com", "c_user", "1000")]
    binary, calls = _fake_fb_ytdlp(tmp_path, "ok")
    meta = _ytdlp_at(binary).extract_metadata(FB_REEL, browser="firefox")
    assert meta.id == "1515445133963008" and meta.streams
    runs = calls.read_text().splitlines()
    assert len(runs) == 2
    assert "--impersonate" not in runs[0], "lượt đầu chạy như cũ — Facebook chưa đổi ở mọi nơi"
    assert "--impersonate firefox" in runs[1] and "--cookies" in runs[1], "giả dạng mà vẫn giữ phiên đăng nhập"


def test_missing_curl_cffi_stops_with_the_original_page_error(tmp_path, browsers):
    browsers["firefox"] = [_row(".facebook.com", "c_user", "1000")]
    binary, calls = _fake_fb_ytdlp(tmp_path, "missing")
    with pytest.raises(ImpersonationUnavailable) as info:
        _ytdlp_at(binary).extract_metadata(FB_REEL, browser="firefox")
    assert "Cannot parse data" in str(info.value), "Chi tiết là lỗi của trang, không phải traceback"
    assert len(calls.read_text().splitlines()) == 2, "lượt chạy lại không cookie chỉ ra đúng lỗi cũ"


def test_impersonation_that_still_fails_is_not_retried_again(tmp_path, browsers):
    browsers["firefox"] = [_row(".facebook.com", "c_user", "1000")]
    binary, calls = _fake_fb_ytdlp(tmp_path, "broken")
    with pytest.raises(RuntimeError) as info:
        _ytdlp_at(binary).extract_metadata(FB_REEL, browser="firefox")
    assert not isinstance(info.value, ImpersonationUnavailable)
    runs = calls.read_text().splitlines()
    # có cookie → có cookie + Firefox → không cookie + Firefox
    assert len(runs) == 3 and all("--impersonate firefox" in r for r in runs[1:])
    assert "--cookies" in runs[1] and "--cookies" not in runs[2]


def test_other_sites_are_never_impersonated(tmp_path, browsers):
    binary, calls = _fake_fb_ytdlp(tmp_path, "ok")
    with pytest.raises(RuntimeError):
        _ytdlp_at(binary).extract_metadata("https://www.bilibili.com/video/BV13x41117TL", browser="none")
    assert "--impersonate" not in calls.read_text()


def test_dispatcher_asks_to_install_curl_cffi_not_to_log_in_or_update(tmp_path, monkeypatch, browsers):
    binary, _ = _fake_fb_ytdlp(tmp_path, "missing")
    d = MediaDispatcher()
    d.ytdlp.binary_path = binary
    monkeypatch.setattr(d.resolver, "resolve_url", lambda url, expected_platform=None: type(
        "R", (), {"is_shortened": False, "resolved_url": url})())
    monkeypatch.setattr(d.gallery, "extract_gallery", lambda *a, **k: (_ for _ in ()).throw(
        RuntimeError("gallery-dl extract thất bại: Unsupported URL")))
    monkeypatch.setattr(d.web_scraper, "extract", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("không có video")))
    # Khách (chưa đăng nhập): trước đây "Cannot parse data" luôn bị báo là cần đăng nhập
    with pytest.raises(RuntimeError) as info:
        d.extract(FB_REEL, browser="none")
    msg = str(info.value)
    assert msg.startswith("Facebook vừa thay đổi") and "thiếu thư viện curl_cffi" in msg
    assert "pip install --upgrade curl_cffi" in msg
    assert "yêu cầu đăng nhập" not in msg and "Bản mới nhất" not in msg
