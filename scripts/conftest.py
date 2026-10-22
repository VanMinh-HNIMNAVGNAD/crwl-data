"""Cấu hình chung cho các bài kiểm thử pytest trong scripts/."""

import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


@pytest.fixture(autouse=True)
def _no_real_dns_block_check(monkeypatch):
    """Kết quả không được phụ thuộc DNS của máy chạy test.

    Dispatcher dừng ngay khi tên miền bị DNS chặn (trỏ về 127.0.0.1) — trên mạng chặn
    www.reddit.com, mọi bài kiểm thử dùng link Reddit giả lập sẽ nhận lỗi đó thay vì đi
    qua đúng nhánh cần kiểm tra. Bài kiểm thử riêng của tính năng này tự giả lập DNS.
    """
    import core.dispatcher

    monkeypatch.setattr(core.dispatcher, "dns_blocked_host", lambda url: None)
