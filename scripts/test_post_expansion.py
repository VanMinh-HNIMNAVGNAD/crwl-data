#!/usr/bin/env python3
"""
Kiểm thử "Quét tài khoản phải lấy ĐỦ ảnh trong từng bài đăng".

Bối cảnh lỗi gốc: một bài đăng Instagram dạng carousel có nhiều ảnh, nhưng khi
quét theo tài khoản app chỉ nhận về ĐÚNG MỘT ảnh bìa (ảnh đại diện) của bài đó.
Hai nguyên nhân đã xác định:

  1. `--range 1-N` của gallery-dl đếm theo TỆP, không theo BÀI ĐĂNG, nên nó cắt
     ngang một bài đăng đang dở.
  2. Instagram qua GraphQL khi phiên đăng nhập không đầy đủ: bài carousel
     (`typename == "GraphSidecar"`) không kèm `edge_sidecar_to_children`, nên
     gallery-dl chỉ phát ra đúng ảnh bìa.

TEST #1 — Logic gom nhóm & phát hiện bài đăng bị thiếu tệp (chạy offline).
TEST #2 — Toàn bộ luồng crawl_profile: giới hạn theo bài đăng, bung bài thiếu,
          giữ nguyên thứ tự, gắn đúng metadata bài đăng (chạy offline, lặp 2 lần
          để chứng minh tính ổn định).
"""

import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.extractors.gallery import GalleryDlExtractor


def _file(url, **meta):
    return [3, url, meta]


def _ig_carousel_cover(shortcode):
    """Đúng payload Instagram trả về khi carousel bị rút gọn còn ảnh bìa."""
    return _file(
        f"https://cdn.instagram.com/{shortcode}_cover.jpg",
        category="instagram",
        typename="GraphSidecar",
        post_id=f"id_{shortcode}",
        post_shortcode=shortcode,
        post_url=f"https://www.instagram.com/p/{shortcode}/",
        num=1,
        count=1,
        extension="jpg",
        width=1080,
        height=1080,
    )


def _ig_full_post(shortcode, total):
    # Bài 1 ảnh là GraphImage; carousel (>= 2 ảnh) mới là GraphSidecar.
    typename = "GraphSidecar" if total > 1 else "GraphImage"
    return [
        _file(
            f"https://cdn.instagram.com/{shortcode}_{i}.jpg",
            category="instagram",
            typename=typename,
            post_id=f"id_{shortcode}",
            post_shortcode=shortcode,
            post_url=f"https://www.instagram.com/p/{shortcode}/",
            **({"sidecar_media_id": f"id_{shortcode}"} if total > 1 else {}),
            num=i,
            count=total,
            extension="jpg",
        )
        for i in range(1, total + 1)
    ]


# ─────────────────────────────────────────────────────────────────────────────
# TEST #1 — gom nhóm + phát hiện thiếu tệp
# ─────────────────────────────────────────────────────────────────────────────

def run_test_1() -> bool:
    print("=" * 70)
    print("  🚀 TEST #1 — Gom nhóm theo bài đăng & phát hiện bài bị thiếu tệp")
    print("=" * 70)
    g = GalleryDlExtractor()

    # 1.1 Gom nhóm giữ nguyên thứ tự, nhiều tệp cùng bài đăng về chung một nhóm
    entries = [
        [2, {"category": "instagram", "username": "nguoidung"}],
        *_ig_full_post("AAA", 3),
        *_ig_full_post("BBB", 1),
        *_ig_full_post("CCC", 2),
    ]
    groups = g._group_by_post(entries)
    assert len(groups) == 3, f"Phải gom thành 3 bài đăng, nhận {len(groups)}"
    assert [len(x["entries"]) for x in groups] == [3, 1, 2], "Sai số tệp mỗi bài"
    assert groups[0]["url"] == "https://www.instagram.com/p/AAA/", "Sai URL bài đăng"
    assert [x["entries"][0][2]["post_shortcode"] for x in groups] == ["AAA", "BBB", "CCC"], \
        "Thứ tự bài đăng phải giữ nguyên"
    print("  ✅ [PASS] Gom đúng 3 bài đăng (3+1+2 tệp), giữ nguyên thứ tự xuất hiện")

    # 1.2 Bài đầy đủ -> KHÔNG bị coi là thiếu
    assert g._is_incomplete_post(_ig_full_post("AAA", 3)) is False
    assert g._is_incomplete_post(_ig_full_post("BBB", 1)) is False
    print("  ✅ [PASS] Bài đăng đã đủ tệp không bị bung lại (không tốn request thừa)")

    # 1.3 --range cắt ngang bài đăng: count=5 nhưng chỉ nhận 2 tệp
    cut = _ig_full_post("DDD", 5)[:2]
    assert g._is_incomplete_post(cut) is True, "count > số tệp nhận được phải bị coi là thiếu"
    print("  ✅ [PASS] Phát hiện bài bị --range cắt ngang (khai báo 5 tệp, nhận 2)")

    # 1.4 Instagram GraphSidecar chỉ trả ảnh bìa (count=1, num=1) -> vẫn phải phát hiện
    assert g._is_incomplete_post([_ig_carousel_cover("EEE")]) is True, \
        "Carousel GraphSidecar chỉ có ảnh bìa phải bị coi là thiếu"
    print("  ✅ [PASS] Phát hiện carousel Instagram chỉ trả về 1 ảnh đại diện")

    # 1.5 Ảnh đơn thật sự (không phải carousel) KHÔNG được bung nhầm
    single = [_file(
        "https://cdn.instagram.com/single.jpg",
        category="instagram", typename="GraphImage",
        post_shortcode="FFF", post_url="https://www.instagram.com/p/FFF/",
        num=1, count=1, extension="jpg",
    )]
    assert g._is_incomplete_post(single) is False, "Ảnh đơn không được coi là thiếu"
    print("  ✅ [PASS] Bài đăng 1 ảnh thật sự không bị bung thừa")

    # 1.6 Dựng URL bài đăng cho các nền tảng khác nhau
    assert g._post_url_of({"category": "instagram", "post_shortcode": "XYZ"}) \
        == "https://www.instagram.com/p/XYZ/"
    assert g._post_url_of({
        "category": "bluesky",
        "uri": "at://did:plc:abc/app.bsky.feed.post/3kk",
        "author": {"handle": "ai.do"},
    }) == "https://bsky.app/profile/ai.do/post/3kk"
    assert g._post_url_of({
        "category": "twitter", "tweet_id": 123, "author": {"name": "someone"},
    }) == "https://x.com/someone/status/123"
    assert g._post_url_of({"category": "unknown"}) is None
    print("  ✅ [PASS] Dựng đúng URL bài đăng cho Instagram / Bluesky / X")

    # 1.7 Suy ra category để chọn cách giới hạn theo bài đăng
    assert g._category_of("https://www.instagram.com/abc/posts/") == "instagram"
    assert g._category_of("https://bsky.app/profile/a/media") == "bluesky"
    assert g._category_of("https://x.com/abc/media") == "twitter"
    assert "instagram" in GalleryDlExtractor._MAX_POSTS_CATEGORIES
    assert "bluesky" not in GalleryDlExtractor._MAX_POSTS_CATEGORIES
    print("  ✅ [PASS] Nhận diện nền tảng để chọn max-posts hay --range có headroom")

    print("\n  🎉 KẾT QUẢ TEST #1: ĐẠT\n")
    return True


# ─────────────────────────────────────────────────────────────────────────────
# TEST #2 — toàn bộ luồng crawl_profile
# ─────────────────────────────────────────────────────────────────────────────

def run_test_2(iteration: int) -> bool:
    print("=" * 70)
    print(f"  🚀 TEST #2.{iteration} — Luồng crawl_profile đầy đủ (giả lập gallery-dl)")
    print("=" * 70)

    g = GalleryDlExtractor()
    g.binary_path = "/bin/true"  # bỏ qua kiểm tra binary thật
    calls = {"profile": [], "posts": []}

    # Tài khoản: bài 1 là carousel 4 ảnh nhưng CHỈ trả về ảnh bìa (lỗi gốc),
    # bài 2 là ảnh đơn, bài 3 là carousel 3 ảnh bị --range cắt còn 1 tệp.
    profile_payload = [
        [2, {"category": "instagram", "username": "nguoidung", "avatar_url": "https://cdn/av.jpg"}],
        _ig_carousel_cover("POST1"),
        *_ig_full_post("POST2", 1),
        _ig_full_post("POST3", 3)[0],
        *_ig_full_post("POST4", 2),
    ]
    post_payloads = {
        "https://www.instagram.com/p/POST1/": _ig_full_post("POST1", 4),
        "https://www.instagram.com/p/POST3/": _ig_full_post("POST3", 3),
    }

    def fake_run_process(cmd, timeout=60, cwd=None, env=None):
        url = cmd[-1]
        if url in post_payloads:
            calls["posts"].append(url)
            return 0, json.dumps(post_payloads[url]), ""
        calls["profile"].append(list(cmd))
        return 0, json.dumps(profile_payload), ""

    g.run_process = fake_run_process

    res = g.crawl_profile(
        "https://www.instagram.com/nguoidung/posts/",
        limit=3, media_type="all", browser="none",
    )

    # a) Instagram hỗ trợ max-posts -> phải dùng max-posts, KHÔNG dùng --range
    cmd = calls["profile"][0]
    assert "--range" not in cmd, f"Instagram không được giới hạn theo tệp: {cmd}"
    assert "max-posts=3" in cmd, f"Phải giới hạn theo bài đăng: {cmd}"
    print("  ✅ [PASS] Instagram giới hạn bằng max-posts=3, không dùng --range theo tệp")

    # b) Chỉ bung đúng 2 bài thiếu tệp, không đụng bài đã đủ
    assert sorted(calls["posts"]) == sorted(post_payloads), f"Bung sai bài: {calls['posts']}"
    print(f"  ✅ [PASS] Chỉ bung đúng {len(calls['posts'])} bài đăng bị thiếu tệp")

    # c) Cắt đúng 3 bài đăng đầu tiên
    post_ids = []
    for m in res.media:
        if m.post_id not in post_ids:
            post_ids.append(m.post_id)
    assert post_ids == ["POST1", "POST2", "POST3"], f"Sai danh sách bài đăng: {post_ids}"
    print("  ✅ [PASS] Giữ đúng 3 bài đăng đầu (POST4 bị cắt theo yêu cầu)")

    # d) Tổng số tệp = 4 (POST1) + 1 (POST2) + 3 (POST3) = 8, thay vì 3 ảnh bìa
    assert len(res.media) == 8, f"Phải có 8 tệp sau khi bung, nhận {len(res.media)}"
    print("  ✅ [PASS] Nhận đủ 8 tệp (trước khi sửa chỉ được 3 ảnh đại diện)")

    # e) Thứ tự trong từng bài đăng được giữ nguyên & metadata đúng
    p1 = [m for m in res.media if m.post_id == "POST1"]
    assert len(p1) == 4
    assert [m.index_in_post for m in p1] == [1, 2, 3, 4], f"Sai thứ tự ảnh: {p1}"
    assert all(m.total_in_post == 4 for m in p1)
    assert all(m.post_url == "https://www.instagram.com/p/POST1/" for m in p1)
    assert len({m.url for m in res.media}) == 8, "Các tệp phải có URL khác nhau"
    assert len({m.id for m in res.media}) == 8, "ID hiển thị phải là duy nhất"
    print("  ✅ [PASS] Ảnh trong bài đánh số 1..4/4, kèm postUrl, URL & ID không trùng")

    # f) Ảnh đơn không bị gắn nhãn nhiều ảnh
    p2 = [m for m in res.media if m.post_id == "POST2"][0]
    assert p2.index_in_post is None and p2.total_in_post is None, "Ảnh đơn không được gắn n/m"
    print("  ✅ [PASS] Bài đăng 1 ảnh không bị gắn nhãn 'ảnh n/m'")

    # g) Serialize sang JSON cho giao diện
    d = res.to_dict()
    first = d["media"][0]
    assert first["postUrl"] == "https://www.instagram.com/p/POST1/"
    assert first["indexInPost"] == 1 and first["totalInPost"] == 4
    assert "bài đăng" in d["stats"], f"stats phải nêu số bài đăng: {d['stats']}"
    print(f"  ✅ [PASS] JSON gửi UI có postUrl/indexInPost/totalInPost — stats: '{d['stats']}'")

    # h) Lọc theo loại media vẫn hoạt động trên dữ liệu đã bung
    res_img = g.crawl_profile(
        "https://www.instagram.com/nguoidung/posts/",
        limit=3, media_type="image", browser="none",
    )
    assert len(res_img.media) == 8 and all(m.type == "image" for m in res_img.media)
    print("  ✅ [PASS] Bộ lọc 'Ảnh' giữ nguyên toàn bộ 8 ảnh đã bung")

    print(f"\n  🎉 KẾT QUẢ TEST #2.{iteration}: ĐẠT\n")
    return True


def main():
    ok1 = run_test_1()
    ok2 = run_test_2(1)
    ok3 = run_test_2(2)  # lặp lại trên instance sạch để chứng minh tính ổn định

    print("=" * 70)
    print("TỔNG KẾT:")
    print(f"TEST #1: {'PASS' if ok1 else 'FAIL'}")
    print(f"TEST #2: {'PASS' if ok2 and ok3 else 'FAIL'} (chạy lặp 2 lần đều đồng nhất)")
    print("=" * 70)
    if not (ok1 and ok2 and ok3):
        sys.exit(1)


if __name__ == "__main__":
    main()
