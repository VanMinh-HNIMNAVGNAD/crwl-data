# Social Media Crawler — Core Python Engine

The `core/` package provides a standalone, robust media extraction and crawler engine designed for desktop sidecar IPC and CLI operations.

## Architecture

- **`extractor_cli.py`**: CLI entry point and IPC sidecar worker. Supports `--json`, `--ipc-stream` (NDJSON line protocol for Tauri sidecar), and `--output` flags.
- **`dispatcher.py`**: Intelligent routing mechanism that resolves input URLs and dispatches them to the best-suited extractor.
- **`models.py`**: Standardized dataclasses (`MediaItem`, `ExtractionResult`, `JobStatus`) ensuring unified output across all extractors.
- **`resolver/`**:
  - `url_resolver.py`: Normalizes shortlinks (`vt.tiktok.com`, `t.co`, `youtu.be`, `pin.it`, `fb.watch`), expands `@username` profiles to canonical URLs, and strips tracking query parameters (`utm_*`, `si`, `igsh`, etc.).
- **`cookies/`**:
  - `browser_cookies.py`: Safely extracts decryption-free cookies directly from installed local Linux browsers (Chromium, Brave, Chrome, Edge, Firefox).
- **`extractors/`**:
  - `base.py`: Abstract base class defining common methods for extractors.
  - `ytdlp.py`: High-performance extractor wrapping `yt-dlp` for YouTube, Facebook, Twitter/X, SoundCloud, Twitch, etc.
  - `gallery.py`: Multi-image, carousel, and manga/album crawler wrapping `gallery-dl` (Instagram, Pinterest, Reddit, Pixiv, Bluesky).
  - `tiktok.py`: Optimized TikTok single and profile video metadata extractor.
  - `movie.py`: Direct video stream analyzer with HLS (`.m3u8`) and MPD manifest detection.
  - `direct.py`: Direct image and audio header inspection and URL handling.

## Usage

```bash
# Single URL extraction via CLI
python3 core/extractor_cli.py "https://www.youtube.com/watch?v=dQw4w9WgXcQ" --json

# Profile crawling
python3 core/extractor_cli.py "@username" --platform tiktok --type profile --json

# Running system tests
python3 scripts/test_desktop_system.py
```
