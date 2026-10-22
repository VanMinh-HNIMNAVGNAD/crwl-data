"""
Bóc tách TOÀN BỘ ảnh / video của MỘT bài viết trên trang web bất kỳ.

Trước đây trang không thuộc nền tảng quen thuộc đi qua chuỗi yt-dlp → gallery-dl →
web_scraper → Playwright, và:
  - bài chỉ có ảnh: web_scraper tìm được ảnh nhưng dispatcher bỏ đi vì "không có
    stream", còn Playwright chỉ nhận ảnh CDN của Facebook/Instagram/X → báo
    "Không tìm được nguồn video", 0 ảnh;
  - bài có nhiều video: yt-dlp chỉ trả video ĐẦU TIÊN, ảnh trong bài mất hết;
  - bài dựng bằng JavaScript: chỉ bắt được luồng video qua mạng, mất hết ảnh;
  - trang cần đăng nhập: web_scraper không gửi cookie nên chỉ thấy trang đăng nhập.

Module này:
  1. Tải HTML kèm cookie đăng nhập của trình duyệt cho ĐÚNG tên miền của trang (nếu
     có) và đọc bằng html.parser — thời gian tuyến tính. Regex dò thẻ kiểu cũ có
     thể chạy hàng phút trên trang lớn và khoá cả engine (regex giữ GIL).
  2. Xác định vùng BÀI VIẾT (article / main / khung nội dung) để bỏ logo, quảng
     cáo, bài liên quan, bình luận.
  3. Trang dựng bằng JS, cần đăng nhập, ảnh lazy-load, player video ẩn, hoặc HTML
     tĩnh không có media: mở bằng Chromium headless (stream_sniffer) kèm cookie,
     cuộn để nạp ảnh, đọc DOM và bắt luồng video qua mạng.
  4. Gộp các bản thu nhỏ của cùng một ảnh (srcset, "-300x200", link tới ảnh gốc):
     tải bản lớn nhất, xem trước bằng bản nhỏ (WebKitGTK giải mã nguyên ảnh gốc
     trong lưới xem trước — vài chục ảnh 4K là vài GB RAM).
"""

import http.cookiejar
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import zlib
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple

from .base import SOCIAL_DECOR_URL_PARTS, BaseExtractor
from ..cancellation import cap_timeout, raise_if_cancelled
from ..cookies.browser_cookies import expiry_seconds, get_browser_cookies_txt
from ..models import MediaImage, MediaMetadata, StreamFormat


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# Chỉ đọc tối đa chừng này byte HTML. Trang lớn hơn vẫn được đọc phần đầu (bài
# viết gần như luôn nằm ở đó) thay vì báo lỗi như web_scraper cũ.
MAX_HTML_BYTES = _env_int("CRWL_PAGE_MAX_HTML", 8 * 1024 * 1024)
# Số tệp tối đa trả về cho MỘT trang: kết quả đi qua IPC, SQLite và lưới xem
# trước của giao diện — trang "vô tận" hàng chục nghìn ảnh không được làm treo app.
MAX_ITEMS = max(1, _env_int("CRWL_PAGE_MAX_ITEMS", 500))
MAX_RAW_CANDIDATES = 20000
MAX_JSONLD_BYTES = 2 * 1024 * 1024
MAX_URL_LEN = 4096
# HTML lỗi / cố ý lồng sâu: không theo dõi quá chừng này thẻ đang mở
MAX_STACK = 3000
MAX_OPEN_CONTAINERS = 64
FETCH_TIMEOUT = 20

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

IMAGE_EXTS = frozenset({"jpg", "jpeg", "jfif", "png", "gif", "webp", "avif", "bmp", "heic", "heif", "tif", "tiff"})
VIDEO_EXTS = frozenset({"mp4", "m4v", "webm", "mov", "mkv", "ogv", "3gp", "avi", "flv", "wmv"})
STREAM_EXTS = frozenset({"m3u8", "mpd"})
SEGMENT_EXTS = frozenset({"ts", "m4s"})

_VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
    "param", "source", "track", "wbr", "keygen", "command",
})

# Thuộc tính lazy-load phổ biến (lazysizes, WordPress, Jetpack, theme tin tức...)
_LAZY_ATTRS = (
    "data-src", "data-lazy-src", "data-original", "data-lazy", "data-url", "data-echo",
    "data-image", "data-img", "data-orig-src", "data-actualsrc", "data-pagespeed-lazy-src",
    "data-ll-src", "data-cfsrc", "data-ssrc",
)
_SRCSET_ATTRS = ("srcset", "data-srcset", "data-lazy-srcset")
# Thuộc tính trỏ tới bản GỐC / lớn của ảnh (lightbox, Jetpack, zoom)
_FULL_ATTRS = (
    "data-orig-file", "data-large-file", "data-full-url", "data-full", "data-fullsrc",
    "data-full-src", "data-zoom-image", "data-zoom-src", "data-large", "data-large-src",
    "data-hi-res-src", "data-hires", "data-highres", "data-original-src", "data-src-large",
)
# Tên thuộc tính gửi xuống bộ thu thập DOM của Playwright (cùng ý nghĩa như trên)
DOM_IMG_ATTRS = ("src",) + _SRCSET_ATTRS + _LAZY_ATTRS + _FULL_ATTRS + ("width", "height", "alt", "class")

_PLACEHOLDER_HINTS = (
    "blank.gif", "spacer.gif", "pixel.gif", "transparent.gif", "transparent.png",
    "placeholder", "lazyload", "lazy-load", "loading.gif", "loader.gif", "grey.gif", "gray.gif",
)

# Máy chủ quảng cáo / đo lường và ảnh trang trí: không bao giờ là nội dung bài viết
_JUNK_URL_PARTS = (
    "doubleclick.net", "google-analytics.com", "googletagmanager.com", "googlesyndication.com",
    "googleadservices.com", "adservice.google", "scorecardresearch.com", "quantserve.com",
    "facebook.com/tr", "pixel.wp.com", "stats.wp.com", "bat.bing.com", "adnxs.com",
    "amazon-adsystem.com", "taboola.com", "outbrain.com", "criteo.", "gravatar.com",
    "s.w.org/images/core/emoji", "/smilies/", "/emoji/", "twemoji", "emojione", "joypixels",
    "favicon", "sprite", "spinner",
    # Avatar mặc định của người bình luận trên Reddit: trang bài viết khi chưa đăng nhập
    # từng ra "7 ảnh và 1 video" — 7 ảnh là avatar_default_0..7.png.
    "redditstatic.com/avatars/",
) + SOCIAL_DECOR_URL_PARTS
# class của <img> trang trí: emoji / mặt cười chèn trong chữ, avatar người đăng, icon.
# Emoji của diễn đàn (XenForo "smilie") khai báo 72x72 nên lọt bộ lọc theo kích thước.
_DECOR_CLASS_WORDS = frozenset({
    "emoji", "smilie", "smiley", "smilies", "emoticon", "wp-smiley", "avatar", "icon",
    "gravatar", "reaction", "badge",
})
# og:image chung cho cả trang (thẻ chia sẻ mặc định, logo): không phải media của bài.
# Bài chỉ có chữ từng ra "1 ảnh trong bài viết" — chính là ảnh chia sẻ mặc định.
_GENERIC_CARD_RE = re.compile(
    r"(?:default|ogimage|og[-_]image|share[-_]?image|social[-_]?(?:image|card|share)|placeholder"
    r"|fallback|logo|/wp-content/themes/|/themes/|/assets/)",
    re.I,
)

# Ảnh mang tên logo / icon / avatar: chỉ bỏ khi nằm NGOÀI bài viết (bài viết về
# logo vẫn giữ được ảnh của nó).
_DECOR_NAME_RE = re.compile(r"(?:^|[/_.-])(?:icons?|logos?|avatars?|badges?|emojis?)(?:[/_.-]|$)", re.I)

# Từ trong class/id đánh dấu vùng KHÔNG phải nội dung bài viết
_CHROME_WORDS = frozenset({
    "sidebar", "widget", "widgets", "comment", "comments", "related", "share", "sharing",
    "sharedaddy", "social", "footer", "navbar", "nav", "navigation", "menu", "breadcrumb",
    "breadcrumbs", "advert", "advertisement", "ads", "ad", "adsbygoogle", "popup", "modal",
    "newsletter", "subscribe", "cookie", "cookies", "promo", "sponsored", "recommended",
    "recommendation", "recommendations", "trending", "outbrain", "taboola", "relatedposts",
})
# class/id (đã chuẩn hoá về chữ thường, nối bằng "-") của khung nội dung bài viết
_MAIN_TOKENS = frozenset({
    "entry-content", "post-content", "post-body", "article-body", "article-content",
    "articlebody", "story-body", "content-body", "post-entry", "blog-post", "single-post",
    "post-text", "article-text", "main-content", "maincontent", "news-content",
    "detail-content", "content-detail", "postcontent", "td-post-content", "article-detail",
    "fck-detail", "singular-content", "the-article-body", "knc-content", "post-single",
    "entry-body", "story-content", "article-main", "post-inner", "single-content",
})
_ARTICLE_ITEMTYPES = (
    "/article", "/newsarticle", "/blogposting", "/socialmediaposting",
    "/discussionforumposting", "/liveblogposting", "/reportagenewsarticle",
)
# Kiểu JSON-LD mà trường "image" là ảnh của chính bài viết (không phải logo, tác giả)
_LD_POST_TYPES = frozenset({
    "article", "newsarticle", "blogposting", "socialmediaposting", "discussionforumposting",
    "liveblogposting", "reportagenewsarticle", "imagegallery", "mediagallery", "recipe",
    "howto", "webpage", "itempage", "posting",
})

_WP_SIZE_RE = re.compile(r"-(\d{2,5})x(\d{2,5})(?=\.[a-z0-9]{2,5}$)", re.I)
_WP_SCALED_RE = re.compile(r"-scaled(?=\.[a-z0-9]{2,5}$)", re.I)
_SHOPIFY_SIZE_RE = re.compile(
    r"_(?:\d{2,5}x\d{0,5}|x\d{2,5}|pico|icon|thumb|small|compact|medium|large|grande|original|master)"
    r"(?:@\dx)?(?=\.[a-z0-9]{2,5}$)",
    re.I,
)
_BLOGGER_SEG_RE = re.compile(r"/(?:s\d{2,5}|w\d{2,5}-h\d{2,5})(?:-[a-z0-9-]+)?/", re.I)
_GOOGLE_SIZE_SUFFIX_RE = re.compile(r"=[swh]\d{1,5}(?:-[a-z0-9-]+)*$", re.I)
_SIZE_QUERY_KEYS = frozenset({
    "w", "h", "width", "height", "resize", "fit", "crop", "quality", "q", "auto", "dpr",
    "fm", "format", "size", "sz", "strip", "ixlib", "s", "v", "ver", "version", "ssl",
})
_CHARSET_RE = re.compile(rb"""<meta[^>]{0,200}?charset=["']?([A-Za-z0-9_\-:.]{1,40})""", re.I)
# `url(...)` trong style nội tuyến; nội dung giới hạn độ dài nên không quay lui xa
_CSS_URL_RE = re.compile(r"""url\(\s*['"]?([^'")\s]{1,2048})['"]?\s*\)""", re.I)
# Thuộc tính data-* dạng srcset (lazysizes data-bgset...)
_DATA_SRCSET_ATTRS = ("data-bgset", "data-srcset", "data-background-set")
# Thẻ đã có cách đọc riêng (hoặc không bao giờ chứa media của bài)
_NO_DATA_SCAN_TAGS = frozenset({"img", "source", "video", "audio", "iframe", "script", "style", "link", "meta", "base", "input"})


class NoPostMediaError(RuntimeError):
    """Không thấy ảnh/video nào trong bài viết.

    `dynamic_ran`: đã mở trang bằng trình duyệt thật (kèm cookie) mà vẫn không thấy —
    bên gọi không cần dò thêm bằng trình duyệt lần nữa.
    """

    def __init__(self, message: str, dynamic_ran: bool = False):
        super().__init__(message)
        self.dynamic_ran = dynamic_ran


class LoginRequiredError(RuntimeError):
    """Trang chỉ cho xem bài viết khi đăng nhập.

    Bên gọi KHÔNG được thay bằng ảnh khác trên trang (og:image, ảnh giữ chỗ): đó không
    phải media của bài viết.
    """


# X (giao diện web mới, 10/2026) trả bài có media nhạy cảm / giới hạn độ tuổi cho khách
# dưới dạng "BlurredMediaTombstone": trang chỉ có MỘT ảnh làm mờ dùng chung cho mọi
# bài như vậy. Trước đây ảnh đó được báo là "1 ảnh trong bài viết" — ba bài của ba tài
# khoản khác nhau cùng ra đúng một ảnh mờ.
_X_BLURRED_MARKER = "BlurredMediaTombstone"
X_SENSITIVE_LOGIN_MESSAGE = (
    "Bài đăng X (Twitter) này có nội dung nhạy cảm / giới hạn độ tuổi nên X chỉ hiện bản "
    "làm mờ — tải ảnh/video gốc yêu cầu đăng nhập. Hãy đăng nhập X trên trình duyệt "
    "(app tự dùng trình duyệt đang đăng nhập) hoặc dán cookie X vào Cookie Manager (🍪). "
    "Đã đăng nhập mà vẫn gặp: bật \"Hiển thị nội dung có thể nhạy cảm\" trong Cài đặt X."
)


def login_only_notice(html_text: str, page_url: str) -> Optional[str]:
    """Thông báo nếu trang chỉ cho khách xem bản thay thế (ảnh mờ) của bài viết, hoặc None."""
    host = (urllib.parse.urlsplit(page_url).hostname or "").lower()
    is_x = any(host == d or host.endswith("." + d) for d in ("x.com", "twitter.com"))
    if is_x and _X_BLURRED_MARKER in html_text:
        return X_SENSITIVE_LOGIN_MESSAGE
    return None


def describe_media(images: List[MediaImage], dynamic_used: bool = False, truncated: bool = False) -> str:
    n_video = sum(1 for i in images if i.type == "video")
    n_image = len(images) - n_video
    parts = []
    if n_image:
        parts.append(f"{n_image} ảnh")
    if n_video:
        parts.append(f"{n_video} video")
    desc = f"Tìm thấy {' và '.join(parts) or '0 tệp'} trong bài viết"
    if dynamic_used:
        desc += " (đã mở trang bằng trình duyệt để nạp hết media)"
    if truncated:
        desc += f" — chỉ lấy {MAX_ITEMS} tệp đầu tiên"
    return desc + "."


# ─────────────────────────────────────────────────────────────────────────────
# Kiểu dữ liệu trung gian
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class _Container:
    """Một vùng của trang: bài viết, khung chính, hay "khung viền" (menu, bình luận...)."""
    cid: int
    # 'article' | 'main' | 'body' (nội dung) — 'chrome' (thẻ <aside>/<nav>/<footer>)
    # — 'hint' (class/id kiểu "sidebar", "comments") — 'header'
    level: str
    parent: Optional[int]
    tag: str
    text: int = 0         # độ dài chữ, cộng dồn cả vùng con (tính lúc chốt)


@dataclass
class _Candidate:
    kind: str                       # 'image' | 'video'
    url: str
    thumb: Optional[str] = None
    via: Optional[str] = None       # 'ytdlp' khi không tải thẳng được bằng curl
    width: int = 0                  # kích thước của CHÍNH url (nếu biết)
    height: int = 0
    attr_w: int = 0                 # kích thước khai báo / hiển thị của thẻ
    attr_h: int = 0
    natural_w: int = 0
    natural_h: int = 0
    path: Tuple[int, ...] = ()
    order: int = 0
    source: str = ""                # img | link | full | video | iframe | meta | jsonld | bg | data | network
    title: Optional[str] = None
    duration: Optional[str] = None
    decor: bool = False             # class của thẻ cho biết là emoji / avatar / icon


@dataclass
class PageScan:
    """Kết quả đọc MỘT trang (HTML tĩnh hoặc DOM đã dựng)."""
    final_url: str
    title: Optional[str] = None
    site_name: Optional[str] = None
    description: Optional[str] = None
    containers: Optional[List[_Container]] = None
    candidates: Optional[List[_Candidate]] = None
    meta_candidates: Optional[List[_Candidate]] = None
    has_password: bool = False
    text_len: int = 0
    script_count: int = 0
    player_hint: bool = False       # có <video> không nguồn / iframe player lạ
    lazy_unresolved: int = 0        # ảnh chỉ có placeholder, không đọc ra link thật
    status: int = 200
    direct_kind: Optional[str] = None   # liên kết là thẳng một tệp ảnh/video
    # Vị trí (đường vùng) của ảnh chỉ có placeholder và của player JS chưa có nguồn.
    # select_post_media() đếm những cái nằm TRONG bài viết: chỉ chúng mới đáng để mở
    # trình duyệt (10-25 giây) — ảnh lazy ở sidebar hay bản đồ Google Maps thì không.
    lazy_paths: Optional[List[Tuple[int, ...]]] = None
    player_paths: Optional[List[Tuple[int, ...]]] = None
    lazy_in_post: int = 0
    players_in_post: int = 0


# ─────────────────────────────────────────────────────────────────────────────
# Tiện ích URL
# ─────────────────────────────────────────────────────────────────────────────

def _path_ext(url: str) -> str:
    try:
        path = urllib.parse.urlsplit(url).path
    except ValueError:
        return ""
    name = path.rsplit("/", 1)[-1]
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[-1].lower()


def media_kind_of_url(url: str) -> Optional[str]:
    """'image' | 'video' | 'stream' | 'segment' theo đuôi tệp trong PATH (None = không rõ)."""
    ext = _path_ext(url)
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in STREAM_EXTS:
        return "stream"
    if ext in SEGMENT_EXTS:
        return "segment"
    return None


def _clean_url(raw: Optional[str], base: str) -> Optional[str]:
    """URL tuyệt đối http(s) hoặc None (data:, blob:, javascript:, quá dài...)."""
    if not raw or not isinstance(raw, str):
        return None
    value = raw.strip()
    if not value or len(value) > MAX_URL_LEN:
        return None
    low = value[:16].lower()
    if low.startswith(("data:", "blob:", "javascript:", "about:", "mailto:", "tel:", "#")):
        return None
    try:
        absolute = urllib.parse.urljoin(base, value)
        parts = urllib.parse.urlsplit(absolute)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    # Bỏ phần #fragment: cùng một tệp, khác fragment là trùng
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def _is_placeholder(raw: Optional[str]) -> bool:
    if not raw:
        return True
    value = raw.strip().lower()
    if value.startswith("data:") or value.startswith("blob:"):
        return True
    return any(hint in value for hint in _PLACEHOLDER_HINTS)


def _to_int(value: Any) -> int:
    try:
        text = str(value).strip().lower().rstrip("px")
        return max(0, int(float(text))) if text else 0
    except (TypeError, ValueError):
        return 0


def parse_srcset(value: Optional[str]) -> List[Tuple[str, float, float]]:
    """Đọc `srcset` → [(url, độ rộng 'w', mật độ 'x')], theo ngữ pháp của HTML.

    URL kết thúc ở khoảng trắng (URL Cloudinary có dấu phẩy bên trong vẫn đúng),
    mô tả kéo tới dấu phẩy kế tiếp nằm ngoài ngoặc.
    """
    out: List[Tuple[str, float, float]] = []
    if not value or not isinstance(value, str):
        return out
    s = value.strip()
    n = len(s)
    i = 0
    while i < n and len(out) < 64:
        while i < n and (s[i].isspace() or s[i] == ","):
            i += 1
        if i >= n:
            break
        j = i
        while j < n and not s[j].isspace():
            j += 1
        url = s[i:j]
        i = j
        desc = ""
        if url.endswith(","):
            url = url.rstrip(",")
        else:
            k = i
            depth = 0
            while k < n:
                c = s[k]
                if c == "(":
                    depth += 1
                elif c == ")" and depth:
                    depth -= 1
                elif c == "," and depth == 0:
                    break
                k += 1
            desc = s[i:k].strip()
            i = k + 1
        w = x = 0.0
        for d in desc.split():
            d = d.lower()
            try:
                if d.endswith("w"):
                    w = float(d[:-1])
                elif d.endswith("x"):
                    x = float(d[:-1])
            except ValueError:
                pass
        if url:
            out.append((url, w, x))
    return out


def _largest(entries: List[Tuple[str, float, float]]) -> Optional[Tuple[str, float, float]]:
    if not entries:
        return None
    return max(entries, key=lambda e: (e[1], e[2]))


def _smallest_preview(entries: List[Tuple[str, float, float]]) -> Optional[str]:
    """Bản nhỏ nhất vẫn đủ nét cho ô xem trước (>= 240px nếu có)."""
    sized = [e for e in entries if e[1] > 0]
    if sized:
        good = [e for e in sized if e[1] >= 240]
        return min(good or sized, key=lambda e: e[1])[0]
    return entries[0][0] if entries else None


def _wp_size(url: str) -> Tuple[int, int]:
    m = _WP_SIZE_RE.search(urllib.parse.urlsplit(url).path)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def variant_key(url: str) -> str:
    """Khoá gộp các bản thu nhỏ / đổi cỡ của CÙNG một ảnh về một mục."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    path = parts.path
    path = _WP_SIZE_RE.sub("", path)
    path = _WP_SCALED_RE.sub("", path)
    path = _SHOPIFY_SIZE_RE.sub("", path)
    path = _BLOGGER_SEG_RE.sub("/", path)
    path = _GOOGLE_SIZE_SUFFIX_RE.sub("", path)
    query = ""
    if parts.query:
        kept = [
            (k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
            if k.lower() not in _SIZE_QUERY_KEYS
        ]
        query = urllib.parse.urlencode(sorted(kept))
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return f"{host}{path.lower()}?{query}"


def _is_original_size(url: str) -> bool:
    path = urllib.parse.urlsplit(url).path
    return not (_WP_SIZE_RE.search(path) or _SHOPIFY_SIZE_RE.search(path) or _BLOGGER_SEG_RE.search(path))


def embed_info(url: str) -> Optional[Tuple[str, Optional[str]]]:
    """Iframe nhúng video của nền tảng quen thuộc → (URL trang video cho yt-dlp, ảnh bìa)."""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    path = parts.path

    def host_is(*domains: str) -> bool:
        return any(host == d or host.endswith("." + d) for d in domains)

    if host_is("youtube.com", "youtube-nocookie.com"):
        m = re.match(r"^/(?:embed|shorts|v)/([A-Za-z0-9_-]{11})", path)
        if m:
            vid = m.group(1)
            return f"https://www.youtube.com/watch?v={vid}", f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
        return None
    if host_is("youtu.be"):
        m = re.match(r"^/([A-Za-z0-9_-]{11})", path)
        if m:
            vid = m.group(1)
            return f"https://www.youtube.com/watch?v={vid}", f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
        return None
    if host_is("player.vimeo.com"):
        m = re.match(r"^/video/(\d+)", path)
        return (f"https://vimeo.com/{m.group(1)}", None) if m else None
    if host_is("dailymotion.com", "geo.dailymotion.com"):
        m = re.match(r"^/(?:embed/)?video/([A-Za-z0-9]+)", path)
        if not m:
            q = dict(urllib.parse.parse_qsl(parts.query))
            vid = q.get("video")
            return (f"https://www.dailymotion.com/video/{vid}", None) if vid else None
        return f"https://www.dailymotion.com/video/{m.group(1)}", None
    if host_is("facebook.com") and path.startswith("/plugins/video.php"):
        href = dict(urllib.parse.parse_qsl(parts.query)).get("href")
        return (href, None) if href and href.startswith(("http://", "https://")) else None
    if host_is("tiktok.com") and "/embed" in path:
        m = re.search(r"/embed(?:/v2)?/(\d{6,})", path)
        return (f"https://www.tiktok.com/embed/v2/{m.group(1)}", None) if m else None
    if host_is("instagram.com"):
        m = re.match(r"^/(p|reel|tv)/([A-Za-z0-9_-]+)", path)
        return (f"https://www.instagram.com/{m.group(1)}/{m.group(2)}/", None) if m else None
    if host_is("streamable.com"):
        m = re.match(r"^/(?:e|o|s)/([A-Za-z0-9]+)", path)
        return (f"https://streamable.com/{m.group(1)}", None) if m else None
    if host_is("player.twitch.tv", "clips.twitch.tv", "rumble.com", "player.bilibili.com",
               "w.soundcloud.com", "odysee.com", "ok.ru", "vk.com", "vkvideo.ru", "loom.com",
               "wistia.com", "wistia.net", "fast.wistia.net", "jwplayer.com", "brightcove.net",
               "kaltura.com", "video.vnexpress.net", "tv.zing.vn"):
        return url, None
    return None


def _decor_or_junk(url: str) -> Tuple[bool, bool]:
    """(luôn là rác, chỉ là ảnh trang trí theo tên — logo/icon/avatar)."""
    low = url.lower()
    if any(part in low for part in _JUNK_URL_PARTS):
        return True, False
    ext = _path_ext(url)
    if ext == "ico":
        return True, False
    path = urllib.parse.urlsplit(url).path
    return False, bool(_DECOR_NAME_RE.search(path))


# ─────────────────────────────────────────────────────────────────────────────
# Ứng viên dùng chung cho HTML tĩnh và DOM đã dựng
# ─────────────────────────────────────────────────────────────────────────────

def image_candidate(
    attrs: Dict[str, str],
    base: str,
    *,
    current_src: Optional[str] = None,
    picture_srcsets: Optional[List[str]] = None,
    link_href: Optional[str] = None,
) -> Tuple[Optional[_Candidate], bool]:
    """Ứng viên ảnh từ thuộc tính của <img>. Trả về (ứng viên, chỉ có placeholder)."""
    src_raw = current_src or attrs.get("src") or ""
    src = None if _is_placeholder(src_raw) else _clean_url(src_raw, base)

    srcset: List[Tuple[str, float, float]] = []
    for key in _SRCSET_ATTRS:
        entries = [
            (u, w, x) for (u, w, x) in parse_srcset(attrs.get(key))
            if not _is_placeholder(u) and _clean_url(u, base)
        ]
        if entries:
            srcset = [(_clean_url(u, base) or u, w, x) for (u, w, x) in entries]
            break
    for raw_set in picture_srcsets or []:
        srcset.extend(
            (_clean_url(u, base) or u, w, x) for (u, w, x) in parse_srcset(raw_set)
            if not _is_placeholder(u) and _clean_url(u, base)
        )

    lazy = next(
        (u for u in (_clean_url(attrs.get(k), base) for k in _LAZY_ATTRS
                     if attrs.get(k) and not _is_placeholder(attrs.get(k))) if u),
        None,
    )
    full = next(
        (u for u in (_clean_url(attrs.get(k), base) for k in _FULL_ATTRS if attrs.get(k)) if u),
        None,
    )
    link = _clean_url(link_href, base) if link_href else None
    if link and media_kind_of_url(link) != "image":
        link = None

    best_set = _largest(srcset)
    width = 0
    if link:
        best, source = link, "link"
    elif full:
        best, source = full, "full"
    elif best_set and (best_set[1] > 0 or best_set[2] > 0):
        best, source, width = best_set[0], "img", int(best_set[1])
    else:
        # Không có mô tả kích thước (<picture><source srcset="anh.jpg"> cạnh
        # <img src="anh-300x225.jpg">): ưu tiên bản không mang hậu tố thu nhỏ.
        options = [u for u in [lazy, src] + [e[0] for e in srcset] if u]
        best = next((u for u in options if _is_original_size(u)), options[0] if options else None)
        source = "img"
    if not best:
        return None, bool(src_raw)

    thumb = src or _smallest_preview(srcset) or lazy or best
    if not width and best == src and not srcset:
        width = _to_int(attrs.get("width"))
    wp_w, wp_h = _wp_size(best)
    class_words = set()
    for token in (attrs.get("class") or "").lower().split()[:20]:
        class_words.add(token)
        class_words.update(w for w in token.replace("_", "-").split("-") if w)
    cand = _Candidate(
        kind="image",
        url=best,
        thumb=thumb,
        width=width or wp_w,
        height=wp_h,
        attr_w=_to_int(attrs.get("width")),
        attr_h=_to_int(attrs.get("height")),
        source=source,
        decor=bool(class_words & _DECOR_CLASS_WORDS),
    )
    return cand, False


def attribute_images(attrs: Dict[str, str], base: str) -> List[str]:
    """Ảnh mà một thẻ BẤT KỲ trỏ tới qua thuộc tính data-* hoặc style nội tuyến.

    Khung so sánh trước/sau, slider, ảnh nền lazy-load (`data-bg`, `data-bgset`,
    `data-component-back`...) giữ ảnh trong thuộc tính của thẻ <div>/<a> — JS mới vẽ
    ra <img>. Chỉ nhận giá trị trông như URL tệp ẢNH (theo đuôi tệp).
    """
    out: List[str] = []
    for key, value in attrs.items():
        if not value or len(value) > MAX_URL_LEN:
            continue
        if key in _DATA_SRCSET_ATTRS:
            best = _largest([(u, w, x) for (u, w, x) in parse_srcset(value) if not _is_placeholder(u)])
            url = _clean_url(best[0], base) if best else None
            if url and url not in out:
                out.append(url)
            continue
        if key == "style":
            if "url(" not in value:
                continue
            raws = _CSS_URL_RE.findall(value)[:5]
        elif key.startswith("data-"):
            text = value.strip()
            if any(ch.isspace() for ch in text) or not (text.startswith(("http://", "https://", "//", "/")) or "." in text):
                continue
            raws = [text]
        else:
            continue
        for raw in raws:
            if _is_placeholder(raw):
                continue
            url = _clean_url(raw, base)
            if url and media_kind_of_url(url) == "image" and url not in out:
                out.append(url)
    return out


def _video_rank(url: str) -> int:
    kind = media_kind_of_url(url)
    ext = _path_ext(url)
    if ext == "mp4":
        return 0
    if kind == "video":
        return 1
    if ext == "m3u8":
        return 2
    if ext == "mpd":
        return 3
    return 4


def video_candidate(
    src: Optional[str],
    sources: List[Tuple[str, str]],
    poster: Optional[str],
    base: str,
) -> Optional[_Candidate]:
    """Một <video> (các <source> là bản thay thế của CÙNG một video) → một ứng viên."""
    urls: List[str] = []
    for raw in [src] + [s for s, _ in sources]:
        u = _clean_url(raw, base)
        if u and u not in urls:
            urls.append(u)
    if not urls:
        return None
    best = min(urls, key=_video_rank)
    thumb = _clean_url(poster, base) if poster else None
    if thumb and media_kind_of_url(thumb) in ("video", "stream", "segment"):
        thumb = None
    kind = media_kind_of_url(best)
    if kind == "segment":
        return None
    return _Candidate(
        kind="video",
        url=best,
        thumb=thumb,
        via="ytdlp" if kind == "stream" or kind is None else None,
        source="video",
    )


# Iframe nhúng KHÔNG phải trình phát video: bản đồ, biểu mẫu, bình luận, nút like...
_NON_PLAYER_IFRAME_PARTS = (
    "google.com/maps", "maps.google.", "openstreetmap.", "docs.google.", "forms.gle",
    "calendar.google.", "recaptcha", "accounts.google.", "disqus", "facebook.com/plugins/page",
    "facebook.com/plugins/like", "facebook.com/plugins/comments", "facebook.com/plugins/share",
    "open.spotify.com", "about:blank",
)


def iframe_candidate(src: Optional[str], base: str) -> Tuple[Optional[_Candidate], bool]:
    """(ứng viên video nhúng, là iframe lạ có thể chứa player)."""
    url = _clean_url(src, base)
    if not url:
        return None, False
    info = embed_info(url)
    if info:
        page_url, thumb = info
        return _Candidate(kind="video", url=page_url, thumb=thumb, via="ytdlp", source="iframe"), False
    junk, _ = _decor_or_junk(url)
    low = url.lower()
    return None, not junk and not any(part in low for part in _NON_PLAYER_IFRAME_PARTS)


# ─────────────────────────────────────────────────────────────────────────────
# Phân loại vùng trang
# ─────────────────────────────────────────────────────────────────────────────

def _class_words(attrs: Dict[str, str]) -> Tuple[List[str], List[str]]:
    """(token class/id đã chuẩn hoá, các từ con tách theo - và _)."""
    raw = f"{attrs.get('class') or ''} {attrs.get('id') or ''}".lower()
    tokens = [t.replace("_", "-") for t in raw.split() if t][:40]
    words: List[str] = []
    for t in tokens:
        words.extend(w for w in t.split("-") if w)
    return tokens, words


def container_level(tag: str, attrs: Dict[str, str]) -> Optional[str]:
    role = (attrs.get("role") or "").lower()
    itemprop = (attrs.get("itemprop") or "").lower()
    itemtype = (attrs.get("itemtype") or "").lower()
    if (
        tag == "article"
        or role == "article"
        or itemprop in ("blogpost", "articlebody")
        or (itemtype and any(t in itemtype for t in _ARTICLE_ITEMTYPES))
    ):
        return "article"
    if tag in ("aside", "nav", "footer") or role in ("navigation", "complementary", "contentinfo"):
        return "chrome"
    tokens, words = _class_words(attrs)
    if tag not in ("body", "html", "main") and (
        any(w in _CHROME_WORDS for w in words) or any(t in _CHROME_WORDS for t in tokens)
    ):
        # Chỉ là GỢI Ý: khung bố cục kiểu "content-sidebar-wrap" bọc cả bài viết.
        return "hint"
    if tag == "main" or role == "main":
        return "main"
    if any(t in _MAIN_TOKENS for t in tokens):
        return "body"
    if tag == "header" or role == "banner":
        return "header"
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Trình đọc HTML tĩnh (tuyến tính)
# ─────────────────────────────────────────────────────────────────────────────

class _MediaHTMLParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self._base_set = False
        self.stack: List[Tuple[str, Optional[int]]] = []
        self.open: List[int] = []
        self.containers: List[_Container] = []
        self._own_text: List[int] = []
        self.cands: List[_Candidate] = []
        self.meta_cands: List[_Candidate] = []
        self.meta: Dict[str, str] = {}
        self.title_parts: List[str] = []
        self._in_title = False
        self._cdata: Optional[str] = None
        self._ld_parts: Optional[List[str]] = None
        self._ld_total = 0
        self.ld_blocks: List[str] = []
        self._link: Optional[Dict[str, Any]] = None
        self._picture: Optional[Dict[str, Any]] = None
        self._video: Optional[Dict[str, Any]] = None
        self.has_password = False
        self.text_len = 0
        self.script_count = 0
        self.player_hint = False
        self.lazy_unresolved = 0
        self.lazy_paths: List[Tuple[int, ...]] = []
        self.player_paths: List[Tuple[int, ...]] = []
        self._order = 0

    # ── tiện ích ──────────────────────────────────────────────────────────
    def _add(self, cand: Optional[_Candidate], meta: bool = False) -> None:
        if cand is None:
            return
        target = self.meta_cands if meta else self.cands
        if len(target) >= MAX_RAW_CANDIDATES:
            return
        cand.order = self._order
        self._order += 1
        if not meta:
            cand.path = tuple(self.open)
        target.append(cand)

    @staticmethod
    def _attrs(attrs: List[Tuple[str, Optional[str]]]) -> Dict[str, str]:
        out: Dict[str, str] = {}
        for key, value in attrs:
            if key not in out:
                out[key] = value if value is not None else ""
        return out

    # ── sự kiện của HTMLParser ────────────────────────────────────────────
    def handle_starttag(self, tag: str, attrs_list: List[Tuple[str, Optional[str]]]) -> None:
        attrs = self._attrs(attrs_list)
        if tag in ("script", "style"):
            self._cdata = tag
            if tag == "script":
                self.script_count += 1
                if "ld+json" in (attrs.get("type") or "").lower() and self._ld_total < MAX_JSONLD_BYTES:
                    self._ld_parts = []
        if tag in _VOID_TAGS:
            self._void(tag, attrs)
            return

        cid: Optional[int] = None
        if len(self.stack) < MAX_STACK:
            level = container_level(tag, attrs) if len(self.open) < MAX_OPEN_CONTAINERS else None
            if level:
                cid = len(self.containers)
                parent = self.open[-1] if self.open else None
                self.containers.append(_Container(cid, level, parent, tag))
                self._own_text.append(0)
                self.open.append(cid)
            self.stack.append((tag, cid))
        depth = len(self.stack)

        if tag not in _NO_DATA_SCAN_TAGS and len(attrs) > 0:
            for url in attribute_images(attrs, self.base_url):
                self._add(_Candidate(kind="image", url=url, thumb=None, source="data"))

        if tag == "a":
            href = _clean_url(attrs.get("href"), self.base_url)
            kind = media_kind_of_url(href) if href else None
            if kind in ("image", "video"):
                self._link = {"url": href, "kind": kind, "depth": depth, "used": False}
        elif tag == "picture":
            self._picture = {"sets": [], "depth": depth}
        elif tag in ("video", "audio"):
            self._video = {
                "tag": tag,
                "src": attrs.get("src") or attrs.get("data-src") or "",
                "poster": attrs.get("poster") or attrs.get("data-poster") or "",
                "sources": [],
                "depth": depth,
                "path": tuple(self.open),
            }
        elif tag == "iframe":
            cand, unknown = iframe_candidate(
                attrs.get("src") or attrs.get("data-src") or attrs.get("data-lazy-src"), self.base_url
            )
            self._add(cand)
            if unknown:
                self.player_hint = True
                if len(self.player_paths) < 200:
                    self.player_paths.append(tuple(self.open))
        elif tag == "title":
            self._in_title = True

    def handle_startendtag(self, tag: str, attrs_list: List[Tuple[str, Optional[str]]]) -> None:
        self.handle_starttag(tag, attrs_list)
        if tag not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            if tag == "script" and self._ld_parts is not None:
                block = "".join(self._ld_parts)
                self._ld_parts = None
                if block.strip():
                    self.ld_blocks.append(block)
            self._cdata = None
        if tag == "title":
            self._in_title = False
        if tag in _VOID_TAGS:
            return
        # Thẻ đóng lạc (HTML lỗi) không được đóng nhầm cả trang: chỉ tìm trong vài
        # chục thẻ gần nhất.
        stop = max(-1, len(self.stack) - 64)
        for i in range(len(self.stack) - 1, stop, -1):
            if self.stack[i][0] == tag:
                while len(self.stack) > i:
                    depth = len(self.stack)
                    closed_tag, cid = self.stack.pop()
                    self._close(closed_tag, cid, depth)
                return

    def handle_data(self, data: str) -> None:
        if self._cdata == "script":
            if self._ld_parts is not None and self._ld_total < MAX_JSONLD_BYTES:
                self._ld_parts.append(data)
                self._ld_total += len(data)
            return
        if self._cdata == "style":
            return
        if self._in_title and len(self.title_parts) < 50:
            self.title_parts.append(data)
        n = len(data.strip())
        if n:
            self.text_len += n
            if self.open:
                self._own_text[self.open[-1]] += n

    # ── xử lý từng loại thẻ ───────────────────────────────────────────────
    def _close(self, tag: str, cid: Optional[int], depth: int) -> None:
        if cid is not None:
            if self.open and self.open[-1] == cid:
                self.open.pop()
            elif cid in self.open:
                self.open.remove(cid)
        if tag == "a" and self._link and self._link["depth"] == depth:
            link = self._link
            self._link = None
            if not link["used"]:
                # Link "xem ảnh gốc" không bọc <img> (gallery dùng ảnh nền CSS)
                if link["kind"] == "image":
                    self._add(_Candidate(kind="image", url=link["url"], thumb=None, source="link"))
                else:
                    self._add(video_candidate(link["url"], [], link.get("thumb"), self.base_url))
        elif tag == "picture" and self._picture and self._picture["depth"] == depth:
            self._picture = None
        elif tag in ("video", "audio") and self._video and self._video["depth"] == depth:
            video = self._video
            self._video = None
            if video["tag"] == "audio":
                return
            cand = video_candidate(video["src"], video["sources"], video["poster"], self.base_url)
            if cand is None:
                # <video> không có nguồn trong HTML: player JS sẽ gắn luồng (thường là blob:)
                self.player_hint = True
                if len(self.player_paths) < 200:
                    self.player_paths.append(tuple(video["path"]))
                return
            saved_open = self.open
            self.open = list(video["path"])
            self._add(cand)
            self.open = saved_open

    def _void(self, tag: str, attrs: Dict[str, str]) -> None:
        if tag == "img":
            link_href = None
            if self._link and self._link["kind"] == "image":
                link_href = self._link["url"]
            cand, placeholder_only = image_candidate(
                attrs,
                self.base_url,
                picture_srcsets=self._picture["sets"] if self._picture else None,
                link_href=link_href,
            )
            if cand is None:
                if placeholder_only:
                    self.lazy_unresolved += 1
                    if len(self.lazy_paths) < 500:
                        self.lazy_paths.append(tuple(self.open))
                return
            if self._link and self._link["kind"] == "video":
                # <a href="clip.mp4"><img></a>: ảnh chỉ là ảnh bìa của video
                self._link.setdefault("thumb", cand.thumb or cand.url)
                return
            if self._link:
                self._link["used"] = True
            self._add(cand)
        elif tag == "source":
            if self._video is not None:
                self._video["sources"].append((attrs.get("src") or attrs.get("data-src") or "", attrs.get("type") or ""))
            elif self._picture is not None:
                value = attrs.get("srcset") or attrs.get("data-srcset") or ""
                if value:
                    self._picture["sets"].append(value)
        elif tag == "meta":
            self._meta(attrs)
        elif tag == "link":
            rel = (attrs.get("rel") or "").lower()
            if "image_src" in rel:
                self._add(_Candidate(kind="image", url=_clean_url(attrs.get("href"), self.base_url) or "", source="meta"), meta=True)
        elif tag == "base":
            href = attrs.get("href")
            if href and not self._base_set:
                joined = _clean_url(href, self.base_url)
                if joined:
                    self.base_url = joined
                self._base_set = True
        elif tag == "input":
            if (attrs.get("type") or "").lower() == "password":
                self.has_password = True

    def _meta(self, attrs: Dict[str, str]) -> None:
        key = (attrs.get("property") or attrs.get("name") or attrs.get("itemprop") or "").strip().lower()
        content = (attrs.get("content") or "").strip()
        if not key or not content:
            return
        if key in ("og:title", "twitter:title", "og:description", "description", "twitter:description",
                   "og:site_name", "og:type", "og:image:width"):
            self.meta.setdefault(key, content[:1000])
            return
        if key in ("og:image", "og:image:url", "og:image:secure_url", "twitter:image", "twitter:image:src", "image", "thumbnailurl"):
            url = _clean_url(content, self.base_url)
            if url and len(self.meta_cands) < 40:
                self._add(_Candidate(kind="image", url=url, thumb=url, source="meta"), meta=True)
            return
        if key in ("og:video", "og:video:url", "og:video:secure_url", "twitter:player:stream", "contenturl", "embedurl", "twitter:player"):
            url = _clean_url(content, self.base_url)
            if not url:
                return
            info = embed_info(url)
            if info:
                self._add(_Candidate(kind="video", url=info[0], thumb=info[1], via="ytdlp", source="meta"), meta=True)
            elif media_kind_of_url(url) in ("video", "stream"):
                self._add(video_candidate(url, [], None, self.base_url), meta=True)

    # ── kết thúc ──────────────────────────────────────────────────────────
    def finish(self) -> None:
        # Cộng dồn chữ của vùng con lên vùng cha: id cha luôn nhỏ hơn id con.
        for c in self.containers:
            c.text = self._own_text[c.cid]
        for c in reversed(self.containers):
            if c.parent is not None:
                self.containers[c.parent].text += c.text
        for block in self.ld_blocks[:30]:
            try:
                data = json.loads(block)
            except (ValueError, RecursionError):
                continue
            _walk_ld(data, self.base_url, self.meta_cands, [4000])


def _walk_ld(node: Any, base: str, out: List[_Candidate], budget: List[int], depth: int = 0) -> None:
    """Lấy ảnh / video của bài viết từ JSON-LD (schema.org)."""
    if depth > 12 or budget[0] <= 0 or len(out) >= 60:
        return
    budget[0] -= 1
    if isinstance(node, list):
        for item in node[:200]:
            _walk_ld(item, base, out, budget, depth + 1)
        return
    if not isinstance(node, dict):
        return
    raw_types = node.get("@type")
    types = {str(t).lower() for t in (raw_types if isinstance(raw_types, list) else [raw_types]) if t}

    def add_image(value: Any) -> None:
        if isinstance(value, str):
            url = _clean_url(value, base)
            if url:
                out.append(_Candidate(kind="image", url=url, thumb=url, source="jsonld", order=len(out)))
        elif isinstance(value, list):
            for v in value[:30]:
                add_image(v)
        elif isinstance(value, dict):
            add_image(value.get("contentUrl") or value.get("url"))

    if "videoobject" in types:
        thumb = node.get("thumbnailUrl")
        if isinstance(thumb, list):
            thumb = thumb[0] if thumb else None
        thumb_url = _clean_url(thumb, base) if isinstance(thumb, str) else None
        for key in ("contentUrl", "embedUrl"):
            url = _clean_url(node.get(key), base) if isinstance(node.get(key), str) else None
            if not url:
                continue
            info = embed_info(url)
            if info:
                out.append(_Candidate(kind="video", url=info[0], thumb=thumb_url or info[1], via="ytdlp", source="jsonld", order=len(out)))
                break
            if media_kind_of_url(url) in ("video", "stream"):
                cand = video_candidate(url, [], thumb_url, base)
                if cand:
                    cand.source = "jsonld"
                    cand.order = len(out)
                    out.append(cand)
                break
        return
    if "imageobject" in types:
        add_image(node.get("contentUrl") or node.get("url"))
        return
    if types & _LD_POST_TYPES:
        add_image(node.get("image"))
        if node.get("video") is not None:
            _walk_ld(node.get("video"), base, out, budget, depth + 1)
    for key, value in node.items():
        if key in ("image", "video", "logo", "author", "publisher", "creator", "brand"):
            continue
        if isinstance(value, (dict, list)):
            _walk_ld(value, base, out, budget, depth + 1)


def scan_html(html_text: str, page_url: str) -> PageScan:
    """Đọc HTML tĩnh → PageScan. Thời gian tuyến tính theo độ dài trang."""
    parser = _MediaHTMLParser(page_url)
    try:
        parser.feed(html_text)
        parser.close()
    except Exception:
        # html.parser gần như không ném lỗi; nếu có thì dùng phần đã đọc được
        pass
    parser.finish()
    meta = parser.meta
    title = meta.get("og:title") or meta.get("twitter:title")
    if not title:
        title = " ".join("".join(parser.title_parts).split())[:300] or None
    return PageScan(
        final_url=page_url,
        title=title,
        site_name=meta.get("og:site_name"),
        description=meta.get("og:description") or meta.get("description") or meta.get("twitter:description"),
        containers=parser.containers,
        candidates=parser.cands,
        meta_candidates=parser.meta_cands,
        has_password=parser.has_password,
        text_len=parser.text_len,
        script_count=parser.script_count,
        player_hint=parser.player_hint,
        lazy_unresolved=parser.lazy_unresolved,
        lazy_paths=parser.lazy_paths,
        player_paths=parser.player_paths,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Chọn media của BÀI VIẾT
# ─────────────────────────────────────────────────────────────────────────────

def _is_chrome_path(
    path: Tuple[int, ...],
    by_id: Dict[int, _Container],
    neutral: frozenset = frozenset(),
) -> bool:
    """Ứng viên nằm trong vùng viền (menu, bình luận, bài liên quan...)?

    - <aside>/<nav>/<footer>: luôn là viền.
    - class/id gợi ý ("sidebar", "comments", "related"...): chỉ quyết định khi là dấu
      hiệu TRONG CÙNG — <div class="content-sidebar-wrap"><article> vẫn là bài viết,
      còn <article><section class="related-posts"> thì không.
    - <header> chỉ là viền khi nằm NGOÀI bài viết: header của chính bài thường chứa
      ảnh đại diện (featured image).
    """
    inside_post = False
    innermost_hint = False
    for cid in path:
        c = by_id.get(cid)
        if c is None or cid in neutral:
            continue
        if c.level == "chrome":
            return True
        if c.level == "header" and not inside_post:
            return True
        if c.level in ("article", "main", "body"):
            inside_post = True
            innermost_hint = False
        elif c.level == "hint":
            innermost_hint = True
    return innermost_hint


def _ancestors(c: _Container, by_id: Dict[int, _Container]) -> Tuple[int, ...]:
    chain: List[int] = []
    seen = set()
    p = c.parent
    while p is not None and p not in seen and len(chain) < MAX_OPEN_CONTAINERS + 1:
        seen.add(p)
        chain.append(p)
        parent = by_id.get(p)
        p = parent.parent if parent else None
    return tuple(reversed(chain))


# Một tệp media trong vùng "nặng ký" bằng chừng này ký tự chữ: bài ảnh thường rất ít
# chữ, nếu chỉ so độ dài chữ thì vài bài liên quan có đoạn trích dài sẽ thắng bài chính.
_MEDIA_WEIGHT = 400


def wrapper_hints(containers: List[_Container], page_text: int) -> frozenset:
    """Khung mang class gợi ý "viền" nhưng chứa phần lớn chữ của trang là khung BỌC trang.

    `<div id="wrapper" class="has-sidebar">` bọc cả bài viết: nếu coi là viền thì
    trang không có thẻ article/main mất sạch media. Sidebar thật chỉ chứa ít chữ.
    """
    total = max(page_text, max((c.text for c in containers), default=0))
    if total <= 0:
        return frozenset()
    return frozenset(c.cid for c in containers if c.level == "hint" and c.text >= 0.5 * total)


def choose_scope(
    containers: List[_Container],
    media_counts: Optional[Dict[int, int]] = None,
    neutral: frozenset = frozenset(),
) -> Tuple[Optional[List[_Container]], str]:
    """Vùng chứa bài viết: (các vùng, 'article'|'main'|'body'|'feed'|'page').

    'feed' (thread diễn đàn, trang danh sách): mọi bài <article> ngoài cùng đều là
    phạm vi; None ('page') = cả trang trừ vùng viền.
    """
    by_id = {c.cid: c for c in containers}
    counts = media_counts or {}

    def weight(c: _Container) -> int:
        return c.text + _MEDIA_WEIGHT * counts.get(c.cid, 0)

    usable = [c for c in containers if not _is_chrome_path(_ancestors(c, by_id) + (c.cid,), by_id, neutral)]
    articles = [c for c in usable if c.level == "article"]
    if articles:
        outer = [
            c for c in articles
            if not any(by_id[a].level == "article" for a in _ancestors(c, by_id) if a in by_id)
        ]
        total = sum(weight(c) for c in outer)
        best = max(outer, key=lambda c: (weight(c), -c.cid))
        # Trang nhiều bài (thread diễn đàn, trang chủ, newsfeed): không bài nào trội
        # hẳn → lấy media của MỌI bài, nhưng không lấy thứ nằm ngoài các bài (khung
        # phủ, banner, video hướng dẫn của giao diện...).
        if len(outer) >= 3 and total and weight(best) < 0.5 * total:
            return outer, "feed"
        return [best], "article"
    for level in ("main", "body"):
        group = [c for c in usable if c.level == level]
        if group:
            return [max(group, key=lambda c: (weight(c), -c.cid))], level
    return None, "page"


def _junk(c: _Candidate, in_post: bool) -> bool:
    always, decor = _decor_or_junk(c.url)
    if always:
        return True
    if c.kind != "image":
        return False
    if c.decor:
        return True
    if _path_ext(c.url) == "svg" and not (in_post and max(c.attr_w, c.natural_w) >= 300):
        return True
    # Pixel đo lường / icon khai báo kích thước rất nhỏ
    if c.attr_w and c.attr_h and c.attr_w <= 2 and c.attr_h <= 2:
        return True
    if c.attr_w and c.attr_h and c.attr_w < 48 and c.attr_h < 48 and max(c.natural_w, c.width) < 200:
        return True
    if c.natural_w and c.natural_h and c.natural_w < 64 and c.natural_h < 64:
        return True
    if decor and (not in_post or max(c.attr_w, c.natural_w) < 120):
        return True
    return False


def _rank(c: _Candidate) -> Tuple[int, int, int, int]:
    """Bản tốt nhất trong các bản của cùng một ảnh: link ảnh gốc > cỡ gốc > lớn hơn."""
    explicit_full = 1 if c.source in ("link", "full") else 0
    return (explicit_full, 1 if _is_original_size(c.url) else 0, max(c.width, c.natural_w), -c.order)


def dedupe(cands: List[_Candidate]) -> List[_Candidate]:
    """Gộp các bản của cùng một ảnh/video; giữ vị trí xuất hiện ĐẦU TIÊN, lấy bản tốt nhất."""
    groups: Dict[str, List[_Candidate]] = {}
    order: List[str] = []
    for c in cands:
        key = ("v:" + c.url) if c.kind == "video" else ("i:" + variant_key(c.url))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(c)
    out: List[_Candidate] = []
    for key in order:
        members = groups[key]
        first = members[0]
        best = max(members, key=_rank) if len(members) > 1 else first
        if best is not first:
            merged = _Candidate(**{**best.__dict__})
            merged.order = first.order
            merged.path = first.path
            # Xem trước bằng bản NHỎ nhất có sẵn (thường là ảnh đang hiển thị)
            merged.thumb = first.thumb or best.thumb
            best = merged
        if best.thumb is None and best.kind == "image":
            small = min(members, key=lambda m: (max(m.width, m.natural_w) or 10**9))
            best.thumb = small.thumb or small.url
        out.append(best)
    return out


def select_post_media(scan: PageScan) -> Tuple[List[_Candidate], str]:
    """Ứng viên thuộc BÀI VIẾT, đã gộp trùng, theo thứ tự xuất hiện → (danh sách, phạm vi)."""
    containers = scan.containers or []
    cands = scan.candidates or []
    by_id = {c.cid: c for c in containers}
    media_counts: Dict[int, int] = {}
    for c in cands:
        for cid in c.path:
            media_counts[cid] = media_counts.get(cid, 0) + 1
    neutral = wrapper_hints(containers, scan.text_len)
    scope, scope_kind = choose_scope(containers, media_counts, neutral)
    scope_ids = {c.cid for c in scope} if scope is not None else None

    def is_chrome(path: Tuple[int, ...]) -> bool:
        return _is_chrome_path(path, by_id, neutral)

    def path_in_scope(path: Tuple[int, ...]) -> bool:
        if scope_ids is not None and not scope_ids.intersection(path):
            return False
        return not is_chrome(path)

    def in_scope(c: _Candidate) -> bool:
        return path_in_scope(c.path)

    scan.lazy_in_post = sum(1 for p in (scan.lazy_paths or []) if path_in_scope(p))
    scan.players_in_post = sum(1 for p in (scan.player_paths or []) if path_in_scope(p))

    picked = [c for c in cands if in_scope(c) and not _junk(c, True)]
    if picked and scope is not None:
        # Ảnh đại diện (featured image) hay nằm NGOÀI thẻ <article> (khung hero phía
        # trên). Nhận ra nó vì og:image của trang trỏ đúng ảnh đó và ảnh có hiển thị
        # trên trang (không phải thẻ chia sẻ được dựng riêng cho mạng xã hội).
        og_keys = {variant_key(m.url) for m in (scan.meta_candidates or []) if m.kind == "image" and m.source == "meta"}
        picked_keys = {variant_key(c.url) for c in picked if c.kind == "image"}
        heroes = [
            c for c in cands
            if c.kind == "image" and not in_scope(c) and variant_key(c.url) in og_keys
            and variant_key(c.url) not in picked_keys
            and not is_chrome(c.path) and not _junk(c, True)
        ]
        if heroes:
            picked = heroes[:1] + picked
    if not picked and scope is not None:
        # Ảnh của bài nằm ngoài khung đã chọn (vd. ảnh đại diện đặt trước <article>).
        # KHÔNG lấy tới vùng viền: bài chỉ có chữ không được trả về ảnh sidebar/footer.
        scope_kind = "page"
        picked = [c for c in cands if not is_chrome(c.path) and not _junk(c, False)]
    picked = dedupe(picked)

    # og:image / JSON-LD / og:video: chỉ bù khi trong bài không có loại media đó.
    # Ảnh og:image thường là thẻ chia sẻ đã cắt khung — đã có ảnh thật thì bỏ.
    metas = [m for m in dedupe(scan.meta_candidates or []) if m.url and not _junk(m, True)]
    has_video = any(c.kind == "video" for c in picked)
    has_image = any(c.kind == "image" for c in picked)
    keys = {("v:" + c.url) if c.kind == "video" else ("i:" + variant_key(c.url)) for c in picked}
    extra: List[_Candidate] = []
    for m in metas:
        key = ("v:" + m.url) if m.kind == "video" else ("i:" + variant_key(m.url))
        if key in keys:
            continue
        if m.kind == "video" and not has_video:
            extra.append(m)
            keys.add(key)
        elif (
            m.kind == "image" and not has_image and not has_video
            and not any(e.kind == "image" for e in extra)
            and not _GENERIC_CARD_RE.search(urllib.parse.urlsplit(m.url).path)
        ):
            extra.append(m)
            keys.add(key)
    return extra + picked, scope_kind


# ─────────────────────────────────────────────────────────────────────────────
# Tải trang
# ─────────────────────────────────────────────────────────────────────────────

def load_cookie_jar(path: str) -> Optional[http.cookiejar.CookieJar]:
    """Đọc tệp cookie Netscape → CookieJar.

    Không dùng MozillaCookieJar.load(): cookie phiên (hạn 0 — rất nhiều cookie đăng
    nhập) bị nó coi là đã hết hạn và KHÔNG được gửi đi, nên trang vẫn thấy khách
    chưa đăng nhập.
    """
    jar = http.cookiejar.CookieJar()
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.read(4 * 1024 * 1024).splitlines()
    except OSError:
        return None
    count = 0
    for line in lines:
        if line.startswith("#HttpOnly_"):
            line = line[len("#HttpOnly_"):]
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, flag, path_, secure, expires, name, value = parts[:7]
        domain = domain.strip()
        if not domain or not name:
            continue
        # Tệp cookie dán tay / xuất từ Firefox có thể ghi hạn bằng mili-giây
        exp = expiry_seconds(expires.strip())
        domain_specified = domain.startswith(".") or flag.upper() == "TRUE"
        cookie = http.cookiejar.Cookie(
            version=0, name=name, value=value, port=None, port_specified=False,
            domain=domain if domain.startswith(".") or not domain_specified else "." + domain,
            domain_specified=domain_specified,
            domain_initial_dot=domain.startswith("."),
            path=path_ or "/", path_specified=True,
            secure=secure.upper() == "TRUE",
            expires=exp if exp > 0 else None,
            discard=exp <= 0, comment=None, comment_url=None, rest={},
        )
        jar.set_cookie(cookie)
        count += 1
    return jar if count else None


def _decode_body(body: bytes, content_type: str, encoding: str) -> str:
    enc = (encoding or "").lower()
    if enc in ("gzip", "x-gzip") or body[:2] == b"\x1f\x8b":
        try:
            d = zlib.decompressobj(16 + zlib.MAX_WBITS)
            body = d.decompress(body, MAX_HTML_BYTES)
        except zlib.error:
            pass
    elif enc == "deflate":
        try:
            body = zlib.decompressobj().decompress(body, MAX_HTML_BYTES)
        except zlib.error:
            pass
    charset = None
    m = re.search(r"charset=([A-Za-z0-9_\-:.]+)", content_type or "", re.I)
    if m:
        charset = m.group(1)
    if not charset:
        mm = _CHARSET_RE.search(body[:4096])
        if mm:
            charset = mm.group(1).decode("ascii", "ignore")
    try:
        return body.decode(charset or "utf-8", errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")



def _direct_kind(content_type: str) -> Optional[str]:
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct.startswith("image/"):
        return "image"
    if ct.startswith("video/"):
        return "video"
    if ct in ("application/vnd.apple.mpegurl", "application/x-mpegurl", "audio/mpegurl",
              "audio/x-mpegurl", "application/dash+xml"):
        return "stream"
    return None


def fetch_page(url: str, browser: Optional[str], timeout: float = FETCH_TIMEOUT) -> Tuple[str, int, str, Optional[str], Optional[str]]:
    """Tải trang kèm cookie của ĐÚNG tên miền → (url cuối, mã HTTP, Content-Type, HTML, loại tệp thẳng).

    Không ném lỗi với mã 4xx/5xx: trang đăng nhập / chặn bot vẫn được trả về để bên
    gọi quyết định thử lại bằng trình duyệt thật.
    """
    jar = None
    cookie_file = None
    if browser != "none":
        host = urllib.parse.urlsplit(url).hostname
        cookie_file = get_browser_cookies_txt(browser or "auto", domain=host)
    try:
        if cookie_file and os.path.exists(cookie_file):
            jar = load_cookie_jar(cookie_file)
    finally:
        if cookie_file and os.path.exists(cookie_file):
            try:
                os.remove(cookie_file)
            except OSError:
                pass

    handlers: List[Any] = []
    if jar is not None:
        handlers.append(urllib.request.HTTPCookieProcessor(jar))
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "vi,en-US;q=0.9,en;q=0.8",
    })
    raise_if_cancelled()
    try:
        resp = opener.open(req, timeout=cap_timeout(timeout))
    except urllib.error.HTTPError as err:
        resp = err
    with resp:
        status = int(getattr(resp, "status", None) or resp.getcode() or 0)
        content_type = resp.headers.get("Content-Type", "") if resp.headers else ""
        final_url = resp.geturl() or url
        direct = _direct_kind(content_type)
        ct = content_type.split(";")[0].strip().lower()
        if not direct and ct in ("application/octet-stream", "binary/octet-stream", "application/binary"):
            # S3 / CDN hay trả tệp media với kiểu chung chung: nhận theo đuôi tệp
            kind = media_kind_of_url(final_url)
            direct = kind if kind in ("image", "video", "stream") else None
        if direct:
            # Không đọc thân tệp: có thể là video vài GB
            return final_url, status, content_type, None, direct
        if ct and not any(t in ct for t in ("html", "xml", "text/plain")):
            return final_url, status, content_type, None, None
        chunks: List[bytes] = []
        total = 0
        while total < MAX_HTML_BYTES:
            chunk = resp.read(min(64 * 1024, MAX_HTML_BYTES - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        body = b"".join(chunks)
        encoding = resp.headers.get("Content-Encoding", "") if resp.headers else ""
    return final_url, status, content_type, _decode_body(body, content_type, encoding), None


# ─────────────────────────────────────────────────────────────────────────────
# Extractor
# ─────────────────────────────────────────────────────────────────────────────

class PageMediaExtractor(BaseExtractor):
    """Lấy hết ảnh / video của bài viết trên trang web bất kỳ (có hoặc không đăng nhập)."""

    def __init__(self, sniffer: Any = None):
        super().__init__()
        self.sniffer = sniffer

    # ── điểm vào ──────────────────────────────────────────────────────────
    def extract(
        self,
        url: str,
        browser: Optional[str] = None,
        allow_dynamic: bool = True,
        force_dynamic: bool = False,
        platform: str = "generic",
    ) -> MediaMetadata:
        target = url.strip()
        self.log(f"Bóc tách media của bài viết: {target}")

        static: Optional[PageScan] = None
        static_err: Optional[Exception] = None
        login_notice: Optional[str] = None
        try:
            final_url, status, ctype, text, direct = fetch_page(target, browser)
            if direct:
                return self._direct_result(target, final_url, direct, platform)
            if text is not None:
                login_notice = login_only_notice(text, final_url)
                if login_notice is None:
                    static = scan_html(text, final_url)
                    static.status = status
        except Exception as err:  # mạng lỗi, chặn bot... → còn trình duyệt thật
            if type(err).__name__ in ("RequestCancelled", "RequestTimedOut"):
                raise
            static_err = err
            self.warn(f"Không tải được HTML tĩnh ({err}), thử trình duyệt headless...")
        if login_notice:
            # Mở bằng trình duyệt thật cũng chỉ thấy đúng ảnh mờ đó
            self.warn("Trang chỉ hiện bản làm mờ của bài viết cho khách — cần đăng nhập")
            raise LoginRequiredError(login_notice)

        static_items: List[_Candidate] = []
        scope = "page"
        if static is not None:
            static_items, scope = select_post_media(static)
            self.log(
                f"HTML tĩnh: {len(static_items)} media trong bài (phạm vi: {scope}), "
                f"HTTP {static.status}"
            )

        items = static_items
        scan = static
        dynamic_used = False
        dynamic_ran = False
        if allow_dynamic and self._needs_dynamic(static, static_items, scope, force_dynamic):
            dyn_scan, dyn_items = self._dynamic(target, browser)
            dynamic_ran = dyn_scan is not None
            if dyn_scan is not None and dyn_items:
                items = self._merge(dyn_items, static_items)
                scan = dyn_scan if static is None else self._prefer_meta(dyn_scan, static)
                dynamic_used = True
            elif dyn_scan is not None and scan is None:
                scan = dyn_scan

        if not items:
            if scan is not None and scan.has_password:
                raise LoginRequiredError(
                    "Trang này yêu cầu đăng nhập để xem bài viết. Hãy đăng nhập trang này trên "
                    "trình duyệt (Chrome/Edge/Firefox) rồi thử lại, hoặc mở Cookie Manager (🍪) "
                    "và dán cookie của trang."
                )
            if static_err is not None and scan is None:
                raise RuntimeError(f"Không thể truy cập trang web để bóc tách: {static_err}")
            raise NoPostMediaError("Không tìm thấy ảnh hoặc video nào trong bài viết này.", dynamic_ran)

        return self._build(target, scan, items, dynamic_used, platform)

    # ── quyết định có cần trình duyệt thật không ──────────────────────────
    def _needs_dynamic(
        self,
        static: Optional[PageScan],
        items: List[_Candidate],
        scope: str,
        force: bool,
    ) -> bool:
        if self.sniffer is None or not getattr(self.sniffer, "is_available", lambda: False)():
            return False
        if force or static is None:
            return True
        if static.status >= 400 or not items:
            return True
        # Trang dựng bằng JS: gần như không có chữ trong HTML tải về
        if static.text_len < 400 and static.script_count > 0:
            return True
        # Ảnh chỉ có placeholder (lazy-load bằng JS), <video> không nguồn, iframe player
        # lạ — chỉ tính những cái nằm TRONG bài viết
        if static.lazy_in_post > 0 or static.players_in_post > 0:
            return True
        # Không xác định được bài viết và trang lại cần đăng nhập
        if static.has_password and scope in ("page", "feed"):
            return True
        return False

    def _dynamic(self, url: str, browser: Optional[str]) -> Tuple[Optional[PageScan], List[_Candidate]]:
        try:
            raw = self.sniffer.collect_page_media(url, browser=browser)
        except Exception as err:
            if type(err).__name__ in ("RequestCancelled", "RequestTimedOut"):
                raise
            self.warn(f"Trình duyệt headless lỗi: {err}")
            return None, []
        if not raw or raw.get("error"):
            if raw and raw.get("error"):
                self.warn(f"Trình duyệt headless lỗi: {raw.get('error')}")
            if not raw or not raw.get("dom"):
                return None, []
        scan = scan_from_dom(raw)
        items, scope = select_post_media(scan)
        items = add_network_videos(items, scan, raw.get("network") or [])
        self.log(f"Trình duyệt headless: {len(items)} media trong bài (phạm vi: {scope})")
        return scan, items

    @staticmethod
    def _merge(dynamic: List[_Candidate], static: List[_Candidate]) -> List[_Candidate]:
        """Ưu tiên DOM đã dựng; bù video nhúng / ảnh mà chỉ HTML tĩnh thấy.

        Ảnh của HTML tĩnh chỉ được bù khi DOM không có ảnh nào — tránh đưa lại ảnh
        mà bản DOM đã loại (quảng cáo chèn bằng JS, ảnh của bài khác).
        """
        out = list(dynamic)
        keys = {("v:" + c.url) if c.kind == "video" else ("i:" + variant_key(c.url)) for c in out}
        dyn_has_image = any(c.kind == "image" for c in dynamic)
        for c in static:
            key = ("v:" + c.url) if c.kind == "video" else ("i:" + variant_key(c.url))
            if key in keys:
                continue
            if c.kind == "video" or not dyn_has_image:
                out.append(c)
                keys.add(key)
        return out

    @staticmethod
    def _prefer_meta(dyn: PageScan, static: PageScan) -> PageScan:
        dyn.title = static.title or dyn.title
        dyn.site_name = static.site_name or dyn.site_name
        dyn.description = static.description or dyn.description
        return dyn

    # ── dựng kết quả ──────────────────────────────────────────────────────
    def _direct_result(self, url: str, final_url: str, kind: str, platform: str) -> MediaMetadata:
        name = urllib.parse.unquote(urllib.parse.urlsplit(final_url).path.rsplit("/", 1)[-1]) or None
        if kind == "image":
            return MediaMetadata(
                id=str(abs(hash(url))), platform=platform, title=name, type="album",
                original_url=url, thumbnail=final_url, high_res_thumbnail=final_url,
                images=[MediaImage(id=1, url=final_url, title=name, type="gif" if _path_ext(final_url) == "gif" else "image",
                                   ext=_path_ext(final_url) or None, thumb=final_url)],
            )
        fmt = "HLS" if kind == "stream" else (_path_ext(final_url) or "mp4").upper()
        return MediaMetadata(
            id=str(abs(hash(url))), platform=platform, title=name, type="video", original_url=url,
            referer=None,
            streams=[StreamFormat(format_id="web_video_1", quality="Tệp video gốc" if kind != "stream" else "HLS Stream",
                                  format=fmt, stream_type="full", has_audio=True, has_video=True, url=final_url)],
        )

    def _build(
        self,
        url: str,
        scan: Optional[PageScan],
        items: List[_Candidate],
        dynamic_used: bool,
        platform: str,
    ) -> MediaMetadata:
        truncated = len(items) > MAX_ITEMS
        items = items[:MAX_ITEMS]
        images: List[MediaImage] = []
        for idx, c in enumerate(items, 1):
            ext = _path_ext(c.url) or None
            if c.kind == "video":
                kind = "video"
                thumb = c.thumb if c.thumb and media_kind_of_url(c.thumb) not in ("video", "stream", "segment") else None
            else:
                kind = "gif" if ext == "gif" else "image"
                thumb = c.thumb or c.url
            w, h = c.width, c.height
            images.append(MediaImage(
                id=idx,
                url=c.url,
                title=c.title,
                resolution=f"{w}x{h}" if w and h else None,
                type=kind,
                ext=ext if ext and len(ext) <= 5 else None,
                thumb=thumb,
                duration=c.duration,
                via=c.via,
            ))

        final_url = scan.final_url if scan else url
        title = (scan.title if scan else None) or None

        first_thumb = next((i.thumb for i in images if i.thumb and i.type != "video"), None) or \
            next((i.thumb for i in images if i.thumb), None)
        desc = describe_media(images, dynamic_used, truncated)

        # Một video duy nhất (tệp hoặc luồng trực tiếp): trả dạng video để giao diện
        # hiện ngay dòng "Tải về" như trước.
        if len(images) == 1 and images[0].type == "video":
            only = images[0]
            fmt = "HLS" if _path_ext(only.url) == "m3u8" else ("DASH" if _path_ext(only.url) == "mpd" else (only.ext or "mp4").upper())
            return MediaMetadata(
                id=str(abs(hash(url))), platform=platform, title=title, author=scan.site_name if scan else None,
                thumbnail=only.thumb, high_res_thumbnail=only.thumb, type="video", original_url=url,
                description=desc, referer=final_url,
                streams=[StreamFormat(format_id="web_video_1", quality="Video trong bài viết", format=fmt,
                                      stream_type="full", has_audio=True, has_video=True, url=only.url)],
            )

        return MediaMetadata(
            id=str(abs(hash(url))),
            platform=platform,
            title=title,
            author=scan.site_name if scan else None,
            thumbnail=first_thumb,
            high_res_thumbnail=first_thumb,
            type="album",
            original_url=url,
            description=desc,
            images=images,
            referer=final_url,
        )


# ─────────────────────────────────────────────────────────────────────────────
# DOM đã dựng (Playwright) → PageScan
# ─────────────────────────────────────────────────────────────────────────────

def scan_from_dom(raw: Dict[str, Any]) -> PageScan:
    """Chuyển kết quả của bộ thu thập DOM (stream_sniffer.collect_page_media) thành PageScan."""
    dom = raw.get("dom") or {}
    base = str(dom.get("url") or raw.get("url") or "")
    containers: List[_Container] = []
    for c in (dom.get("containers") or [])[:5000]:
        try:
            containers.append(_Container(
                cid=int(c["id"]), level=str(c["level"]),
                parent=int(c["parent"]) if c.get("parent") is not None else None,
                tag=str(c.get("tag") or ""), text=_to_int(c.get("text")),
            ))
        except (KeyError, TypeError, ValueError):
            continue

    cands: List[_Candidate] = []
    lazy_unresolved = 0
    iframe_paths: Dict[str, Tuple[int, ...]] = {}

    def path_of(item: Dict[str, Any]) -> Tuple[int, ...]:
        try:
            return tuple(int(p) for p in (item.get("path") or [])[:MAX_OPEN_CONTAINERS])
        except (TypeError, ValueError):
            return ()

    for order, item in enumerate((dom.get("items") or [])[:MAX_RAW_CANDIDATES]):
        if not isinstance(item, dict):
            continue
        kind = item.get("kind")
        path = path_of(item)
        cand: Optional[_Candidate] = None
        if kind == "img":
            attrs = {k: str(v) for k, v in (item.get("attrs") or {}).items() if isinstance(v, (str, int, float))}
            cand, placeholder_only = image_candidate(
                attrs, base,
                current_src=item.get("src") or None,
                picture_srcsets=[s for s in (item.get("pictureSrcsets") or []) if isinstance(s, str)],
                link_href=item.get("link") or None,
            )
            if cand is None:
                lazy_unresolved += 1 if placeholder_only else 0
                continue
            rw, rh = _to_int(item.get("rw")), _to_int(item.get("rh"))
            if rw and rh:
                cand.attr_w, cand.attr_h = rw, rh
            # naturalWidth/Height là của ảnh ĐANG HIỂN THỊ. Chỉ dùng khi bản được chọn
            # là chính nó hoặc một bản khác cỡ của nó — ảnh placeholder 1x1 của lazy-load
            # không được làm ảnh thật (data-src) bị coi là pixel rác.
            displayed = _clean_url(item.get("src"), base)
            nw, nh = _to_int(item.get("nw")), _to_int(item.get("nh"))
            if displayed and (cand.url == displayed or variant_key(cand.url) == variant_key(displayed)):
                cand.natural_w, cand.natural_h = nw, nh
                if cand.url == displayed and nw and nh:
                    cand.width, cand.height = nw, nh
        elif kind == "video":
            sources = [(s.get("src") or "", s.get("type") or "") for s in (item.get("sources") or []) if isinstance(s, dict)]
            src = item.get("src") or ""
            cand = video_candidate(src if not str(src).startswith("blob:") else None, sources, item.get("poster"), base)
            if cand is None:
                continue
        elif kind == "iframe":
            cand, _unknown = iframe_candidate(item.get("src"), base)
            url = _clean_url(item.get("src"), base)
            if url:
                iframe_paths.setdefault(url, path)
            if cand is None:
                continue
        elif kind == "bg":
            url = _clean_url(item.get("src"), base)
            if not url or _is_placeholder(url):
                continue
            cand = _Candidate(kind="image", url=url, thumb=url, source="bg",
                              attr_w=_to_int(item.get("rw")), attr_h=_to_int(item.get("rh")))
        elif kind == "data":
            attrs = {k: str(v) for k, v in (item.get("attrs") or {}).items() if isinstance(v, str)}
            for url in attribute_images(attrs, base):
                extra = _Candidate(kind="image", url=url, thumb=None, source="data")
                extra.path = path
                extra.order = order
                cands.append(extra)
            continue
        elif kind == "a":
            url = _clean_url(item.get("src"), base)
            mk = media_kind_of_url(url) if url else None
            if mk == "image":
                cand = _Candidate(kind="image", url=url, thumb=None, source="link")
            elif mk in ("video", "stream"):
                cand = video_candidate(url, [], None, base)
        if cand is None:
            continue
        cand.path = path
        cand.order = order
        cands.append(cand)

    # Media bên trong iframe (player nhúng từ tên miền khác) mang vùng của thẻ iframe
    next_order = len(cands)
    for frame in (raw.get("frames") or [])[:20]:
        if not isinstance(frame, dict):
            continue
        frame_url = _clean_url(frame.get("url"), base) or ""
        path = iframe_paths.get(frame_url, ())
        for item in (frame.get("items") or [])[:500]:
            if not isinstance(item, dict) or item.get("kind") != "video":
                continue
            sources = [(s.get("src") or "", s.get("type") or "") for s in (item.get("sources") or []) if isinstance(s, dict)]
            src = item.get("src") or ""
            cand = video_candidate(src if not str(src).startswith("blob:") else None, sources, item.get("poster"), frame_url or base)
            if cand is None:
                continue
            cand.path = path
            cand.order = next_order
            next_order += 1
            cands.append(cand)

    meta = dom.get("meta") or {}
    meta_cands: List[_Candidate] = []
    for key in ("og:video", "og:video:url", "og:video:secure_url", "twitter:player:stream"):
        value = meta.get(key)
        url = _clean_url(value, base) if isinstance(value, str) else None
        if url:
            info = embed_info(url)
            if info:
                meta_cands.append(_Candidate(kind="video", url=info[0], thumb=info[1], via="ytdlp", source="meta"))
            elif media_kind_of_url(url) in ("video", "stream"):
                cand = video_candidate(url, [], None, base)
                if cand:
                    meta_cands.append(cand)
            break
    for key in ("og:image", "og:image:secure_url", "twitter:image"):
        value = meta.get(key)
        url = _clean_url(value, base) if isinstance(value, str) else None
        if url:
            meta_cands.append(_Candidate(kind="image", url=url, thumb=url, source="meta"))
            break
    for block in (dom.get("ld") or [])[:20]:
        if not isinstance(block, str):
            continue
        try:
            data = json.loads(block)
        except (ValueError, RecursionError):
            continue
        _walk_ld(data, base, meta_cands, [4000])

    title = meta.get("og:title") if isinstance(meta.get("og:title"), str) else None
    return PageScan(
        final_url=base,
        title=title or (str(dom.get("title"))[:300] if dom.get("title") else None),
        site_name=meta.get("og:site_name") if isinstance(meta.get("og:site_name"), str) else None,
        description=meta.get("og:description") if isinstance(meta.get("og:description"), str) else None,
        containers=containers,
        candidates=cands,
        meta_candidates=meta_cands,
        has_password=bool(dom.get("hasPassword")),
        text_len=_to_int(dom.get("textLen")),
        lazy_unresolved=lazy_unresolved,
    )


def add_network_videos(items: List[_Candidate], scan: PageScan, network: List[Dict[str, Any]]) -> List[_Candidate]:
    """Bù video mà player phát bằng blob: (MSE) — chỉ thấy được qua lưu lượng mạng.

    Chỉ thêm khi bài viết có thẻ <video> mà không đọc ra nguồn thật, và chỉ lấy
    manifest / tệp video đầy đủ (bỏ các mảnh .ts/.m4s).
    """
    has_video = any(c.kind == "video" for c in items)
    if has_video or not network:
        return items
    seen = set()
    picked: List[_Candidate] = []
    for entry in network[:500]:
        if not isinstance(entry, dict):
            continue
        url = _clean_url(entry.get("url"), scan.final_url)
        if not url or url in seen:
            continue
        seen.add(url)
        ctype = str(entry.get("ctype") or "").lower()
        kind = media_kind_of_url(url)
        if kind == "segment" or "mp2t" in ctype or "iso.segment" in ctype:
            continue
        is_stream = kind == "stream" or "mpegurl" in ctype or "dash+xml" in ctype
        is_video = kind == "video" or ctype.startswith("video/")
        if not (is_stream or is_video):
            continue
        if _decor_or_junk(url)[0]:
            continue
        # Bỏ các tệp video quá nhỏ (quảng cáo / ảnh động nền) khi biết kích thước
        length = _to_int(entry.get("length"))
        if is_video and not is_stream and 0 < length < 200 * 1024:
            continue
        picked.append(_Candidate(kind="video", url=url, via="ytdlp" if is_stream else None, source="network",
                                 order=len(items) + len(picked)))
    # Manifest trước, tệp đầy đủ sau; tối đa vài nguồn để không đưa vào cả loạt quảng cáo
    picked.sort(key=lambda c: (0 if c.via == "ytdlp" else 1, c.order))
    return items + picked[:3]
