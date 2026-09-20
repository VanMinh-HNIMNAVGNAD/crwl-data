"""
Direct Image Extractor Engine.
Detects direct CDN image links (.jpg, .png, .webp, etc.),
retrieves image metadata and returns a synthetic single-image album.
Ported from MediaDispatcherService into standalone Python.
"""

import os
import time
import urllib.request
from typing import Optional
from .base import BaseExtractor
from ..models import MediaMetadata, MediaImage

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".gif", ".svg", ".bmp", ".avif", ".heic")
KNOWN_IMAGE_CDNS = [
    "images.unsplash.com", "i.pinimg.com", "pbs.twimg.com/media",
    "i.imgur.com", "fbcdn.net", "cdninstagram.com", "pximg.net",
    "redd.it/media", "preview.redd.it",
]


class DirectImageExtractor(BaseExtractor):
    """Trích xuất metadata ảnh trực tiếp từ CDN"""

    @staticmethod
    def is_direct_image_url(url: str) -> bool:
        clean = url.split("?")[0].lower()
        if any(clean.endswith(ext) for ext in IMAGE_EXTENSIONS):
            return True
        lower = url.lower()
        return any(cdn in lower for cdn in KNOWN_IMAGE_CDNS)

    def extract(self, url: str) -> MediaMetadata:
        target_url = url.strip()
        filename = None
        size_str = None
        ext = None

        try:
            clean_path = target_url.split("?")[0]
            basename = os.path.basename(clean_path)
            if basename:
                filename = basename
                if "." in basename:
                    ext = basename.split(".")[-1].lower()

            req = urllib.request.Request(
                target_url,
                headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) Chrome/131.0.0.0 Safari/537.36"},
                method="HEAD",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                cl = resp.headers.get("Content-Length")
                if cl and cl.isdigit():
                    from .ytdlp import YtDlpExtractor
                    size_str = YtDlpExtractor.format_bytes(int(cl))
        except Exception:
            pass

        img_item = MediaImage(
            id=1,
            url=target_url,
            title=filename,
            resolution=None,
            size=size_str,
            type="gif" if ext == "gif" else "image",
            ext=ext,
            thumb=target_url,
        )

        return MediaMetadata(
            id=str(int(time.time() * 1000)),
            platform="direct",
            title=filename,
            author=None,
            author_url=None,
            duration=None,
            views=None,
            thumbnail=target_url,
            high_res_thumbnail=target_url,
            type="album",
            original_url=target_url,
            images=[img_item],
        )
