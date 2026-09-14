"""
URL Resolver & Platform Identifier.
Ported from UrlResolverService to pure Python.
Expands shortened URLs, cleans tracking params, and identifies target platforms.
"""

import re
import urllib.request
import urllib.parse
from typing import Optional, Dict, Any, List, Tuple
from ..models import ResolveUrlResult

SUPPORTED_PLATFORMS = [
    {
        "id": "youtube",
        "name": "YouTube",
        "domains": ["youtube.com", "youtu.be", "youtube-nocookie.com"],
    },
    {
        "id": "instagram",
        "name": "Instagram",
        "domains": ["instagram.com", "instagr.am"],
    },
    {
        "id": "tiktok",
        "name": "TikTok",
        "domains": ["tiktok.com"],
    },
    {
        "id": "facebook",
        "name": "Facebook",
        "domains": ["facebook.com", "fb.watch", "fb.com", "fb.me"],
    },
    {
        "id": "x",
        "name": "X (Twitter)",
        "domains": ["twitter.com", "x.com", "t.co"],
    },
    {
        "id": "pinterest",
        "name": "Pinterest",
        "domains": [
            "pinterest.com",
            "pin.it",
            "pinterest.co.uk",
            "pinterest.ca",
            "pinterest.fr",
            "pinterest.de",
            "pinterest.jp",
        ],
    },
    {
        "id": "reddit",
        "name": "Reddit",
        "domains": ["reddit.com", "redd.it"],
    },
    {
        "id": "soundcloud",
        "name": "SoundCloud",
        "domains": ["soundcloud.com", "on.soundcloud.com"],
    },
    {
        "id": "twitch",
        "name": "Twitch",
        "domains": ["twitch.tv"],
    },
    {
        "id": "dailymotion",
        "name": "Dailymotion",
        "domains": ["dailymotion.com", "dai.ly"],
    },
    {
        "id": "bilibili",
        "name": "Bilibili",
        "domains": ["bilibili.com", "b23.tv"],
    },
    {
        "id": "threads",
        "name": "Threads",
        "domains": ["threads.net"],
    },
    {
        "id": "bluesky",
        "name": "Bluesky",
        "domains": ["bsky.app"],
    },
    {
        "id": "movie",
        "name": "Phim & HLS",
        "domains": [
            "motchill", "phimmoi", "ophim", "kkphim", "subnhanh", "tvhay", "bilutv",
            "dongphim", "xemphim", "rosetv", "phim3s", "hdonline", "animehay", "vuighe",
            "fmovies", "123movies", "soap2day", "bflix", "gogoanime", "aniwatch", "hianime",
            "lookmovie", "sflix", "vidsrc", "streamtape", "doodstream",
            "phimhay", "phimchill", "phimfast", "phimhd", "phimnhanh", "phimplus",
            "vuphim", "hdviet", "kphim", "phimgio", "iuphim", "phimbathu",
        ],
    },
]

# Keywords trong hostname dùng nhận diện trang phim/media chưa biết
MOVIE_HOSTNAME_KEYWORDS = [
    "phim", "movie", "cinema", "stream", "film", "series", "anime",
    "xemphim", "vietsub", "thuyetminh",
]

# Keywords trong hostname nhận diện trang nhạc/âm nhạc chưa biết  
MUSIC_HOSTNAME_KEYWORDS = [
    "nhac", "music", "audio", "nhacviet", "beatvn", "soundvn",
]

GENERIC_SHORTENER_DOMAINS = [
    "bit.ly", "tinyurl.com", "t.ly", "cutt.ly", "is.gd", "v.gd", "rb.gy",
    "shorturl.at", "goo.gl", "ow.ly", "buff.ly", "clck.ru", "rebrand.ly",
    "bl.ink", "lnkd.in", "snip.ly", "s.id", "linktr.ee", "shorte.st", "adf.ly",
]

TRACKING_QUERY_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "fbclid", "igshid", "si", "ref", "ref_src", "feature", "share_id",
}


class UrlResolver:
    """Xác thực, phân giải redirect và chuẩn hóa URL"""

    @staticmethod
    def parse_hostname(raw_url: str) -> Optional[str]:
        if not raw_url or not isinstance(raw_url, str):
            return None
        trimmed = raw_url.strip()
        if trimmed.startswith("@") or ("." not in trimmed and "/" not in trimmed):
            return None
        if not re.match(r"^https?://", trimmed, re.IGNORECASE):
            trimmed = "https://" + trimmed
        try:
            parsed = urllib.parse.urlparse(trimmed)
            hostname = (parsed.hostname or "").lower().rstrip(".")
            return hostname if hostname else None
        except Exception:
            return None

    @classmethod
    def match_domain(cls, hostname: str, domain_pattern: str) -> bool:
        pattern = domain_pattern.lower()
        if "." not in pattern:
            return pattern in hostname
        return hostname == pattern or hostname.endswith("." + pattern)

    @classmethod
    def detect_platform(cls, url: str) -> Tuple[Optional[str], Optional[str]]:
        hostname = cls.parse_hostname(url)
        if not hostname:
            return None, None

        # Direct stream URL → movie
        url_lower = url.lower()
        if any(ext in url_lower for ext in (".m3u8", ".mpd", "/hls/", "/dash/", ".ts", ".m4s")):
            return "movie", "Phim & Stream"

        # Khớp với danh sách platform đã biết
        for p in SUPPORTED_PLATFORMS:
            for d in p["domains"]:
                if cls.match_domain(hostname, d):
                    return p["id"], p["name"]

        # Heuristic: nhận diện trang phim/media chưa biết qua hostname keyword
        if any(kw in hostname for kw in MOVIE_HOSTNAME_KEYWORDS):
            return "movie", "Trang Phim Web"

        # Heuristic: nhận diện trang nhạc chưa biết
        if any(kw in hostname for kw in MUSIC_HOSTNAME_KEYWORDS):
            return "movie", "Trang Nhạc Web"

        return "generic", "Web Chung"

    @classmethod
    def is_shortened_url(cls, url: str) -> bool:
        hostname = cls.parse_hostname(url)
        if not hostname:
            return False
        # Specific shorteners
        if any(cls.match_domain(hostname, d) for d in GENERIC_SHORTENER_DOMAINS):
            return True
        # Platform shorteners
        if hostname in ("youtu.be", "t.co", "pin.it", "fb.watch", "fb.me", "dai.ly", "b23.tv", "redd.it"):
            return True
        # Facebook share redirect links (e.g. facebook.com/share/...)
        lower_url = url.lower()
        if ("facebook.com" in lower_url or "fb.com" in lower_url) and "/share/" in lower_url:
            return True
        # TikTok shortlink like vt.tiktok.com or vm.tiktok.com
        if hostname.endswith(".tiktok.com") and hostname != "www.tiktok.com" and hostname != "tiktok.com":
            return True
        return False

    @staticmethod
    def clean_tracking_params(url: str) -> str:
        try:
            parsed = urllib.parse.urlparse(url)
            if not parsed.query:
                return url
            query_dict = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
            cleaned_query = {
                k: v for k, v in query_dict.items() if k.lower() not in TRACKING_QUERY_PARAMS
            }
            new_query = urllib.parse.urlencode(cleaned_query, doseq=True)
            return urllib.parse.urlunparse(
                (parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment)
            )
        except Exception:
            return url

    @classmethod
    def unshorten_url(cls, url: str, max_hops: int = 5, timeout: int = 10) -> str:
        """Lần theo HTTP Redirect để lấy URL cuối cùng, ưu tiên dùng curl để tránh lỗi 400 Bad Request"""
        current_url = url.strip()
        if not re.match(r"^https?://", current_url, re.IGNORECASE):
            current_url = "https://" + current_url

        # 1. Thử dùng curl -sIL để lấy url_effective (nhanh, chuẩn HTTP/2, không bị Facebook chặn 400)
        try:
            import subprocess
            cmd = [
                "curl", "-sIL", "-o", "/dev/null", "-w", "%{url_effective}",
                "-A", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "-H", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "-H", "Sec-Fetch-Mode: navigate",
                "-H", "Sec-Fetch-Site: none",
                "--max-time", str(timeout),
                current_url,
            ]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 2)
            if proc.returncode == 0:
                eff = proc.stdout.strip()
                if eff and eff.startswith("http") and eff != current_url:
                    return eff
        except Exception:
            pass

        # 2. Fallback urllib nếu curl không khả dụng
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Sec-Fetch-Mode": "navigate",
        }

        for _ in range(max_hops):
            try:
                req = urllib.request.Request(current_url, headers=headers, method="HEAD")
                opener = urllib.request.build_opener(urllib.request.HTTPRedirectHandler)
                with opener.open(req, timeout=timeout) as resp:
                    next_url = resp.geturl()
                    if next_url == current_url:
                        break
                    current_url = next_url
            except urllib.error.HTTPError as e:
                if e.code in (405, 403):
                    try:
                        req = urllib.request.Request(current_url, headers=headers, method="GET")
                        with urllib.request.urlopen(req, timeout=timeout) as resp:
                            next_url = resp.geturl()
                            if next_url == current_url:
                                break
                            current_url = next_url
                    except Exception:
                        break
                elif 300 <= e.code < 400 and "Location" in e.headers:
                    loc = e.headers["Location"]
                    current_url = urllib.parse.urljoin(current_url, loc)
                else:
                    break
            except Exception:
                break

        return current_url

    @classmethod
    def resolve_url(cls, raw_url: str, expected_platform: Optional[str] = None) -> ResolveUrlResult:
        """Phân tích, giải mã redirect và chuẩn hóa đầy đủ (hỗ trợ cả @username)"""
        original_url = raw_url.strip()

        # Xử lý username / handle (@username hoặc chuỗi không chứa domain)
        if original_url.startswith("@") or ("." not in original_url and "/" not in original_url):
            username = original_url.lstrip("@")
            ep = (expected_platform or "tiktok").lower()
            if ep == "youtube":
                full_url = f"https://www.youtube.com/@{username}"
                p_name = "YouTube"
            elif ep == "instagram":
                full_url = f"https://www.instagram.com/{username}/"
                p_name = "Instagram"
            elif ep in ("x", "twitter"):
                full_url = f"https://x.com/{username}"
                p_name = "X (Twitter)"
            elif ep == "pinterest":
                full_url = f"https://www.pinterest.com/{username}/"
                p_name = "Pinterest"
            elif ep == "reddit":
                full_url = f"https://www.reddit.com/user/{username}/"
                p_name = "Reddit"
            elif ep == "soundcloud":
                full_url = f"https://soundcloud.com/{username}"
                p_name = "SoundCloud"
            elif ep == "twitch":
                full_url = f"https://www.twitch.tv/{username}"
                p_name = "Twitch"
            else:
                full_url = f"https://www.tiktok.com/@{username}"
                ep = "tiktok"
                p_name = "TikTok"

            return ResolveUrlResult(
                original_url=original_url,
                resolved_url=full_url,
                platform=ep,
                platform_name=p_name,
                is_shortened=False,
                is_supported=True,
                is_matching_expected=True,
            )

        is_shortened = cls.is_shortened_url(original_url)

        resolved_url = original_url
        if is_shortened:
            resolved_url = cls.unshorten_url(original_url)

        resolved_url = cls.clean_tracking_params(resolved_url)

        # Chuẩn hóa nếu là Facebook share link dạng số
        if ("facebook.com" in resolved_url.lower() or "fb.com" in resolved_url.lower()) and "/share/" in resolved_url.lower():
            m_v = re.search(r"/share/v/(\d+)", resolved_url)
            if m_v:
                resolved_url = f"https://www.facebook.com/watch/?v={m_v.group(1)}"
            else:
                m_r = re.search(r"/share/r/(\d+)", resolved_url)
                if m_r:
                    resolved_url = f"https://www.facebook.com/reel/{m_r.group(1)}"
                else:
                    m_p = re.search(r"/share/p/(\d+)", resolved_url)
                    if m_p:
                        resolved_url = f"https://www.facebook.com/photo/?fbid={m_p.group(1)}"

        platform_id, platform_name = cls.detect_platform(resolved_url)
        is_supported = platform_id is not None and platform_id != "generic"

        is_matching_expected = True
        if expected_platform and expected_platform.lower() != "auto":
            is_matching_expected = platform_id == expected_platform.lower()

        return ResolveUrlResult(
            original_url=original_url,
            resolved_url=resolved_url,
            platform=platform_id or "generic",
            platform_name=platform_name or "Web Chung",
            is_shortened=is_shortened,
            is_supported=is_supported,
            is_matching_expected=is_matching_expected,
        )
