"""
Huỷ request đang chạy.

Trước đây nút "Hủy" chỉ dừng vòng lặp JavaScript: request đang bay vẫn chạy tới
cùng, tiến trình yt-dlp/gallery-dl vẫn ngốn CPU và vẫn giữ một slot trong
ThreadPoolExecutor của sidecar. Module này theo dõi mọi tiến trình con theo
request-id để lệnh huỷ thật sự kill được chúng.
"""

import os
import signal
import subprocess
import sys
import threading
import time
from typing import Dict, Optional, Set


class RequestCancelled(BaseException):
    """Báo hiệu request đã bị người dùng huỷ.

    Kế thừa ``BaseException`` (không phải ``Exception``) là CỐ Ý: chuỗi fallback
    của dispatcher bọc mọi bước trong ``except Exception`` để thử engine kế tiếp.
    Nếu tín hiệu huỷ là ``Exception``, nó sẽ bị nuốt và dispatcher lại đi khởi
    chạy yt-dlp → gallery-dl → web_scraper → Playwright sau khi đã bị huỷ.
    """

    def __init__(self, req_id: Optional[str] = None):
        self.req_id = req_id
        super().__init__(f"Yêu cầu đã bị huỷ ({req_id or 'không rõ id'})")


class RequestTimedOut(RequestCancelled):
    """Request đã dùng hết ngân sách thời gian mà Rust cấp (`budget_sec`).

    Cùng họ với ``RequestCancelled`` để xuyên qua mọi ``except Exception`` của
    chuỗi fallback — hết giờ thì không được khởi động thêm engine nào nữa.
    """

    def __init__(self, req_id: Optional[str] = None):
        super().__init__(req_id)
        self.args = (f"Hết thời gian xử lý cho yêu cầu ({req_id or 'không rõ id'})",)


class _RequestState:
    __slots__ = ("procs", "cancelled", "deadline", "created", "active")

    def __init__(self) -> None:
        self.procs: Set[subprocess.Popen] = set()
        self.cancelled = False
        self.deadline: Optional[float] = None  # time.monotonic()
        self.created = time.monotonic()
        self.active = False


# Cờ huỷ của request chưa bao giờ bắt đầu (huỷ sau khi đã xong) được giữ tối đa chừng này.
_STALE_CANCEL_TTL = 600.0


_local = threading.local()
_lock = threading.Lock()
_registry: Dict[str, _RequestState] = {}


# ─────────────────────────────────────────────────────────────────────────────
# Vòng đời request
# ─────────────────────────────────────────────────────────────────────────────

def begin_request(req_id: Optional[str], budget_sec: Optional[float] = None) -> None:
    """Đánh dấu luồng hiện tại đang phục vụ `req_id`.

    `budget_sec`: số giây tối đa request được phép chạy (Rust sẽ bỏ cuộc sau mốc
    này). Mọi tiến trình con bị giới hạn theo thời gian còn lại.
    """
    _local.req_id = req_id
    if not req_id:
        return
    with _lock:
        # Lệnh huỷ có thể tới TRƯỚC khi worker kịp bắt đầu (người dùng bấm Hủy
        # ngay). Giữ nguyên state đã có để cờ cancelled không bị xoá mất.
        state = _registry.setdefault(req_id, _RequestState())
        state.active = True
        if budget_sec and budget_sec > 0:
            state.deadline = time.monotonic() + float(budget_sec)


def end_request(req_id: Optional[str]) -> None:
    _local.req_id = None
    if not req_id:
        return
    with _lock:
        _registry.pop(req_id, None)


def current_request_id() -> Optional[str]:
    return getattr(_local, "req_id", None)


def attach_request(req_id: Optional[str]) -> Optional[str]:
    """Gắn luồng phụ vào request của luồng cha, trả về giá trị cũ để khôi phục.

    Cờ huỷ và sổ tiến trình con được tra theo thread-local ``req_id``. Luồng
    worker (ví dụ khi bung ảnh của nhiều bài đăng song song) sinh ra với
    ``req_id = None``, nên tiến trình gallery-dl chúng tạo sẽ KHÔNG được ghi vào
    sổ và lệnh "Hủy" không thể kill được. Hàm này chỉ gán thread-local, tuyệt đối
    không đụng tới ``_registry`` — vòng đời request vẫn do luồng cha quản lý.
    """
    previous = getattr(_local, "req_id", None)
    _local.req_id = req_id
    return previous


def detach_request(previous: Optional[str] = None) -> None:
    """Khôi phục req_id của luồng sau khi ``attach_request``."""
    _local.req_id = previous


# ─────────────────────────────────────────────────────────────────────────────
# Theo dõi tiến trình con
# ─────────────────────────────────────────────────────────────────────────────

def register_process(proc: subprocess.Popen) -> None:
    req_id = current_request_id()
    if not req_id:
        return
    with _lock:
        state = _registry.get(req_id)
        if state is not None:
            state.procs.add(proc)


def unregister_process(proc: subprocess.Popen) -> None:
    req_id = current_request_id()
    if not req_id:
        return
    with _lock:
        state = _registry.get(req_id)
        if state is not None:
            state.procs.discard(proc)


def _kill(proc: subprocess.Popen) -> None:
    """Kill cả nhóm tiến trình — yt-dlp/gallery-dl còn sinh ffmpeg, curl con."""
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, OSError):
        try:
            proc.kill()
        except Exception:
            pass
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Huỷ
# ─────────────────────────────────────────────────────────────────────────────

def cancel_request(req_id: str) -> int:
    """Đánh dấu huỷ và kill mọi tiến trình con của request. Trả về số tiến trình đã kill."""
    if not req_id:
        return 0
    with _lock:
        # Huỷ một request đã xong (hoặc không tồn tại) để lại state mồ côi mà
        # end_request() không bao giờ gỡ — dọn các state như vậy theo tuổi.
        now = time.monotonic()
        for rid in [
            rid for rid, st in _registry.items()
            if not st.active and st.cancelled and now - st.created > _STALE_CANCEL_TTL
        ]:
            _registry.pop(rid, None)
        state = _registry.setdefault(req_id, _RequestState())
        state.cancelled = True
        procs = list(state.procs)
        state.procs.clear()

    for proc in procs:
        _kill(proc)
    return len(procs)


def is_cancelled(req_id: Optional[str] = None) -> bool:
    rid = req_id or current_request_id()
    if not rid:
        return False
    with _lock:
        state = _registry.get(rid)
        return bool(state and state.cancelled)


def raise_if_cancelled() -> None:
    rid = current_request_id()
    if rid and is_cancelled(rid):
        raise RequestCancelled(rid)


def remaining_time() -> Optional[float]:
    """Số giây còn lại trong ngân sách của request hiện tại (None = không giới hạn)."""
    rid = current_request_id()
    if not rid:
        return None
    with _lock:
        state = _registry.get(rid)
        deadline = state.deadline if state else None
    if deadline is None:
        return None
    return deadline - time.monotonic()


def cap_timeout(timeout: float, minimum: float = 1.0) -> float:
    """Giới hạn `timeout` theo ngân sách còn lại; hết ngân sách thì ném RequestTimedOut."""
    left = remaining_time()
    if left is None:
        return timeout
    if left < minimum:
        raise RequestTimedOut(current_request_id())
    return min(timeout, left)
