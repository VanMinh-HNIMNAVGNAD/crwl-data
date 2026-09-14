"""
Linux Browser Cookie Exporter.
Extracts cookies from Chrome, Edge, Firefox, Brave, Chromium on Linux
into Netscape HTTP Cookie file (.txt) format for yt-dlp and gallery-dl.
"""

import os
import sys
import glob
import shutil
import sqlite3
import hashlib
import tempfile
from typing import Dict, List, Optional, Tuple, Set

# Try importing cryptography & secretstorage
try:
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

try:
    import secretstorage
    HAS_SECRETSTORAGE = True
except ImportError:
    HAS_SECRETSTORAGE = False


class BrowserCookieExporter:
    """Trích xuất cookies từ trình duyệt trên Linux"""

    def __init__(self):
        self.home = os.path.expanduser("~")

    def get_chromium_cookie_db_paths(self, browser: str) -> List[str]:
        browser = browser.lower()
        candidates = []
        if browser in ("edge", "microsoft-edge"):
            base = os.path.join(self.home, ".config/microsoft-edge")
            for profile in ("Default", "Profile 1", "Profile 2"):
                candidates.append(os.path.join(base, profile, "Cookies"))
                candidates.append(os.path.join(base, profile, "Network", "Cookies"))
        elif browser in ("chrome", "google-chrome"):
            base = os.path.join(self.home, ".config/google-chrome")
            for profile in ("Default", "Profile 1", "Profile 2"):
                candidates.append(os.path.join(base, profile, "Cookies"))
                candidates.append(os.path.join(base, profile, "Network", "Cookies"))
        elif browser in ("brave", "brave-browser"):
            base = os.path.join(self.home, ".config/BraveSoftware/Brave-Browser")
            for profile in ("Default", "Profile 1"):
                candidates.append(os.path.join(base, profile, "Cookies"))
                candidates.append(os.path.join(base, profile, "Network", "Cookies"))
        elif browser in ("chromium", "chromium-browser"):
            base = os.path.join(self.home, ".config/chromium")
            for profile in ("Default", "Profile 1"):
                candidates.append(os.path.join(base, profile, "Cookies"))
                candidates.append(os.path.join(base, profile, "Network", "Cookies"))

        return [p for p in candidates if os.path.exists(p)]

    def get_firefox_cookie_db_paths(self) -> List[str]:
        patterns = [
            os.path.join(self.home, ".mozilla/firefox/*/cookies.sqlite"),
            os.path.join(self.home, "snap/firefox/common/.mozilla/firefox/*/cookies.sqlite"),
            os.path.join(self.home, ".var/app/org.mozilla.firefox/.mozilla/firefox/*/cookies.sqlite"),
        ]
        results = []
        for pat in patterns:
            for p in glob.glob(pat):
                if os.path.isfile(p):
                    results.append(p)
        return sorted(results, key=lambda x: os.path.getmtime(x), reverse=True)

    def get_candidate_passwords(self, browser: str) -> List[Tuple[str, bytes]]:
        candidates: List[Tuple[str, bytes]] = []
        if HAS_SECRETSTORAGE:
            try:
                bus = secretstorage.dbus_init()
                collection = secretstorage.get_default_collection(bus)
                for item in collection.get_all_items():
                    lbl = item.get_label()
                    attrs = item.get_attributes()
                    app = attrs.get("application", "")
                    if "chromium" in app.lower() or "edge" in app.lower() or "chrome" in app.lower():
                        candidates.insert(0, (f"keyring:{app}:{lbl}", item.get_secret()))
                    elif "safe storage" in lbl.lower():
                        candidates.append((f"keyring:{app}:{lbl}", item.get_secret()))
            except Exception:
                pass

        candidates.append(("basic:peanuts", b"peanuts"))
        candidates.append(("empty", b""))
        return candidates

    def unpad_pkcs7(self, data: bytes) -> bytes:
        if not data:
            return b""
        pad_len = data[-1]
        if 1 <= pad_len <= 16:
            return data[:-pad_len]
        return data

    def decrypt_aes_cbc(self, key: bytes, ciphertext: bytes, hash_prefix: bool = True) -> Optional[str]:
        if not HAS_CRYPTO:
            return None
        try:
            cipher = Cipher(algorithms.AES(key), modes.CBC(b" " * 16), backend=default_backend())
            dec = cipher.decryptor().update(ciphertext) + cipher.decryptor().finalize()
            dec = self.unpad_pkcs7(dec)
            if hash_prefix and len(dec) > 32:
                try:
                    return dec[32:].decode("utf-8")
                except UnicodeDecodeError:
                    pass
            return dec.decode("utf-8")
        except Exception:
            return None

    def find_working_key(self, db_copy_path: str, candidate_passwords: List[Tuple[str, bytes]]) -> Optional[bytes]:
        if not HAS_CRYPTO:
            return None
        try:
            conn = sqlite3.connect(db_copy_path)
            c = conn.cursor()
            c.execute("SELECT encrypted_value FROM cookies WHERE length(encrypted_value) > 3 LIMIT 50")
            sample_encrypted = [r[0] for r in c.fetchall() if r[0] and (r[0].startswith(b"v10") or r[0].startswith(b"v11"))]
            conn.close()
        except Exception:
            return None

        if not sample_encrypted:
            return hashlib.pbkdf2_hmac("sha1", b"peanuts", b"saltysalt", 1, 16)

        for _, pwd in candidate_passwords:
            key = hashlib.pbkdf2_hmac("sha1", pwd, b"saltysalt", 1, 16)
            for enc in sample_encrypted:
                is_v11 = enc.startswith(b"v11")
                ciphertext = enc[3:]
                result = self.decrypt_aes_cbc(key, ciphertext, hash_prefix=is_v11)
                if result and any(ch.isalnum() for ch in result):
                    return key
        return None

    def extract_chromium_cookies(self, browser: str) -> List[Tuple[str, str, str, str, int, str, str]]:
        db_paths = self.get_chromium_cookie_db_paths(browser)
        if not db_paths:
            return []

        db_path = db_paths[0]
        cookies_out = []

        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_db = os.path.join(tmp_dir, "Cookies")
            try:
                shutil.copy2(db_path, temp_db)
                for ext in ("-journal", "-wal", "-shm"):
                    src = db_path + ext
                    if os.path.exists(src):
                        shutil.copy2(src, temp_db + ext)
            except Exception:
                return []

            candidate_pwds = self.get_candidate_passwords(browser)
            key = self.find_working_key(temp_db, candidate_pwds)

            try:
                conn = sqlite3.connect(temp_db)
                c = conn.cursor()
                c.execute("SELECT host_key, path, is_secure, expires_utc, name, encrypted_value, value FROM cookies")
                rows = c.fetchall()
                conn.close()
            except Exception:
                return []

            for host, path, is_sec, exp, name, enc, val in rows:
                cookie_val = val or ""
                if enc and (enc.startswith(b"v10") or enc.startswith(b"v11")):
                    if key:
                        is_v11 = enc.startswith(b"v11")
                        dec = self.decrypt_aes_cbc(key, enc[3:], hash_prefix=is_v11)
                        if dec is not None:
                            cookie_val = dec

                domain_flag = "TRUE" if host.startswith(".") else "FALSE"
                sec_flag = "TRUE" if is_sec else "FALSE"
                exp_unix = (exp // 1000000) - 11644473600 if exp > 0 else 0
                if exp_unix < 0:
                    exp_unix = 0

                cookies_out.append((host, domain_flag, path, sec_flag, exp_unix, name, cookie_val))

        return cookies_out

    def extract_firefox_cookies(self) -> List[Tuple[str, str, str, str, int, str, str]]:
        db_paths = self.get_firefox_cookie_db_paths()
        if not db_paths:
            return []

        db_path = db_paths[0]
        cookies_out = []

        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_db = os.path.join(tmp_dir, "cookies.sqlite")
            try:
                shutil.copy2(db_path, temp_db)
                for ext in ("-wal", "-shm"):
                    src = db_path + ext
                    if os.path.exists(src):
                        shutil.copy2(src, temp_db + ext)
            except Exception:
                return []

            try:
                conn = sqlite3.connect(temp_db)
                c = conn.cursor()
                c.execute("SELECT host, path, isSecure, expiry, name, value FROM moz_cookies")
                rows = c.fetchall()
                conn.close()
            except Exception:
                return []

            for host, path, is_sec, exp, name, val in rows:
                domain_flag = "TRUE" if host.startswith(".") else "FALSE"
                sec_flag = "TRUE" if is_sec else "FALSE"
                exp_unix = exp if exp else 0
                cookies_out.append((host, domain_flag, path, sec_flag, exp_unix, name, val or ""))

        return cookies_out

    def export_cookies_netscape(self, browser: str, domain_filter: Optional[str] = None) -> str:
        """Xuất cookies sang chuỗi định dạng Netscape chuẩn"""
        b = browser.lower()
        if "firefox" in b:
            raw_cookies = self.extract_firefox_cookies()
        else:
            raw_cookies = self.extract_chromium_cookies(b)

        lines = [
            "# Netscape HTTP Cookie File",
            "# http://curl.haxx.se/rfc/cookie_spec.html",
            "# This is a generated file! Do not edit.",
            "",
        ]

        filter_clean = domain_filter.lower().lstrip(".") if domain_filter else None

        for host, domain_flag, path, sec_flag, exp_unix, name, val in raw_cookies:
            if filter_clean:
                h = host.lower().lstrip(".")
                if h != filter_clean and not h.endswith("." + filter_clean):
                    continue
            lines.append(f"{host}\t{domain_flag}\t{path}\t{sec_flag}\t{exp_unix}\t{name}\t{val}")

        return "\n".join(lines) + "\n"


def get_browser_cookies_txt(browser: str, domain: Optional[str] = None, output_path: Optional[str] = None) -> Optional[str]:
    """Helper: lấy cookies file cho browser, trả về đường dẫn file cookies tạm"""
    try:
        exporter = BrowserCookieExporter()
        content = exporter.export_cookies_netscape(browser, domain)
        if not content.strip():
            return None
        if not output_path:
            fd, tmp_file = tempfile.mkstemp(prefix="cookies_", suffix=".txt")
            os.close(fd)
            output_path = tmp_file
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(content)
        return output_path
    except Exception as e:
        sys.stderr.write(f"[Cookies] Export warning: {e}\n")
        return None
