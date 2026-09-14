"""
Base Extractor class.
Provides executable discovery, subprocess execution with timeouts,
and stderr logging helpers.
"""

import os
import sys
import shutil
import subprocess
from typing import List, Optional, Tuple, Dict, Any


class BaseExtractor:
    """Lớp cơ sở cho các engine trích xuất"""

    def __init__(self):
        self.project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

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
        timeout: int = 60,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> Tuple[int, str, str]:
        """Thực thi tiến trình và thu thập kết quả với timeout"""
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd or self.project_root,
                env=merged_env,
            )
            return proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired:
            self.warn(f"Command timed out ({timeout}s): {' '.join(cmd[:4])}...")
            return -1, "", f"Timeout after {timeout} seconds"
        except Exception as e:
            self.error(f"Failed to run command: {e}")
            return -1, "", str(e)
