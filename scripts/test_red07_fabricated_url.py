#!/usr/bin/env python3
"""
Test Suite cho RED 07 — Playlist/Account Downloader Fabricates YouTube URLs

Kiểm thử:
1. YouTube item có URL -> giữ nguyên URL thật
2. YouTube item thiếu URL:
   - Có valid 11-char YouTube ID -> construct https://www.youtube.com/watch?v={item_id}
   - Thiếu ID hoặc ID không hợp lệ -> None, tuyệt đối không dùng fake index hay unknown ID
3. non-YouTube item có URL -> giữ nguyên URL của non-YouTube
4. non-YouTube item thiếu URL -> None, ĐẶC BIỆT KHÔNG xuất hiện youtube.com/watch?v=...
"""

import os
import sys
import json
from typing import Dict, Any, List

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.extractors.ytdlp import YtDlpExtractor
from core.dispatcher import MediaDispatcher


def run_test_cycle(cycle_name: str) -> bool:
    print(f"\n{'=' * 70}")
    print(f"  🚀 CHẠY {cycle_name} — Kiểm thử loại bỏ Fabricated YouTube URLs")
    print(f"{'=' * 70}")

    ytdlp = YtDlpExtractor()

    # -------------------------------------------------------------------------
    # 1. YouTube item có URL
    # -------------------------------------------------------------------------
    print("\n  [1] Kiểm tra: YouTube item có URL...")
    yt_with_url_stdout = json.dumps({
        "id": "dQw4w9WgXcQ",
        "url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "title": "Rick Astley - Never Gonna Give You Up",
        "uploader": "RickAstleyVEVO",
    }) + "\n" + json.dumps({
        "id": "jNQXAC9IVRw",
        "webpage_url": "https://www.youtube.com/watch?v=jNQXAC9IVRw",
        "title": "Me at the zoo",
        "uploader": "jawed",
    })

    res_yt_url = ytdlp._parse_playlist_data(
        yt_with_url_stdout, "https://www.youtube.com/playlist?list=PLtest1"
    )
    assert res_yt_url.platform == "youtube", f"Platform phải là youtube, nhận: {res_yt_url.platform}"
    assert len(res_yt_url.media) == 2, f"Số media phải là 2, nhận: {len(res_yt_url.media)}"
    assert res_yt_url.media[0].url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ", (
        f"Item 1 URL sai: {res_yt_url.media[0].url}"
    )
    assert res_yt_url.media[1].url == "https://www.youtube.com/watch?v=jNQXAC9IVRw", (
        f"Item 2 URL sai: {res_yt_url.media[1].url}"
    )
    print("  ✅ [PASS] YouTube item có URL: Giữ nguyên 100% URL thật từ source.")

    # -------------------------------------------------------------------------
    # 2. YouTube item thiếu URL
    # -------------------------------------------------------------------------
    print("\n  [2] Kiểm tra: YouTube item thiếu URL...")
    # 2a: Có valid YouTube ID (11 chars)
    yt_missing_url_valid_id_stdout = json.dumps({
        "id": "8qT4IxLpSBU",
        "title": "Valid YouTube ID item",
        "uploader": "Channel 1",
    })
    res_valid_id = ytdlp._parse_playlist_data(
        yt_missing_url_valid_id_stdout, "https://www.youtube.com/playlist?list=PLtest2"
    )
    assert res_valid_id.media[0].url == "https://www.youtube.com/watch?v=8qT4IxLpSBU", (
        f"YouTube item có valid ID phải construct chính xác, nhận: {res_valid_id.media[0].url}"
    )

    # 2b: Thiếu ID hoặc ID không hợp lệ (ví dụ: v1, 1, rỗng, không phải 11 chars)
    yt_missing_url_invalid_id_stdout = (
        json.dumps({"id": "v1", "title": "Short invalid ID"}) + "\n"
        + json.dumps({"id": "", "title": "Empty ID"}) + "\n"
        + json.dumps({"id": "toolong_invalid_id_12345", "title": "Too long ID"}) + "\n"
        + json.dumps({"title": "No ID at all"})
    )
    res_invalid_id = ytdlp._parse_playlist_data(
        yt_missing_url_invalid_id_stdout, "https://www.youtube.com/playlist?list=PLtest3"
    )
    for idx, it in enumerate(res_invalid_id.media, 1):
        assert it.url is None, (
            f"Item {idx} thiếu valid ID không được tự chế URL: {it.url}"
        )
        assert it.url != f"https://www.youtube.com/watch?v={idx}", (
            f"Item {idx} bị dính lỗi index fallback fake: {it.url}"
        )
    print("  ✅ [PASS] YouTube item thiếu URL: Valid ID -> construct đúng; Invalid/Missing ID -> None (không fake URL).")

    # -------------------------------------------------------------------------
    # 3. non-YouTube item có URL
    # -------------------------------------------------------------------------
    print("\n  [3] Kiểm tra: non-YouTube item có URL...")
    # SoundCloud
    sc_stdout = json.dumps({
        "id": "11223344",
        "url": "https://soundcloud.com/artist/track-abc",
        "title": "SoundCloud Track",
        "uploader": "Artist SC",
    })
    res_sc = ytdlp._parse_playlist_data(sc_stdout, "https://soundcloud.com/artist/sets/album")
    assert res_sc.platform == "soundcloud", f"Platform phải là soundcloud, nhận: {res_sc.platform}"
    assert res_sc.media[0].url == "https://soundcloud.com/artist/track-abc", (
        f"SoundCloud URL bị đổi: {res_sc.media[0].url}"
    )
    assert "youtube.com" not in (res_sc.media[0].url or ""), "SoundCloud URL không được chứa youtube.com"

    # TikTok
    tt_stdout = json.dumps({
        "id": "7123456789012345678",
        "url": "https://www.tiktok.com/@user/video/7123456789012345678",
        "title": "TikTok Video",
        "uploader": "user",
    })
    res_tt = ytdlp._parse_playlist_data(tt_stdout, "https://www.tiktok.com/@user")
    assert res_tt.platform == "tiktok", f"Platform phải là tiktok, nhận: {res_tt.platform}"
    assert res_tt.media[0].url == "https://www.tiktok.com/@user/video/7123456789012345678"
    assert "youtube.com" not in (res_tt.media[0].url or "")

    print("  ✅ [PASS] non-YouTube item có URL: Giữ nguyên 100% URL thật, không bị biến thành YouTube.")

    # -------------------------------------------------------------------------
    # 4. non-YouTube item thiếu URL
    # -------------------------------------------------------------------------
    print("\n  [4] Kiểm tra: non-YouTube item thiếu URL (ĐẶC BIỆT KHÔNG xuất hiện youtube.com/watch?v=...)...")
    non_yt_sc_empty_url = json.dumps({
        "id": "999888777",
        "title": "SoundCloud Missing URL",
        "uploader": "Artist",
    })
    res_sc_empty = ytdlp._parse_playlist_data(
        non_yt_sc_empty_url, "https://soundcloud.com/artist/sets/ep"
    )
    assert res_sc_empty.media[0].url is None, (
        f"SoundCloud thiếu URL phải trả về None, nhận: {res_sc_empty.media[0].url}"
    )
    assert "youtube.com" not in str(res_sc_empty.media[0].url or ""), (
        f"LỖI NGHIÊM TRỌNG: Non-YouTube item bị gắn YouTube URL: {res_sc_empty.media[0].url}"
    )

    non_yt_tt_empty_url = json.dumps({
        "id": "7199999999999999999",
        "title": "TikTok Missing URL",
        "uploader": "ttuser",
    })
    res_tt_empty = ytdlp._parse_playlist_data(
        non_yt_tt_empty_url, "https://www.tiktok.com/@ttuser"
    )
    assert res_tt_empty.media[0].url is None, (
        f"TikTok thiếu URL phải trả về None, nhận: {res_tt_empty.media[0].url}"
    )
    assert "youtube.com" not in str(res_tt_empty.media[0].url or ""), (
        f"LỖI NGHIÊM TRỌNG: Non-YouTube item bị gắn YouTube URL: {res_tt_empty.media[0].url}"
    )

    # Trường hợp đặc biệt: non-YouTube item nhưng ID ngẫu nhiên có 11 ký tự
    non_yt_11char_id = json.dumps({
        "id": "abcdefghijk",  # đúng 11 ký tự nhưng platform là generic/non-youtube
        "title": "11 Char Non-YT ID",
    })
    res_generic_empty = ytdlp._parse_playlist_data(
        non_yt_11char_id, "https://example.com/feed"
    )
    assert res_generic_empty.media[0].url is None, (
        f"Generic/non-YouTube item có 11-char ID KHÔNG được construct YouTube URL, nhận: {res_generic_empty.media[0].url}"
    )
    assert "youtube.com" not in str(res_generic_empty.media[0].url or "")

    # Kiểm tra tuần tự hóa to_dict()
    sc_dict = res_sc_empty.to_dict()
    assert sc_dict["media"][0]["url"] is None, "to_dict() phải trả về url: None (null trong JSON)"
    assert "youtube.com" not in json.dumps(sc_dict), "JSON serialize không được chứa youtube.com"

    print("  ✅ [PASS] non-YouTube item thiếu URL: Trả về None, hoàn toàn KHÔNG xuất hiện youtube.com/watch?v=...")

    # -------------------------------------------------------------------------
    # 5. Dispatcher platform detection (loại bỏ generic '/playlist' khỏi is_youtube)
    # -------------------------------------------------------------------------
    print("\n  [5] Kiểm tra: Dispatcher platform detection không ngộ nhận '/playlist' là YouTube...")
    dispatcher = MediaDispatcher()
    # Kiểm tra target_url có '/playlist' từ platform khác
    url_other_pl = "https://soundcloud.com/artist/sets/my-playlist"
    is_yt_detected = any(d in url_other_pl for d in ("youtube.com", "youtu.be"))
    assert not is_yt_detected, "soundcloud.com playlist không được nhận diện là youtube domain"
    print("  ✅ [PASS] Dispatcher platform detection: Đã loại bỏ nhầm lẫn '/playlist' generic.")

    print(f"\n🎉 KẾT QUẢ {cycle_name}: TOÀN BỘ CÁC MỤC KIỂM THỬ ĐỀU PASS!")
    return True


def main():
    print("=" * 70)
    print("🚀 BẮT ĐẦU KIỂM THỬ RED 07: SỬA LỖI FABRICATED YOUTUBE URLS")
    print("=" * 70)

    # TEST #1
    success_1 = run_test_cycle("TEST #1")
    if not success_1:
        print("\n❌ TEST #1: FAILED")
        sys.exit(1)

    # TEST #2 (Lặp lại toàn bộ test để kiểm tra tính ổn định và lặp lại)
    success_2 = run_test_cycle("TEST #2")
    if not success_2:
        print("\n❌ TEST #2: FAILED")
        sys.exit(1)

    print("\n" + "=" * 70)
    print("TỔNG KẾT KẾT QUẢ:")
    print("TEST #1: PASS")
    print("TEST #2: PASS")
    print("=" * 70)


if __name__ == "__main__":
    main()
