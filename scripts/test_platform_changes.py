#!/usr/bin/env python3
"""
Kiểm thử các lỗi khiến "đồng loạt các nền tảng không lấy được media" (10/2026):

  1. App tự chọn Edge làm nguồn cookie; Edge chỉ đăng nhập Instagram/Facebook nên với
     X, Reddit... app luôn là KHÁCH dù Firefox đang đăng nhập. Nay trang nào trình
     duyệt đang chọn chưa đăng nhập thì dùng trình duyệt khác đang đăng nhập — cả ở
     bước phân tích lẫn bước tải (yt-dlp `--cookies-from-browser`).
  2. X trả bài có media nhạy cảm cho khách dưới dạng "BlurredMediaTombstone": app nhặt
     ảnh mờ dùng chung đó làm "1 ảnh trong bài viết" (3 bài khác nhau cùng 1 ảnh).
  3. Lỗi thật của gallery-dl ('Unavailable', "redirect to login"...) bị thay bằng câu
     chung chung; bài ảnh Instagram chưa đăng nhập báo "There is no video in this post".
  4. Instagram đổi API: gallery-dl bản ổn định báo "không tìm thấy" MỌI tài khoản —
     thông báo phải chỉ cách cài bản mới nhất. Ảnh ".heic?stp=dst-jpg" là JPEG.
  5. Trang Reddit khi chưa đăng nhập: 7 avatar mặc định lọt vào kết quả.

Chạy offline:  python3 -m pytest -q scripts/test_platform_changes.py
"""

import os
import stat
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.cookies import browser_cookies
from core.cookies.browser_cookies import (
    BrowserCookieExporter,
    _rows_have_login,
    get_browser_cookies_txt,
    has_login_session,
    pick_cookie_browser,
)
from core.dispatcher import MediaDispatcher
from core.extractors import page_media
from core.extractors.gallery import GalleryDlExtractor
from core.extractors.page_media import LoginRequiredError, PageMediaExtractor, _decor_or_junk


# ─────────────────────────────────────────────────────────────────────────────
# 1. Dùng trình duyệt ĐANG ĐĂNG NHẬP trang cần tải
# ─────────────────────────────────────────────────────────────────────────────

def _row(host, name, value):
    return (host, "TRUE", "/", "TRUE", 0, name, value)


# Đúng tình trạng máy người dùng: Edge đăng nhập Instagram, Firefox đăng nhập X nhưng
# chỉ còn cookie khách (ds_user_id, csrftoken) của Instagram.
FAKE_BROWSERS = {
    "edge": [
        _row(".instagram.com", "sessionid", "IG-SESSION"),
        _row(".instagram.com", "csrftoken", "edge-csrf"),
        _row(".facebook.com", "c_user", "1000"),
    ],
    "firefox": [
        _row(".x.com", "auth_token", "X-AUTH"),
        _row(".x.com", "ct0", "x-ct0"),
        _row(".instagram.com", "ds_user_id", "42"),
        _row(".instagram.com", "csrftoken", "ff-csrf"),
        _row(".example.com", "pref", "1"),
    ],
}


@pytest.fixture
def fake_browsers(monkeypatch, tmp_path):
    # Không đọc kho cookie / tệp cookie thủ công thật của máy
    monkeypatch.setenv("CRWL_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(BrowserCookieExporter, "_raw_cookies_cached", lambda self, b: FAKE_BROWSERS.get(b, []))
    browser_cookies.clear_cookie_cache()
    yield tmp_path
    browser_cookies.clear_cookie_cache()


def _exported(browser, domain):
    path = get_browser_cookies_txt(browser, domain=domain)
    if path is None:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    finally:
        os.remove(path)


def test_selected_browser_without_login_falls_back_to_the_logged_in_one(fake_browsers):
    # Edge chưa đăng nhập X → dùng phiên của Firefox (trước đây: không có cookie nào)
    x = _exported("edge", "x.com")
    assert x is not None and "auth_token\tX-AUTH" in x
    # Firefox chỉ còn cookie khách của Instagram → dùng phiên của Edge
    ig = _exported("firefox", "www.instagram.com")
    assert "sessionid\tIG-SESSION" in ig and "ds_user_id" not in ig


def test_selected_browser_that_is_logged_in_is_kept(fake_browsers):
    ig = _exported("edge", "instagram.com")
    assert "sessionid\tIG-SESSION" in ig
    assert "ff-csrf" not in ig


def test_no_fallback_for_sites_without_known_login_or_when_cookies_are_off(fake_browsers):
    # Không phải nền tảng cần đăng nhập: giữ đúng trình duyệt đã chọn
    assert _exported("edge", "example.com") is None
    assert "pref\t1" in _exported("firefox", "example.com")
    # "Tắt Cookies" là tắt hẳn
    assert get_browser_cookies_txt("none", domain="x.com") is None


def test_auto_mode_prefers_a_logged_in_browser_over_guest_cookies(fake_browsers):
    # Firefox đứng đầu danh sách dò và CÓ cookie instagram.com — nhưng chỉ là cookie khách
    assert BrowserCookieExporter().find_best_browser("www.instagram.com") == "edge"
    assert "sessionid\tIG-SESSION" in _exported("auto", "instagram.com")


def test_guest_cookies_are_not_counted_as_a_login():
    assert not _rows_have_login([_row(".instagram.com", "ds_user_id", "42")], "instagram.com")
    assert _rows_have_login([_row(".instagram.com", "sessionid", "s")], "instagram.com")
    assert not _rows_have_login([_row(".x.com", "ct0", "guest")], "x.com")
    # Pinterest đặt _auth=0 cho khách
    assert not _rows_have_login([_row(".pinterest.com", "_auth", "0")], "pinterest.com")
    assert _rows_have_login([_row(".pinterest.com", "_auth", "1")], "pinterest.com")
    # Cookie của tên miền khác không tính
    assert not _rows_have_login([_row(".notx.com", "auth_token", "a")], "x.com")


def test_download_step_reads_cookies_from_the_logged_in_browser(fake_browsers):
    assert pick_cookie_browser("https://x.com/a/status/1?s=20", "edge") == "firefox"
    assert pick_cookie_browser("https://www.instagram.com/p/abc/", "edge") == "edge"
    assert pick_cookie_browser("https://www.instagram.com/reel/abc/", "firefox") == "edge"
    assert pick_cookie_browser("https://example.com/video", "edge") == "edge"
    assert pick_cookie_browser("https://x.com/a/status/1", "none") == "none"
    # Có tệp cookie thủ công: Rust dùng tệp đó, giữ nguyên lựa chọn
    cookies_dir = fake_browsers / "cookies"
    cookies_dir.mkdir()
    (cookies_dir / "twitter.txt").write_text("# Netscape HTTP Cookie File\n.x.com\tTRUE\t/\tTRUE\t0\tauth_token\tm\n")
    assert pick_cookie_browser("https://x.com/a/status/1", "edge") == "edge"


def test_cookie_browser_ipc_action(fake_browsers):
    from core.extractor_cli import QUICK_ACTIONS, handle_request

    res = handle_request(None, None, {
        "id": "t1", "action": "cookie_browser", "url": "https://x.com/a/status/1", "browser": "edge",
    })
    assert res == {"id": "t1", "success": True, "data": {"browser": "firefox"}}
    # Bước tải đang chờ: không xếp hàng sau các lượt bóc tách dài
    assert "cookie_browser" in QUICK_ACTIONS


# ─────────────────────────────────────────────────────────────────────────────
# 2. X: bài nhạy cảm chỉ có ảnh mờ khi chưa đăng nhập
# ─────────────────────────────────────────────────────────────────────────────

BLUR = "https://pbs.twimg.com/media/GxJIrSUagAAK-ZP?format=jpg&amp;name=240x240"
X_BLURRED_HTML = f"""<!DOCTYPE html><html><head>
<meta property="og:title" content="Ai đó (@someone) trên X"/>
<meta property="og:description" content="Nội dung người lớn có giới hạn độ tuổi."/>
<link rel="preload" as="image" href="{BLUR}"/></head>
<body><main><article>
<p>Một bài đăng có video</p>
<div class="relative overflow-hidden rounded-2xl"><img alt="" class="absolute inset-0 size-full object-cover"
 src="{BLUR}" width="1200" height="675"/><div class="absolute inset-0 bg-black/20"></div></div>
</article></main>
<script>self.__next_f.push([1,"{{\\"__typename\\":\\"TweetTombstone\\",\\"tombstone\\":{{\\"__typename\\":\\"BlurredMediaTombstone\\",\\"blurred_image_url\\":\\"https://pbs.twimg.com/media/GxJIrSUagAAK-ZP?format=jpg&name=240x240\\"}}}}"])</script>
</body></html>"""


def _serve_html(monkeypatch, html):
    monkeypatch.setattr(
        page_media, "fetch_page",
        lambda url, browser, timeout=None: (url, 200, "text/html; charset=utf-8", html, None),
    )


def test_blurred_x_post_is_a_login_error_not_a_one_image_album(monkeypatch):
    _serve_html(monkeypatch, X_BLURRED_HTML)
    with pytest.raises(LoginRequiredError) as info:
        PageMediaExtractor(sniffer=None).extract("https://x.com/someone/status/2087744762526740496")
    assert "làm mờ" in str(info.value)
    assert "yêu cầu đăng nhập" in str(info.value)


def test_tombstone_marker_only_matters_on_x(monkeypatch):
    # Một bài báo trích nguyên văn JSON của X không phải là trang bị khoá
    _serve_html(monkeypatch, X_BLURRED_HTML)
    res = PageMediaExtractor(sniffer=None).extract("https://blog.example.com/x-explained")
    assert res.images or res.streams


def _dispatcher(monkeypatch, gallery_error, ytdlp_error):
    d = MediaDispatcher()
    monkeypatch.setattr(d.resolver, "resolve_url", lambda url, expected_platform=None: type(
        "R", (), {"is_shortened": False, "resolved_url": url})())

    def fail_gallery(url, browser=None, timeout=30):
        raise gallery_error

    def fail_ytdlp(url, browser=None, timeout=30):
        raise ytdlp_error

    monkeypatch.setattr(d.gallery, "extract_gallery", fail_gallery)
    monkeypatch.setattr(d.ytdlp, "extract_metadata", fail_ytdlp)
    monkeypatch.setattr(d.web_scraper, "extract", lambda *a, **k: pytest.fail("không được vớt og:image của trang"))
    return d


def test_dispatcher_reports_login_for_blurred_x_post(monkeypatch):
    d = _dispatcher(
        monkeypatch,
        RuntimeError("gallery-dl không lấy được media: AbortExtraction: 'Unavailable'"),
        RuntimeError("yt-dlp extract thất bại: ERROR: [twitter] 2087744762526740496: No video could be found in this tweet"),
    )
    _serve_html(monkeypatch, X_BLURRED_HTML)
    with pytest.raises(LoginRequiredError):
        d.extract("https://x.com/someone/status/2087744762526740496?s=20", browser="none")


# ─────────────────────────────────────────────────────────────────────────────
# 3. Lỗi thật của gallery-dl
# ─────────────────────────────────────────────────────────────────────────────

def _fake_gallery_dl(tmp_path, stdout):
    script = tmp_path / "gallery-dl"
    script.write_text(f"#!/usr/bin/env python3\nprint({stdout!r})\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    ext = GalleryDlExtractor()
    ext.binary_path = str(script)
    return ext


def test_gallery_dl_error_entry_is_reported_as_is(tmp_path):
    ext = _fake_gallery_dl(tmp_path, '[[-1, {"error": "AbortExtraction", "message": "\'Unavailable\'"}]]')
    with pytest.raises(RuntimeError) as info:
        ext.extract_gallery("https://x.com/a/status/1", browser="none")
    assert "AbortExtraction: 'Unavailable'" in str(info.value)


def test_instagram_redirect_to_login_page_is_an_auth_error():
    raw = [[-1, {"error": "AbortExtraction",
                 "message": "HTTP redirect to login page (https://www.instagram.com/accounts/login/)"}]]
    assert GalleryDlExtractor._detect_auth_error(raw, "") is not None


def test_instagram_photo_post_without_login_reports_login_not_no_video(monkeypatch):
    url = "https://www.instagram.com/p/DecBqpqPzfp/"
    d = _dispatcher(
        monkeypatch,
        RuntimeError(GalleryDlExtractor._login_required_message(url, "AbortExtraction: HTTP redirect to login page")),
        RuntimeError("yt-dlp extract thất bại: ERROR: [Instagram] DecBqpqPzfp: There is no video in this post"),
    )
    monkeypatch.setattr(d.page_media, "extract", lambda *a, **k: pytest.fail("không được đọc trang khi đã biết cần đăng nhập"))
    with pytest.raises(RuntimeError) as info:
        d.extract(url, browser="none")
    assert "Instagram yêu cầu đăng nhập" in str(info.value)


REDDIT_URL = "https://www.reddit.com/r/videos/comments/6rrwyj/that_small_heart_attack/"


def _reddit_dispatcher(monkeypatch):
    return _dispatcher(
        monkeypatch,
        RuntimeError("gallery-dl không lấy được media: AbortExtraction: \"You've been blocked by network security.\""),
        RuntimeError("yt-dlp extract thất bại: ERROR: [Reddit] 6rrwyj: Account authentication is required."),
    )


def test_reddit_guest_gets_a_login_error_instead_of_unrelated_images(monkeypatch, fake_browsers):
    d = _reddit_dispatcher(monkeypatch)
    monkeypatch.setattr(d.page_media, "extract", lambda *a, **k: pytest.fail("không có phiên đăng nhập: không đọc trang"))
    assert not has_login_session(REDDIT_URL, "edge")
    with pytest.raises(RuntimeError) as info:
        d.extract(REDDIT_URL, browser="edge")
    assert "Reddit yêu cầu đăng nhập" in str(info.value)


def test_logged_in_reddit_still_tries_the_page(monkeypatch, fake_browsers):
    monkeypatch.setitem(FAKE_BROWSERS, "firefox", FAKE_BROWSERS["firefox"] + [_row(".reddit.com", "reddit_session", "r")])
    assert has_login_session(REDDIT_URL, "edge")
    assert not has_login_session(REDDIT_URL, "none")
    d = _reddit_dispatcher(monkeypatch)
    called = []

    def read_page(*args, **kwargs):
        called.append(args)
        raise RuntimeError("trang không có media")

    def scrape(*args, **kwargs):
        raise RuntimeError("không có og:image")

    monkeypatch.setattr(d.page_media, "extract", read_page)
    monkeypatch.setattr(d.web_scraper, "extract", scrape)
    with pytest.raises(RuntimeError):
        d.extract(REDDIT_URL, browser="edge")
    assert called, "có phiên đăng nhập thì vẫn phải thử đọc bài viết"


def test_facebook_guest_still_reads_a_public_photo_page(monkeypatch, fake_browsers):
    # Không trình duyệt nào đăng nhập Facebook; yt-dlp báo "Cannot parse data" (bị coi
    # là lỗi đăng nhập) — nhưng trang ảnh công khai vẫn cho khách thấy chính ảnh đó.
    monkeypatch.setitem(FAKE_BROWSERS, "edge", [r for r in FAKE_BROWSERS["edge"] if r[5] != "c_user"])
    from core.models import MediaImage, MediaMetadata

    url = "https://www.facebook.com/photo/?fbid=123"
    d = _dispatcher(
        monkeypatch,
        RuntimeError("gallery-dl không lấy được media: HttpError: 500 Internal Server Error"),
        RuntimeError("yt-dlp extract thất bại: ERROR: [facebook] 123: Cannot parse data"),
    )
    photo = MediaMetadata(id="1", platform="facebook", type="album", original_url=url,
                          images=[MediaImage(id=1, url="https://scontent.xx.fbcdn.net/v/p.jpg")])
    monkeypatch.setattr(d.page_media, "extract", lambda *a, **k: photo)
    assert d.extract(url, browser="edge").images[0].url.endswith("p.jpg")


# ─────────────────────────────────────────────────────────────────────────────
# 4. Instagram đổi API / ảnh HEIC thực ra là JPEG
# ─────────────────────────────────────────────────────────────────────────────

def test_instagram_profile_not_found_points_to_the_latest_gallery_dl():
    msg = GalleryDlExtractor.instagram_outdated_message(
        "https://www.instagram.com/natgeo/posts/", "NotFoundError: Requested user could not be found"
    )
    assert "Bản mới nhất" in msg and "Công cụ" in msg
    assert "Requested user could not be found" in msg
    # Lỗi khác / nền tảng khác: không gợi ý bừa
    assert GalleryDlExtractor.instagram_outdated_message(
        "https://www.instagram.com/natgeo/posts/", "HttpError: 429 Too Many Requests") is None
    assert GalleryDlExtractor.instagram_outdated_message(
        "https://x.com/natgeo/media", "NotFoundError: Requested user could not be found") is None


def test_probe_error_for_instagram_profile_suggests_updating(tmp_path):
    ext = _fake_gallery_dl(
        tmp_path, '[[-1, {"error": "NotFoundError", "message": "Requested user could not be found"}]]')
    with pytest.raises(RuntimeError) as info:
        ext._raise_probe_error("https://www.instagram.com/lilithzxm/posts/", "none")
    assert "Bản mới nhất" in str(info.value)


def test_heic_url_served_as_jpeg_is_labelled_jpg():
    jpeg_heic = "https://instagram.fhan20-1.fna.fbcdn.net/v/t51/847_n.heic?stp=dst-jpg_e35_tt6&_nc_cat=1"
    assert GalleryDlExtractor._display_ext("heic", jpeg_heic) == "jpg"
    assert GalleryDlExtractor._display_ext("heic", "https://cdn/x/photo.heic") == "heic"
    assert GalleryDlExtractor._display_ext("mp4", jpeg_heic) == "mp4"
    res = GalleryDlExtractor()._normalize_gallery(
        [[3, jpeg_heic, {"extension": "heic", "category": "instagram", "num": 1, "count": 1}]],
        "https://www.instagram.com/p/DecBqpqPzfp/",
    )
    assert res.images[0].ext == "jpg"


# ─────────────────────────────────────────────────────────────────────────────
# 5. Avatar mặc định của Reddit
# ─────────────────────────────────────────────────────────────────────────────

def test_reddit_default_avatars_are_never_post_media():
    for n in range(8):
        url = f"https://www.redditstatic.com/avatars/defaults/v2/avatar_default_{n}.png"
        assert _decor_or_junk(url) == (True, False)
