"""
yt-dlp Extractor Engine.
Wraps yt-dlp binary to extract rich video/audio metadata and playlist crawl data.
Ported from YtDlpService into standalone Python.
"""

import json
import math
import os
import re
from typing import Optional, List, Dict, Any, Tuple
from .base import BaseExtractor
from ..models import (
    MediaMetadata,
    StreamFormat,
    SubtitleItem,
    ChapterItem,
    CrawlMediaItem,
    ProfileCrawlResult,
)
from ..cookies.browser_cookies import get_browser_cookies_txt


class YtDlpExtractor(BaseExtractor):
    """Wrapper cho yt-dlp binary"""

    def __init__(self):
        super().__init__()
        self.binary_path = self.find_binary("yt-dlp", "YT_DLP_PATH")

    def is_available(self) -> bool:
        return self.binary_path is not None and os.path.exists(self.binary_path)

    def get_base_args(self, allow_playlist: bool = False, browser: Optional[str] = None, target_url: str = "") -> Tuple[List[str], Optional[str]]:
        """Xây dựng arguments cơ bản cho yt-dlp"""
        args = [
            "--no-warnings",
            "--ignore-errors",
            "--socket-timeout", "15",   # Ngắt kết nối sau 15s idle (tránh treo vô thời hạn)
            "--retries", "2",           # Chỉ thử lại 2 lần
            "--fragment-retries", "2",
        ]


        # Sử dụng Node.js runtime cho YouTube n-sig solver nếu có
        node_bin = self.find_binary("node")
        if node_bin and os.path.exists(node_bin):
            args.extend(["--js-runtimes", f"node:{node_bin}"])

        if not allow_playlist:
            args.append("--no-playlist")

        tmp_cookie_file = None
        if browser != "none":
            # Thử tự động xuất cookies từ trình duyệt Linux
            domain = None
            if target_url:
                import urllib.parse
                try:
                    parsed = urllib.parse.urlparse(target_url)
                    domain = (parsed.hostname or parsed.netloc or None)
                except Exception:
                    pass
            exported = get_browser_cookies_txt(browser or "auto", domain=domain)
            if exported and os.path.exists(exported):
                args.extend(["--cookies", exported])
                tmp_cookie_file = exported
            elif browser and browser not in ("auto", ""):
                args.extend(["--cookies-from-browser", browser])

        return args, tmp_cookie_file

    def extract_metadata(self, url: str, browser: Optional[str] = None, timeout: int = 30) -> MediaMetadata:
        """Trích xuất chi tiết metadata của 1 URL video/audio"""
        if not self.is_available():
            raise RuntimeError("yt-dlp binary không được tìm thấy trên hệ thống.")

        args, tmp_cookie = self.get_base_args(allow_playlist=False, browser=browser, target_url=url)
        cmd = [self.binary_path] + args + ["--dump-json", url]

        self.log(f"yt-dlp extract: {url}")
        try:
            code, stdout, stderr = self.run_process(cmd, timeout=timeout)
        finally:
            if tmp_cookie and os.path.exists(tmp_cookie):
                try:
                    os.remove(tmp_cookie)
                except Exception:
                    pass

        if code != 0 or not stdout.strip():
            if browser and browser != "none":
                self.warn("yt-dlp extract lỗi với cookies, đang thử lại không dùng cookies ('none')...")
                return self.extract_metadata(url, browser="none", timeout=timeout)
            raise RuntimeError(f"yt-dlp extract thất bại: {stderr.strip() or f'Exit code {code}'}")

        # Lấy dòng JSON đầu tiên
        json_line = None
        for line in stdout.strip().split("\n"):
            if line.strip().startswith("{"):
                json_line = line.strip()
                break

        if not json_line:
            if browser and browser != "none":
                self.warn("yt-dlp không trả metadata hợp lệ với cookies, thử lại không cookie...")
                return self.extract_metadata(url, browser="none", timeout=timeout)
            raise ValueError("Không tìm thấy dữ liệu JSON hợp lệ từ kết quả yt-dlp.")

        try:
            raw_data = json.loads(json_line)
        except json.JSONDecodeError as e:
            if browser and browser != "none":
                self.warn("yt-dlp trả JSON lỗi với cookies, thử lại không cookie...")
                return self.extract_metadata(url, browser="none", timeout=timeout)
            raise ValueError(f"Lỗi parse JSON yt-dlp: {e}")

        return self._normalize_metadata(raw_data, url)

    def extract_playlist(
        self,
        url: str,
        limit: int = 50,
        browser: Optional[str] = None,
        from_item: Optional[int] = None,
        to_item: Optional[int] = None,
        timeout: Optional[int] = None,
    ) -> ProfileCrawlResult:
        """Quét playlist / channel / profile bằng --flat-playlist"""
        if not self.is_available():
            raise RuntimeError("yt-dlp binary không được tìm thấy trên hệ thống.")

        # Kênh/playlist lớn cần nhiều thời gian hơn mốc cố định 45s trước đây
        if timeout is None:
            if from_item and to_item and to_item >= from_item:
                count = to_item - from_item + 1
            else:
                count = limit
            timeout = 540 if not count or count <= 0 else max(90, min(540, 60 + int(count * 1.2)))

        args, tmp_cookie = self.get_base_args(allow_playlist=True, browser=browser, target_url=url)
        if from_item and to_item and to_item >= from_item:
            range_spec = f"{from_item}-{to_item}"
        elif limit and limit > 0:
            range_spec = f"1-{limit}"
        else:
            range_spec = None

        cmd = [
            self.binary_path,
            *args,
            "--flat-playlist",
            "--dump-json",
        ]
        if range_spec:
            cmd.extend(["--playlist-items", range_spec])
        cmd.append(url)

        self.log(f"yt-dlp playlist ({range_spec or 'all'}): {url}")
        code, stdout, stderr = self.run_process(cmd, timeout=timeout)

        if tmp_cookie and os.path.exists(tmp_cookie):
            try:
                os.remove(tmp_cookie)
            except Exception:
                pass

        if code != 0 and not stdout.strip():
            if browser and browser != "none":
                self.warn("yt-dlp playlist lỗi với cookies, đang thử lại không dùng cookies ('none')...")
                return self.extract_playlist(url, limit, browser="none", from_item=from_item, to_item=to_item, timeout=timeout)
            raise RuntimeError(f"yt-dlp playlist thất bại: {stderr.strip() or f'Exit code {code}'}")

        return self._parse_playlist_data(stdout, url)

    # ─────────────────────────────────────────────────────────────────────────
    # Normalizers
    # ─────────────────────────────────────────────────────────────────────────

    def _normalize_metadata(self, raw: Dict[str, Any], original_url: str) -> MediaMetadata:
        raw_platform = (raw.get("extractor_key") or raw.get("extractor") or "media").lower()
        platform = self._detect_platform_string(raw_platform, original_url)

        title = raw.get("title") or "Không có tiêu đề"
        author = raw.get("uploader") or raw.get("channel") or raw.get("creator") or "Tác giả"
        author_url = raw.get("uploader_url") or raw.get("channel_url") or original_url
        duration_sec = raw.get("duration") or 0
        duration = self.format_duration(duration_sec)

        view_count = raw.get("view_count")
        views = f"{self.format_number(view_count)} lượt xem" if view_count is not None else "Không xác định"

        like_count = raw.get("like_count")
        likes = f"{self.format_number(like_count)} lượt thích" if like_count is not None else None

        comment_count = raw.get("comment_count")
        comments = f"{self.format_number(comment_count)} bình luận" if comment_count is not None else None

        description = raw.get("description") or ""
        tags = raw.get("tags")[:10] if isinstance(raw.get("tags"), list) else None

        upload_date_raw = raw.get("upload_date")
        upload_date = (
            f"{upload_date_raw[:4]}-{upload_date_raw[4:6]}-{upload_date_raw[6:8]}"
            if upload_date_raw and len(upload_date_raw) >= 8
            else None
        )

        is_live = bool(raw.get("is_live") or raw.get("live_status") == "is_live")

        # Thumbnails
        thumbnail = raw.get("thumbnail") or ""
        high_res_thumbnail = thumbnail
        thumbnails = raw.get("thumbnails")
        if isinstance(thumbnails, list) and thumbnails:
            sorted_thumbs = sorted(thumbnails, key=lambda t: t.get("width") or 0, reverse=True)
            if sorted_thumbs:
                high_res_thumbnail = sorted_thumbs[0].get("url") or thumbnail
                good_thumb = next((t.get("url") for t in sorted_thumbs if (t.get("width") or 0) >= 480), None)
                thumbnail = good_thumb or high_res_thumbnail

        # Subtitles
        subtitles: List[SubtitleItem] = []
        seen_langs = set()
        for sub_type, is_auto in [("subtitles", False), ("automatic_captions", True)]:
            sub_dict = raw.get(sub_type)
            if isinstance(sub_dict, dict):
                for lang, s_list in sub_dict.items():
                    if lang not in seen_langs:
                        seen_langs.add(lang)
                        first = s_list[0] if isinstance(s_list, list) and s_list else {}
                        s_name = first.get("name") or lang.upper()
                        if is_auto:
                            s_name += " (Tự động)"
                        subtitles.append(
                            SubtitleItem(
                                lang=lang,
                                name=s_name,
                                ext=first.get("ext") or "vtt",
                                url=first.get("url"),
                                is_auto_generated=is_auto,
                            )
                        )

        # Chapters
        chapters: List[ChapterItem] = []
        raw_chapters = raw.get("chapters")
        if isinstance(raw_chapters, list):
            for ch in raw_chapters:
                st = ch.get("start_time") or 0.0
                et = ch.get("end_time") or 0.0
                chapters.append(
                    ChapterItem(
                        title=ch.get("title") or "Chương",
                        start_time=float(st),
                        end_time=float(et),
                        start_formatted=self.format_duration(st),
                    )
                )

        # Streams
        streams: List[StreamFormat] = []
        formats = raw.get("formats") or []

        if is_live:
            streams.append(
                StreamFormat(
                    format_id="live_best",
                    quality="Live Stream (Chất lượng tốt nhất)",
                    format="HLS",
                    size="Live",
                    stream_type="full",
                    has_audio=True,
                    has_video=True,
                )
            )
        else:
            video_formats = [
                f for f in formats
                if (f.get("vcodec") and f.get("vcodec") != "none")
                or (f.get("video_ext") and f.get("video_ext") != "none")
                or (not f.get("acodec") and f.get("ext") == "mp4")
                or str(f.get("format_id", "")).lower() in ("hd", "sd", "best", "default")
            ]

            available_heights = sorted(
                list({f["height"] for f in video_formats if f.get("height")}),
                reverse=True,
            )

            target_heights = [2160, 1440, 1080, 720, 480, 360]
            for h in target_heights:
                matched = next((ah for ah in available_heights if ah >= h * 0.95), None)
                if matched or (h == 720 and available_heights):
                    if h >= 2160:
                        label = "4K (2160p Ultra HD)"
                    elif h >= 1440:
                        label = "2K (1440p Quad HD)"
                    elif h >= 1080:
                        label = "Full HD (1080p)"
                    elif h >= 720:
                        label = "HD (720p)"
                    elif h >= 480:
                        label = "Chuẩn SD (480p)"
                    else:
                        label = "Tiết kiệm (360p)"

                    muxed_spec = f"bestvideo[height<={h}]+bestaudio/best[height<={h}]"
                    bitrate_k = 12000 if h >= 2160 else 6000 if h >= 1440 else 3000 if h >= 1080 else 1500 if h >= 720 else 800
                    approx_size = self.format_bytes(int(duration_sec * bitrate_k * 1000 / 8)) if duration_sec else "Tự động"

                    streams.append(
                        StreamFormat(
                            format_id=muxed_spec,
                            quality=f"{label} — Có âm thanh đầy đủ",
                            format="MP4",
                            size=approx_size,
                            stream_type="full",
                            has_audio=True,
                            has_video=True,
                            fps="60fps" if h >= 1080 else "30fps",
                            bitrate=f"{bitrate_k}kbps",
                        )
                    )

            # Trường hợp không có thông tin height (như Facebook có hd/sd)
            if not available_heights and video_formats:
                hd = next((f for f in video_formats if str(f.get("format_id", "")).lower() == "hd" or str(f.get("format_note", "")).lower() == "hd"), None)
                sd = next((f for f in video_formats if str(f.get("format_id", "")).lower() == "sd" or str(f.get("format_note", "")).lower() == "sd"), None)
                if hd:
                    sz = hd.get("filesize") or hd.get("filesize_approx")
                    streams.append(
                        StreamFormat(
                            format_id="hd",
                            quality="HD (Độ phân giải cao) — Có âm thanh đầy đủ",
                            format=(hd.get("ext") or "mp4").upper(),
                            size=self.format_bytes(sz) if sz else "Tự động",
                            raw_size=sz,
                            stream_type="full",
                            has_audio=True,
                            has_video=True,
                            url=hd.get("url"),
                        )
                    )
                if sd:
                    sz = sd.get("filesize") or sd.get("filesize_approx")
                    streams.append(
                        StreamFormat(
                            format_id="sd",
                            quality="SD (Tiêu chuẩn) — Có âm thanh đầy đủ",
                            format=(sd.get("ext") or "mp4").upper(),
                            size=self.format_bytes(sz) if sz else "Tự động",
                            raw_size=sz,
                            stream_type="full",
                            has_audio=True,
                            has_video=True,
                            url=sd.get("url"),
                        )
                    )

            # Mute streams (chỉ video không tiếng)
            seen_mute = set()
            for f in video_formats:
                h = f.get("height")
                acodec = f.get("acodec")
                if (not acodec or acodec == "none") and h and h not in seen_mute:
                    seen_mute.add(h)
                    sz = f.get("filesize") or f.get("filesize_approx")
                    streams.append(
                        StreamFormat(
                            format_id=str(f.get("format_id")),
                            quality=f"{h}p (Chỉ video / Không tiếng)",
                            format=(f.get("ext") or "mp4").upper(),
                            size=self.format_bytes(sz) if sz else "Tự động",
                            raw_size=sz,
                            stream_type="mute",
                            has_audio=False,
                            has_video=True,
                            fps=f"{round(f['fps'])}fps" if f.get("fps") else None,
                            bitrate=f"{round(f['tbr'])}kbps" if f.get("tbr") else None,
                            vcodec=f.get("vcodec"),
                            url=f.get("url"),
                        )
                    )

            # Audio formats chuẩn
            audio_presets = [
                ("opus_best", "OPUS Chuẩn gốc (160 kbps, không suy hao)", "OPUS", 160),
                ("mp3_320k", "MP3 Chất lượng cao (320 kbps)", "MP3", 320),
                ("mp3_192k", "MP3 Chuẩn phổ biến (192 kbps)", "MP3", 192),
                ("m4a_aac", "M4A / AAC Gốc (256 kbps)", "M4A", 256),
                ("ogg_vorbis", "OGG Vorbis (192 kbps)", "OGG", 192),
                ("flac_lossless", "FLAC Âm thanh lossless (phòng thu)", "FLAC", 900),
                ("wav_lossless", "WAV Bản ghi không nén (Uncompressed)", "WAV", 1411),
                ("alac_lossless", "ALAC Chuẩn Apple Lossless", "ALAC", 900),
            ]
            for fid, q_label, ext_label, br in audio_presets:
                sz = self.format_bytes(int(duration_sec * br * 1000 / 8)) if duration_sec else "Tự động"
                streams.append(
                    StreamFormat(
                        format_id=fid,
                        quality=q_label,
                        format=ext_label,
                        size=sz,
                        stream_type="audio",
                        has_audio=True,
                        has_video=False,
                        bitrate=f"{br}kbps" if br < 900 else "Lossless",
                    )
                )

        # Reels / Shorts detection
        url_lower = original_url.lower()
        is_short = (
            "/shorts/" in url_lower
            or "/shorts/" in str(raw.get("webpage_url", "")).lower()
            or (platform == "youtube" and 0 < duration_sec <= 65 and (raw.get("height") or 0) > (raw.get("width") or 0))
        )
        is_reel = (
            "/reel/" in url_lower
            or "/reels/" in url_lower
            or "/share/r/" in url_lower
            or "/reel/" in str(raw.get("webpage_url", "")).lower()
        )

        return MediaMetadata(
            id=str(raw.get("id") or "media"),
            platform=platform,
            title=title,
            author=author,
            author_url=author_url,
            duration=duration,
            views=views,
            likes=likes,
            comments=comments,
            thumbnail=thumbnail,
            high_res_thumbnail=high_res_thumbnail,
            type="live" if is_live else "video",
            original_url=original_url,
            description=description,
            tags=tags,
            upload_date=upload_date,
            subtitles=subtitles if subtitles else None,
            chapters=chapters if chapters else None,
            streams=streams,
            is_live=is_live,
            live_stream_url=raw.get("url") if is_live else None,
            is_reel=is_reel or None,
            is_short=is_short or None,
        )

    def _parse_playlist_data(self, stdout_data: str, url: str) -> ProfileCrawlResult:
        lines = [l.strip() for l in stdout_data.strip().split("\n") if l.strip().startswith("{")]
        media: List[CrawlMediaItem] = []
        channel_name = "Playlist / Channel"
        channel_handle = "@channel"
        avatar = ""

        for idx, line in enumerate(lines, 1):
            try:
                item = json.loads(line)
                if item.get("channel") or item.get("uploader"):
                    channel_name = item.get("channel") or item.get("uploader")
                    handle_id = item.get("channel_id") or item.get("uploader_id") or channel_name.lower().replace(" ", "")
                    channel_handle = f"@{handle_id}"

                thumbs = item.get("thumbnails")
                best_thumb = thumbs[-1].get("url") if isinstance(thumbs, list) and thumbs else (item.get("thumbnail") or "")

                video_url = item.get("url") or ""
                if not video_url.startswith("http"):
                    item_id = item.get("id") or str(idx)
                    video_url = f"https://www.youtube.com/watch?v={item_id}"

                v_lower = video_url.lower()
                is_short = "/shorts/" in v_lower
                is_reel = "/reel/" in v_lower or "/reels/" in v_lower

                dur = item.get("duration")
                duration_str = self.format_duration(dur) if dur else "Video"

                vc = item.get("view_count")
                views_str = f"{self.format_number(vc)} lượt xem" if vc is not None else None

                media.append(
                    CrawlMediaItem(
                        id=idx,
                        type="video",
                        title=item.get("title") or f"Video {idx}",
                        thumb=best_thumb,
                        url=video_url,
                        duration=duration_str,
                        quality="HD",
                        size="Tự động",
                        author=item.get("uploader") or item.get("channel") or channel_name,
                        views=views_str,
                        is_reel=is_reel or None,
                        is_short=is_short or None,
                    )
                )
            except Exception:
                continue

        platform = self._detect_platform_string("", url)

        return ProfileCrawlResult(
            platform=platform,
            name=channel_name,
            handle=channel_handle,
            url=url,
            avatar=avatar,
            stats=f"Đã quét {len(media)} tệp phương tiện",
            media=media,
            total_count=len(media),
        )

    def _detect_platform_string(self, raw_platform: str, url: str) -> str:
        s = f"{raw_platform} {url}".lower()
        if "youtube" in s or "youtu.be" in s:
            return "youtube"
        if "tiktok" in s:
            return "tiktok"
        if "instagram" in s:
            return "instagram"
        if "facebook" in s or "fb.watch" in s or "fb.com" in s:
            return "facebook"
        if "twitter" in s or "x.com" in s:
            return "x"
        if "pinterest" in s or "pin.it" in s:
            return "pinterest"
        if "reddit" in s or "redd.it" in s:
            return "reddit"
        if "soundcloud" in s:
            return "soundcloud"
        if "twitch" in s:
            return "twitch"
        if "dailymotion" in s:
            return "dailymotion"
        if "bilibili" in s:
            return "bilibili"
        if "bluesky" in s or "bsky" in s:
            return "bluesky"
        if "movie" in s or ".m3u8" in s or ".mpd" in s:
            return "movie"
        return "generic"

    @staticmethod
    def format_duration(seconds: Optional[float]) -> str:
        if not seconds or math.isnan(seconds):
            return "00:00"
        s = int(seconds)
        secs = s % 60
        mins = (s // 60) % 60
        hrs = s // 3600
        if hrs > 0:
            return f"{hrs:02d}:{mins:02d}:{secs:02d}"
        return f"{mins:02d}:{secs:02d}"

    @staticmethod
    def format_bytes(b: int) -> str:
        if not b or b <= 0:
            return "0 B"
        sizes = ["B", "KB", "MB", "GB", "TB"]
        i = int(math.floor(math.log(b, 1024)))
        p = math.pow(1024, i)
        s = round(b / p, 1)
        return f"{s} {sizes[i]}"

    @staticmethod
    def format_number(n: Optional[int]) -> str:
        if n is None:
            return ""
        if n >= 1_000_000_000:
            return f"{n / 1_000_000_000:.1f}B"
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n / 1_000:.1f}K"
        return str(n)
