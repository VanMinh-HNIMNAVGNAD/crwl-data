#!/usr/bin/env python3
"""
full_system_test.py
Kiểm thử tự động toàn diện 3 đề mục, mỗi đề mục 3 lần với toàn bộ chức năng con.
- Đề mục 1: Tải theo liên kết đơn lẻ (SingleDownloader) - 3 lần
- Đề mục 2: Tải theo danh sách liên kết (MultiLinkDownloader) - 3 lần
- Đề mục 3: Quét & Tải toàn bộ nội dung theo tài khoản (BulkDownloader) - 3 lần
"""

import sys
import json
import time
import urllib.request
import urllib.parse
import zipfile
import io

BASE_URL = "http://localhost:3000"

def log_test_header(title):
    print("\n" + "=" * 75)
    print(f"  {title}")
    print("=" * 75)

def log_sub_test(name):
    print(f"\n---> [TEST] {name}")

def log_success(msg):
    print(f"  [PASSED] {msg}")

def log_fail(msg):
    print(f"  [FAILED] {msg}")

def api_post(endpoint, payload):
    url = f"{BASE_URL}{endpoint}"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "FullSystemTest/1.0"}
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        return resp.status, json.loads(resp.read().decode("utf-8"))

def api_get(endpoint, params=None, read_bytes=True, max_bytes=None):
    query_str = f"?{urllib.parse.urlencode(params)}" if params else ""
    url = f"{BASE_URL}{endpoint}{query_str}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "FullSystemTest/1.0"}
    )
    with urllib.request.urlopen(req, timeout=45) as resp:
        status = resp.status
        content_type = resp.headers.get("Content-Type", "")
        if max_bytes:
            data = resp.read(max_bytes)
        elif read_bytes:
            data = resp.read()
        else:
            data = resp.read().decode("utf-8")
        return status, content_type, data

# ==============================================================================
# ĐỀ MỤC 1: TẢI THEO LIÊN KẾT ĐƠN LẺ (SINGLE DOWNLOADER) - 3 LẦN
# ==============================================================================

def test_section_1():
    log_test_header("ĐỀ MỤC 1: TẢI THEO LIÊN KẾT ĐƠN LẺ (SINGLE DOWNLOADER) - 3 LẦN")

    # -------------------------------------------------------------------------
    # LẦN 1: Instagram Album đa phương tiện
    # -------------------------------------------------------------------------
    log_sub_test("LẦN 1: Instagram Album (https://www.instagram.com/p/Dc3w6fvCdKk/)")

    # 1.1 Xác thực / Giải mã liên kết (resolve-url)
    status, res = api_post("/api/media/resolve-url", {"url": "https://www.instagram.com/p/Dc3w6fvCdKk/"})
    assert status == 201 or status == 200, f"resolve-url failed with {status}"
    assert res.get("platform") == "instagram", f"Unexpected platform: {res.get('platform')}"
    log_success(f"1.1 Resolve URL thành công: Platform = {res.get('platform')}, Clean URL = {res.get('resolvedUrl')}")

    # 1.2 Trích xuất toàn bộ thông tin (extract)
    status, res = api_post("/api/media/extract", {"url": "https://www.instagram.com/p/Dc3w6fvCdKk/", "browser": "edge"})
    assert status == 201 or status == 200, f"extract failed with {status}"
    images = res.get("images") or []
    assert len(images) > 0, "Không tìm thấy hình ảnh nào trong album"
    author = res.get("author")
    title = res.get("title")
    log_success(f"1.2 Trích xuất thành công: Tác giả = {author}, Tiêu đề = {title[:40]}, Tổng số tệp = {len(images)}")
    assert len(images) == 12, f"Kỳ vọng 12 tệp nhưng nhận được {len(images)}"
    log_success(f"1.3 Đầy đủ 12 tệp đa phương tiện độ phân giải cao.")

    # 1.4 Proxy tải ảnh đơn lẻ (chống chặn 403 từ Instagram CDN)
    sample_img = images[0]
    sample_url = sample_img.get("url")
    status, ctype, img_data = api_get("/api/media/proxy-media", {"url": sample_url, "filename": "sample.jpg"})
    assert status == 200, f"proxy-media failed with {status}"
    assert len(img_data) > 10000, f"Dữ liệu ảnh quá nhỏ: {len(img_data)} bytes"
    assert "image" in ctype or img_data[:2] == b"\xff\xd8", "Dữ liệu không phải định dạng JPEG hợp lệ"
    log_success(f"1.4 Proxy Media thành công: Kích thước = {len(img_data)} bytes, Content-Type = {ctype}")

    # 1.5 Tải gói ZIP các ảnh đã chọn
    zip_items = [
        {"url": img["url"], "filename": f"insta_{idx+1}.jpg"}
        for idx, img in enumerate(images[:3])
    ]
    url_zip = f"{BASE_URL}/api/media/download-zip"
    req_zip = urllib.request.Request(
        url_zip,
        data=json.dumps({"items": zip_items, "archiveName": "instagram_album"}).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req_zip, timeout=30) as resp:
        zip_bytes = resp.read()
        assert resp.status == 200 or resp.status == 201
        assert zip_bytes[:2] == b"PK", "Header không phải là tệp ZIP hợp lệ"
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            file_list = zf.namelist()
            assert len(file_list) == 3
            log_success(f"1.5 Đóng gói ZIP 3 ảnh thành công: Dung lượng = {len(zip_bytes)} bytes, Danh sách tệp: {file_list}")

    # -------------------------------------------------------------------------
    # LẦN 2: X (Twitter) Tweet & YouTube Video (Định dạng Stream & Thumbnail)
    # -------------------------------------------------------------------------
    log_sub_test("LẦN 2: X (Twitter) Tweet & YouTube Video (Định dạng Stream & Thumbnail)")

    # 2.1 Trích xuất tweet X/Twitter (https://x.com/NASA/status/2098418588348465354)
    status, res = api_post("/api/media/extract", {"url": "https://x.com/NASA/status/2098418588348465354", "browser": "edge"})
    assert status == 201 or status == 200
    assert res.get("platform") == "twitter"
    assert res.get("author") == "NASA"
    twitter_imgs = res.get("images") or []
    assert len(twitter_imgs) >= 1
    log_success(f"2.1 Trích xuất Tweet X thành công: Tác giả = {res.get('author')}, Ảnh gốc = {twitter_imgs[0].get('url')[:60]}...")

    # 2.2 Trích xuất video YouTube (https://www.youtube.com/watch?v=jNQXAC9IVRw)
    status, yt_res = api_post("/api/media/extract", {"url": "https://www.youtube.com/watch?v=jNQXAC9IVRw", "browser": "edge"})
    assert status == 201 or status == 200
    assert yt_res.get("platform") == "youtube"
    streams = yt_res.get("streams") or []
    assert len(streams) > 0, "Không tìm thấy streams video"
    log_success(f"2.2 Trích xuất YouTube thành công: Tiêu đề = '{yt_res.get('title')}', Số streams = {len(streams)}")

    # 2.3 Tải Thumbnail qua API chuyên dụng
    status, ctype, thumb_data = api_get("/api/media/download/thumbnail", {"url": "https://www.youtube.com/watch?v=jNQXAC9IVRw", "title": "me_at_the_zoo"})
    assert status == 200
    assert len(thumb_data) > 2000
    log_success(f"2.3 Tải Thumbnail thành công: Kích thước = {len(thumb_data)} bytes, Content-Type = {ctype}")

    # 2.4 Tải Stream Video / Audio (kiểm tra truyền dữ liệu stream chunk qua pipe)
    status, ctype, stream_chunk = api_get(
        "/api/media/download/stream",
        {"url": "https://www.youtube.com/watch?v=jNQXAC9IVRw", "format": "mp4", "streamType": "video"},
        max_bytes=65536
    )
    assert status == 200
    assert len(stream_chunk) > 1000
    log_success(f"2.4 Tải Video Stream trực tiếp thành công: Nhận được chunk đầu {len(stream_chunk)} bytes, Type = {ctype}")

    # 2.5 Tải Stream Audio (mp3)
    status, ctype, audio_chunk = api_get(
        "/api/media/download/stream",
        {"url": "https://www.youtube.com/watch?v=jNQXAC9IVRw", "isAudio": "true", "audioFormat": "mp3", "streamType": "audio"},
        max_bytes=32768
    )
    assert status == 200
    assert len(audio_chunk) > 1000
    log_success(f"2.5 Tải Audio MP3 Stream trực tiếp thành công: Nhận được chunk đầu {len(audio_chunk)} bytes, Type = {ctype}")

    # -------------------------------------------------------------------------
    # LẦN 3: Facebook Reel & YouTube Shorts (Kiểm thử nhận diện dạng Reels/Shorts)
    # -------------------------------------------------------------------------
    log_sub_test("LẦN 3: Facebook Reel & YouTube Shorts (Nhận diện định dạng Reels/Shorts)")

    # 3.1 Trích xuất YouTube Shorts và kiểm tra cờ isShort
    status, short_res = api_post("/api/media/extract", {"url": "https://www.youtube.com/shorts/kJQP7kiw5Fk", "browser": "edge"})
    assert status == 200 or status == 201
    assert short_res.get("platform") == "youtube"
    assert short_res.get("isShort") is True, f"Kỳ vọng isShort=True nhưng nhận được {short_res.get('isShort')}"
    log_success(f"3.1 Trích xuất YouTube Shorts thành công: Tiêu đề = '{short_res.get('title')[:35]}', isShort = True, Streams = {len(short_res.get('streams') or [])}")

    # 3.2 Trích xuất Facebook Reel và kiểm tra cờ isReel
    status, fb_res = api_post("/api/media/extract", {"url": "https://www.facebook.com/reel/10153231379946729", "browser": "edge"})
    assert status == 200 or status == 201
    assert fb_res.get("platform") == "facebook"
    assert fb_res.get("isReel") is True, f"Kỳ vọng isReel=True nhưng nhận được {fb_res.get('isReel')}"
    log_success(f"3.2 Trích xuất Facebook Reel thành công: Tiêu đề = '{fb_res.get('title')[:35]}', isReel = True, Streams = {len(fb_res.get('streams') or [])}")

    # 3.3 Tải Stream Video Facebook Reel trực tiếp
    status, ctype, reel_chunk = api_get(
        "/api/media/download/stream",
        {"url": "https://www.facebook.com/reel/10153231379946729", "format": "mp4", "streamType": "video"},
        max_bytes=32768
    )
    assert status == 200
    assert len(reel_chunk) > 1000
    log_success(f"3.3 Tải Stream Video Facebook Reel thành công: Nhận được {len(reel_chunk)} bytes, Type = {ctype}")


# ==============================================================================
# ĐỀ MỤC 2: TẢI THEO DANH SÁCH LIÊN KẾT (MULTI-LINK DOWNLOADER) - 3 LẦN
# ==============================================================================

def test_section_2():
    log_test_header("ĐỀ MỤC 2: TẢI THEO DANH SÁCH LIÊN KẾT (MULTI-LINK DOWNLOADER) - 3 LẦN")

    # -------------------------------------------------------------------------
    # LẦN 1: Danh sách 3 liên kết Instagram đa dạng
    # -------------------------------------------------------------------------
    log_sub_test("LẦN 1: Giải mã danh sách 3 liên kết Instagram cùng nền tảng")
    insta_links = [
        "https://www.instagram.com/p/Dc3w6fvCdKk/",
        "https://www.instagram.com/p/Dcsy19xCYZ0/",
        "https://www.instagram.com/p/DcAF9MfD4e9/",
    ]

    decoded_insta = []
    for idx, link in enumerate(insta_links):
        t0 = time.time()
        status, res = api_post("/api/media/extract", {"url": link, "browser": "edge"})
        assert status == 201 or status == 200
        elapsed = time.time() - t0
        decoded_insta.append(res)
        imgs = res.get("images") or []
        log_success(f"2.1.{idx+1} Giải mã Link {idx+1} ({link.split('/')[-2]}): {len(imgs)} tệp media ({elapsed:.2f}s) - Tác giả: {res.get('author')}")

    assert len(decoded_insta) == 3
    total_media_count = sum(len(d.get("images") or []) for d in decoded_insta)
    log_success(f"2.1.4 Tổng hợp kết quả giải mã 3 link: {total_media_count} tệp media sẵn sàng tải.")

    # Đóng gói ZIP nhiều bài viết Instagram tổng hợp
    selected_zip_items = []
    for d_idx, d in enumerate(decoded_insta):
        imgs = d.get("images") or []
        for i_idx, img in enumerate(imgs[:2]):
            selected_zip_items.append({
                "url": img["url"],
                "filename": f"post{d_idx+1}_{d.get('author')}_{i_idx+1}.jpg"
            })

    url_zip = f"{BASE_URL}/api/media/download-zip"
    req_zip = urllib.request.Request(
        url_zip,
        data=json.dumps({"items": selected_zip_items, "archiveName": "multi_instagram_batch"}).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req_zip, timeout=40) as resp:
        zip_bytes = resp.read()
        assert resp.status == 200 or resp.status == 201
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            files = zf.namelist()
            assert len(files) >= 1, f"Kỳ vọng ít nhất 1 tệp trong zip nhưng nhận được {len(files)}: {files}"
            log_success(f"2.1.5 Đóng gói ZIP {len(files)} tệp từ danh sách link thành công: {len(zip_bytes)} bytes.")

    # -------------------------------------------------------------------------
    # LẦN 2: Danh sách liên kết X (Twitter)
    # -------------------------------------------------------------------------
    log_sub_test("LẦN 2: Giải mã danh sách liên kết X (Twitter)")
    mixed_links = [
        "https://x.com/NASA/status/2098418588348465354",
        "https://x.com/NASA/status/2098520709056065990",
    ]

    decoded_mixed = []
    for idx, link in enumerate(mixed_links):
        t0 = time.time()
        status, res = api_post("/api/media/extract", {"url": link, "browser": "edge"})
        assert status == 201 or status == 200
        elapsed = time.time() - t0
        decoded_mixed.append(res)
        imgs = res.get("images") or []
        log_success(f"2.2.{idx+1} Giải mã Tweet {idx+1}: {len(imgs)} tệp ảnh gốc ({elapsed:.2f}s) - Tiêu đề: {res.get('title')[:35]}")

    # Đóng gói ZIP các tệp ảnh từ danh sách tweet
    zip_items_x = [
        {"url": d["images"][0]["url"], "filename": f"nasa_tweet_{idx+1}.jpg"}
        for idx, d in enumerate(decoded_mixed) if d.get("images")
    ]
    req_zip_x = urllib.request.Request(
        f"{BASE_URL}/api/media/download-zip",
        data=json.dumps({"items": zip_items_x, "archiveName": "nasa_tweets_batch"}).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req_zip_x, timeout=30) as resp:
        zip_bytes_x = resp.read()
        assert resp.status == 200 or resp.status == 201
        log_success(f"2.2.3 Đóng gói ZIP các tweet thành công: {len(zip_bytes_x)} bytes.")

    # -------------------------------------------------------------------------
    # LẦN 3: Danh sách liên kết hỗn hợp đa nền tảng (Shorts + Reel + Bài viết)
    # -------------------------------------------------------------------------
    log_sub_test("LẦN 3: Danh sách liên kết hỗn hợp đa nền tảng (YouTube Shorts, Facebook Reel, Instagram)")
    multi_platform_links = [
        "https://www.youtube.com/shorts/kJQP7kiw5Fk",
        "https://www.facebook.com/reel/10153231379946729",
        "https://www.instagram.com/p/Dc3w6fvCdKk/",
    ]

    decoded_multi = []
    for idx, link in enumerate(multi_platform_links):
        t0 = time.time()
        status, res = api_post("/api/media/extract", {"url": link, "browser": "edge"})
        assert status == 200 or status == 201
        elapsed = time.time() - t0
        decoded_multi.append(res)
        is_reel = res.get("isReel")
        is_short = res.get("isShort")
        log_success(f"2.3.{idx+1} Giải mã Link {idx+1} ({res.get('platform').upper()}): isReel={is_reel}, isShort={is_short} ({elapsed:.2f}s) - Tiêu đề: {res.get('title')[:35]}")

    assert len(decoded_multi) == 3
    assert decoded_multi[0].get("isShort") is True, "Link 1 không nhận diện được Shorts"
    assert decoded_multi[1].get("isReel") is True, "Link 2 không nhận diện được Reel"
    log_success("2.3.4 Xác nhận nhận diện đúng định dạng Shorts và Reels trong danh sách liên kết đa nền tảng.")


# ==============================================================================
# ĐỀ MỤC 3: QUÉT & TẢI TOÀN BỘ NỘI DUNG THEO TÀI KHOẢN (BULK DOWNLOADER) - 3 LẦN
# ==============================================================================

def test_section_3():
    log_test_header("ĐỀ MỤC 3: QUÉT & TẢI TOÀN BỘ NỘI DUNG THEO TÀI KHOẢN (BULK DOWNLOADER) - 3 LẦN")

    # -------------------------------------------------------------------------
    # LẦN 1: Quét tài khoản Instagram (Profile URL & Username)
    # -------------------------------------------------------------------------
    log_sub_test("LẦN 1: Quét tài khoản Instagram (https://www.instagram.com/chosngi/)")

    t0 = time.time()
    status, res = api_post("/api/media/crawl-profile", {
        "url": "https://www.instagram.com/chosngi/",
        "limit": 5,
        "mediaType": "image",
        "browser": "edge"
    })
    elapsed = time.time() - t0
    assert status == 201 or status == 200, f"crawl-profile failed with {status}: {res}"
    assert res.get("platform") == "instagram"
    media_list = res.get("media") or []
    assert len(media_list) > 0, "Không có tệp media nào được quét"
    log_success(f"3.1.1 Quét thành công trang cá nhân Instagram trong {elapsed:.2f}s!")
    log_success(f"3.1.2 Thông tin người dùng: Tên = {res.get('name')}, Handle = {res.get('handle')}")
    log_success(f"3.1.3 Avatar URL = {res.get('avatar')[:65]}...")
    log_success(f"3.1.4 Số tệp đã thu thập = {len(media_list)} tệp (Kỳ vọng <= 5)")

    first = media_list[0]
    assert first.get("url"), "Thiếu url phương tiện"
    assert first.get("thumb"), "Thiếu thumbnail"
    assert first.get("quality"), "Thiếu thông tin kích thước/độ phân giải"
    log_success(f"3.1.5 Kiểm tra tệp 1: Title={first.get('title')[:30]}, Độ phân giải={first.get('quality')}, Type={first.get('type')}")

    # Đóng gói ZIP 3 tệp media quét được từ profile
    zip_items = [
        {"url": m["url"], "filename": f"{m.get('title', 'image')}.jpg"}
        for m in media_list[:3]
    ]
    req_zip = urllib.request.Request(
        f"{BASE_URL}/api/media/download-zip",
        data=json.dumps({"items": zip_items, "archiveName": "chosngi_profile_media"}).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req_zip, timeout=30) as resp:
        zip_bytes = resp.read()
        assert resp.status == 200 or resp.status == 201
        log_success(f"3.1.6 Đóng gói ZIP {len(zip_items)} tệp từ tài khoản Instagram thành công: {len(zip_bytes)} bytes.")

    # -------------------------------------------------------------------------
    # LẦN 2: Quét tài khoản X (Twitter) (@NASA)
    # -------------------------------------------------------------------------
    log_sub_test("LẦN 2: Quét tài khoản X (Twitter) (https://x.com/NASA)")

    t0 = time.time()
    status, res_x = api_post("/api/media/crawl-profile", {
        "url": "https://x.com/NASA",
        "limit": 5,
        "mediaType": "all",
        "browser": "edge"
    })
    elapsed = time.time() - t0
    assert status == 201 or status == 200, f"crawl-profile NASA failed with {status}: {res_x}"
    assert res_x.get("platform") == "twitter"
    media_x = res_x.get("media") or []
    assert len(media_x) > 0
    log_success(f"3.2.1 Quét thành công timeline X (@NASA) trong {elapsed:.2f}s!")
    log_success(f"3.2.2 Tên = {res_x.get('name')}, Handle = {res_x.get('handle')}, Avatar = {res_x.get('avatar')[:65]}...")
    log_success(f"3.2.3 Đã thu thập {len(media_x)} tệp đa phương tiện từ NASA.")

    first_x = media_x[0]
    assert first_x.get("url"), "Thiếu url"
    log_success(f"3.2.4 Tệp 1: Title={first_x.get('title')}, Chất lượng={first_x.get('quality')}, URL={first_x.get('url')[:60]}...")

    # Đóng gói ZIP các tệp media quét được từ profile X
    zip_items_x = [
        {"url": m["url"], "filename": f"nasa_{m.get('title', 'img')}.jpg"}
        for m in media_x[:3]
    ]
    req_zip_x = urllib.request.Request(
        f"{BASE_URL}/api/media/download-zip",
        data=json.dumps({"items": zip_items_x, "archiveName": "nasa_profile_media"}).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req_zip_x, timeout=30) as resp:
        zip_bytes_x = resp.read()
        assert resp.status == 200 or resp.status == 201
        log_success(f"3.2.5 Đóng gói ZIP các tệp quét từ NASA thành công: {len(zip_bytes_x)} bytes.")

    # -------------------------------------------------------------------------
    # LẦN 3: Quét tab Reels của Instagram (@instagram với mediaType=video)
    # -------------------------------------------------------------------------
    log_sub_test("LẦN 3: Quét tab Reels Instagram (@instagram với mediaType: video)")

    t0 = time.time()
    status, res_reels = api_post("/api/media/crawl-profile", {
        "url": "https://www.instagram.com/instagram",
        "limit": 3,
        "mediaType": "video",
        "platform": "instagram",
        "browser": "edge"
    })
    elapsed = time.time() - t0
    assert status == 200 or status == 201, f"crawl-profile Reels failed with {status}: {res_reels}"
    assert res_reels.get("platform") == "instagram"
    reels_list = res_reels.get("media") or []
    assert len(reels_list) > 0, "Không thu thập được video Reels nào từ Instagram"
    log_success(f"3.3.1 Quét thành công tab Reels Instagram (@instagram) trong {elapsed:.2f}s!")
    log_success(f"3.3.2 Thu thập thành công {len(reels_list)} video Reels.")

    first_reel = reels_list[0]
    assert first_reel.get("type") == "video", f"Kỳ vọng type=video nhưng nhận được {first_reel.get('type')}"
    assert first_reel.get("isReel") is True, f"Kỳ vọng isReel=True nhưng nhận được {first_reel.get('isReel')}"
    assert first_reel.get("url"), "Thiếu url video Reel"
    log_success(f"3.3.3 Video Reel 1: Type={first_reel.get('type')}, isReel=True, Title={first_reel.get('title')[:30]}, URL={first_reel.get('url')[:60]}...")

    # Đóng gói ZIP các video Reels quét được
    zip_items_reels = [
        {"url": m["url"], "filename": f"reel_{idx+1}.mp4"}
        for idx, m in enumerate(reels_list[:2])
    ]
    req_zip_reels = urllib.request.Request(
        f"{BASE_URL}/api/media/download-zip",
        data=json.dumps({"items": zip_items_reels, "archiveName": "instagram_reels_media"}).encode("utf-8"),
        headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req_zip_reels, timeout=30) as resp:
        zip_bytes_r = resp.read()
        assert resp.status == 200 or resp.status == 201
        log_success(f"3.3.4 Đóng gói ZIP {len(zip_items_reels)} video Reels thành công: {len(zip_bytes_r)} bytes.")

    # -------------------------------------------------------------------------
    # LẦN 4: Quét tài khoản TikTok (@mrbeast)
    # -------------------------------------------------------------------------
    log_sub_test("LẦN 4: Quét tài khoản TikTok (@mrbeast)")
    t0 = time.time()
    status, res_tt = api_post("/api/media/crawl-profile", {
        "url": "@mrbeast",
        "platform": "tiktok",
        "limit": 3
    })
    elapsed = time.time() - t0
    assert status == 200 or status == 201, f"TikTok crawl failed: {res_tt}"
    assert res_tt.get("platform") == "tiktok"
    tt_media = res_tt.get("media") or []
    assert len(tt_media) >= 1, "Không tìm thấy video TikTok nào"
    first_tt = tt_media[0]
    assert first_tt.get("url"), "Thiếu url video TikTok"
    assert first_tt.get("thumb"), "Thiếu thumbnail video TikTok"
    log_success(f"3.4.1 Quét thành công tài khoản TikTok @mrbeast trong {elapsed:.2f}s! ({len(tt_media)} videos)")
    log_success(f"3.4.2 Video 1: Title={first_tt.get('title')[:35]}, Quality={first_tt.get('quality')}")

    # 3.5 Kiểm tra lịch sử tải xuống và ghi nhận database
    status, history = api_post("/api/media/resolve-url", {"url": "https://x.com/NASA"})
    status, _, hist_data = api_get("/api/media/history", {"limit": 10}, read_bytes=False)
    hist_json = json.loads(hist_data)
    assert hist_json.get("total", 0) > 0
    log_success(f"3.5 Lịch sử tải xuống (Database PostgreSQL): Ghi nhận thành công {hist_json.get('total')} lượt tải gần nhất.")

def main():
    print("BẮT ĐẦU KIỂM THỬ PHẦN MỀM TOÀN DIỆN VỚI 3 ĐỀ MỤC - MỖI ĐỀ MỤC 3 LẦN")
    start_all = time.time()
    try:
        test_section_1()
        test_section_2()
        test_section_3()
        total_time = time.time() - start_all
        print("\n" + "=" * 75)
        print(f"  TẤT CẢ CÁC BÀI KIỂM THỬ TRÊN 3 ĐỀ MỤC (9 LẦN CHẠY) ĐỀU THÀNH CÔNG 100% ({total_time:.2f}s)")
        print("=" * 75)
    except AssertionError as e:
        import traceback
        print(f"\n❌ LỖI KIỂM THỬ: {e}")
        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ LỖI NGOẠI LỆ: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
