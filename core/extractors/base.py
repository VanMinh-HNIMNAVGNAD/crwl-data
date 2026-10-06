"""
Base Extractor class.
Provides executable discovery, subprocess execution with timeouts,
and stderr logging helpers.
"""

import os
import sys
import signal
import shutil
import subprocess
from typing import List, Optional, Tuple, Dict, Any

from ..cancellation import (
    cap_timeout,
    raise_if_cancelled,
    register_process,
    unregister_process,
)

# Đuôi stderr mà run_process() gắn vào khi phải kill tiến trình vì quá giờ
TIMEOUT_MARK = "Timeout after"


class BaseExtractor:
    """Lớp cơ sở cho các engine trích xuất"""

    def __init__(self):
        self.project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

    @staticmethod
    def is_timeout(code: int, stderr: str) -> bool:
        """run_process() đã kill tiến trình vì quá giờ (stdout có thể vẫn có dữ liệu dở dang)."""
        return code == -1 and TIMEOUT_MARK in (stderr or "")

    @staticmethod
    def log(msg: str) -> None:
        """Ghi log ra stderr để không làm ô nhiễm stdout JSON"""
        sys.stderr.write(f"[Extractor] {msg}\n")
        sys.stderr.flush()

    @staticmethod
    def warn(msg: str) -> None:
        sys.stderr.write(f"[Extractor:WARN] {msg}\n")
        sys.stderr.flush()

    @staticmethod
    def error(msg: str) -> None:
        sys.stderr.write(f"[Extractor:ERROR] {msg}\n")
        sys.stderr.flush()

    def find_binary(self, name: str, env_var: Optional[str] = None) -> Optional[str]:
        # 1. Biến môi trường
        if env_var and os.environ.get(env_var):
            p = os.environ[env_var]
            if os.path.isfile(p) and os.access(p, os.X_OK):
                return p

        # 2. System PATH
        system_path = shutil.which(name)
        if system_path:
            return system_path

        # 3. Local dirs (apps/backend/bin hoặc bin)
        candidates = [
            os.path.join(self.project_root, "bin", name),
            os.path.join(self.project_root, "apps", "backend", "bin", name),
            os.path.join(os.path.expanduser("~"), ".local", "bin", name),
        ]
        for c in candidates:
            if os.path.isfile(c) and os.access(c, os.X_OK):
                return c

        return None

    def run_process(
        self,
        cmd: List[str],
        timeout: float = 60,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> Tuple[int, str, str]:
        """Thực thi tiến trình và thu thập kết quả với timeout.

        Dùng Popen + os.killpg để đảm bảo kill toàn bộ process group
        (bao gồm child processes của yt-dlp/gallery-dl) khi timeout.
        """
        merged_env = os.environ.copy()
        # yt-dlp / gallery-dl là chương trình Python: buộc xuất UTF-8 để khớp với cách
        # giải mã bên dưới (trên Windows chúng mặc định ghi theo code page của console).
        merged_env.setdefault("PYTHONIOENCODING", "utf-8")
        if env:
            merged_env.update(env)

        popen_kwargs: Dict[str, Any] = {}
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True

        # Đã bị huỷ trước khi kịp chạy thì đừng khởi động tiến trình nào nữa.
        raise_if_cancelled()
        # Không chạy quá ngân sách thời gian Rust cấp cho request; hết hẳn thì
        # cap_timeout() ném RequestTimedOut để dispatcher không thử engine kế tiếp.
        timeout = cap_timeout(timeout)

        try:
            # Tạo process group riêng (CREATE_NEW_PROCESS_GROUP trên Windows, start_new_session trên POSIX) để kill cả nhóm khi timeout
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                # Một byte không phải UTF-8 (tiêu đề, thông báo lỗi theo locale...) từng
                # làm communicate() ném UnicodeDecodeError giữa chừng: mất toàn bộ kết
                # quả và tiến trình con bị bỏ lại không ai đọc.
                encoding="utf-8",
                errors="replace",
                cwd=cwd or self.project_root,
                env=merged_env,
                **popen_kwargs,
            )
            # Đăng ký để lệnh huỷ có thể kill cả nhóm tiến trình này.
            register_process(proc)
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
                # Tiến trình vừa kết thúc có thể vì BỊ KILL do huỷ, chứ không phải
                # chạy xong. Ném RequestCancelled để dispatcher không thử engine kế tiếp.
                raise_if_cancelled()
                return proc.returncode, stdout, stderr
            except subprocess.TimeoutExpired:
                self.warn(f"Command timed out ({timeout:.0f}s): {' '.join(cmd[:4])}...")
                # Kill toàn bộ process group / process tree để không để zombie
                if sys.platform == "win32":
                    try:
                        # Trên Windows, dùng taskkill để kill cả cây tiến trình (/F ép buộc, /T kill tree)
                        subprocess.run(
                            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            check=False,
                        )
                    except Exception:
                        pass
                    try:
                        proc.kill()
                    except (ProcessLookupError, OSError):
                        pass
                else:
                    try:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    except (ProcessLookupError, OSError):
                        proc.kill()
                # Giữ lại phần output đã nhận: yt-dlp --flat-playlist và gallery-dl
                # (output.jsonl) in từng mục ngay khi có, nên quét dở vẫn dùng được.
                partial_out, partial_err = "", ""
                try:
                    partial_out, partial_err = proc.communicate(timeout=3)
                except Exception:
                    pass
                raise_if_cancelled()
                tail = f"{TIMEOUT_MARK} {timeout:.0f} seconds"
                return -1, partial_out or "", f"{(partial_err or '').strip()}\n{tail}".strip()
            finally:
                unregister_process(proc)
        except Exception as e:
            self.error(f"Failed to run command: {e}")
            return -1, "", str(e)
