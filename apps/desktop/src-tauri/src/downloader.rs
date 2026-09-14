use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::Arc;
use log::info;
use regex::Regex;
use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter};
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::Command;

use crate::binary_manager::BinaryManager;
use crate::cookies::CookieService;
use crate::db::Database;

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct DownloadOptions {
    pub url: String,
    #[serde(alias = "formatId")]
    pub format_id: Option<String>,
    #[serde(default, alias = "isAudio")]
    pub is_audio: bool,
    #[serde(alias = "audioFormat")]
    pub audio_format: Option<String>,
    #[serde(alias = "audioBitrate")]
    pub audio_bitrate: Option<String>,
    pub title: Option<String>,
    #[serde(alias = "destDir")]
    pub dest_dir: Option<String>,
    pub browser: Option<String>,
    #[serde(default, alias = "deviceId")]
    pub device_id: String,
    #[serde(alias = "startTime")]
    pub start_time: Option<String>,
    #[serde(alias = "endTime")]
    pub end_time: Option<String>,
    #[serde(default, alias = "isMute")]
    pub is_mute: bool,
    #[serde(default, alias = "sponsorBlock")]
    pub sponsor_block: bool,
    #[serde(default, alias = "embedSubs")]
    pub embed_subs: bool,
    #[serde(default, alias = "embedThumbnail")]
    pub embed_thumbnail: bool,
    #[serde(default, alias = "embedMetadata")]
    pub embed_metadata: bool,
    #[serde(default, alias = "splitChapters")]
    pub split_chapters: bool,
    #[serde(alias = "concurrentFragments")]
    pub concurrent_fragments: Option<u32>,
    #[serde(alias = "proxy")]
    pub proxy: Option<String>,
    #[serde(alias = "videoFormat")]
    pub video_format: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DownloadProgressPayload {
    pub percent: f64,
    pub speed: String,
    pub eta: String,
    pub status: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DownloadResult {
    pub success: bool,
    pub file_path: Option<String>,
    pub file_name: Option<String>,
    pub message: String,
}

pub struct DownloaderService;

impl DownloaderService {
    /// Tìm vị trí binary yt-dlp
    pub fn find_ytdlp() -> Result<PathBuf, String> {
        let home = dirs::home_dir().unwrap_or_else(|| PathBuf::from("/tmp"));
        let candidates = vec![
            home.join(".local/bin/yt-dlp"),
            PathBuf::from("/usr/local/bin/yt-dlp"),
            PathBuf::from("/usr/bin/yt-dlp"),
            PathBuf::from("bin/yt-dlp"),
            PathBuf::from("yt-dlp"),
        ];

        for c in candidates {
            if c.exists() {
                return Ok(c);
            }
        }

        // Kiểm tra trong PATH
        if let Ok(path) = which::which("yt-dlp") {
            return Ok(path);
        }

        Ok(PathBuf::from("yt-dlp"))
    }

    /// Lấy thư mục tải mặc định
    pub fn get_default_download_dir() -> PathBuf {
        dirs::download_dir().unwrap_or_else(|| {
            dirs::home_dir()
                .map(|h| h.join("Downloads"))
                .unwrap_or_else(|| PathBuf::from("/tmp"))
        })
    }

    /// Mở hộp thoại chọn thư mục lưu (Native File Dialog)
    pub async fn select_directory() -> Option<String> {
        let default_dir = Self::get_default_download_dir();
        let dialog = rfd::AsyncFileDialog::new()
            .set_title("Chọn thư mục lưu tệp tải về")
            .set_directory(&default_dir);

        if let Some(folder) = dialog.pick_folder().await {
            let path_str = folder.path().to_string_lossy().to_string();
            info!("Người dùng đã chọn thư mục tải về: {path_str}");
            Some(path_str)
        } else {
            None
        }
    }

    /// Mở thư mục tải về bằng File Manager Linux (Nautilus, Dolphin, Thunar, ...)
    pub fn open_folder(path_str: &str) -> Result<(), String> {
        let path = Path::new(path_str);
        let target = if path.is_file() {
            path.parent().unwrap_or(path)
        } else {
            path
        };

        if !target.exists() {
            return Err(format!("Thư mục không tồn tại: {:?}", target));
        }

        info!("Đang mở thư mục trong File Manager Linux: {:?}", target);
        open::that(target).map_err(|e| format!("Không thể mở thư mục: {e}"))
    }

    /// Nhận diện platform từ URL để chọn đúng cookie file
    fn detect_platform_from_url(url: &str) -> Option<&'static str> {
        let lower = url.to_lowercase();
        if lower.contains("instagram.com") || lower.contains("instagr.am") {
            return Some("instagram");
        }
        if lower.contains("tiktok.com") {
            return Some("tiktok");
        }
        if lower.contains("facebook.com") || lower.contains("fb.watch") {
            return Some("facebook");
        }
        if lower.contains("twitter.com") || lower.contains("x.com") {
            return Some("twitter");
        }
        if lower.contains("youtube.com") || lower.contains("youtu.be") {
            return Some("youtube");
        }
        if lower.contains("pinterest.com") || lower.contains("pin.it") {
            return Some("pinterest");
        }
        if lower.contains("threads.net") {
            return Some("threads");
        }
        if lower.contains("linkedin.com") {
            return Some("linkedin");
        }
        None
    }

    /// Thực thi tiến trình tải xuống bằng yt-dlp và stream % tiến trình về UI
    pub async fn start_download(
        app_handle: AppHandle,
        db: Arc<Database>,
        opts: DownloadOptions,
    ) -> Result<DownloadResult, String> {
        let ytdlp_path = Self::find_ytdlp()?;
        let dest_folder = opts
            .dest_dir
            .as_ref()
            .map(PathBuf::from)
            .unwrap_or_else(Self::get_default_download_dir);

        if !dest_folder.exists() {
            let _ = tokio::fs::create_dir_all(&dest_folder).await;
        }

        // Định dạng tên file đầu ra: dest_folder/%(title)s [%(id)s].%(ext)s
        let output_template = dest_folder
            .join("%(title).100s [%(id)s].%(ext)s")
            .to_string_lossy()
            .to_string();

        let mut cmd = Command::new(&ytdlp_path);
        cmd.arg(&opts.url);
        cmd.arg("-o").arg(&output_template);
        cmd.arg("--no-playlist");
        cmd.arg("--newline");
        cmd.arg("--progress-template")
            .arg("download-progress:%(progress._percent_str)s|%(progress._speed_str)s|%(progress._eta_str)s");

        // Tự động dùng Node.js runtime cho YouTube n-sig challenge nếu có
        if let Some(node_path) = BinaryManager::find_binary("node") {
            cmd.arg("--js-runtimes").arg(format!("node:{}", node_path.to_string_lossy()));
        }

        // Tăng tốc tải đa luồng song song (-N 8)
        let frags = opts.concurrent_fragments.unwrap_or(8);
        cmd.arg("-N").arg(frags.to_string());

        // Sử dụng aria2c làm downloader ngoài nếu đã cài đặt
        if BinaryManager::has_binary("aria2c") {
            info!("Phát hiện aria2c: Kích hoạt bộ tải ngoài tăng tốc đa luồng");
            cmd.arg("--downloader").arg("aria2c");
            cmd.arg("--downloader-args").arg("aria2c:-s 16 -x 16 -k 1M -j 16");
        }

        // Cắt clip theo mốc thời gian (Trimmer)
        let normalize_time = |t: Option<&str>| -> Option<String> {
            let s = t?.trim();
            if s.is_empty() {
                return None;
            }
            let parts: Vec<&str> = s.split(':').collect();
            match parts.len() {
                1 => Some(s.to_string()),
                2 => Some(format!("00:{:0>2}:{:0>2}", parts[0], parts[1])),
                3 => Some(format!("{:0>2}:{:0>2}:{:0>2}", parts[0], parts[1], parts[2])),
                _ => Some(s.to_string()),
            }
        };

        let start_norm = normalize_time(opts.start_time.as_deref());
        let end_norm = normalize_time(opts.end_time.as_deref());
        if start_norm.is_some() || end_norm.is_some() {
            let sec = format!("*{}-{}", start_norm.unwrap_or_else(|| "00:00:00".to_string()), end_norm.unwrap_or_else(|| "inf".to_string()));
            cmd.arg("--download-sections").arg(sec);
        }

        // SponsorBlock — tự động bỏ qua intro/sponsor trên YouTube
        if opts.sponsor_block && (opts.url.contains("youtube.com") || opts.url.contains("youtu.be")) {
            cmd.arg("--sponsorblock-remove").arg("default");
        }

        // Proxy nếu được chỉ định
        if let Some(ref proxy) = opts.proxy {
            if !proxy.trim().is_empty() {
                cmd.arg("--proxy").arg(proxy.trim());
            }
        }

        // Tùy chọn định dạng Video hoặc Audio
        if opts.is_audio {
            cmd.arg("-x");
            let fmt = opts.audio_format.as_deref().unwrap_or("mp3");
            let safe_fmt = if fmt == "opus" {
                "opus"
            } else if fmt == "ogg" || fmt == "vorbis" {
                "vorbis"
            } else if fmt == "flac" {
                "flac"
            } else if fmt == "wav" {
                "wav"
            } else if fmt == "m4a" || fmt == "aac" {
                "m4a"
            } else if fmt == "alac" {
                "alac"
            } else {
                "mp3"
            };
            cmd.arg("--audio-format").arg(safe_fmt);
            let quality = match opts.audio_bitrate.as_deref() {
                Some("320") | Some("320k") | Some("320kbps") => "0",
                Some("256") | Some("256k") | Some("256kbps") => "2",
                Some("192") | Some("192k") | Some("192kbps") => "4",
                Some("128") | Some("128k") | Some("128kbps") => "6",
                _ => "0",
            };
            cmd.arg("--audio-quality").arg(quality);
            cmd.arg("--embed-metadata");
            cmd.arg("--embed-thumbnail");
        } else if opts.is_mute {
            // Tải video câm (chỉ luồng hình ảnh, không ghép audio)
            if let Some(ref fid) = opts.format_id {
                cmd.arg("-f").arg(fid);
            } else {
                cmd.arg("-f").arg("bestvideo/best");
            }
        } else if let Some(ref fid) = opts.format_id {
            if fid == "thumbnail" {
                // Tải ảnh bìa (thumbnail) cao nhất có thể
                cmd.arg("--write-thumbnail")
                    .arg("--skip-download")
                    .arg("--convert-thumbnails")
                    .arg("jpg");
            } else if fid.starts_with("subtitle:") {
                // Tải phụ đề: format = "subtitle:vi:vtt" -> lang=vi, ext=vtt
                let parts: Vec<&str> = fid.splitn(3, ':').collect();
                let lang = parts.get(1).unwrap_or(&"en");
                let sub_fmt = parts.get(2).unwrap_or(&"vtt");
                cmd.arg("--write-subs")
                    .arg("--sub-lang")
                    .arg(lang)
                    .arg("--sub-format")
                    .arg(sub_fmt)
                    .arg("--skip-download");
            } else if fid.starts_with("mp3") || fid.starts_with("m4a") || fid.starts_with("flac") || fid.starts_with("opus") || fid.starts_with("ogg") || fid.starts_with("wav") || fid.starts_with("alac") {
                cmd.arg("-x");
                let fmt = if fid.contains("flac") {
                    "flac"
                } else if fid.contains("opus") {
                    "opus"
                } else if fid.contains("ogg") {
                    "vorbis"
                } else if fid.contains("wav") {
                    "wav"
                } else if fid.contains("alac") {
                    "alac"
                } else if fid.contains("m4a") {
                    "m4a"
                } else {
                    "mp3"
                };
                cmd.arg("--audio-format").arg(fmt);
                cmd.arg("--embed-metadata");
                cmd.arg("--embed-thumbnail");
            } else if fid == "best" {
                cmd.arg("-f").arg("bestvideo+bestaudio/best");
            } else {
                cmd.arg("-f").arg(format!("{fid}+bestaudio/bestvideo+bestaudio/{fid}/best"));
            }
        } else {
            cmd.arg("-f").arg("bestvideo+bestaudio/best");
        }

        // Tùy chọn chuyển đổi Container Video (MKV, MOV, AVI, WEBM, MP4, GIF)
        if !opts.is_audio {
            if let Some(ref vfmt) = opts.video_format {
                let vf = vfmt.trim().to_lowercase();
                if vf == "gif" {
                    cmd.arg("--recode-video").arg("gif");
                } else if vf == "mkv" || vf == "mov" || vf == "avi" || vf == "webm" || vf == "mp4" {
                    cmd.arg("--remux-video").arg(&vf);
                }
            }
        }

        // Nhúng phụ đề vào video nếu được bật
        if opts.embed_subs && !opts.is_audio {
            cmd.arg("--embed-subs");
            cmd.arg("--sub-langs").arg("all");
        }

        // Nhúng thumbnail vào video/audio nếu bật
        if opts.embed_thumbnail && !opts.is_audio {
            cmd.arg("--embed-thumbnail");
        }

        // Nhúng metadata & chapters
        if opts.embed_metadata && !opts.is_audio {
            cmd.arg("--embed-metadata");
            cmd.arg("--embed-chapters");
        }

        // Tách theo chapter
        if opts.split_chapters {
            cmd.arg("--split-chapters");
        }

        // Cookies — ưu tiên: 1) file cookie đã lưu, 2) cookie-from-browser
        if let Some(ref b) = opts.browser {
            if !b.trim().is_empty() && b != "none" {
                // Thử detect platform từ URL để dùng đúng cookie file
                let platform = Self::detect_platform_from_url(&opts.url);
                let cookie_file = platform
                    .and_then(|p| CookieService::get_cookie_file_path(p));

                if let Some(ref cookie_path) = cookie_file {
                    info!("Dùng cookie file đã lưu: {:?}", cookie_path);
                    cmd.arg("--cookies").arg(cookie_path);
                } else {
                    cmd.arg("--cookies-from-browser").arg(b);
                }
            }
        }

        cmd.stdout(Stdio::piped()).stderr(Stdio::piped());

        info!("Bắt đầu tải tệp với yt-dlp: {}", opts.url);
        let mut child = cmd.spawn().map_err(|e| format!("Không thể khởi chạy yt-dlp: {e}"))?;

        let stdout = child.stdout.take().ok_or("Không thể đọc stdout của yt-dlp")?;
        let mut reader = BufReader::new(stdout).lines();

        let percent_regex = Regex::new(r"([\d\.]+)%").map_err(|e| e.to_string())?;
        let mut last_percent = 0.0;
        let mut downloaded_file_path: Option<String> = None;

        while let Ok(Some(line)) = reader.next_line().await {
            if line.starts_with("download-progress:") {
                let parts: Vec<&str> = line["download-progress:".len()..].split('|').collect();
                if !parts.is_empty() {
                    let percent_str = parts[0].trim();
                    let speed = parts.get(1).unwrap_or(&"").trim().to_string();
                    let eta = parts.get(2).unwrap_or(&"").trim().to_string();

                    let percent = if let Some(caps) = percent_regex.captures(percent_str) {
                        caps.get(1).and_then(|m| m.as_str().parse::<f64>().ok()).unwrap_or(last_percent)
                    } else {
                        last_percent
                    };

                    last_percent = percent;
                    let payload = DownloadProgressPayload {
                        percent,
                        speed,
                        eta,
                        status: "downloading".to_string(),
                    };
                    let _ = app_handle.emit("download-progress", payload);
                }
            } else if line.contains("[download] Destination:") || line.contains("[Merger] Merging formats into") || line.contains("[ExtractAudio] Destination:") {
                // Ghi nhận tên file video/audio
                if let Some(pos) = line.find(':') {
                    let path = line[pos + 1..].trim().trim_matches('"').to_string();
                    downloaded_file_path = Some(path);
                }
            } else if line.contains("Writing video thumbnail") || line.contains("Writing video subtitles to:") || line.contains("[Thumbnails] Writing thumbnail to:") {
                // Ghi nhận tên file thumbnail hoặc subtitle
                if let Some(pos) = line.rfind(':') {
                    let path = line[pos + 1..].trim().trim_matches('"').to_string();
                    if !path.is_empty() {
                        downloaded_file_path = Some(path);
                    }
                }
            }
        }

        let status = child.wait().await.map_err(|e| format!("Lỗi chờ yt-dlp: {e}"))?;

        if !status.success() {
            let err_msg = "Tiến trình tải thất bại qua yt-dlp".to_string();
            let _ = app_handle.emit(
                "download-progress",
                DownloadProgressPayload {
                    percent: last_percent,
                    speed: "".to_string(),
                    eta: "".to_string(),
                    status: "error".to_string(),
                },
            );

            // Ghi nhận lỗi vào DB
            db.record_download_history(
                &opts.device_id,
                opts.title.as_deref().unwrap_or("Untitled"),
                "failed_download",
                "auto",
                None,
                None,
                "failed",
                Some(&err_msg),
            ).await;

            return Err(err_msg);
        }

        // Báo cáo hoàn tất
        let _ = app_handle.emit(
            "download-progress",
            DownloadProgressPayload {
                percent: 100.0,
                speed: "".to_string(),
                eta: "00:00".to_string(),
                status: "completed".to_string(),
            },
        );

        let final_path = downloaded_file_path.unwrap_or_else(|| dest_folder.to_string_lossy().to_string());
        let file_name = Path::new(&final_path)
            .file_name()
            .map(|f| f.to_string_lossy().to_string())
            .unwrap_or_else(|| "media_download".to_string());

        // Ghi nhận lịch sử tải thành công vào database PostgreSQL
        db.record_download_history(
            &opts.device_id,
            opts.title.as_deref().unwrap_or(&file_name),
            &file_name,
            "auto",
            None,
            None,
            "success",
            None,
        ).await;

        Ok(DownloadResult {
            success: true,
            file_path: Some(final_path),
            file_name: Some(file_name),
            message: "Tải xuống thành công và lưu trực tiếp vào máy tính!".to_string(),
        })
    }

    /// Lấy Referer header chuẩn cho URL để chống chặn 403 Forbidden
    pub fn get_referer_for_url(url: &str, custom_referer: Option<&str>) -> String {
        if let Some(r) = custom_referer {
            if !r.trim().is_empty() {
                return r.to_string();
            }
        }
        let lower = url.to_lowercase();
        if lower.contains("instagram.com") || lower.contains("cdninstagram.com") {
            "https://www.instagram.com/".to_string()
        } else if lower.contains("pinterest.com") || lower.contains("pinimg.com") {
            "https://www.pinterest.com/".to_string()
        } else if lower.contains("twitter.com") || lower.contains("twimg.com") || lower.contains("x.com") {
            "https://x.com/".to_string()
        } else if lower.contains("pixiv.net") || lower.contains("pximg.net") {
            "https://www.pixiv.net/".to_string()
        } else if lower.contains("reddit.com") || lower.contains("redd.it") || lower.contains("redditmedia.com") {
            "https://www.reddit.com/".to_string()
        } else if lower.contains("weibo.com") || lower.contains("sinaimg.cn") {
            "https://weibo.com/".to_string()
        } else if lower.contains("tiktok.com") || lower.contains("tiktokcdn.com") {
            "https://www.tiktok.com/".to_string()
        } else if lower.contains("facebook.com") || lower.contains("fbcdn.net") {
            "https://www.facebook.com/".to_string()
        } else if lower.contains("threads.net") {
            "https://www.threads.net/".to_string()
        } else {
            "https://www.google.com/".to_string()
        }
    }

    /// Tải một tệp đơn lẻ trực tiếp (dùng curl kèm Referer và User-Agent)
    pub async fn download_direct_file(
        url: &str,
        file_name: Option<&str>,
        referer: Option<&str>,
        dest_dir: Option<&str>,
        device_id: &str,
        db: Arc<Database>,
    ) -> Result<DownloadResult, String> {
        let dest_folder = dest_dir
            .map(PathBuf::from)
            .unwrap_or_else(Self::get_default_download_dir);

        if !dest_folder.exists() {
            let _ = tokio::fs::create_dir_all(&dest_folder).await;
        }

        let clean_name = file_name
            .map(|f| f.replace(['/', '\\', '?', '%', '*', ':', '|', '"', '<', '>'], "_").trim().to_string())
            .filter(|f| !f.is_empty())
            .unwrap_or_else(|| format!("photo_{}.jpg", chrono::Utc::now().timestamp()));

        let target_path = dest_folder.join(&clean_name);
        let referer_header = Self::get_referer_for_url(url, referer);
        let user_agent = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36";

        info!("Đang tải tệp ảnh trực tiếp qua curl: {url} -> {:?}", target_path);
        let status = Command::new("curl")
            .arg("-s")
            .arg("-L")
            .arg("-f")
            .arg("--retry")
            .arg("2")
            .arg("-A")
            .arg(user_agent)
            .arg("-e")
            .arg(&referer_header)
            .arg(url)
            .arg("-o")
            .arg(&target_path)
            .status()
            .await
            .map_err(|e| format!("Không thể khởi chạy curl: {e}"))?;

        if !status.success() {
            return Err("Tải tệp ảnh trực tiếp thất bại qua curl".to_string());
        }

        let file_size = target_path.metadata().map(|m| m.len() as i64).ok();
        let path_str = target_path.to_string_lossy().to_string();

        db.record_download_history(
            device_id,
            &clean_name,
            &clean_name,
            "image",
            file_size,
            None,
            "success",
            None,
        ).await;

        Ok(DownloadResult {
            success: true,
            file_path: Some(path_str),
            file_name: Some(clean_name),
            message: "Tải tệp ảnh thành công!".to_string(),
        })
    }

    /// Tải album nhiều ảnh hoặc đóng gói thành file ZIP
    pub async fn download_album_batch(
        items: Vec<DirectFileItem>,
        album_name: Option<&str>,
        dest_dir: Option<&str>,
        as_zip: bool,
        device_id: &str,
        db: Arc<Database>,
    ) -> Result<DownloadResult, String> {
        let base_dest = dest_dir
            .map(PathBuf::from)
            .unwrap_or_else(Self::get_default_download_dir);

        let raw_title = album_name.unwrap_or("Album_Media");
        let clean_title = raw_title.replace(['/', '\\', '?', '%', '*', ':', '|', '"', '<', '>'], "_").trim().to_string();
        let target_dir = base_dest.join(&clean_title);

        if !target_dir.exists() {
            let _ = tokio::fs::create_dir_all(&target_dir).await;
        }

        let user_agent = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36";
        let mut downloaded_files = Vec::new();

        for (idx, item) in items.iter().enumerate() {
            let ext = if item.url.contains(".png") { "png" } else if item.url.contains(".webp") { "webp" } else { "jpg" };
            let raw_fname = item.filename.as_deref().unwrap_or("");
            let fname = if !raw_fname.is_empty() {
                raw_fname.replace(['/', '\\', '?', '%', '*', ':', '|', '"', '<', '>'], "_")
            } else {
                format!("{clean_title}_{}.{ext}", idx + 1)
            };

            let dest_file = target_dir.join(&fname);
            let referer_header = Self::get_referer_for_url(&item.url, item.referer.as_deref());

            let ok = Command::new("curl")
                .arg("-s")
                .arg("-L")
                .arg("-f")
                .arg("--retry")
                .arg("2")
                .arg("-A")
                .arg(user_agent)
                .arg("-e")
                .arg(&referer_header)
                .arg(&item.url)
                .arg("-o")
                .arg(&dest_file)
                .status()
                .await
                .map(|s| s.success())
                .unwrap_or(false);

            if ok {
                downloaded_files.push(dest_file);
            }
        }

        if downloaded_files.is_empty() {
            return Err("Không tải được tệp nào từ album".to_string());
        }

        if as_zip {
            let zip_filename = format!("{clean_title}.zip");
            let zip_path = base_dest.join(&zip_filename);
            let py_code = r#"
import sys, os, zipfile
zip_path = sys.argv[1]
src_dir = sys.argv[2]
with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_STORED) as zf:
    for f in os.listdir(src_dir):
        fp = os.path.join(src_dir, f)
        if os.path.isfile(fp):
            zf.write(fp, arcname=f)
"#;
            let zip_status = Command::new("python3")
                .arg("-c")
                .arg(py_code)
                .arg(&zip_path)
                .arg(&target_dir)
                .status()
                .await;

            if let Ok(st) = zip_status {
                if st.success() {
                    let zip_size = zip_path.metadata().map(|m| m.len() as i64).ok();
                    let zip_str = zip_path.to_string_lossy().to_string();

                    // Dọn dẹp thư mục nguồn sau khi đã nén vào ZIP để tránh nhân đôi dung lượng
                    let _ = tokio::fs::remove_dir_all(&target_dir).await;

                    db.record_download_history(
                        device_id,
                        &format!("Album ZIP: {clean_title}"),
                        &zip_filename,
                        "album",
                        zip_size,
                        None,
                        "success",
                        None,
                    ).await;

                    return Ok(DownloadResult {
                        success: true,
                        file_path: Some(zip_str),
                        file_name: Some(zip_filename),
                        message: format!("Đã đóng gói {} tệp vào file ZIP!", downloaded_files.len()),
                    });
                }
            }
        }

        let dir_str = target_dir.to_string_lossy().to_string();
        db.record_download_history(
            device_id,
            &format!("Album: {clean_title}"),
            &clean_title,
            "album",
            None,
            None,
            "success",
            None,
        ).await;

        Ok(DownloadResult {
            success: true,
            file_path: Some(dir_str),
            file_name: Some(clean_title),
            message: format!("Đã tải thành công {} tệp vào thư mục album!", downloaded_files.len()),
        })
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DirectFileItem {
    pub url: String,
    pub filename: Option<String>,
    pub referer: Option<String>,
}
