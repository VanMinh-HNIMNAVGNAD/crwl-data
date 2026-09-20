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


class OversizedResponseError(RuntimeError):
    """Ném ra khi phản hồi HTTP vượt quá giới hạn dung lượng tối đa cho phép (chống OOM/RAM spike)."""
    pass


class WebScraperExtractor(BaseExtractor):
    """Trích xuất phương tiện từ trang web/blog thông thường"""

    # Giới hạn dung lượng HTTP response mặc định: 50 MB (có thể tùy biến qua env CRWL_MAX_RESPONSE_SIZE)
    DEFAULT_MAX_RESPONSE_SIZE = int(os.environ.get("CRWL_MAX_RESPONSE_SIZE", str(50 * 1024 * 1024)))
    CHUNK_SIZE = 64 * 1024  # Đọc theo từng khối 64 KB

    # Phân loại phương tiện (Media Classification)
    MEDIA_TYPE_IMAGE = "image"
    MEDIA_TYPE_VIDEO = "video"
    MEDIA_TYPE_VIDEO_MANIFEST = "video_manifest"
    MEDIA_TYPE_VIDEO_SEGMENT = "video_segment"
    MEDIA_TYPE_UNKNOWN = "unknown"

    IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "avif", "gif", "bmp", "svg"}
    VIDEO_EXTENSIONS = {"mp4", "webm", "mkv", "mov", "avi", "m4v", "flv", "3gp", "wmv", "ogv"}
    MANIFEST_EXTENSIONS = {"m3u8", "mpd"}
    SEGMENT_EXTENSIONS = {"m4s", "ts"}

    @classmethod
    def classify_url(cls, url: str) -> str:
        """
        Phân loại URL phương tiện:
        - 'image': tệp hình ảnh (.jpg, .jpeg, .png, .webp, .avif, .gif, .bmp, .svg)
        - 'video': tệp video độc lập (.mp4, .webm, .mkv, .mov, .avi, v.v.)
        - 'video_manifest': manifest phát trực tuyến HLS / DASH (.m3u8, .mpd)
        - 'video_segment': phân đoạn video streaming (.m4s, .ts)
        - 'unknown': không xác định
        """
        if not url or not isinstance(url, str):
            return cls.MEDIA_TYPE_UNKNOWN

        clean_url = url.strip()
        parsed = urlparse(clean_url)
        path = parsed.path.lower()
        clean_path = path.split("?")[0].split("#")[0]
        ext = clean_path.split(".")[-1] if "." in clean_path else ""

        # 1. Kiểm tra manifest (.m3u8, .mpd)
        if ext in cls.MANIFEST_EXTENSIONS or ".m3u8" in clean_path or clean_path.endswith(".mpd"):
            return cls.MEDIA_TYPE_VIDEO_MANIFEST

        # 2. Kiểm tra segment (.m4s, .ts)
        if ext in cls.SEGMENT_EXTENSIONS:
            return cls.MEDIA_TYPE_VIDEO_SEGMENT

        # 3. Kiểm tra video (.mp4, .webm, ...)
        if ext in cls.VIDEO_EXTENSIONS:
            return cls.MEDIA_TYPE_VIDEO

        # 4. Kiểm tra image (.jpg, .jpeg, .png, .webp, .avif, ...)
        if ext in cls.IMAGE_EXTENSIONS:
            return cls.MEDIA_TYPE_IMAGE

        # 5. Nếu path không có extension, kiểm tra query params / full url
        lower_url = clean_url.lower()
        if re.search(r'\.m3u8(\b|\?|#|$)|format=m3u8', lower_url):
            return cls.MEDIA_TYPE_VIDEO_MANIFEST
        if re.search(r'\.mpd(\b|\?|#|$)|format=mpd', lower_url):
            return cls.MEDIA_TYPE_VIDEO_MANIFEST
        if re.search(r'\.(m4s|ts)(\b|\?|#|$)', lower_url):
            return cls.MEDIA_TYPE_VIDEO_SEGMENT
        if re.search(r'\.(mp4|webm|mkv|mov|avi|m4v|flv)(\b|\?|#|$)', lower_url):
            return cls.MEDIA_TYPE_VIDEO
        if re.search(r'\.(jpe?g|png|webp|avif|gif|bmp|svg)(\b|\?|#|$)', lower_url):
            return cls.MEDIA_TYPE_IMAGE

        return cls.MEDIA_TYPE_UNKNOWN

    @classmethod
    def is_image_url(cls, url: str) -> bool:
        return cls.classify_url(url) == cls.MEDIA_TYPE_IMAGE

    @classmethod
    def is_video_manifest_url(cls, url: str) -> bool:
        return cls.classify_url(url) == cls.MEDIA_TYPE_VIDEO_MANIFEST

    @classmethod
    def is_video_segment_url(cls, url: str) -> bool:
        return cls.classify_url(url) == cls.MEDIA_TYPE_VIDEO_SEGMENT

    @classmethod
    def is_video_url(cls, url: str) -> bool:
        return cls.classify_url(url) in (cls.MEDIA_TYPE_VIDEO, cls.MEDIA_TYPE_VIDEO_MANIFEST)

    @classmethod
    def is_thumbnail_candidate(cls, url: str) -> bool:
        """Một URL chỉ được coi là thumbnail nếu là ảnh hợp lệ, tuyệt đối không phải manifest, segment hay video."""
        media_type = cls.classify_url(url)
        if media_type in (cls.MEDIA_TYPE_VIDEO_MANIFEST, cls.MEDIA_TYPE_VIDEO_SEGMENT, cls.MEDIA_TYPE_VIDEO):
            return False
        return media_type == cls.MEDIA_TYPE_IMAGE

    def __init__(self, max_response_size: Optional[int] = None):
        super().__init__()
        self.max_response_size = (
            max_response_size
            if max_response_size is not None
            else self.DEFAULT_MAX_RESPONSE_SIZE
        )

    def is_likely_webpage(self, url: str) -> bool:
        if not url or not isinstance(url, str):
            return False
        clean = url.strip().lower()
        return clean.startswith("http://") or clean.startswith("https://")

    def _read_response_safely(self, resp: Any, max_size: int) -> bytes:
        """
        Đọc HTTP response theo cơ chế chunking/streaming an toàn, bảo vệ RAM khỏi spike/OOM.
        1. Kiểm tra header Content-Length trước khi đọc dữ liệu (abort sớm).
        2. Đọc luồng theo từng khối nhỏ (chunk) và cập nhật bộ đếm dung lượng đã nhận.
        3. Ngắt kết nối (abort) ngay lập tức khi vượt quá giới hạn max_size.
        """
        # 1. Kiểm tra Content-Length header nếu server cung cấp
        content_length_hdr = resp.headers.get("Content-Length")
        if content_length_hdr:
            try:
                content_length = int(content_length_hdr.strip())
                if content_length > max_size:
                    try:
                        resp.close()
                    except Exception:
                        pass
                    raise OversizedResponseError(
                        f"Kích thước Content-Length ({content_length} bytes) "
                        f"vượt quá giới hạn tối đa cho phép ({max_size} bytes). Đã hủy kết nối an toàn."
                    )
            except ValueError:
                # Nếu Content-Length không hợp lệ (không phải số nguyên), tiếp tục kiểm tra qua chunked stream
                pass

        # 2. Đọc stream theo từng chunk và kiểm soát bộ nhớ
        chunks: List[bytes] = []
        total_bytes = 0
        chunk_size = min(self.CHUNK_SIZE, max_size + 1) if max_size > 0 else self.CHUNK_SIZE

        while True:
            chunk = resp.read(chunk_size)
            if not chunk:
                break
            total_bytes += len(chunk)
            if total_bytes > max_size:
                try:
                    resp.close()
                except Exception:
                    pass
                raise OversizedResponseError(
                    f"Dung lượng HTTP Response thực tế ({total_bytes} bytes) "
                    f"vượt quá giới hạn tối đa cho phép ({max_size} bytes). Đã ngắt kết nối an toàn."
                )
            chunks.append(chunk)

        return b"".join(chunks)

    def extract(self, url: str, timeout: int = 25, max_size: Optional[int] = None) -> MediaMetadata:
        target_url = url.strip()
        self.log(f"Bắt đầu cào phương tiện HTML tổng hợp: {target_url}")

        limit = max_size if max_size is not None else self.max_response_size

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
                raw_bytes = self._read_response_safely(resp, max_size=limit)
                html = raw_bytes.decode("utf-8", errors="ignore")
        except OversizedResponseError:
            raise
        except Exception as e:
            raise RuntimeError(f"Không thể truy cập trang web để bóc tách: {e}")

        parsed_url = urlparse(target_url)
        domain = parsed_url.netloc

        # 1. Trích xuất Tiêu đề
        title = None
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
        author = None
        og_site = re.search(r'<meta[^>]+property=["\']og:site_name["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
        if og_site:
            author = og_site.group(1).strip()

        # 4. Trích xuất Video nếu có (<video src>, <source src>, .mp4, .m3u8, poster)
        streams: List[StreamFormat] = []
        found_videos: List[str] = []
        found_posters: List[str] = []

        # Bóc tách video poster từ thẻ <video>
        video_tags = re.findall(r'<video[^>]*>', html, re.I)
        for vtag in video_tags:
            poster_m = re.search(r'(?:poster|data-poster)=["\']([^"\']+)["\']', vtag, re.I)
            if poster_m:
                raw_poster = poster_m.group(1).strip()
                if raw_poster and not raw_poster.startswith("data:"):
                    clean_poster = urljoin(target_url, raw_poster)
                    if self.is_thumbnail_candidate(clean_poster) and clean_poster not in found_posters:
                        found_posters.append(clean_poster)

        og_video = re.search(r'<meta[^>]+property=["\'](?:og:video|og:video:url|og:video:secure_url)["\'][^>]+content=["\']([^"\']+)["\']', html, re.I)
        if not og_video:
            og_video = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\'](?:og:video|og:video:url|og:video:secure_url)["\']', html, re.I)
        if og_video:
            v_cand = urljoin(target_url, og_video.group(1).strip())
            # Không nhận segment hay image vào danh sách video
            if not self.is_video_segment_url(v_cand) and not self.is_image_url(v_cand):
                found_videos.append(v_cand)

        video_srcs = re.findall(r'<(?:video|source)[^>]+src=["\']([^"\']+)["\']', html, re.I)
        for v in video_srcs:
            clean_v = urljoin(target_url, v.strip())
            if clean_v not in found_videos and not clean_v.startswith("blob:"):
                # Không nhận segment (.ts, .m4s) hay image (.jpg, .png) vào stream list
                if not self.is_video_segment_url(clean_v) and not self.is_image_url(clean_v):
                    found_videos.append(clean_v)

        for idx, v_url in enumerate(found_videos, 1):
            is_hls = self.is_video_manifest_url(v_url) or (".m3u8" in v_url.lower())
            is_dash = ".mpd" in v_url.lower()
            if is_hls:
                fmt = "HLS"
                quality = "HLS Stream"
            elif is_dash:
                fmt = "DASH"
                quality = "DASH Stream"
            else:
                ext_str = self._extract_ext(v_url) or "MP4"
                fmt = ext_str.upper()
                quality = None

            streams.append(
                StreamFormat(
                    format_id=f"web_video_{idx}",
                    quality=quality,
                    format=fmt,
                    size=None,
                    raw_size=None,
                    stream_type="full",
                    has_audio=True,
                    has_video=True,
                    url=v_url,
                )
            )

        # 5. Trích xuất Hình ảnh
        images: List[MediaImage] = []
        seen_img_urls = set()
        ordered_img_urls: List[str] = []

        # Ưu tiên og:image, twitter:image
        og_img_patterns = [
            r'<meta[^>]+property=["\'](?:og:image|og:image:url|og:image:secure_url)["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\'](?:og:image|og:image:url|og:image:secure_url)["\']',
            r'<meta[^>]+name=["\'](?:twitter:image|twitter:image:src)["\'][^>]+content=["\']([^"\']+)["\']',
            r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\'](?:twitter:image|twitter:image:src)["\']',
        ]
        for pat in og_img_patterns:
            for match in re.finditer(pat, html, re.I):
                raw_img = match.group(1).strip()
                if raw_img and not raw_img.startswith("data:"):
                    full_img = urljoin(target_url, raw_img)
                    # Bắt buộc: Phải là URL ảnh hợp lệ, tuyệt đối không nhận .m3u8, .m4s, .ts, .mp4
                    if self.is_thumbnail_candidate(full_img) and full_img not in seen_img_urls:
                        seen_img_urls.add(full_img)
                        ordered_img_urls.append(full_img)

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

            # Bắt buộc: Loại trừ nếu URL là video, manifest (.m3u8, .mpd) hay segment (.m4s, .ts)
            if self.is_video_manifest_url(full_src) or self.is_video_segment_url(full_src) or (self.classify_url(full_src) == self.MEDIA_TYPE_VIDEO):
                continue

            # Chỉ giữ các tệp có đuôi ảnh hoặc nằm trong wp-content/uploads hoặc images
            has_img_ext = self.is_image_url(full_src) or any(f".{ext}" in lower_src for ext in self.IMAGE_EXTENSIONS)
            is_media_path = any(p in lower_src for p in ("/upload", "/uploads/", "/image", "/images/", "/photo", "/photos/", "/media/"))

            if (has_img_ext or is_media_path) and full_src not in seen_img_urls:
                seen_img_urls.add(full_src)
                ordered_img_urls.append(full_src)

        # Chuyển đổi ordered_img_urls thành MediaImage bảo toàn thứ tự xuất hiện ban đầu
        index = 1
        for img_url in ordered_img_urls:
            clean_ext = self._extract_ext(img_url) or "jpg"
            images.append(
                MediaImage(
                    id=index,
                    url=img_url,
                    title=None,
                    resolution=None,
                    size=None,
                    type="image",
                    ext=clean_ext,
                    thumb=img_url,
                )
            )
            index += 1

        if not images and not streams:
            raise RuntimeError("Không tìm thấy hình ảnh hoặc video nào trên trang web này.")

        # Lựa chọn Thumbnail:
        # 1. Ưu tiên hình ảnh hợp lệ đầu tiên từ images
        # 2. Nếu không có ảnh nhưng video có poster hợp lệ -> sử dụng poster đó
        # 3. Tuyệt đối KHÔNG fallback sang found_videos[0] (.m3u8, .mp4, v.v.)
        first_thumb: Optional[str] = None
        if images:
            first_thumb = images[0].url
        elif found_posters:
            first_thumb = found_posters[0]
        else:
            first_thumb = None

        is_video_primary = bool(streams and not images)

        return MediaMetadata(
            id=str(abs(hash(target_url))),
            platform="generic",
            title=title,
            author=author,
            author_url=None,
            duration=None,
            views=None,
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

    @staticmethod
    def deduplicate_urls(urls: List[str]) -> List[str]:
        """
        Deduplicate danh sách URL nhưng bảo toàn thứ tự xuất hiện ban đầu (insertion order).
        - first occurrence -> giữ lại
        - duplicate -> loại bỏ
        - original order -> giữ nguyên
        """
        seen = set()
        ordered: List[str] = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                ordered.append(u)
        return ordered

