"""
Movie & Streaming Extractor Engine.
Detects direct HLS (.m3u8) / DASH (.mpd) stream links and online movie sites,
extracts stream links using regex and delegates to yt-dlp.
Ported from MovieExtractorService into standalone Python.
"""

import re
import urllib.request
import urllib.parse
from typing import Optional, List, Tuple
from .base import BaseExtractor
from .ytdlp import YtDlpExtractor
from ..models import MediaMetadata

KNOWN_MOVIE_DOMAINS = [
    "motchill", "phimmoi", "ophim", "kkphim", "subnhanh", "tvhay", "bilutv",
    "dongphim", "xemphim", "rosetv", "phim3s", "hdonline", "animehay", "vuighe",
    "fmovies", "123movies", "soap2day", "bflix", "gogoanime", "aniwatch", "hianime",
    "lookmovie", "sflix", "vidsrc", "streamtape", "doodstream", "mixdrop",
    "streamwish", "filemoon", "upstream", "voe", "rabbitstream", "megacloud",
    "phimhay", "phimchill", "phimfast", "phimhd", "phimnhanh", "phimplus",
    "vuphim", "hdviet", "kphim", "phimgio", "iuphim", "phimbathu", "phimmoichill",
    "fullphim", "phimxuyendem",
]

# Heuristic keywords để nhận diện trang phim/media chưa biết (trong hostname)
MOVIE_HOSTNAME_KEYWORDS = [
    "phim", "movie", "cinema", "stream", "film", "series",
    "anime", "xemphim", "vietsub", "thuyetminh",
    "nhac", "music", "audio", "nhacviet",
]

# Heuristic cho path URL
MOVIE_PATH_KEYWORDS = [
    "episode", "/tap-", "/phan-", "/season", "watch?", "/play/",
    "vietsub", "thuyet-minh", "-full-hd", "-vietsub",
]

EXCLUDED_SOCIAL_DOMAINS = [
    "youtube.com", "youtu.be", "facebook.com", "fb.watch", "fb.com",
    "instagram.com", "instagr.am", "tiktok.com", "twitter.com", "x.com",
    "pinterest.com", "pin.it", "reddit.com", "redd.it", "soundcloud.com",
    "twitch.tv", "dailymotion.com", "dai.ly", "bilibili.com", "b23.tv",
    "threads.net", "bsky.app",
]

# Regex patterns tìm stream URLs trong HTML/JS (theo thứ tự ưu tiên)
STREAM_URL_PATTERNS = [
    (r'(https?://[^\s"\'<>\{\}]+\.m3u8[^\s"\'<>\{\}]*)', "hls"),
    (r'(https?://[^\s"\'<>\{\}]+\.mpd[^\s"\'<>\{\}]*)', "dash"),
    (r'(https?://[^\s"\'<>\{\}]+/hls/[^\s"\'<>\{\}]*)', "hls"),
    (r'(https?://[^\s"\'<>\{\}]+/dash/[^\s"\'<>\{\}]*)', "dash"),
    (r'(https?://[^\s"\'<>\{\}]+\.mp4[^\s"\'<>\{\}]*)', "mp4"),
    (r'(https?://[^\s"\'<>\{\}]+\.m4s[^\s"\'<>\{\}]*)', "m4s"),
]

# JSON property keys thường chứa stream URL
STREAM_JSON_KEY_PATTERN = re.compile(
    r'"(?:file|src|url|source|stream|hls|dash|mp4|stream_url|video_url|media_url|'
    r'playback_url|master|playlist|embed|link)"\s*:\s*"(https?://[^"]{10,})"',
    re.IGNORECASE,
)

# JS variable assignment patterns
STREAM_JS_VAR_PATTERN = re.compile(
    r'(?:file|src|url|source|stream|hls|dash|mp4|link)\s*[=:]\s*["\']'
    r'(https?://[^"\']{10,}\.(?:m3u8|mpd|mp4|ts|m4s|webm)[^"\']*)["\']',
    re.IGNORECASE,
)

# Iframe embed keywords
EMBED_KEYWORDS = [
    "embed", "player", "stream", "video", "vidsrc", "jwplayer",
    "plyr", "vidcloud", "filemoon", "streamwish", "streamtape",
    "doodstream", "upstream", "megacloud", "rabbitstream",
]


class MovieExtractor(BaseExtractor):
    """Trích xuất nguồn phim và luồng phát trực tiếp HLS/DASH"""

    def __init__(self, ytdlp_extractor: Optional[YtDlpExtractor] = None):
        super().__init__()
        self.ytdlp = ytdlp_extractor or YtDlpExtractor()

    def is_movie_or_stream_url(self, raw_url: str) -> bool:
        if not raw_url or not isinstance(raw_url, str):
            return False
        lower = raw_url.strip().lower()

        # 1. Luồng stream trực tiếp (bất kể domain)
        if any(ext in lower for ext in (".m3u8", ".mpd", "/hls/", "master.m3u8", "playlist.m3u8")):
            return True

        # 2. Loại trừ mạng xã hội đã biết
        if any(d in lower for d in EXCLUDED_SOCIAL_DOMAINS):
            return False

        # 3. Web phim/stream đã biết (danh sách cứng)
        if any(d in lower for d in KNOWN_MOVIE_DOMAINS):
            return True

        # 4. Heuristic dựa trên hostname keyword
        try:
            url_to_parse = lower if lower.startswith("http") else "https://" + lower
            parsed = urllib.parse.urlparse(url_to_parse)
            hostname = (parsed.hostname or "").lower()
            path = (parsed.path or "").lower()

            if any(kw in hostname for kw in MOVIE_HOSTNAME_KEYWORDS):
                return True
            if any(kw in path for kw in MOVIE_PATH_KEYWORDS):
                return True
        except Exception:
            pass

        return False

    def extract(self, url: str, browser: Optional[str] = None, timeout: int = 45) -> MediaMetadata:
        target_url = url.strip()
        lower = target_url.lower()

        # Nếu là direct stream, chuyển thẳng sang yt-dlp
        if any(ext in lower for ext in (".m3u8", ".mpd", "/hls/")):
            meta = self.ytdlp.extract_metadata(target_url, browser=browser, timeout=timeout)
            meta.platform = "movie"
            return meta

        # Thử scrape HTML tĩnh trước (nhanh, không cần browser)
        self.log(f"Đang tìm kiếm luồng video trong HTML của trang phim: {target_url}")
        scrape_result = self._scrape_stream_url(target_url)

        if scrape_result:
            stream_url, embed_urls = scrape_result
            if stream_url:
                self.log(f"Tìm thấy luồng stream (HTML tĩnh): {stream_url}")
                try:
                    meta = self.ytdlp.extract_metadata(stream_url, browser=browser, timeout=timeout)
                    meta.platform = "movie"
                    meta.original_url = target_url
                    return meta
                except Exception as e:
                    self.warn(f"yt-dlp với stream URL thất bại ({e})")

            # Thử embed URLs
            for embed_url in (embed_urls or [])[:3]:
                self.log(f"Thử embed player: {embed_url[:80]}")
                try:
                    sub = self._scrape_stream_url(embed_url)
                    if sub and sub[0]:
                        meta = self.ytdlp.extract_metadata(sub[0], browser=browser, timeout=timeout)
                        meta.platform = "movie"
                        meta.original_url = target_url
                        return meta
                except Exception:
                    continue

        # Fallback sang yt-dlp trực tiếp
        meta = self.ytdlp.extract_metadata(target_url, browser=browser, timeout=timeout)
        meta.platform = "movie"
        return meta

    def _scrape_stream_url(self, page_url: str, timeout: int = 15) -> Optional[Tuple[Optional[str], List[str]]]:
        """
        Tải HTML tĩnh và quét tìm stream URL bằng nhiều kỹ thuật.
        Returns: (stream_url_or_None, [embed_urls])
        """
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Referer": page_url,
            "Accept": "text/html,application/xhtml+xml,*/*;q=0.9",
            "Accept-Language": "vi,en-US;q=0.9,en;q=0.8",
        }
        try:
            req = urllib.request.Request(page_url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
        except Exception as e:
            self.warn(f"Scrape movie page error: {e}")
            return None

        found_stream: Optional[str] = None
        embed_urls: List[str] = []

        # --- Kỹ thuật 1: Regex tìm stream URLs trực tiếp trong HTML ---
        for pattern, kind in STREAM_URL_PATTERNS:
            matches = re.findall(pattern, html, re.IGNORECASE)
            if matches and kind in ("hls", "dash", "mp4") and not found_stream:
                candidate = matches[0].split('"')[0].split("'")[0].replace("\\/", "/").rstrip("\\")
                if len(candidate) > 15:
                    found_stream = candidate
                    self.log(f"Tìm thấy [{kind.upper()}] qua HTML regex: {found_stream[:80]}")
                    break

        # --- Kỹ thuật 2: Tìm trong JSON blobs ---
        if not found_stream:
            for url_cand in STREAM_JSON_KEY_PATTERN.findall(html):
                lower_cand = url_cand.lower()
                if any(ext in lower_cand for ext in (".m3u8", ".mpd", ".mp4", "/hls/", "/dash/")):
                    found_stream = url_cand
                    self.log(f"Tìm thấy qua JSON key: {found_stream[:80]}")
                    break

        # --- Kỹ thuật 3: Tìm trong JS variable assignments ---
        if not found_stream:
            js_matches = STREAM_JS_VAR_PATTERN.findall(html)
            if js_matches:
                found_stream = js_matches[0]
                self.log(f"Tìm thấy qua JS variable: {found_stream[:80]}")

        # --- Kỹ thuật 4: Tìm iframe embed players ---
        iframe_matches = re.findall(r'<iframe[^>]+src=["\']([^"\']+)["\']', html, re.IGNORECASE)
        for iframe_url in iframe_matches:
            if iframe_url.startswith("//"):
                iframe_url = "https:" + iframe_url
            elif not iframe_url.startswith("http"):
                iframe_url = urllib.parse.urljoin(page_url, iframe_url)
            if any(kw in iframe_url.lower() for kw in EMBED_KEYWORDS):
                if iframe_url not in embed_urls and iframe_url != page_url:
                    embed_urls.append(iframe_url)

        return (found_stream, embed_urls)
