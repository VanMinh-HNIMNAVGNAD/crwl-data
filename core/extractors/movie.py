"""
Movie & Streaming Extractor Engine.
Detects direct HLS (.m3u8) / DASH (.mpd) stream links and online movie sites,
extracts stream links using regex and delegates to yt-dlp.
Ported from MovieExtractorService into standalone Python.
"""

import re
import urllib.request
from typing import Optional, List
from .base import BaseExtractor
from .ytdlp import YtDlpExtractor
from ..models import MediaMetadata

KNOWN_MOVIE_DOMAINS = [
    "motchill", "phimmoi", "ophim", "kkphim", "subnhanh", "tvhay", "bilutv",
    "dongphim", "xemphim", "rosetv", "phim3s", "hdonline", "animehay", "vuighe",
    "fmovies", "123movies", "soap2day", "bflix", "gogoanime", "aniwatch", "hianime",
    "lookmovie", "sflix", "vidsrc", "streamtape", "doodstream", "mixdrop",
    "streamwish", "filemoon", "upstream", "voe", "rabbitstream", "megacloud",
]

EXCLUDED_SOCIAL_DOMAINS = [
    "youtube.com", "youtu.be", "facebook.com", "fb.watch", "fb.com",
    "instagram.com", "instagr.am", "tiktok.com", "twitter.com", "x.com",
    "pinterest.com", "pin.it", "reddit.com", "redd.it", "soundcloud.com",
    "twitch.tv", "dailymotion.com", "dai.ly", "bilibili.com", "b23.tv",
    "threads.net", "bsky.app",
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

        # 1. Luồng stream trực tiếp
        if any(ext in lower for ext in (".m3u8", ".mpd", "/hls/", "master.m3u8", "playlist.m3u8")):
            return True

        # 2. Loại trừ mạng xã hội
        if any(d in lower for d in EXCLUDED_SOCIAL_DOMAINS):
            return False

        # 3. Web phim đã biết
        return any(d in lower for d in KNOWN_MOVIE_DOMAINS)

    def extract(self, url: str, browser: Optional[str] = None, timeout: int = 45) -> MediaMetadata:
        target_url = url.strip()
        lower = target_url.lower()

        # Nếu là direct stream (.m3u8 / .mpd), chuyển thẳng sang yt-dlp
        if any(ext in lower for ext in (".m3u8", ".mpd", "/hls/")):
            meta = self.ytdlp.extract_metadata(target_url, browser=browser, timeout=timeout)
            meta.platform = "movie"
            return meta

        # Nếu là trang web phim, thử tìm URL m3u8 trong mã nguồn HTML
        self.log(f"Đang tìm kiếm luồng video trong HTML của trang phim: {target_url}")
        found_stream = self._scrape_stream_url(target_url)
        if found_stream:
            self.log(f"Tìm thấy luồng stream: {found_stream}")
            meta = self.ytdlp.extract_metadata(found_stream, browser=browser, timeout=timeout)
            meta.platform = "movie"
            meta.original_url = target_url
            return meta

        # Fallback sang yt-dlp trực tiếp
        meta = self.ytdlp.extract_metadata(target_url, browser=browser, timeout=timeout)
        meta.platform = "movie"
        return meta

    def _scrape_stream_url(self, page_url: str) -> Optional[str]:
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Referer": page_url,
        }
        try:
            req = urllib.request.Request(page_url, headers=headers)
            with urllib.request.urlopen(req, timeout=12) as resp:
                html = resp.read().decode("utf-8", errors="ignore")

            # Regex tìm file .m3u8
            m3u8_matches = re.findall(r'(https?://[^\s"\'<>]+\.m3u8[^\s"\'<>]*)', html)
            if m3u8_matches:
                return m3u8_matches[0]

            # Regex tìm iframe embed
            iframe_matches = re.findall(r'<iframe[^>]+src=[\"\'](https?://[^\"\']+)[\"\']', html, re.IGNORECASE)
            for iframe_url in iframe_matches:
                if any(k in iframe_url for k in ("embed", "player", "stream", "video", "vidsrc")):
                    return iframe_url
        except Exception as e:
            self.warn(f"Scrape movie page error: {e}")
        return None
