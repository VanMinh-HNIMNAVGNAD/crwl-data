"""
Generic Webpage Media Scraper.
Fallback extractor for general web pages, blogs, and forums (WordPress, CMS, etc.)
when neither yt-dlp nor gallery-dl supports the specific platform/URL.
Extracts high-resolution images, embedded HTML5 videos, and HLS/MP4 streams.
"""

import os
import re
import urllib.request
from urllib.parse import urljoin, urlparse
from typing import Optional, List, Dict, Any, Tuple

from .base import BaseExtractor
from ..models import MediaMetadata, MediaImage, StreamFormat


class WebScraperExtractor(BaseExtractor):
    """Trích xuất phương tiện từ trang web/blog thông thường"""

    def __init__(self):
        super().__init__()

    def is_likely_webpage(self, url: str) -> bool:
        if not url or not isinstance(url, str):
            return False
        clean = url.strip().lower()
        return clean.startswith("http://") or clean.startswith("https://")

    def extract(self, url: str, timeout: int = 25) -> MediaMetadata:
        target_url = url.strip()
        self.log(f"Bắt đầu cào phương tiện HTML tổng hợp: {target_url}")

        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "vi,en-US;q=0.9,en;q=0.8",
            "Referer": target_url,
        }

        req = urllib.request.Request(target_url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                content_type = resp.headers.get("Content-Type", "").lower()
                raw_bytes = resp.read()
                html = raw_bytes.decode("utf-8", errors="ignore")
        except Exception as e:
            raise RuntimeError(f"Không thể truy cập trang web để bóc tách: {e}")

        parsed_url = urlparse(target_url)
        domain = parsed_url.netloc

        # 1. Trích xuất Tiêu đề
        title = "Bộ sưu tập trang web"
        og_title = re.search(r'<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
        if not og_title:
            og_title = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']', html, re.I)
        if og_title:
            title = og_title.group(1).strip()
        else:
            title_tag = re.search(r'<title[^>]*>(.*?)</title>', html, re.I | re.S)
            if title_tag:
                title = title_tag.group(1).strip()

        # 2. Trích xuất Mô tả
        description = ""
        og_desc = re.search(r'<meta[^>]+(?:property=["\']og:description["\']|name=["\']description["\'])[^>]+content=["\']([^"\']+)["\']', html, re.I)
        if og_desc:
            description = og_desc.group(1).strip()

        # 3. Trích xuất Tác giả / Tên trang
        author = domain
        og_site = re.search(r'<meta[^>]+property=["\']og:site_name["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
        if og_site:
            author = og_site.group(1).strip()

        # 4. Trích xuất Video nếu có (<video src>, <source src>, .mp4, .m3u8)
        streams: List[StreamFormat] = []
        found_videos: List[str] = []

        og_video = re.search(r'<meta[^>]+property=["\'](?:og:video|og:video:url|og:video:secure_url)["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
        if og_video:
            found_videos.append(urljoin(target_url, og_video.group(1)))

        video_srcs = re.findall(r'<(?:video|source)[^>]+src=["\']([^"\']+)["\']', html, re.I)
        for v in video_srcs:
            clean_v = urljoin(target_url, v)
            if clean_v not in found_videos and not clean_v.startswith("blob:"):
                found_videos.append(clean_v)

        for idx, v_url in enumerate(found_videos, 1):
            is_hls = ".m3u8" in v_url.lower()
            streams.append(
                StreamFormat(
                    format_id=f"web_video_{idx}",
                    quality="Video Web HD" if not is_hls else "HLS Stream",
                    format="HLS" if is_hls else "MP4",
                    size="Tự động",
                    stream_type="full",
                    has_audio=True,
                    has_video=True,
                    url=v_url,
                )
            )

        # 5. Trích xuất Hình ảnh
        images: List[MediaImage] = []
        seen_img_urls = set()

        # Ưu tiên og:image
        og_imgs = re.findall(r'<meta[^>]+property=["\'](?:og:image|og:image:secure_url)["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
        for og_img in og_imgs:
            full_img = urljoin(target_url, og_img)
            if full_img not in seen_img_urls:
                seen_img_urls.add(full_img)

        # Lấy từ <img> tags (ưu tiên data-original, data-src, data-lazy-src, src)
        img_tags = re.findall(r'<img[^>]+>', html, re.I)
        for tag in img_tags:
            src = None
            for attr in ("data-original", "data-src", "data-lazy-src", "data-full-url", "data-url", "src"):
                m = re.search(rf'{attr}=["\']([^"\']+)["\']', tag, re.I)
                if m:
                    val = m.group(1).strip()
                    if val and not val.startswith("data:image/"):
                        src = val
                        break

            if not src:
                continue

            # Xử lý srcset nếu có
            srcset_m = re.search(r'srcset=["\']([^"\']+)["\']', tag, re.I)
            if srcset_m:
                parts = [p.strip().split(" ")[0] for p in srcset_m.group(1).split(",") if p.strip()]
                if parts:
                    src = parts[-1]  # lấy chất lượng cao nhất

            full_src = urljoin(target_url, src)

            # Lọc bỏ avatar, icon, emoji, tracking pixel, counter
            lower_src = full_src.lower()
            skip_patterns = [
                "avatar", "gravatar", "emoji", "favicon", "logo", "icon",
                "pixel", "tracking", "counter", "yandex.ru/watch", "google-analytics",
                "badge", "spinner", "loader", "blank.gif", "transparent.png"
            ]
            if any(p in lower_src for p in skip_patterns):
                continue

            # Chỉ giữ các tệp có đuôi ảnh hoặc nằm trong wp-content/uploads hoặc images
            has_img_ext = any(ext in lower_src for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"))
            is_media_path = any(p in lower_src for p in ("/upload", "/uploads/", "/image", "/images/", "/photo", "/photos/", "/media/"))

            if (has_img_ext or is_media_path) and full_src not in seen_img_urls:
                seen_img_urls.add(full_src)

        # Chuyển đổi seen_img_urls thành MediaImage
        index = 1
        for img_url in seen_img_urls:
            clean_ext = self._extract_ext(img_url) or "jpg"
            images.append(
                MediaImage(
                    id=index,
                    url=img_url,
                    title=f"{title} - Ảnh {index}",
                    resolution="Ảnh HD",
                    size="Tự động",
                    type="image",
                    ext=clean_ext,
                    thumb=img_url,
                )
            )
            index += 1

        if not images and not streams:
            raise RuntimeError("Không tìm thấy hình ảnh hoặc video nào trên trang web này.")

        first_thumb = images[0].url if images else (found_videos[0] if found_videos else "")
        is_video_primary = bool(streams and not images)

        return MediaMetadata(
            id=str(abs(hash(target_url))),
            platform="generic",
            title=title,
            author=author,
            author_url=target_url,
            duration=f"{len(images)} hình ảnh" if images else "Video",
            views="Chất lượng gốc",
            thumbnail=first_thumb,
            high_res_thumbnail=first_thumb,
            type="video" if is_video_primary else "album",
            original_url=target_url,
            description=description or None,
            streams=streams if streams else None,
            images=images if images else None,
        )

    @staticmethod
    def _extract_ext(url: str) -> Optional[str]:
        try:
            clean = url.split("?")[0].split("#")[0]
            ext = clean.split(".")[-1].lower()
            if 2 <= len(ext) <= 5 and ext.isalnum():
                return ext
        except Exception:
            pass
        return None
