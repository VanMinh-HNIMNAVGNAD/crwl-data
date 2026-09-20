"""
TikTok Extractor Engine.
Uses curl_cffi with TLS impersonation to extract TikTok embed state (Frontity Connect)
and falls back to sec_uid yt-dlp pagination.
Integrated from tiktok_resolver.py into standalone Python.
"""

import json
import os
import re
import subprocess
from typing import Optional, List, Dict, Any
from .base import BaseExtractor
from ..models import CrawlMediaItem, ProfileCrawlResult

try:
    from curl_cffi import requests as cffi_requests
except ImportError:
    cffi_requests = None


class TikTokExtractor(BaseExtractor):
    """Bóc tách TikTok Profile và Video sử dụng Frontity Embed State và TLS Impersonation"""

    def __init__(self):
        super().__init__()
        self.yt_dlp_bin = self.find_binary("yt-dlp", "YT_DLP_PATH") or "yt-dlp"

    @staticmethod
    def extract_username(raw_input: str) -> Optional[str]:
        raw = raw_input.strip()
        if "@" in raw:
            match = re.search(r"@([A-Za-z0-9_.-]+)", raw)
            if match:
                return match.group(1).rstrip("/")
        match = re.search(r"tiktok\.com/@?([A-Za-z0-9_.-]+)", raw)
        if match:
            return match.group(1).rstrip("/")
        clean = raw.replace("https://", "").replace("http://", "").strip("/")
        if clean and not clean.startswith("www.") and "/" not in clean:
            return clean
        return None

    def resolve_profile(self, username_or_url: str, limit: int = 50) -> Optional[ProfileCrawlResult]:
        username = self.extract_username(username_or_url)
        if not username:
            self.warn(f"Không thể trích xuất TikTok username từ: {username_or_url}")
            return None

        video_list = []
        embed_data = {}

        if cffi_requests:
            targets = ["safari18_0", "chrome131", "edge101", "chrome120"]
            for imp in targets:
                try:
                    url = f"https://www.tiktok.com/embed/@{username}"
                    resp = cffi_requests.get(url, impersonate=imp, timeout=10)
                    if resp.status_code == 200 and "<script" in resp.text:
                        m = re.search(
                            r'<script[^>]+id=[\"\']__FRONTITY_CONNECT_STATE__[\"\'][^>]*>(.*?)</script>',
                            resp.text,
                            re.DOTALL,
                        )
                        if m:
                            data = json.loads(m.group(1))
                            source_data = data.get("source", {}).get("data", {})
                            for _, v in source_data.items():
                                if isinstance(v, dict) and "videoList" in v and v["videoList"]:
                                    video_list = v["videoList"]
                                    embed_data = v
                                    break
                            if video_list:
                                break
                except Exception:
                    continue

        # Tạo danh sách CrawlMediaItem
        media_entries: List[CrawlMediaItem] = []
        for idx, item in enumerate(video_list[:limit], 1):
            vid_id = item.get("id") or str(idx)
            title = (item.get("desc") or "").strip() or None
            w = item.get("width")
            h = item.get("height")
            quality = f"{w}x{h}" if w and h else None
            thumb = item.get("coverUrl") or item.get("originCoverUrl") or item.get("dynamicCoverUrl") or None
            play_url = item.get("playAddr") or f"https://www.tiktok.com/@{username}/video/{vid_id}"

            media_entries.append(
                CrawlMediaItem(
                    id=idx,
                    type="video",
                    title=title,
                    duration=None,
                    quality=quality,
                    size=None,
                    thumb=thumb,
                    url=play_url,
                    author=username,
                    is_reel=True,
                )
            )

        # Nếu cần nhiều hơn số item từ Embed (thường là 10), thử lấy secUid qua yt-dlp
        if limit > len(media_entries) and video_list:
            sec_uid = None
            for item in video_list[:3]:
                vid_id = item.get("id")
                if not vid_id:
                    continue
                v_url = f"https://www.tiktok.com/@{username}/video/{vid_id}"
                cmd = [self.yt_dlp_bin, "--no-warnings", "--ignore-errors", "--dump-json", v_url]
                code, stdout, _ = self.run_process(cmd, timeout=12)
                if code == 0 and stdout.strip():
                    try:
                        v_json = json.loads(stdout.strip().split("\n")[-1])
                        cand_uid = v_json.get("channel_id")
                        if cand_uid and cand_uid.startswith("MS4wLjABAAAA") and len(cand_uid) == 76:
                            sec_uid = cand_uid
                            break
                    except Exception:
                        pass

            if sec_uid:
                cmd = [
                    self.yt_dlp_bin,
                    "--no-warnings",
                    "--ignore-errors",
                    "--flat-playlist",
                    "--playlist-end",
                    str(limit),
                    "--print",
                    "%(id)s|||%(title)s|||%(thumbnail)s|||%(duration)s|||%(width)s|||%(height)s",
                    f"tiktokuser:{sec_uid}",
                ]
                code, stdout, _ = self.run_process(cmd, timeout=20)
                if code == 0 and stdout.strip():
                    more_entries = []
                    lines = [l for l in stdout.strip().split("\n") if l.strip()]
                    for i, l in enumerate(lines, 1):
                        parts = l.split("|||")
                        v_id = parts[0] if len(parts) > 0 else str(i)
                        v_title = parts[1] if len(parts) > 1 and parts[1] and parts[1] != "NA" else None
                        v_thumb = parts[2] if len(parts) > 2 and parts[2] != "NA" else None
                        dur_sec = parts[3] if len(parts) > 3 and parts[3] != "NA" else None
                        from .ytdlp import YtDlpExtractor
                        v_dur = YtDlpExtractor.format_duration(float(dur_sec)) if dur_sec and dur_sec.replace(".", "", 1).isdigit() else None
                        v_w = parts[4] if len(parts) > 4 and parts[4] != "NA" else None
                        v_h = parts[5] if len(parts) > 5 and parts[5] != "NA" else None
                        v_quality = f"{v_w}x{v_h}" if v_w and v_h else None

                        more_entries.append(
                            CrawlMediaItem(
                                id=i,
                                type="video",
                                title=v_title,
                                duration=v_dur,
                                quality=v_quality,
                                size=None,
                                thumb=v_thumb,
                                url=f"https://www.tiktok.com/@{username}/video/{v_id}",
                                author=username,
                                is_reel=True,
                            )
                        )
                    if len(more_entries) >= len(media_entries):
                        media_entries = more_entries

        if not media_entries:
            return None

        return ProfileCrawlResult(
            platform="tiktok",
            name=username,
            handle=f"@{username}",
            url=f"https://www.tiktok.com/@{username}",
            avatar="",
            stats=f"Đã quét {len(media_entries)} tệp phương tiện",
            media=media_entries,
            total_count=len(media_entries),
        )
