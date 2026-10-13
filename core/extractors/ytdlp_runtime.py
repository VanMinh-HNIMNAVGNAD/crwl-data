"""
Chạy yt-dlp bản zipapp từ một bản GIẢI NÉN có bytecode.

Bản yt-dlp chính thức cho Linux/macOS (tệp `yt-dlp` cài bằng curl, cập nhật bằng
`yt-dlp -U`) là một zipapp: Python không lưu được bytecode cho mã nằm trong tệp zip
nên MỖI lần chạy phải biên dịch lại toàn bộ mã nó dùng. Đo trên máy thật: `--version`
0,75 s (giải nén: 0,24 s), bóc tách một video YouTube 3,2-3,7 s (giải nén: 1,8-2,1 s).
App gọi yt-dlp cho mọi lượt phân tích và mọi lượt tải nên phần này cộng dồn rất lớn.

Engine giải nén zipapp MỘT lần cho mỗi phiên bản yt-dlp vào thư mục cache của app,
ghi tệp mô tả `crwl-runtime.json`; cả engine (Python) lẫn bước tải (Rust) đọc tệp
đó và chạy `<python> <thư mục>/__main__.py` — đúng như cách hệ điều hành chạy zipapp
(trình thông dịch lấy từ dòng `#!`, thư mục mã nằm đầu sys.path). Phiên bản đổi (sau
`yt-dlp -U`) thì kích thước / thời điểm sửa của tệp đổi, mô tả cũ không còn khớp và
mọi nơi tự quay về chạy zipapp gốc cho tới khi bản mới được giải nén.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

MANIFEST_NAME = "crwl-runtime.json"
# Thư mục giải nén của phiên bản cũ chỉ bị xoá khi đã cũ hơn chừng này: một lượt tải
# đang chạy từ thư mục đó vẫn nạp thêm module (ghép video, extractor...) giữa chừng.
STALE_AFTER_SEC = 24 * 3600


@dataclass(frozen=True)
class UnpackedRuntime:
    python: str
    main: str
    js_runtimes: bool

    @property
    def command(self) -> List[str]:
        return [self.python, self.main]


def cache_root() -> str:
    """<cache của người dùng>/crwl/ytdlp-unpacked — khớp với `dirs::cache_dir()` bên Rust."""
    base = os.environ.get("CRWL_CACHE_DIR")
    if not base:
        if sys.platform == "darwin":
            root = os.path.join(os.path.expanduser("~"), "Library", "Caches")
        elif sys.platform == "win32":
            root = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        else:
            xdg = os.environ.get("XDG_CACHE_HOME", "")
            root = xdg if os.path.isabs(xdg) else os.path.join(os.path.expanduser("~"), ".cache")
        base = os.path.join(root, "crwl")
    return os.path.join(base, "ytdlp-unpacked")


def _identity(binary: str) -> Optional[Tuple[str, int, int]]:
    try:
        real = os.path.realpath(binary)
        st = os.stat(real)
    except OSError:
        return None
    return real, st.st_size, st.st_mtime_ns


def _key(size: int, mtime_ns: int) -> str:
    return f"{size}-{mtime_ns}"


def _read_manifest(path: str) -> Optional[dict]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _shebang_interpreter(binary: str) -> Optional[str]:
    """Trình thông dịch mà hệ điều hành sẽ dùng cho zipapp (dòng `#!`)."""
    try:
        with open(binary, "rb") as fh:
            first = fh.readline(512)
    except OSError:
        return None
    if not first.startswith(b"#!"):
        return None
    parts = first[2:].decode("utf-8", errors="replace").strip().split()
    if not parts:
        return None
    if os.path.basename(parts[0]) == "env":
        # "#!/usr/bin/env python3" — tuỳ chọn thêm (env -S ...) thì không đoán, chạy zipapp gốc
        if len(parts) != 2 or parts[1].startswith("-"):
            return None
        return shutil.which(parts[1])
    if len(parts) != 1 or not os.path.isabs(parts[0]):
        return None
    return parts[0] if os.access(parts[0], os.X_OK) else None


class _State:
    lock = threading.Lock()
    preparing: set = set()
    found: Dict[Tuple[str, int, int], Optional[UnpackedRuntime]] = {}


def lookup(binary: Optional[str]) -> Optional[UnpackedRuntime]:
    """Bản giải nén còn khớp với `binary`, hoặc None (chạy chính `binary`)."""
    if not binary or sys.platform == "win32":
        return None
    ident = _identity(binary)
    if ident is None:
        return None
    with _State.lock:
        if ident in _State.found:
            cached = _State.found[ident]
            # Thư mục cache bị dọn khi app đang chạy (BleachBit, `rm -rf ~/.cache`...):
            # không được trỏ mãi vào tệp đã mất — mọi lượt phân tích sẽ hỏng tới khi
            # khởi động lại app. Quên nó đi để chạy tệp gốc và giải nén lại ở nền.
            if cached is None or os.path.isfile(cached.main):
                return cached
            del _State.found[ident]
    real, size, mtime_ns = ident
    folder = os.path.join(cache_root(), _key(size, mtime_ns))
    data = _read_manifest(os.path.join(folder, MANIFEST_NAME))
    runtime = None
    if (
        data
        and data.get("source") == real
        and data.get("size") == size
        and data.get("mtime_ns") == mtime_ns
        and isinstance(data.get("python"), str)
        and os.access(data["python"], os.X_OK)
        and os.path.isfile(os.path.join(folder, "__main__.py"))
    ):
        runtime = UnpackedRuntime(
            python=data["python"],
            main=os.path.join(folder, "__main__.py"),
            js_runtimes=bool(data.get("js_runtimes")),
        )
    if runtime is not None:
        with _State.lock:
            _State.found[ident] = runtime
    return runtime


def is_zipapp(binary: str) -> bool:
    try:
        with open(binary, "rb") as fh:
            if fh.read(2) != b"#!":
                return False
        return zipfile.is_zipfile(binary)
    except OSError:
        return False


def prepare(binary: Optional[str]) -> Optional[UnpackedRuntime]:
    """Giải nén zipapp (nếu cần) rồi trả về bản giải nén. Chạy được từ nhiều luồng /
    nhiều tiến trình: thư mục chỉ xuất hiện (đổi tên nguyên tử) khi đã đầy đủ."""
    if not binary or sys.platform == "win32":
        return None
    existing = lookup(binary)
    if existing is not None:
        return existing
    ident = _identity(binary)
    if ident is None:
        return None
    real, size, mtime_ns = ident
    with _State.lock:
        if ident in _State.preparing or ident in _State.found:
            return _State.found.get(ident)
        _State.preparing.add(ident)
    runtime = None
    try:
        runtime = _prepare(real, size, mtime_ns)
    except Exception as err:  # bất kỳ lỗi nào → chạy zipapp gốc như trước
        sys.stderr.write(f"[Extractor:WARN] Không giải nén được yt-dlp để chạy nhanh hơn: {err}\n")
    finally:
        with _State.lock:
            # Không giải nén được (không phải zipapp, thiếu quyền ghi...): nhớ để không thử
            # lại ở mỗi lượt — tới khi tệp yt-dlp đổi (định danh mới).
            _State.found.setdefault(ident, runtime)
            _State.preparing.discard(ident)
    return runtime


def _prepare(real: str, size: int, mtime_ns: int) -> Optional[UnpackedRuntime]:
    if not is_zipapp(real):
        return None
    # Cấu hình / plugin "portable" đặt cạnh tệp yt-dlp chỉ được nạp khi chạy đúng tệp đó
    here = os.path.dirname(real)
    if any(os.path.exists(os.path.join(here, n)) for n in ("yt-dlp.conf", "yt-dlp-plugins", "yt_dlp_plugins")):
        return None
    python = _shebang_interpreter(real)
    if not python:
        return None

    root = cache_root()
    os.makedirs(root, mode=0o700, exist_ok=True)
    final = os.path.join(root, _key(size, mtime_ns))
    tmp = tempfile.mkdtemp(prefix=".unpack-", dir=root)
    try:
        with zipfile.ZipFile(real) as zf:
            names = set(zf.namelist())
            if "__main__.py" not in names or "yt_dlp/__init__.py" not in names:
                return None
            zf.extractall(tmp)
        try:
            with open(os.path.join(tmp, "yt_dlp", "options.py"), "r", encoding="utf-8", errors="replace") as fh:
                js_runtimes = "--js-runtimes" in fh.read()
        except OSError:
            js_runtimes = False
        manifest = {
            "source": real,
            "size": size,
            "mtime_ns": mtime_ns,
            "python": python,
            "js_runtimes": js_runtimes,
            "created": int(time.time()),
        }
        with open(os.path.join(tmp, MANIFEST_NAME), "w", encoding="utf-8") as fh:
            json.dump(manifest, fh)
        try:
            os.rename(tmp, final)
            tmp = None
        except OSError:
            # Tiến trình khác vừa giải nén xong cùng phiên bản
            if not os.path.isfile(os.path.join(final, MANIFEST_NAME)):
                return None
    finally:
        if tmp:
            shutil.rmtree(tmp, ignore_errors=True)

    runtime = lookup(real)
    if runtime is not None:
        # Biên dịch sẵn phần mã yt-dlp nạp khi khởi động (ghi __pycache__ cạnh mã)
        try:
            subprocess.run(runtime.command + ["--version"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=60, check=False)
        except (OSError, subprocess.SubprocessError):
            pass
        _remove_stale(root, os.path.basename(final))
    return runtime


def _remove_stale(root: str, keep: str) -> None:
    now = time.time()
    try:
        entries = os.listdir(root)
    except OSError:
        return
    for name in entries:
        path = os.path.join(root, name)
        if name == keep or not os.path.isdir(path):
            continue
        try:
            age = now - os.path.getmtime(path)
        except OSError:
            continue
        # Thư mục tạm bỏ dở (tiến trình bị kill giữa chừng) hoặc phiên bản cũ
        if age > STALE_AFTER_SEC or (name.startswith(".unpack-") and age > 3600):
            shutil.rmtree(path, ignore_errors=True)


_background_enabled = False


def enable_background_prepare() -> None:
    """Cho phép giải nén ở nền — chỉ engine chạy lâu (sidecar của app) bật. Lệnh CLI
    chạy một lần thoát ngay sau khi xong, luồng nền bị huỷ giữa chừng và để lại thư mục
    giải nén dở."""
    global _background_enabled
    _background_enabled = True


def prepare_in_background(binary: Optional[str]) -> None:
    """Giải nén ở luồng nền để không làm chậm yêu cầu đang chờ."""
    if not binary or sys.platform == "win32" or not _background_enabled:
        return
    ident = _identity(binary)
    with _State.lock:
        if ident is None or ident in _State.found or ident in _State.preparing:
            return
    threading.Thread(target=prepare, args=(binary,), name="crwl-ytdlp-unpack", daemon=True).start()


def command_for(binary: str) -> List[str]:
    """Lệnh chạy yt-dlp: bản giải nén nếu đã sẵn sàng, nếu chưa thì chính `binary`
    (và chuẩn bị bản giải nén ở nền cho các lần sau)."""
    runtime = lookup(binary)
    if runtime is not None:
        return runtime.command
    prepare_in_background(binary)
    return [binary]
