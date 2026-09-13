#!/usr/bin/env python3
"""
export_browser_cookies.py
Xuất cookies từ trình duyệt (Microsoft Edge, Google Chrome, Mozilla Firefox, Brave, Chromium)
ra định dạng chuẩn Netscape HTTP Cookie File (.txt) dùng cho gallery-dl và yt-dlp.

Hỗ trợ Linux (GNOME Keyring / SecretStorage) với thuật toán tự động dò khóa giải mã chính xác,
không bị lỗi chọn sai ứng dụng Electron (Claude, Cursor, v.v.) như yt-dlp mặc định.
Hỗ trợ đọc Firefox (cả phiên bản cài đặt thường và Snap / Flatpak).
Hỗ trợ Smart Merge/Fallback giữa các trình duyệt khi phát hiện thiếu token xác thực.
"""

import argparse
import glob
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from typing import Dict, List, Optional, Set, Tuple

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
    def __init__(self):
        self.home = os.path.expanduser("~")
        self._key_cache: Dict[str, bytes] = {}

    def get_chromium_cookie_db_paths(self, browser: str) -> List[str]:
        """Trả về danh sách đường dẫn SQLite Cookies khả dĩ cho browser Chromium"""
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
        elif browser == "opera":
            candidates.append(os.path.join(self.home, ".config/opera/Cookies"))
            candidates.append(os.path.join(self.home, ".config/opera/Network/Cookies"))

        return [p for p in candidates if os.path.exists(p)]

    def get_firefox_cookie_db_paths(self) -> List[str]:
        """Tìm file cookies.sqlite của Firefox trong profile thường, Snap và Flatpak"""
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
        """Lấy danh sách các mật khẩu khả dĩ từ GNOME Keyring và fallback"""
        candidates: List[Tuple[str, bytes]] = []

        if HAS_SECRETSTORAGE:
            try:
                bus = secretstorage.dbus_init()
                collection = secretstorage.get_default_collection(bus)
                for item in collection.get_all_items():
                    lbl = item.get_label()
                    attrs = item.get_attributes()
                    app = attrs.get("application", "")

                    # Ưu tiên cao nhất: đúng app chromium hoặc microsoft-edge
                    if "chromium" in app.lower() or "edge" in app.lower() or "chrome" in app.lower():
                        candidates.insert(0, (f"keyring:{app}:{lbl}", item.get_secret()))
                    elif "safe storage" in lbl.lower():
                        candidates.append((f"keyring:{app}:{lbl}", item.get_secret()))
            except Exception as e:
                pass

        # Thêm default passwords của Chromium trên Linux
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
        """Giải mã AES-CBC với key và IV = 16 spaces"""
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
        """Dò tìm key giải mã chính xác bằng cách thử giải mã một cookie mã hóa thực tế trong DB"""
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
            # Không có cookie mã hóa nào, dùng default peanuts
            return hashlib.pbkdf2_hmac("sha1", b"peanuts", b"saltysalt", 1, 16)

        for name, pwd in candidate_passwords:
            key = hashlib.pbkdf2_hmac("sha1", pwd, b"saltysalt", 1, 16)
            for enc in sample_encrypted:
                is_v11 = enc.startswith(b"v11")
                ciphertext = enc[3:]
                result = self.decrypt_aes_cbc(key, ciphertext, hash_prefix=is_v11)
                if result and any(ch.isalnum() for ch in result):
                    return key

        return None

    def extract_chromium_cookies(self, browser: str) -> List[Tuple[str, str, str, str, int, str, str]]:
        """
        Trích xuất và giải mã toàn bộ cookies từ trình duyệt Chromium.
        Trả về list tuple: (host, domain_flag, path, secure_flag, expiry_unix, name, value)
        """
        db_paths = self.get_chromium_cookie_db_paths(browser)
        if not db_paths:
            return []

        db_path = db_paths[0]
        cookies_out = []

        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_db = os.path.join(tmp_dir, "Cookies")
            try:
                shutil.copy2(db_path, temp_db)
                # Copy cả journal và wal nếu có
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
                # Chrome epoch (microseconds since 1601-01-01) -> Unix epoch
                exp_unix = (exp // 1000000) - 11644473600 if exp > 0 else 0
                if exp_unix < 0:
                    exp_unix = 0

                cookies_out.append((host, domain_flag, path, sec_flag, exp_unix, name, cookie_val))

        return cookies_out

    def extract_firefox_cookies(self) -> List[Tuple[str, str, str, str, int, str, str]]:
        """
        Trích xuất cookies từ SQLite của Firefox (không cần giải mã vì lưu plaintext).
        Trả về list tuple: (host, domain_flag, path, secure_flag, expiry_unix, name, value)
        """
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
                exp_unix = exp if exp > 0 else 0
                cookies_out.append((host, domain_flag, path, sec_flag, exp_unix, name, val or ""))

        return cookies_out

    def export_cookies(
        self,
        browser: str,
        output_file: str,
        platform: Optional[str] = None,
        enable_fallback: bool = True,
    ) -> Dict:
        """
        Xuất file cookies cho browser. Nếu thiếu token xác thực cho platform,
        tự động quét và bổ sung cookies từ trình duyệt khác (Smart Merge).
        """
        browser = browser.lower().strip()
        sources_used = []
        cookies_map: Dict[Tuple[str, str, str], Tuple[str, str, str, str, int, str, str]] = {}

        # 1. Trích xuất từ browser được yêu cầu
        primary_cookies = []
        if browser in ("edge", "microsoft-edge", "chrome", "google-chrome", "brave", "chromium", "opera"):
            primary_cookies = self.extract_chromium_cookies(browser)
            if primary_cookies:
                sources_used.append(browser)
        elif browser == "firefox":
            primary_cookies = self.extract_firefox_cookies()
            if primary_cookies:
                sources_used.append("firefox")

        for c in primary_cookies:
            # key: (host, path, name)
            cookies_map[(c[0], c[2], c[5])] = c

        # 2. Kiểm tra token thiết yếu theo platform
        has_twitter_auth = any(
            c[5] == "auth_token" and bool(c[6].strip()) and ("x.com" in c[0] or "twitter.com" in c[0])
            for c in cookies_map.values()
        )
        has_insta_auth = any(
            c[5] == "sessionid" and bool(c[6].strip()) and "instagram.com" in c[0]
            for c in cookies_map.values()
        )
        has_reddit_auth = any(
            c[5] in ("reddit_session", "token_v2", "loid") and bool(c[6].strip()) and "reddit.com" in c[0]
            for c in cookies_map.values()
        )

        need_twitter_fallback = (platform in ("twitter", "x", None, "all") and not has_twitter_auth)
        need_insta_fallback = (platform in ("instagram", None, "all") and not has_insta_auth)
        need_reddit_fallback = (platform in ("reddit", None, "all") and not has_reddit_auth)

        # 3. Smart Fallback/Merge nếu cần
        if enable_fallback and (need_twitter_fallback or need_insta_fallback or need_reddit_fallback):
            # Nếu đang dùng Edge/Chrome mà thiếu token, thử bổ sung từ Firefox
            if browser != "firefox":
                ff_cookies = self.extract_firefox_cookies()
                if ff_cookies:
                    added_count = 0
                    for c in ff_cookies:
                        k = (c[0], c[2], c[5])
                        is_target_token = (
                            (need_twitter_fallback and ("x.com" in c[0] or "twitter.com" in c[0])) or
                            (need_insta_fallback and "instagram.com" in c[0]) or
                            (need_reddit_fallback and "reddit.com" in c[0])
                        )
                        # Bổ sung nếu chưa có hoặc cookie hiện tại đang rỗng
                        if is_target_token:
                            if k not in cookies_map or not cookies_map[k][6].strip():
                                cookies_map[k] = c
                                added_count += 1
                    if added_count > 0:
                        sources_used.append("firefox(fallback)")

            # Nếu đang dùng Firefox mà thiếu token, thử bổ sung từ Edge / Chromium
            if browser == "firefox" or need_reddit_fallback:
                edge_cookies = self.extract_chromium_cookies("edge")
                if edge_cookies:
                    added_count = 0
                    for c in edge_cookies:
                        k = (c[0], c[2], c[5])
                        is_target_token = (
                            (need_twitter_fallback and ("x.com" in c[0] or "twitter.com" in c[0])) or
                            (need_insta_fallback and "instagram.com" in c[0]) or
                            (need_reddit_fallback and "reddit.com" in c[0])
                        )
                        if is_target_token:
                            if k not in cookies_map or not cookies_map[k][6].strip():
                                cookies_map[k] = c
                                added_count += 1
                    if added_count > 0 and "edge(fallback)" not in sources_used and browser != "edge":
                        sources_used.append("edge(fallback)")

        # 4. Ghi ra file Netscape format
        os.makedirs(os.path.dirname(os.path.abspath(output_file)), exist_ok=True)
        lines = [
            "# Netscape HTTP Cookie File",
            f"# Generated by export_browser_cookies.py (sources: {', '.join(sources_used)})",
            "",
        ]
        for host, domain_flag, path, sec_flag, exp_unix, name, val in cookies_map.values():
            lines.append(f"{host}\t{domain_flag}\t{path}\t{sec_flag}\t{exp_unix}\t{name}\t{val}")

        with open(output_file, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

        # Kiểm tra lại trạng thái auth sau khi merge
        final_twitter_auth = any(
            c[5] == "auth_token" and bool(c[6].strip()) and ("x.com" in c[0] or "twitter.com" in c[0])
            for c in cookies_map.values()
        )
        final_insta_auth = any(
            c[5] == "sessionid" and bool(c[6].strip()) and "instagram.com" in c[0]
            for c in cookies_map.values()
        )
        final_reddit_auth = any(
            c[5] in ("reddit_session", "token_v2", "loid") and bool(c[6].strip()) and "reddit.com" in c[0]
            for c in cookies_map.values()
        )

        return {
            "success": len(cookies_map) > 0,
            "cookie_count": len(cookies_map),
            "output_file": output_file,
            "sources": sources_used,
            "has_twitter_auth": final_twitter_auth,
            "has_instagram_auth": final_insta_auth,
            "has_reddit_auth": final_reddit_auth,
        }


def main():
    parser = argparse.ArgumentParser(description="Export browser cookies for gallery-dl and yt-dlp")
    parser.add_argument("--browser", "-b", default="edge", help="Browser to export cookies from (edge, chrome, firefox, etc.)")
    parser.add_argument("--output", "-o", required=True, help="Output Netscape .txt file path")
    parser.add_argument("--platform", "-p", default=None, help="Target platform (twitter, x, instagram, etc.)")
    parser.add_argument("--no-fallback", action="store_true", help="Disable smart fallback between browsers")

    args = parser.parse_args()

    exporter = BrowserCookieExporter()
    result = exporter.export_cookies(
        browser=args.browser,
        output_file=args.output,
        platform=args.platform,
        enable_fallback=not args.no_fallback,
    )

    print(json.dumps(result))
    sys.exit(0 if result["success"] else 1)


if __name__ == "__main__":
    main()
