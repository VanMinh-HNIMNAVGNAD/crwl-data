#!/usr/bin/env python3
"""
yt-dlp zipapp phải chạy từ bản GIẢI NÉN có bytecode — và chỉ khi an toàn.

Bản yt-dlp chính thức cho Linux/macOS là zipapp: Python không lưu bytecode cho mã trong
tệp zip nên mỗi lần chạy biên dịch lại toàn bộ (đo: `--version` 0,75 s → 0,24 s, bóc
tách một video YouTube 3,2-3,7 s → 1,8-2,1 s khi chạy bản giải nén). Kiểm thử:

  1. zipapp được giải nén một lần, lệnh chạy là `<python> <thư mục>/__main__.py`.
  2. yt-dlp không phải zipapp (pip/pipx, script) → chạy như cũ, không tạo luồng nền thừa.
  3. Tệp yt-dlp đổi (sau `yt-dlp -U`) → bản giải nén cũ không được dùng.
  4. Có cấu hình / plugin "portable" cạnh tệp yt-dlp → không giải nén (bản giải nén
     sẽ không nạp chúng).
  5. Biết `--js-runtimes` từ mã nguồn đã giải nén, khỏi chạy `yt-dlp --help`.
"""

import os
import stat
import subprocess
import sys
import threading
import time
import zipapp

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from core.extractors import ytdlp_runtime
from core.extractors.ytdlp import YtDlpExtractor

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="zipapp không chạy trực tiếp trên Windows")


def _make_zipapp(tmp_path, main_body, name="yt-dlp", options="# --js-runtimes RUNTIME[:PATH]\n"):
    src = tmp_path / f"src-{name}-{time.time_ns()}"
    (src / "yt_dlp").mkdir(parents=True)
    (src / "__main__.py").write_text(main_body, encoding="utf-8")
    (src / "yt_dlp" / "__init__.py").write_text("", encoding="utf-8")
    (src / "yt_dlp" / "options.py").write_text(options, encoding="utf-8")
    target = tmp_path / "bin" / name
    target.parent.mkdir(exist_ok=True)
    zipapp.create_archive(str(src), str(target), interpreter="/usr/bin/env python3")
    target.chmod(target.stat().st_mode | stat.S_IXUSR)
    return str(target)


@pytest.fixture
def cache(tmp_path, monkeypatch):
    root = tmp_path / "cache"
    monkeypatch.setenv("CRWL_CACHE_DIR", str(root))
    return root


def test_zipapp_runs_from_the_unpacked_copy(tmp_path, cache):
    binary = _make_zipapp(tmp_path, "import sys\nimport yt_dlp  # noqa\nprint('ARGS', ' '.join(sys.argv[1:]))\n")
    runtime = ytdlp_runtime.prepare(binary)
    assert runtime is not None
    assert runtime.main.startswith(str(cache)) and runtime.main.endswith("__main__.py")
    assert ytdlp_runtime.command_for(binary) == runtime.command
    out = subprocess.run(runtime.command + ["--version"], capture_output=True, text=True).stdout
    assert out.strip() == "ARGS --version"
    assert runtime.js_runtimes
    # Bytecode đã được ghi cạnh mã → các lần sau không biên dịch lại
    assert any(name == "__pycache__" for _, dirs, _ in os.walk(os.path.dirname(runtime.main)) for name in dirs)


def test_plain_script_is_left_alone_without_thread_storm(tmp_path, cache, monkeypatch):
    script = tmp_path / "yt-dlp-script"
    script.write_text("#!/usr/bin/env python3\nprint('x')\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(ytdlp_runtime, "_background_enabled", True)
    assert ytdlp_runtime.prepare(str(script)) is None
    before = threading.active_count()
    for _ in range(20):
        assert ytdlp_runtime.command_for(str(script)) == [str(script)]
    assert threading.active_count() <= before, "không phải zipapp thì không được tạo luồng giải nén ở mỗi lần gọi"
    assert not cache.exists() or not any(p.name.startswith(".unpack-") for p in (cache / "ytdlp-unpacked").glob("*"))


def test_updated_binary_does_not_reuse_the_old_copy(tmp_path, cache):
    binary = _make_zipapp(tmp_path, "print('cu')\n")
    assert ytdlp_runtime.prepare(binary) is not None
    time.sleep(0.02)
    _make_zipapp(tmp_path, "print('moi')\n")  # ghi đè cùng đường dẫn, như `yt-dlp -U`
    assert ytdlp_runtime.lookup(binary) is None
    runtime = ytdlp_runtime.prepare(binary)
    out = subprocess.run(runtime.command, capture_output=True, text=True).stdout
    assert out.strip() == "moi"


def test_cleared_cache_falls_back_and_unpacks_again(tmp_path, cache, monkeypatch):
    """Dọn ~/.cache khi app đang chạy không được làm hỏng mọi lượt phân tích."""
    import shutil

    binary = _make_zipapp(tmp_path, "print('ok')\n")
    assert ytdlp_runtime.prepare(binary) is not None
    shutil.rmtree(cache)
    monkeypatch.setattr(ytdlp_runtime, "_background_enabled", True)
    assert ytdlp_runtime.command_for(binary) == [binary], "tệp giải nén đã mất → chạy tệp gốc"
    for _ in range(100):  # bản giải nén mới được dựng lại ở nền
        runtime = ytdlp_runtime.lookup(binary)
        if runtime is not None:
            break
        time.sleep(0.05)
    assert runtime is not None
    assert subprocess.run(runtime.command, capture_output=True, text=True).stdout.strip() == "ok"


def test_portable_config_next_to_binary_keeps_the_original(tmp_path, cache):
    binary = _make_zipapp(tmp_path, "print('x')\n")
    (tmp_path / "bin" / "yt-dlp.conf").write_text("--no-mtime\n", encoding="utf-8")
    assert ytdlp_runtime.prepare(binary) is None
    assert ytdlp_runtime.command_for(binary) == [binary]


def test_background_prepare_is_off_for_one_shot_cli(tmp_path, cache, monkeypatch):
    binary = _make_zipapp(tmp_path, "print('x')\n")
    monkeypatch.setattr(ytdlp_runtime, "_background_enabled", False)
    assert ytdlp_runtime.command_for(binary) == [binary]
    time.sleep(0.2)
    assert ytdlp_runtime.lookup(binary) is None, "CLI chạy một lần không được bắt đầu giải nén rồi bỏ dở"


def test_extractor_uses_unpacked_copy_and_skips_help(tmp_path, cache):
    marker = tmp_path / "help-called"
    payload = {"_type": "video", "id": "a", "title": "A", "formats": [
        {"format_id": "18", "url": "https://x/a.mp4", "ext": "mp4", "height": 360, "vcodec": "avc1", "acodec": "mp4a"}]}
    body = (
        "import sys, json\n"
        f"if '--help' in sys.argv:\n    open({str(marker)!r}, 'w').write('1'); print('--js-runtimes'); sys.exit(0)\n"
        f"print(json.dumps({payload!r}))\n"
    )
    binary = _make_zipapp(tmp_path, body)
    assert ytdlp_runtime.prepare(binary) is not None
    ext = YtDlpExtractor()
    ext.binary_path = binary
    assert ext.command()[1].endswith("__main__.py")
    assert ext.supports_js_runtimes()
    assert not marker.exists(), "đã biết từ mã nguồn giải nén — không chạy `--help`"
    meta = ext.extract_metadata("https://x/watch?v=a", browser="none")
    assert meta.title == "A"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
