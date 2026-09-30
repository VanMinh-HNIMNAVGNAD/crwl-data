"""
Story Extractor Engine — Facebook & Instagram Stories.

Stories có đặc tính riêng biệt so với post/reel thông thường:
  1. Tồn tại tối đa 24 giờ rồi bị gỡ.
  2. CDN URL hết hạn rất nhanh (5-15 phút).
  3. LUÔN yêu cầu cookie đăng nhập (không có public API).
  4. yt-dlp không hỗ trợ Facebook Story; gallery-dl hỗ trợ Instagram Story
     nhưng không ổn định khi token ngắn hạn.

Engine này dùng curl_cffi (TLS impersonation) hoặc requests để gọi trực tiếp
API nội bộ của Facebook/Instagram, parse CDN URL và trả về MediaMetadata chuẩn
cho Rust downloader tải ngay — không đi qua yt-dlp hay gallery-dl.
"""

import html as html_lib
import json
import os
import re
import time
from typing import Optional, List, Dict, Any, Tuple
from .base import BaseExtractor
from ..models import MediaMetadata, MediaImage, StreamFormat
from ..cookies.browser_cookies import get_browser_cookies_txt
from ..cancellation import cap_timeout, raise_if_cancelled

try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    cffi_requests = None

try:
    import requests as stdlib_requests
except ImportError:
    stdlib_requests = None


class StoryExtractor(BaseExtractor):
    """Trích xuất Story từ Facebook và Instagram."""

    # ── Nhận diện URL Story ─────────────────────────────────────────────────

    # Facebook Story URL patterns:
    #   https://www.facebook.com/stories/123456789/
    #   https://www.facebook.com/stories/USERNAME/123456789/
    #   https://m.facebook.com/stories/123456789/
    #   https://fb.watch/story/...  (hiếm)
    _FB_STORY_RE = re.compile(
        r"(?:https?://)?(?:(?:www|m|web|touch)\.)?facebook\.com/stories/(?:(?P<user>[^/]+)/)?(?P<id>\d+)",
        re.IGNORECASE,
    )

    # Instagram Story URL patterns:
    #   https://www.instagram.com/stories/USERNAME/123456789/
    #   https://instagram.com/stories/USERNAME/123456789/
    _IG_STORY_RE = re.compile(
        r"(?:https?://)?(?:(?:www)\.)?instagram\.com/stories/(?P<user>[^/]+)/(?P<id>\d+)",
        re.IGNORECASE,
    )

    # Chuỗi impersonate ưu tiên (trình duyệt mới nhất → cũ)
    _IMPERSONATE_TARGETS = ["chrome131", "safari18_0", "edge101", "chrome120"]

    # Headers mặc định giả lập trình duyệt thật
    _COMMON_HEADERS = {
        "Accept": "*/*",
        "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }

    def __init__(self):
        super().__init__()

    # ── Public API ──────────────────────────────────────────────────────────

    @classmethod
    def is_story_url(cls, url: str) -> bool:
        """URL này có phải là Story của Facebook hoặc Instagram không?"""
        return cls.is_facebook_story(url) or cls.is_instagram_story(url)

    @classmethod
    def is_facebook_story(cls, url: str) -> bool:
        return bool(cls._FB_STORY_RE.search(url or ""))

    @classmethod
    def is_instagram_story(cls, url: str) -> bool:
        return bool(cls._IG_STORY_RE.search(url or ""))

    def extract(self, url: str, browser: Optional[str] = None) -> MediaMetadata:
        """Điểm vào chính: trích xuất Story và trả về MediaMetadata."""
        if self.is_facebook_story(url):
            return self._extract_facebook_story(url, browser)
        if self.is_instagram_story(url):
            return self._extract_instagram_story(url, browser)
        raise ValueError(f"URL không phải Story Facebook/Instagram: {url}")

    # ── Facebook Story ──────────────────────────────────────────────────────

    def _extract_facebook_story(self, url: str, browser: Optional[str] = None) -> MediaMetadata:
        """Trích xuất Facebook Story qua mobile page scraping + GraphQL."""
        match = self._FB_STORY_RE.search(url)
        story_id = match.group("id") if match else "unknown"
        story_user = (match.group("user") if match else None) or ""

        cookies = self._load_cookies_dict(browser, "facebook.com")
        if not cookies or "c_user" not in cookies:
            raise RuntimeError(
                "Facebook Story yêu cầu đăng nhập. "
                "Hãy đăng nhập Facebook trên trình duyệt (Chrome/Edge/Firefox) rồi thử lại, "
                "hoặc mở Cookie Manager (🍪) → tab Facebook và dán cookie."
            )

        # Chuẩn hóa URL sang mobile (cho phản hồi nhẹ hơn, dễ parse hơn)
        mobile_url = re.sub(
            r"(?:www|web|touch)\.facebook\.com",
            "m.facebook.com",
            url,
        )
        if "m.facebook.com" not in mobile_url:
            mobile_url = url.replace("facebook.com", "m.facebook.com")

        self.log(f"Facebook Story: {mobile_url} (story_id={story_id})")

        # Thử lấy media qua mobile page HTML
        result = self._fb_story_from_mobile_page(mobile_url, cookies, story_id, story_user)
        if result:
            return result

        # Fallback: thử GraphQL endpoint
        result = self._fb_story_from_graphql(story_id, cookies, story_user, url)
        if result:
            return result

        raise RuntimeError(
            "Không thể trích xuất Facebook Story. "
            "Story có thể đã hết hạn (quá 24 giờ) hoặc bạn không có quyền xem. "
            "Hãy đảm bảo bạn đã đăng nhập Facebook trên trình duyệt."
        )

    def _fb_story_from_mobile_page(
        self,
        mobile_url: str,
        cookies: Dict[str, str],
        story_id: str,
        story_user: str,
    ) -> Optional[MediaMetadata]:
        """Lấy CDN URL từ mobile Facebook page HTML."""
        headers = {
            **self._COMMON_HEADERS,
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Mobile Safari/537.36"
            ),
            "Referer": "https://m.facebook.com/",
        }

        html = self._http_get(mobile_url, cookies=cookies, headers=headers)
        if not html:
            return None

        # Tìm video URL trong HTML response
        video_urls = self._extract_urls_from_html(html, is_video=True)
        image_urls = self._extract_urls_from_html(html, is_video=False)

        if not video_urls and not image_urls:
            # Thử tìm trong JSON nhúng (Facebook đặt data trong <script> tags)
            video_urls, image_urls = self._extract_from_embedded_json(html)

        if not video_urls and not image_urls:
            return None

        return self._build_story_metadata(
            story_id=story_id,
            platform="facebook",
            author=story_user or None,
            original_url=mobile_url,
            video_urls=video_urls,
            image_urls=image_urls,
        )

    def _fb_story_from_graphql(
        self,
        story_id: str,
        cookies: Dict[str, str],
        story_user: str,
        original_url: str,
    ) -> Optional[MediaMetadata]:
        """Thử lấy Story qua Facebook GraphQL API."""
        fb_dtsg = cookies.get("fb_dtsg") or self._get_fb_dtsg(cookies)
        if not fb_dtsg:
            self.warn("Không lấy được fb_dtsg token cho GraphQL request")
            return None

        api_url = "https://www.facebook.com/api/graphql/"
        headers = {
            **self._COMMON_HEADERS,
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": original_url,
            "X-FB-LSD": cookies.get("lsd", ""),
        }

        # Facebook GraphQL query cho Story
        variables = json.dumps({
            "bucketID": story_id,
            "scale": 2,
        })

        data = {
            "fb_dtsg": fb_dtsg,
            "variables": variables,
            "doc_id": "3768218553259975",  # StoryViewerQuery
        }

        response_text = self._http_post(api_url, data=data, cookies=cookies, headers=headers)
        if not response_text:
            return None

        try:
            result = json.loads(response_text)
        except json.JSONDecodeError:
            # Facebook đôi khi trả về nhiều JSON objects trên nhiều dòng
            for line in response_text.strip().split("\n"):
                try:
                    result = json.loads(line)
                    if isinstance(result, dict):
                        break
                except json.JSONDecodeError:
                    continue
            else:
                return None

        return self._parse_fb_graphql_story(result, story_id, story_user, original_url)

    def _parse_fb_graphql_story(
        self,
        data: Dict[str, Any],
        story_id: str,
        story_user: str,
        original_url: str,
    ) -> Optional[MediaMetadata]:
        """Parse kết quả GraphQL của Facebook để lấy media URLs."""
        video_urls: List[str] = []
        image_urls: List[str] = []

        # Duyệt đệ quy tìm video_url và image_url trong response
        self._deep_extract_urls(data, video_urls, image_urls)

        if not video_urls and not image_urls:
            return None

        return self._build_story_metadata(
            story_id=story_id,
            platform="facebook",
            author=story_user or None,
            original_url=original_url,
            video_urls=video_urls,
            image_urls=image_urls,
        )

    def _get_fb_dtsg(self, cookies: Dict[str, str]) -> Optional[str]:
        """Lấy fb_dtsg token từ trang chủ Facebook."""
        headers = {
            **self._COMMON_HEADERS,
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        }
        html = self._http_get("https://www.facebook.com/", cookies=cookies, headers=headers)
        if not html:
            return None

        # fb_dtsg nằm trong HTML dạng "fb_dtsg":"TOKEN" hoặc name="fb_dtsg" value="TOKEN"
        patterns = [
            r'"fb_dtsg"\s*:\s*"([^"]+)"',
            r'name="fb_dtsg"\s+value="([^"]+)"',
            r'"DTSGInitData"\s*,\s*\[\]\s*,\s*\{"token"\s*:\s*"([^"]+)"',
            r'"DTSGInitialData"\s*,\s*\[\]\s*,\s*\{"token"\s*:\s*"([^"]+)"',
        ]
        for pattern in patterns:
            m = re.search(pattern, html)
            if m:
                return m.group(1)
        return None

    # ── Instagram Story ─────────────────────────────────────────────────────

    def _extract_instagram_story(self, url: str, browser: Optional[str] = None) -> MediaMetadata:
        """Trích xuất Instagram Story qua REST API."""
        match = self._IG_STORY_RE.search(url)
        story_user = match.group("user") if match else "unknown"
        story_id = match.group("id") if match else "unknown"

        cookies = self._load_cookies_dict(browser, "instagram.com")
        if not cookies or "sessionid" not in cookies:
            raise RuntimeError(
                "Instagram Story yêu cầu đăng nhập. "
                "Hãy đăng nhập Instagram trên trình duyệt (Chrome/Edge/Firefox) rồi thử lại, "
                "hoặc mở Cookie Manager (🍪) → tab Instagram và dán cookie."
            )

        self.log(f"Instagram Story: user={story_user}, id={story_id}")

        # Bước 1: Lấy user_id từ username qua web profile info API
        user_id = self._ig_get_user_id(story_user, cookies)
        if not user_id:
            raise RuntimeError(
                f"Không tìm thấy tài khoản Instagram '{story_user}'. "
                f"Kiểm tra lại tên tài khoản hoặc đảm bảo cookie đăng nhập còn hiệu lực."
            )

        # Bước 2: Lấy Story data qua reels_media API
        result = self._ig_get_story_media(user_id, story_id, cookies, url)
        if result:
            return result

        # Bước 3: Fallback - thử highlight reel API
        result = self._ig_get_story_from_highlights(user_id, story_id, cookies, url, story_user)
        if result:
            return result

        raise RuntimeError(
            "Không thể trích xuất Instagram Story. "
            "Story có thể đã hết hạn (quá 24 giờ), bạn không có quyền xem, "
            "hoặc cookie đăng nhập đã hết hiệu lực. Hãy thử đăng nhập lại trên trình duyệt."
        )

    def _ig_get_user_id(self, username: str, cookies: Dict[str, str]) -> Optional[str]:
        """Lấy Instagram user_id từ username."""
        headers = {
            **self._COMMON_HEADERS,
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "X-IG-App-ID": "936619743392459",  # Instagram Web App ID
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"https://www.instagram.com/{username}/",
        }

        api_url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"
        response_text = self._http_get(api_url, cookies=cookies, headers=headers)
        if not response_text:
            return None

        try:
            data = json.loads(response_text)
            user_data = data.get("data", {}).get("user", {})
            return str(user_data.get("id") or user_data.get("pk") or "")
        except (json.JSONDecodeError, AttributeError):
            # Thử parse pattern khác
            m = re.search(r'"id"\s*:\s*"(\d+)"', response_text)
            return m.group(1) if m else None

    def _ig_get_story_media(
        self,
        user_id: str,
        story_id: str,
        cookies: Dict[str, str],
        original_url: str,
    ) -> Optional[MediaMetadata]:
        """Lấy Story media qua Instagram REST API /feed/reels_media/."""
        headers = {
            **self._COMMON_HEADERS,
            "User-Agent": (
                "Instagram 315.0.0.0.58 Android "
                "(33/13; 420dpi; 1080x2220; Google/google; Pixel 7; panther; panther; en_US; 558059709)"
            ),
            "X-IG-App-ID": "936619743392459",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": original_url,
        }

        api_url = f"https://i.instagram.com/api/v1/feed/reels_media/?reel_ids={user_id}"
        response_text = self._http_get(api_url, cookies=cookies, headers=headers)
        if not response_text:
            return None

        try:
            data = json.loads(response_text)
        except json.JSONDecodeError:
            return None

        reels = data.get("reels_media") or data.get("reels") or []
        if isinstance(reels, dict):
            reels = list(reels.values())

        for reel in reels:
            items = reel.get("items") or []
            # Tìm story item có matching ID
            target_item = None
            for item in items:
                item_pk = str(item.get("pk") or item.get("id") or "")
                if item_pk == story_id or story_id in item_pk:
                    target_item = item
                    break

            if not target_item and items:
                # Story được yêu cầu không còn trong danh sách (hết 24 giờ, hoặc là
                # story ghim trong Highlight). KHÔNG lấy đại story đầu tiên — người
                # dùng sẽ tải nhầm story khác mà không hề biết. Để bước sau tra
                # thẳng theo ID (media/{id}/info).
                self.warn(f"Không thấy story {story_id} trong {len(items)} story hiện có của tài khoản")
                continue

            if target_item:
                return self._parse_ig_story_item(
                    target_item,
                    reel.get("user", {}),
                    original_url,
                )

        return None

    def _ig_get_story_from_highlights(
        self,
        user_id: str,
        story_id: str,
        cookies: Dict[str, str],
        original_url: str,
        story_user: str,
    ) -> Optional[MediaMetadata]:
        """Thử lấy Story từ highlights API (story đã ghim)."""
        headers = {
            **self._COMMON_HEADERS,
            "User-Agent": (
                "Instagram 315.0.0.0.58 Android "
                "(33/13; 420dpi; 1080x2220; Google/google; Pixel 7; panther; panther; en_US; 558059709)"
            ),
            "X-IG-App-ID": "936619743392459",
            "X-Requested-With": "XMLHttpRequest",
        }

        # Thử direct media endpoint
        api_url = f"https://i.instagram.com/api/v1/media/{story_id}/info/"
        response_text = self._http_get(api_url, cookies=cookies, headers=headers)
        if response_text:
            try:
                data = json.loads(response_text)
                items = data.get("items") or []
                if items:
                    user_info = items[0].get("user", {})
                    if not user_info.get("username"):
                        user_info["username"] = story_user
                    return self._parse_ig_story_item(items[0], user_info, original_url)
            except json.JSONDecodeError:
                pass

        return None

    def _parse_ig_story_item(
        self,
        item: Dict[str, Any],
        user: Dict[str, Any],
        original_url: str,
    ) -> MediaMetadata:
        """Parse một Instagram Story item thành MediaMetadata."""
        story_id = str(item.get("pk") or item.get("id") or int(time.time()))
        username = user.get("username") or user.get("full_name") or "unknown"

        # Video
        video_versions = item.get("video_versions") or []
        video_urls = [v.get("url") for v in video_versions if v.get("url")]

        # Ảnh: `candidates` là CÙNG MỘT ảnh ở nhiều độ phân giải, không phải nhiều
        # ảnh. Trước đây mỗi candidate thành một ảnh riêng → story 1 ảnh hiện thành
        # "album" gồm các bản sao nhỏ dần. Chỉ giữ bản lớn nhất.
        image_candidates = sorted(
            [c for c in ((item.get("image_versions2") or {}).get("candidates") or []) if c.get("url")],
            key=lambda c: (c.get("width") or 0) * (c.get("height") or 0),
            reverse=True,
        )[:1]
        image_urls = [c.get("url") for c in image_candidates]

        is_video = bool(video_urls) or item.get("media_type") == 2

        # Thumbnail
        thumbnail = None
        if image_urls:
            # Chọn ảnh có kích thước vừa phải làm thumbnail
            thumbnail = image_urls[0]
        elif video_urls:
            # Video story: lấy ảnh bìa nếu có
            cover = (item.get("image_versions2") or {}).get("candidates") or []
            if cover:
                thumbnail = cover[0].get("url")

        # Streams
        streams: List[StreamFormat] = []
        if video_urls:
            for i, vurl in enumerate(video_urls):
                v_info = video_versions[i] if i < len(video_versions) else {}
                w = v_info.get("width", 0)
                h = v_info.get("height", 0)
                quality = f"{w}x{h}" if w and h else "Video gốc"
                streams.append(
                    StreamFormat(
                        format_id=f"story_video_{i}",
                        quality=f"{quality} — Video Story chất lượng cao",
                        format="MP4",
                        size=None,
                        raw_size=None,
                        stream_type="full",
                        has_audio=True,
                        has_video=True,
                        url=vurl,
                    )
                )

        # Images cho album view
        images: List[MediaImage] = []
        if not is_video and image_urls:
            for i, iurl in enumerate(image_urls):
                c = image_candidates[i] if i < len(image_candidates) else {}
                w = c.get("width", 0)
                h = c.get("height", 0)
                images.append(
                    MediaImage(
                        id=i + 1,
                        url=iurl,
                        title=f"Story Photo {i + 1}",
                        resolution=f"{w}x{h}" if w and h else None,
                        size=None,
                        type="image",
                        ext="jpg",
                        thumb=iurl,
                    )
                )

        duration_sec = item.get("video_duration")
        from .ytdlp import YtDlpExtractor
        duration = YtDlpExtractor.format_duration(duration_sec) if duration_sec else None

        return MediaMetadata(
            id=story_id,
            platform="instagram",
            title=f"Story của @{username}",
            author=username,
            author_url=f"https://www.instagram.com/{username}/",
            duration=duration,
            views=None,
            thumbnail=thumbnail,
            high_res_thumbnail=image_urls[0] if image_urls else thumbnail,
            type="video" if is_video else ("album" if len(images) > 1 else "image"),
            original_url=original_url,
            description=f"📷 Instagram Story — {'Video' if is_video else 'Ảnh'} story từ @{username}",
            streams=streams if streams else None,
            images=images if images else None,
            is_story=True,
        )

    # ── HTTP Helpers ────────────────────────────────────────────────────────

    def _http_get(
        self,
        url: str,
        cookies: Optional[Dict[str, str]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Optional[str]:
        """GET request ưu tiên curl_cffi (TLS impersonation), fallback requests."""
        if cffi_requests:
            for target in self._IMPERSONATE_TARGETS:
                raise_if_cancelled()
                try:
                    resp = cffi_requests.get(
                        url,
                        impersonate=target,
                        cookies=cookies,
                        headers=headers,
                        timeout=cap_timeout(15),
                        allow_redirects=True,
                    )
                    if resp.status_code == 200:
                        return resp.text
                    if resp.status_code in (401, 403):
                        self.warn(f"HTTP {resp.status_code} với impersonate={target}")
                        continue
                except Exception as e:
                    self.warn(f"curl_cffi GET lỗi ({target}): {e}")
                    continue

        # Fallback sang requests
        if stdlib_requests:
            try:
                resp = stdlib_requests.get(
                    url,
                    cookies=cookies,
                    headers=headers,
                    timeout=cap_timeout(15),
                    allow_redirects=True,
                )
                if resp.status_code == 200:
                    return resp.text
            except Exception as e:
                self.warn(f"requests GET lỗi: {e}")

        return None

    def _http_post(
        self,
        url: str,
        data: Optional[Dict[str, str]] = None,
        cookies: Optional[Dict[str, str]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> Optional[str]:
        """POST request ưu tiên curl_cffi, fallback requests."""
        if cffi_requests:
            for target in self._IMPERSONATE_TARGETS:
                raise_if_cancelled()
                try:
                    resp = cffi_requests.post(
                        url,
                        data=data,
                        impersonate=target,
                        cookies=cookies,
                        headers=headers,
                        timeout=cap_timeout(15),
                    )
                    if resp.status_code == 200:
                        return resp.text
                    if resp.status_code in (401, 403):
                        continue
                except Exception as e:
                    self.warn(f"curl_cffi POST lỗi ({target}): {e}")
                    continue

        if stdlib_requests:
            try:
                resp = stdlib_requests.post(
                    url,
                    data=data,
                    cookies=cookies,
                    headers=headers,
                    timeout=cap_timeout(15),
                )
                if resp.status_code == 200:
                    return resp.text
            except Exception as e:
                self.warn(f"requests POST lỗi: {e}")

        return None

    # ── Cookie Helpers ──────────────────────────────────────────────────────

    def _load_cookies_dict(
        self,
        browser: Optional[str],
        domain: str,
    ) -> Dict[str, str]:
        """Xuất cookie từ trình duyệt thành dict {name: value}."""
        if browser == "none":
            return {}

        cookie_file = get_browser_cookies_txt(browser or "auto", domain=domain)
        if not cookie_file or not os.path.exists(cookie_file):
            return {}

        cookies: Dict[str, str] = {}
        try:
            with open(cookie_file, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split("\t")
                    if len(parts) >= 7:
                        cookie_name = parts[5]
                        cookie_value = parts[6]
                        cookies[cookie_name] = cookie_value
        except Exception as e:
            self.warn(f"Đọc cookie file lỗi: {e}")
        finally:
            # Dọn file cookie tạm
            try:
                os.remove(cookie_file)
            except Exception:
                pass

        return cookies

    # ── HTML/JSON Parsing Helpers ───────────────────────────────────────────

    @staticmethod
    def _extract_urls_from_html(html: str, is_video: bool = True) -> List[str]:
        """Trích xuất media URLs từ HTML content."""
        urls = []
        if is_video:
            # Tìm video source URLs
            patterns = [
                r'"video_url"\s*:\s*"([^"]+)"',
                r'"playable_url"\s*:\s*"([^"]+)"',
                r'"playable_url_quality_hd"\s*:\s*"([^"]+)"',
                r'"browser_native_hd_url"\s*:\s*"([^"]+)"',
                r'"browser_native_sd_url"\s*:\s*"([^"]+)"',
                r'"hd_src"\s*:\s*"([^"]+)"',
                r'"sd_src"\s*:\s*"([^"]+)"',
                r'<source[^>]+src="([^"]+\.mp4[^"]*)"',
                r'"src"\s*:\s*"(https?://[^"]+\.mp4[^"]*)"',
                r'"url"\s*:\s*"(https?://[^"]+\.mp4[^"]*)"',
            ]
        else:
            # Tìm image URLs
            patterns = [
                r'"image"\s*:\s*\{[^}]*"uri"\s*:\s*"([^"]+)"',
                r'"full_size_url"\s*:\s*"([^"]+)"',
                r'"large_src"\s*:\s*"([^"]+)"',
                r'"previewImage"\s*:\s*\{[^}]*"uri"\s*:\s*"([^"]+)"',
                r'"url"\s*:\s*"(https?://[^"]+\.(?:jpg|jpeg|png|webp)[^"]*)"',
            ]

        seen = set()
        for pattern in patterns:
            for match in re.finditer(pattern, html):
                clean = StoryExtractor._decode_js_string(match.group(1))
                if clean not in seen and clean.startswith("http"):
                    seen.add(clean)
                    urls.append(clean)

        return urls

    @staticmethod
    def _decode_js_string(raw: str) -> str:
        """Giải mã một chuỗi lấy từ JSON/JS nhúng trong HTML.

        Trước đây dùng `raw.encode().decode("unicode_escape")`: cách này để nguyên
        `\\/` (URL ra dạng `https:\\/\\/scontent...`, tải về hỏng) và làm vỡ ký tự
        UTF-8 (tiếng Việt thành mojibake). Giải mã đúng theo cú pháp chuỗi JSON,
        rồi bỏ mã thực thể HTML (`&amp;`) trong thuộc tính thẻ <source>.
        """
        try:
            decoded = json.loads(f'"{raw}"')
        except (ValueError, TypeError):
            decoded = raw.replace("\\/", "/")
        return html_lib.unescape(decoded)

    @staticmethod
    def _extract_from_embedded_json(html: str) -> Tuple[List[str], List[str]]:
        """Tìm media URLs trong các khối JSON nhúng trong <script> tags."""
        video_urls: List[str] = []
        image_urls: List[str] = []

        # Tìm tất cả JSON objects lớn trong script tags
        for match in re.finditer(r'<script[^>]*>(\{.{100,}?\})</script>', html, re.DOTALL):
            try:
                data = json.loads(match.group(1))
                StoryExtractor._deep_extract_urls(data, video_urls, image_urls)
            except json.JSONDecodeError:
                continue

        # Tìm trong các require() calls (Facebook pattern)
        for match in re.finditer(r'require\(\[.*?\],function.*?\{(.*?)\}\)', html, re.DOTALL):
            content = match.group(1)
            v_urls = StoryExtractor._extract_urls_from_html(content, is_video=True)
            i_urls = StoryExtractor._extract_urls_from_html(content, is_video=False)
            video_urls.extend(v_urls)
            image_urls.extend(i_urls)

        return video_urls, image_urls

    @staticmethod
    def _deep_extract_urls(
        data: Any,
        video_urls: List[str],
        image_urls: List[str],
        _depth: int = 0,
    ) -> None:
        """Duyệt đệ quy JSON để tìm tất cả media URLs."""
        if _depth > 15:  # Chống stack overflow
            return

        if isinstance(data, dict):
            for key, value in data.items():
                key_lower = key.lower()
                if isinstance(value, str) and value.startswith("http"):
                    if any(k in key_lower for k in (
                        "video_url", "playable_url", "hd_src", "sd_src",
                        "browser_native", "video_source", "dash_manifest",
                    )):
                        if value not in video_urls:
                            video_urls.append(value)
                    elif any(k in key_lower for k in (
                        "image_url", "full_size", "large_src",
                        "preview_image", "thumbnail",
                    )):
                        if value not in image_urls:
                            image_urls.append(value)
                    elif ".mp4" in value or ".m3u8" in value:
                        if value not in video_urls:
                            video_urls.append(value)
                elif key == "uri" and isinstance(value, str) and value.startswith("http"):
                    if ".mp4" in value:
                        if value not in video_urls:
                            video_urls.append(value)
                    elif any(ext in value for ext in (".jpg", ".jpeg", ".png", ".webp")):
                        if value not in image_urls:
                            image_urls.append(value)
                else:
                    StoryExtractor._deep_extract_urls(value, video_urls, image_urls, _depth + 1)
        elif isinstance(data, list):
            for item in data:
                StoryExtractor._deep_extract_urls(item, video_urls, image_urls, _depth + 1)

    # ── Build Result ────────────────────────────────────────────────────────

    @staticmethod
    def _build_story_metadata(
        story_id: str,
        platform: str,
        author: Optional[str],
        original_url: str,
        video_urls: List[str],
        image_urls: List[str],
    ) -> MediaMetadata:
        """Xây dựng MediaMetadata chuẩn từ danh sách URLs."""
        is_video = bool(video_urls)

        streams: List[StreamFormat] = []
        images: List[MediaImage] = []

        if video_urls:
            for i, vurl in enumerate(video_urls):
                # Các URL được quét từ HTML/JSON của trang theo thứ tự xuất hiện: không
                # biết chắc URL nào là HD/SD, cũng không chắc chúng cùng một story (trang
                # story có thể chứa story kế tiếp). Gắn nhãn đúng sự thật thay vì bịa HD/SD.
                quality = "Nguồn chính" if i == 0 else f"Nguồn phụ {i}"
                streams.append(
                    StreamFormat(
                        format_id=f"story_video_{i}",
                        quality=f"{quality} — Video Story",
                        format="MP4",
                        size=None,
                        raw_size=None,
                        stream_type="full",
                        has_audio=True,
                        has_video=True,
                        url=vurl,
                    )
                )

        if not is_video and image_urls:
            for i, iurl in enumerate(image_urls):
                images.append(
                    MediaImage(
                        id=i + 1,
                        url=iurl,
                        title=f"Story Photo {i + 1}",
                        resolution=None,
                        size=None,
                        type="image",
                        ext="jpg",
                        thumb=iurl,
                    )
                )

        thumbnail = image_urls[0] if image_urls else None
        platform_name = "Facebook" if platform == "facebook" else "Instagram"
        media_type_desc = "Video" if is_video else "Ảnh"
        author_display = f"@{author}" if author else platform_name

        return MediaMetadata(
            id=story_id,
            platform=platform,
            title=f"Story của {author_display}",
            author=author,
            author_url=None,
            duration=None,
            views=None,
            thumbnail=thumbnail,
            high_res_thumbnail=thumbnail,
            type="video" if is_video else ("album" if len(images) > 1 else "image"),
            original_url=original_url,
            description=f"📷 {platform_name} Story — {media_type_desc} story từ {author_display}",
            streams=streams if streams else None,
            images=images if images else None,
            is_story=True,
        )
