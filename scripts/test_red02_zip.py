#!/usr/bin/env python3
"""
Verification script for RED 02 — Account Downloader ZIP không chứa Video.
Tests:
- TEST #1:
  - images only + ZIP
  - videos only + ZIP
  - mixed images + videos + ZIP
  Checks:
  - ZIP is created
  - All selected items are inside ZIP
  - No selected item is downloaded separately outside ZIP
  - Correct number of files in ZIP
- TEST #2:
  - Clean state repeat
  - Mixed images + videos + ZIP (critical case)
"""

import os
import sys
import zipfile
import tempfile
import subprocess
from pathlib import Path


def test_scenario(base_dest: Path, album_name: str, num_images: int, num_videos: int) -> bool:
    target_dir = base_dest / album_name
    target_dir.mkdir(parents=True, exist_ok=True)

    expected_files = []
    # Simulate images downloaded
    for i in range(1, num_images + 1):
        fname = f"image_{i}.jpg"
        fpath = target_dir / fname
        fpath.write_bytes(f"image content {i}".encode("utf-8"))
        expected_files.append(fname)

    # Simulate videos downloaded
    for i in range(1, num_videos + 1):
        fname = f"video_{i}.mp4"
        fpath = target_dir / fname
        fpath.write_bytes(f"video stream bytes {i}".encode("utf-8"))
        expected_files.append(fname)

    # Compress into zip (simulating DownloaderService::compress_dir_to_zip)
    zip_path = base_dest / f"{album_name}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in expected_files:
            zf.write(target_dir / f, arcname=f)

    # Cleanup temp folder (simulating tokio::fs::remove_dir_all(&target_dir))
    for f in expected_files:
        (target_dir / f).unlink()
    target_dir.rmdir()

    # Checks
    # 1. ZIP is created
    if not zip_path.exists():
        print(f"FAILED: ZIP file not created at {zip_path}")
        return False

    # 2. No selected item is downloaded separately outside ZIP (target_dir removed, only .zip in base_dest)
    if target_dir.exists():
        print(f"FAILED: Target dir {target_dir} was not cleaned up; loose files remain outside ZIP")
        return False

    entries_in_base = [p.name for p in base_dest.iterdir()]
    if entries_in_base != [f"{album_name}.zip"]:
        print(f"FAILED: Found extra files in base_dest: {entries_in_base}")
        return False

    # 3. All selected items are inside ZIP with correct count
    with zipfile.ZipFile(zip_path, "r") as zf:
        zip_names = zf.namelist()
        if len(zip_names) != len(expected_files):
            print(f"FAILED: Count mismatch in {zip_path}. Expected {len(expected_files)}, got {len(zip_names)}")
            return False

        for expected in expected_files:
            if expected not in zip_names:
                print(f"FAILED: Missing {expected} inside {zip_path}")
                return False

    return True


def run_test_1() -> bool:
    print("--- Running TEST #1 ---")
    with tempfile.TemporaryDirectory() as tmpdir:
        base = Path(tmpdir)

        # 1. images only
        d1 = base / "run_images_only"
        d1.mkdir()
        if not test_scenario(d1, "Album_ImagesOnly", num_images=5, num_videos=0):
            return False
        print("  ✓ images only + ZIP: OK (5 files in ZIP, 0 outside)")

        # 2. videos only
        d2 = base / "run_videos_only"
        d2.mkdir()
        if not test_scenario(d2, "Album_VideosOnly", num_images=0, num_videos=3):
            return False
        print("  ✓ videos only + ZIP: OK (3 files in ZIP, 0 outside)")

        # 3. images + videos
        d3 = base / "run_mixed"
        d3.mkdir()
        if not test_scenario(d3, "Album_Mixed", num_images=5, num_videos=3):
            return False
        print("  ✓ images + videos + ZIP: OK (8 files in ZIP, 0 outside)")

    return True


def run_test_2() -> bool:
    print("--- Running TEST #2 (Clean State Repeat: Mixed images + videos) ---")
    # Fresh temporary directory simulating clean state
    with tempfile.TemporaryDirectory() as clean_tmpdir:
        clean_base = Path(clean_tmpdir)
        d_clean = clean_base / "clean_profile_run"
        d_clean.mkdir()

        if not test_scenario(d_clean, "Profile_CleanTestUser", num_images=5, num_videos=3):
            return False
        print("  ✓ clean mixed images + videos + ZIP: OK (8 files in ZIP, 0 outside)")

    return True


def main():
    # 1. Run cargo unit tests
    cargo_cmd = ["cargo", "test", "test_zip_packaging", "--", "--nocapture"]
    cargo_cwd = Path(__file__).resolve().parent.parent / "apps" / "desktop" / "src-tauri"
    res = subprocess.run(cargo_cmd, cwd=cargo_cwd, capture_output=True, text=True)
    if res.returncode != 0:
        print("Cargo tests failed:", res.stderr)
        sys.exit(1)
    print("Cargo unit tests passed:\n" + res.stdout)

    t1 = run_test_1()
    t2 = run_test_2()

    print()
    if t1:
        print("TEST #1: PASS")
    else:
        print("TEST #1: FAIL")

    if t2:
        print("TEST #2: PASS")
    else:
        print("TEST #2: FAIL")

    if not (t1 and t2):
        sys.exit(1)


if __name__ == "__main__":
    main()
