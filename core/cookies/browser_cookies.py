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
import threading
import time
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


# Các hậu tố TLD 2 cấp phổ biến (co.uk, com.vn...) — cần giữ 3 nhãn thay vì 2
_MULTI_PART_TLDS = {
    "co.uk", "org.uk", "ac.uk", "gov.uk", "co.jp", "or.jp", "ne.jp",
    "com.vn", "net.vn", "org.vn", "edu.vn", "gov.vn",
    "com.br", "com.au", "com.cn", "com.tw", "com.hk", "com.sg",
    "com.mx", "com.ar", "com.tr", "co.kr", "co.in", "co.id", "co.th",
}


def registrable_domain(host: Optional[str]) -> Optional[str]:
    """Rút gọn hostname về domain đăng ký được.

    www.instagram.com -> instagram.com ; m.facebook.com -> facebook.com
    Cookie đăng nhập hầu như luôn nằm trên domain gốc (.instagram.com), nên nếu
    lọc theo hostname đầy đủ thì sẽ vứt mất sessionid/csrftoken.
    """
    if not host:
        return None
    h = host.strip().lower().lstrip(".")
    if not h or h.replace(".", "").isdigit():  # IP literal
        return h or None
    parts = [p for p in h.split(".") if p]
    if len(parts) <= 2:
        return ".".join(parts)
    if ".".join(parts[-2:]) in _MULTI_PART_TLDS and len(parts) >= 3:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


# Tên cookie chứng minh phiên đăng nhập của từng nền tảng — dùng để cảnh báo sớm
# thay vì để extractor chạy rồi thất bại với lỗi khó hiểu.
_SESSION_COOKIE_NAMES = {
    "instagram.com": {"sessionid", "ds_user_id"},
    "facebook.com": {"c_user", "xs"},
    "threads.net": {"sessionid", "ds_user_id"},
    "x.com": {"auth_token", "ct0"},
    "twitter.com": {"auth_token", "ct0"},
    "reddit.com": {"reddit_session", "token_v2"},
    "tiktok.com": {"sessionid", "sessionid_ss"},
    "pinterest.com": {"_pinterest_sess"},
    "youtube.com": {"SID", "__Secure-3PSID"},
}


def _host_matches(host: str, base_domain: str) -> bool:
    h = host.strip().lower().lstrip(".")
    return h == base_domain or h.endswith("." + base_domain)


# Thời gian sống của cache cookie đã xuất (giây)
_EXPORT_CACHE_TTL = float(os.environ.get("CRWL_COOKIE_CACHE_TTL", "120"))
_export_cache: Dict[Tuple[str, Optional[str]], Tuple[float, str]] = {}
_export_cache_lock = threading.Lock()


def _cache_get(key: Tuple[str, Optional[str]]) -> Optional[str]:
    with _export_cache_lock:
        hit = _export_cache.get(key)
        if hit and (time.monotonic() - hit[0]) < _EXPORT_CACHE_TTL:
            return hit[1]
        if hit:
            _export_cache.pop(key, None)
    return None


def _cache_put(key: Tuple[str, Optional[str]], value: str) -> None:
    with _export_cache_lock:
        if len(_export_cache) > 64:
            _export_cache.clear()
        _export_cache[key] = (time.monotonic(), value)


# Cache riêng cho bước TỐN KÉM nhất: đọc + giải mã toàn bộ cookie DB của một
# trình duyệt. Bước lọc theo domain thì rẻ, nên chỉ cần cache dữ liệu thô là đủ
# dùng lại cho mọi domain khác nhau trong cùng một phiên làm việc.
_raw_cache: Dict[str, Tuple[float, List[Tuple[str, str, str, str, int, str, str]]]] = {}
_raw_cache_lock = threading.Lock()


def clear_cookie_cache() -> None:
    """Xoá cache cookie (gọi sau khi người dùng đăng nhập lại trên trình duyệt)."""
    with _export_cache_lock:
        _export_cache.clear()
    with _raw_cache_lock:
        _raw_cache.clear()


class BrowserCookieExporter:
    """Trích xuất cookies từ trình duyệt trên Linux"""

    def __init__(self):
        self.home = os.path.expanduser("~")

    def get_chromium_cookie_db_paths(self, browser: str) -> List[str]:
        browser = browser.lower()
        candidates = []
        if browser in ("edge", "microsoft-edge"):
            base = os.path.join(self.home, ".config/microsoft-edge")
        elif browser in ("chrome", "google-chrome"):
            base = os.path.join(self.home, ".config/google-chrome")
        elif browser in ("brave", "brave-browser"):
            base = os.path.join(self.home, ".config/BraveSoftware/Brave-Browser")
        elif browser in ("chromium", "chromium-browser"):
            base = os.path.join(self.home, ".config/chromium")
        else:
            return []

        # Profiles are user-created and Chromium moved the DB to Network/Cookies.
        # Inspect both locations instead of assuming only the first few profiles.
        for profile_dir in glob.glob(os.path.join(base, "*")):
            if not os.path.isdir(profile_dir):
                continue
            candidates.append(os.path.join(profile_dir, "Cookies"))
            candidates.append(os.path.join(profile_dir, "Network", "Cookies"))

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
            decryptor = cipher.decryptor()
            dec = decryptor.update(ciphertext) + decryptor.finalize()
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
        """Gộp cookies từ TẤT CẢ profile của trình duyệt (Default, Profile 1, ...).

        Trước đây chỉ đọc profile đầu tiên do glob trả về — nếu người dùng đăng nhập
        ở profile khác thì coi như không có cookie.
        """
        merged: Dict[Tuple[str, str, str], Tuple[str, str, str, str, int, str, str]] = {}
        for db_path in self.get_chromium_cookie_db_paths(browser):
            for row in self._read_chromium_db(browser, db_path):
                key = (row[0], row[2], row[5])  # host, path, name
                prev = merged.get(key)
                # Ưu tiên bản ghi có giá trị thật (giải mã thành công) và hạn xa hơn
                if prev is None or (not prev[6] and row[6]) or (row[6] and row[4] > prev[4]):
                    merged[key] = row
        return list(merged.values())

    def _read_chromium_db(self, browser: str, db_path: str) -> List[Tuple[str, str, str, str, int, str, str]]:
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
                        # Chromium cookie plaintext may contain a 32-byte host hash
                        # (v10/v11). Try the modern layout first, then legacy.
                        dec = self.decrypt_aes_cbc(key, enc[3:], hash_prefix=True)
                        if dec is None:
                            dec = self.decrypt_aes_cbc(key, enc[3:], hash_prefix=False)
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
        """Gộp cookies từ tất cả profile Firefox (mặc định, snap, flatpak)."""
        merged: Dict[Tuple[str, str, str], Tuple[str, str, str, str, int, str, str]] = {}
        for db_path in self.get_firefox_cookie_db_paths():
            for row in self._read_firefox_db(db_path):
                key = (row[0], row[2], row[5])
                prev = merged.get(key)
                if prev is None or (not prev[6] and row[6]) or (row[6] and row[4] > prev[4]):
                    merged[key] = row
        return list(merged.values())

    def _read_firefox_db(self, db_path: str) -> List[Tuple[str, str, str, str, int, str, str]]:
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

    def find_best_browser(self, domain_filter: Optional[str] = None) -> Optional[str]:
        """Tự động tìm trình duyệt phù hợp nhất có chứa cookies cho domain"""
        candidates = ["firefox", "edge", "chrome", "brave", "chromium"]
        clean_d = registrable_domain(domain_filter)

        # 1. Ưu tiên trình duyệt thực sự có cookies cho domain chỉ định
        if clean_d:
            for b in candidates:
                try:
                    c = self.export_cookies_netscape(b, domain_filter=clean_d)
                    # Kiểm tra xem có dòng cookie nào ngoài comment không
                    data_lines = [l for l in c.splitlines() if l and not l.startswith("#")]
                    if data_lines:
                        return b
                except Exception:
                    continue

        # 2. Nếu không tìm thấy domain cụ thể, chọn trình duyệt đầu tiên trích xuất được cookie bất kỳ
        for b in candidates:
            try:
                c = self.export_cookies_netscape(b)
                data_lines = [l for l in c.splitlines() if l and not l.startswith("#")]
                if data_lines:
                    return b
            except Exception:
                continue

        return None

    def _raw_cookies_cached(self, b: str) -> List[Tuple[str, str, str, str, int, str, str]]:
        """Đọc + giải mã cookie DB của một trình duyệt, có cache ngắn hạn."""
        with _raw_cache_lock:
            hit = _raw_cache.get(b)
            if hit and (time.monotonic() - hit[0]) < _EXPORT_CACHE_TTL:
                return hit[1]

        rows = self.extract_firefox_cookies() if "firefox" in b else self.extract_chromium_cookies(b)

        with _raw_cache_lock:
            _raw_cache[b] = (time.monotonic(), rows)
        return rows

    def export_cookies_netscape(self, browser: str, domain_filter: Optional[str] = None) -> str:
        """Xuất cookies sang chuỗi định dạng Netscape chuẩn (có cache ngắn hạn)."""
        b = browser.lower()
        filter_key = registrable_domain(domain_filter) if domain_filter else None

        cached = _cache_get((b, filter_key))
        if cached is not None:
            return cached

        if b in ("auto", ""):
            best = self.find_best_browser(domain_filter)
            if not best:
                _cache_put((b, filter_key), "")
                return ""
            # find_best_browser đã xuất và cache nội dung của trình duyệt này rồi,
            # nên lần gọi dưới đây lấy thẳng từ cache thay vì giải mã DB lần hai.
            result = self.export_cookies_netscape(best, domain_filter)
            _cache_put((b, filter_key), result)
            return result

        raw_cookies = self._raw_cookies_cached(b)

        lines = [
            "# Netscape HTTP Cookie File",
            "# http://curl.haxx.se/rfc/cookie_spec.html",
            "# This is a generated file! Do not edit.",
            "",
        ]

        # Luôn lọc theo domain gốc (instagram.com) chứ không phải hostname đầy đủ
        # (www.instagram.com), nếu không sẽ loại mất toàn bộ cookie .instagram.com.
        filter_clean = registrable_domain(domain_filter) if domain_filter else None

        for host, domain_flag, path, sec_flag, exp_unix, name, val in raw_cookies:
            if filter_clean and not _host_matches(host, filter_clean):
                continue
            lines.append(f"{host}\t{domain_flag}\t{path}\t{sec_flag}\t{exp_unix}\t{name}\t{val}")

        result = "\n".join(lines) + "\n"
        _cache_put((b, filter_clean), result)
        return result


# Ánh xạ domain → tên platform để tìm file cookie thủ công
_DOMAIN_TO_PLATFORM = {
    "facebook.com": "facebook",
    "instagram.com": "instagram",
    "reddit.com": "reddit",
    "redd.it": "reddit",
    "tiktok.com": "tiktok",
    "x.com": "twitter",
    "twitter.com": "twitter",
    "youtube.com": "youtube",
    "youtu.be": "youtube",
    "pinterest.com": "pinterest",
    "pin.it": "pinterest",
    "threads.net": "threads",
    "bilibili.com": "bilibili",
    "soundcloud.com": "soundcloud",
    "tumblr.com": "tumblr",
    "pixiv.net": "pixiv",
    "douyin.com": "douyin",
    "linkedin.com": "linkedin",
}


def _find_manual_cookie_file(domain: Optional[str] = None) -> Optional[str]:
    """Tìm file cookie thủ công trong ~/.config/crwl/cookies/ theo domain hoặc platform."""
    home = os.path.expanduser("~")
    cookies_dir = os.path.join(home, ".config", "crwl", "cookies")
    if not os.path.isdir(cookies_dir):
        return None

    candidates: list = []

    if domain:
        d_clean = domain.lower().replace("www.", "").lstrip(".")
        # Kiểm tra theo ánh xạ platform
        for suffix, platform_name in _DOMAIN_TO_PLATFORM.items():
            if d_clean == suffix or d_clean.endswith("." + suffix):
                candidates.append(platform_name)
                break
        # Kiểm tra theo tên domain trực tiếp (vd: facebook, instagram)
        domain_part = d_clean.split(".")[0]
        if domain_part and domain_part not in candidates:
            candidates.append(domain_part)
        # Kiểm tra toàn bộ domain
        if d_clean not in candidates:
            candidates.append(d_clean)

    # Kiểm tra lần lượt các ứng viên
    for name in candidates:
        path = os.path.join(cookies_dir, f"{name}.txt")
        if os.path.isfile(path) and os.path.getsize(path) > 10:
            return path

    return None


def get_browser_cookies_txt(browser: Optional[str] = None, domain: Optional[str] = None, output_path: Optional[str] = None) -> Optional[str]:
    """Helper: lấy cookies file cho browser, trả về đường dẫn file cookies tạm.

    Thứ tự ưu tiên:
      1. File cookie thủ công trong ~/.config/crwl/cookies/<platform>.txt  (luôn ưu tiên số 1)
      2. Trích xuất từ trình duyệt hệ thống (Chrome, Edge, Firefox, Brave)
         — CHỈ dùng khi có ít nhất 3 cookie có giá trị thực để tránh cookie rỗng/sai
    """
    if browser == "none":
        return None

    try:
        # Ưu tiên 1: file cookie thủ công theo domain/platform
        manual = _find_manual_cookie_file(domain)
        if manual:
            sys.stderr.write(f"[Cookies] Dùng file cookie thủ công: {manual}\n")
            # Extractors remove temporary exports after each request. Copy manual
            # cookies so a successful extraction never deletes the user's source.
            if not output_path:
                fd, output_path = tempfile.mkstemp(prefix="cookies_manual_", suffix=".txt")
                os.close(fd)
            shutil.copyfile(manual, output_path)
            return output_path

        # Ưu tiên 2: trích xuất từ trình duyệt hệ thống
        exporter = BrowserCookieExporter()
        target_browser = browser if (browser and browser != "auto") else "auto"
        content = exporter.export_cookies_netscape(target_browser, domain)

        # Chỉ đếm cookie có GIÁ TRỊ thật — cookie rỗng là dấu hiệu giải mã thất bại.
        data_lines = [
            l for l in content.splitlines()
            if l and not l.startswith("#") and l.count("\t") >= 6 and l.rsplit("\t", 1)[-1].strip()
        ]

        if not data_lines:
            sys.stderr.write(
                f"[Cookies] Không tìm thấy cookie hợp lệ cho domain '{domain or 'bất kỳ'}'. "
                f"Hãy đăng nhập trên trình duyệt hoặc dán cookie thủ công qua Cookie Manager.\n"
            )
            return None

        if domain:
            base = registrable_domain(domain)
            session_names = _SESSION_COOKIE_NAMES.get(base, set())
            if session_names:
                found = {l.split("\t")[5] for l in data_lines}
                if not (found & session_names):
                    sys.stderr.write(
                        f"[Cookies] Tìm thấy {len(data_lines)} cookie cho {base} nhưng THIẾU cookie đăng nhập "
                        f"({', '.join(sorted(session_names))}). Nội dung riêng tư có thể sẽ không tải được.\n"
                    )
                else:
                    sys.stderr.write(f"[Cookies] Dùng {len(data_lines)} cookie đã đăng nhập của {base}\n")

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

