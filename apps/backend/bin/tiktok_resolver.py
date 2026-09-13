#!/usr/bin/env python3
"""
TikTok Profile & Video Resolver
Extracts TikTok user profile and media list using curl_cffi with TLS impersonation
and Frontity Embed state parsing.
"""

import sys
import json
import re
import subprocess
import os

try:
    from curl_cffi import requests
except ImportError:
    requests = None


def extract_username(raw_input: str) -> str:
    raw = raw_input.strip()
    if '@' in raw:
        match = re.search(r'@([A-Za-z0-9_.-]+)', raw)
        if match:
            return match.group(1).rstrip('/')
    # If it's a URL
    match = re.search(r'tiktok\.com/@?([A-Za-z0-9_.-]+)', raw)
    if match:
        return match.group(1).rstrip('/')
    # Plain username
    return raw.replace('https://', '').replace('http://', '').strip('/')


def resolve_profile(username: str, limit: int = 50):
    if not requests:
        raise RuntimeError("curl_cffi is not installed in the python environment")

    # Rotate through impersonation targets if one gets 503 / challenged
    targets = ['safari18_0', 'chrome131', 'edge101', 'chrome120']
    
    video_list = []
    embed_data = {}
    
    for imp in targets:
        try:
            url = f"https://www.tiktok.com/embed/@{username}"
            resp = requests.get(url, impersonate=imp, timeout=8)
            if resp.status_code == 200 and '<script' in resp.text:
                m = re.search(r'<script[^>]+id=[\"\']__FRONTITY_CONNECT_STATE__[\"\'][^>]*>(.*?)</script>', resp.text, re.DOTALL)
                if m:
                    data = json.loads(m.group(1))
                    source_data = data.get('source', {}).get('data', {})
                    for k, v in source_data.items():
                        if isinstance(v, dict) and 'videoList' in v and v['videoList']:
                            video_list = v['videoList']
                            embed_data = v
                            break
                    if video_list:
                        break
        except Exception:
            continue

    if not video_list:
        return None

    # Format media entries from embed page
    media_entries = []
    for idx, item in enumerate(video_list[:limit], 1):
        vid_id = item.get('id') or str(idx)
        title = (item.get('desc') or '').strip()
        if not title:
            title = f"TikTok video by @{username} ({vid_id})"
        
        w = item.get('width') or 576
        h = item.get('height') or 1024
        thumb = item.get('coverUrl') or item.get('originCoverUrl') or item.get('dynamicCoverUrl') or ''
        play_url = item.get('playAddr') or f"https://www.tiktok.com/@{username}/video/{vid_id}"

        media_entries.append({
            "id": idx,
            "type": "video",
            "title": title,
            "duration": "Tự động",
            "quality": f"{w}x{h}",
            "size": "Tự động",
            "thumb": thumb,
            "url": play_url,
            "author": username,
        })

    # If user requested more than embed returns (typically 10), try to resolve secUid for yt-dlp pagination
    if limit > len(media_entries):
        sec_uid = None
        # Try to find sec_uid from one of the videos using yt-dlp
        for item in video_list[:3]:
            vid_id = item.get('id')
            if not vid_id:
                continue
            v_url = f"https://www.tiktok.com/@{username}/video/{vid_id}"
            script_dir = os.path.dirname(os.path.abspath(__file__))
            yt_dlp_bin = os.path.join(script_dir, 'yt-dlp')
            if not os.path.exists(yt_dlp_bin):
                yt_dlp_bin = 'yt-dlp'
            
            try:
                cmd = [yt_dlp_bin, '--no-warnings', '--ignore-errors', '--dump-json', v_url]
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
                if proc.returncode == 0 and proc.stdout.strip():
                    last_line = proc.stdout.strip().split('\n')[-1]
                    v_json = json.loads(last_line)
                    cand_uid = v_json.get('channel_id')
                    # TikTok secUid format typically starts with MS4wLjABAAAA and length is around 50-80 chars
                    if cand_uid and cand_uid.startswith('MS4wLjABAAAA') and len(cand_uid) == 76:
                        sec_uid = cand_uid
                        break
            except Exception:
                pass

        if sec_uid:
            try:
                cmd = [
                    yt_dlp_bin,
                    '--no-warnings',
                    '--ignore-errors',
                    '--flat-playlist',
                    '--playlist-end', str(limit),
                    '--print', '%(id)s|||%(title)s|||%(thumbnail)s|||%(duration)s|||%(width)s|||%(height)s',
                    f"tiktokuser:{sec_uid}"
                ]
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                if proc.returncode == 0 and proc.stdout.strip():
                    more_entries = []
                    lines = [l for l in proc.stdout.strip().split('\n') if l.strip()]
                    for i, l in enumerate(lines, 1):
                        parts = l.split('|||')
                        v_id = parts[0] if len(parts) > 0 else str(i)
                        v_title = parts[1] if len(parts) > 1 and parts[1] else f"TikTok video by @{username}"
                        v_thumb = parts[2] if len(parts) > 2 and parts[2] != 'NA' else ''
                        v_dur = parts[3] if len(parts) > 3 and parts[3] != 'NA' else 'Tự động'
                        v_w = parts[4] if len(parts) > 4 and parts[4] != 'NA' else '576'
                        v_h = parts[5] if len(parts) > 5 and parts[5] != 'NA' else '1024'

                        more_entries.append({
                            "id": i,
                            "type": "video",
                            "title": v_title,
                            "duration": v_dur,
                            "quality": f"{v_w}x{v_h}",
                            "size": "Tự động",
                            "thumb": v_thumb,
                            "url": f"https://www.tiktok.com/@{username}/video/{v_id}",
                            "author": username,
                        })
                    if len(more_entries) >= len(media_entries):
                        media_entries = more_entries
            except Exception:
                pass

    return {
        "platform": "tiktok",
        "name": username,
        "handle": f"@{username}",
        "url": f"https://www.tiktok.com/@{username}",
        "avatar": "",
        "stats": f"Đã quét {len(media_entries)} tệp phương tiện",
        "media": media_entries,
        "totalCount": len(media_entries),
    }


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"error": "Vui lòng cung cấp username hoặc URL TikTok"}))
        sys.exit(1)

    raw_user = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 50

    username = extract_username(raw_user)
    if not username:
        print(json.dumps({"error": f"Không thể trích xuất tên người dùng TikTok từ {raw_user}"}))
        sys.exit(1)

    result = resolve_profile(username, limit)
    if not result or not result.get("media"):
        print(json.dumps({"error": f"Không thể lấy danh sách video từ @{username}. Tài khoản có thể ở chế độ riêng tư."}))
        sys.exit(1)

    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
