#!/usr/bin/env python3
"""
Kiểm thử hai lỗi gốc khiến app không lấy được media tài khoản:

  1. Bộ lọc cookie theo hostname đầy đủ (www.instagram.com) làm mất toàn bộ
     cookie đăng nhập nằm trên domain gốc (.instagram.com).
  2. gallery-dl báo lỗi thiếu đăng nhập qua STDOUT dạng [-1, {...}], nhưng app
     chỉ dò STDERR nên hiện thông báo sai ("không tìm thấy media").

Chạy:  python3 scripts/test_cookies_and_auth.py     (hoặc: pytest scripts/)
"""

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.cookies.browser_cookies import (
    BrowserCookieExporter,
    registrable_domain,
    _host_matches,
)
from core.extractors.gallery import GalleryDlExtractor


# ─────────────────────────────────────────────────────────────────────────────
# 1. Rút gọn hostname về domain đăng ký được
# ─────────────────────────────────────────────────────────────────────────────

def test_registrable_domain_strips_subdomains():
    assert registrable_domain("www.instagram.com") == "instagram.com"
    assert registrable_domain("m.facebook.com") == "facebook.com"
    assert registrable_domain("instagram.fsgn2-9.fna.fbcdn.net") == "fbcdn.net"
    assert registrable_domain(".instagram.com") == "instagram.com"
    assert registrable_domain("x.com") == "x.com"
    assert registrable_domain(None) is None
    assert registrable_domain("") is None


def test_registrable_domain_handles_two_part_tlds():
    assert registrable_domain("www.bbc.co.uk") == "bbc.co.uk"
    assert registrable_domain("shop.vnexpress.com.vn") == "vnexpress.com.vn"


def test_host_matching_covers_domain_cookies():
    # Đây chính là trường hợp bị lọc nhầm trước đây
    assert _host_matches(".instagram.com", "instagram.com") is True
    assert _host_matches("www.instagram.com", "instagram.com") is True
    assert _host_matches("i.instagram.com", "instagram.com") is True
    assert _host_matches("notinstagram.com", "instagram.com") is False


def test_cookie_export_keeps_domain_scoped_cookies():
    """Lọc theo 'www.instagram.com' phải ra đúng bộ cookie như lọc 'instagram.com'."""
    exporter = BrowserCookieExporter()

    def data_lines(content):
        return [l for l in content.splitlines() if l and not l.startswith("#")]

    fake_cookies = [
        (".instagram.com", "TRUE", "/", "TRUE", 0, "sessionid", "abc"),
        (".instagram.com", "TRUE", "/", "TRUE", 0, "csrftoken", "def"),
        ("www.instagram.com", "FALSE", "/", "TRUE", 0, "th_eu_pref", "1"),
        (".facebook.com", "TRUE", "/", "TRUE", 0, "c_user", "999"),
    ]
    exporter.extract_firefox_cookies = lambda: fake_cookies

    by_host = data_lines(exporter.export_cookies_netscape("firefox", "www.instagram.com"))
    by_domain = data_lines(exporter.export_cookies_netscape("firefox", "instagram.com"))

    assert len(by_host) == 3, f"cookie đăng nhập bị lọc mất: {by_host}"
    assert by_host == by_domain
    assert any("sessionid" in l for l in by_host)
    # Không được lẫn cookie của nền tảng khác
    assert not any("facebook" in l for l in by_host)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Nhận diện lỗi cần đăng nhập của gallery-dl
# ─────────────────────────────────────────────────────────────────────────────

def test_detects_instagram_missing_login_from_stdout():
    # Đúng payload gallery-dl trả về khi chưa đăng nhập Instagram
    raw = [[-1, {"error": "KeyError", "message": "'username'"}]]
    assert GalleryDlExtractor._detect_auth_error(raw, "") is not None


def test_detects_auth_errors_from_stderr():
    assert GalleryDlExtractor._detect_auth_error(None, "HttpError: 401 Unauthorized") is not None
    assert GalleryDlExtractor._detect_auth_error(None, "AuthRequired: login required") is not None


def test_does_not_flag_successful_output_as_auth_error():
    ok = [[2, {"category": "instagram"}], [3, "https://cdn/a.jpg", {"extension": "jpg"}]]
    assert GalleryDlExtractor._detect_auth_error(ok, "") is None
    assert GalleryDlExtractor._detect_auth_error([], "") is None


def test_login_message_names_the_platform_and_next_step():
    msg = GalleryDlExtractor._login_required_message(
        "https://www.instagram.com/hawk.389568/posts/", "KeyError 'username'"
    )
    assert "Instagram" in msg
    assert "Cookie Manager" in msg


# ─────────────────────────────────────────────────────────────────────────────
# 3. Thời gian chờ co giãn theo số lượng cần quét
# ─────────────────────────────────────────────────────────────────────────────

def test_crawl_timeout_scales_with_requested_count():
    t = GalleryDlExtractor._timeout_for
    assert t(20, None, None) < t(100, None, None)
    # "Tất cả" (limit = 0) phải được mốc rộng nhất
    assert t(0, None, None) == 540
    assert t(50, None, None) >= 90
    assert t(100000, None, None) <= 540


if __name__ == "__main__":
    failures = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  ✅ [PASS] {name}")
            except AssertionError as e:
                failures += 1
                print(f"  ❌ [FAIL] {name}: {e}")
    print()
    if failures:
        print(f"❌ {failures} kiểm thử thất bại")
        sys.exit(1)
    print("✅ Toàn bộ kiểm thử đã qua")
