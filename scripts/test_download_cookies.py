#!/usr/bin/env python3
"""
Cookie cho bước TẢI tệp bằng curl (ảnh / tệp của bài viết cần đăng nhập).

Trước đây bước tải album không gửi cookie nào: bài viết cần đăng nhập bóc tách được
(engine dùng cookie của trình duyệt) nhưng tệp nằm sau đăng nhập (tệp đính kèm diễn
đàn, CDN dùng cookie ký) tải về toàn là trang đăng nhập và bị loại. Nay Rust xin
engine một tệp cookie tạm chỉ gồm các tên miền của album rồi đưa cho `curl -b`.
"""

import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def test_download_cookie_file_merges_domains_and_removes_sources(tmp_path, monkeypatch):
    from core.cookies import browser_cookies as bc

    asked, sources = [], []

    def fake_export(browser, domain=None):
        asked.append((browser, domain))
        src = tmp_path / f"src_{len(asked)}.txt"
        src.write_text(
            "# Netscape HTTP Cookie File\n"
            f".{domain}\tTRUE\t/\tTRUE\t0\tsession\tgia-tri-{domain}\n"
            f"#HttpOnly_.{domain}\tTRUE\t/\tTRUE\t0\thttponly\tbi-mat\n"
            f".{domain}\tTRUE\t/\tTRUE\t0\tsession\tgia-tri-{domain}\n",
            encoding="utf-8",
        )
        sources.append(src)
        return str(src)

    monkeypatch.setattr(bc, "get_browser_cookies_txt", fake_export)
    path, count = bc.export_cookies_for_domains(
        ["forum.example.com", "cdn.example.com", "img.othercdn.net:8443", "", None], "firefox"
    )
    try:
        # Một lần cho mỗi tên miền ĐĂNG KÝ (forum. và cdn. cùng là example.com)
        assert asked == [("firefox", "example.com"), ("firefox", "othercdn.net")]
        assert count == 4  # trùng lặp bị bỏ, dòng #HttpOnly_ được giữ cho curl
        text = open(path, encoding="utf-8").read()
        assert text.startswith("# Netscape HTTP Cookie File")
        assert "gia-tri-example.com" in text and "#HttpOnly_.othercdn.net" in text
        assert oct(os.stat(path).st_mode & 0o777) == "0o600", "Tệp cookie chỉ chủ sở hữu được đọc"
        assert not any(p.exists() for p in sources), "Tệp xuất trung gian phải bị xoá"
    finally:
        os.remove(path)


def test_download_cookie_file_respects_no_cookie_choice(monkeypatch):
    from core.cookies import browser_cookies as bc

    monkeypatch.setattr(bc, "get_browser_cookies_txt", lambda *a, **k: pytest.fail("không được đọc cookie"))
    assert bc.export_cookies_for_domains(["example.com"], "none") == (None, 0)


def test_cookie_action_over_ipc(monkeypatch):
    from core import extractor_cli

    monkeypatch.setattr(extractor_cli, "export_cookies_for_domains", lambda domains, browser: (None, 0))
    resp = extractor_cli.handle_request(None, None, {
        "id": "c1", "action": "cookies", "url": "example.com", "domains": ["example.com", 5], "browser": "auto",
    })
    assert resp == {"id": "c1", "success": True, "data": {"path": None, "count": 0}}
