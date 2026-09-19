use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::Arc;
use log::{info, warn};
use regex::Regex;
use serde::{Deserialize, Serialize};
use tauri::{AppHandle, Emitter};
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::Command;
use tokio::sync::Mutex as AsyncMutex;

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
    #[serde(alias = "referer")]
    pub referer: Option<String>,
    #[serde(alias = "videoFormat")]
    pub video_format: Option<String>,
    /// Mã định danh riêng của tác vụ tải — UI dùng để lọc đúng sự kiện tiến trình
    /// của mình khi có nhiều tệp tải song song.
    #[serde(default, alias = "taskId")]
    pub task_id: Option<String>,
    /// Bật bộ tải ngoài aria2c. Mặc định TẮT vì aria2c nuốt toàn bộ output tiến
    /// trình của yt-dlp, khiến thanh tiến trình đứng im suốt lúc tải.
    #[serde(default, alias = "useAria2c")]
    pub use_aria2c: bool,
    #[serde(alias = "clientIp")]
    pub client_ip: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct DownloadProgressPayload {
    /// Trùng với `task_id` của request — UI chỉ nhận sự kiện của tác vụ mình gửi.
    pub id: String,
    pub percent: f64,
    pub speed: String,
    pub eta: String,
    /// preparing | downloading | processing | completed | error
    pub status: String,
    /// Mô tả bước đang chạy để UI hiển thị đúng thay vì đoán theo phần trăm
    pub phase: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub file_path: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub message: Option<String>,
    /// Báo cho UI biết tiến trình không xác định chính xác % (ví dụ: đang tải qua aria2c, hậu xử lý)
    #[serde(default)]
    pub is_indeterminate: bool,
}

impl DownloadProgressPayload {
    fn new(id: &str, percent: f64, status: &str, phase: &str) -> Self {
        Self {
            id: id.to_string(),
            percent,
            speed: String::new(),
            eta: String::new(),
            status: status.to_string(),
            phase: phase.to_string(),
            file_path: None,
            message: None,
            is_indeterminate: false,
        }
    }

    pub fn indeterminate(id: &str, status: &str, phase: &str) -> Self {
        Self {
            id: id.to_string(),
            percent: 0.0,
            speed: String::new(),
            eta: String::new(),
            status: status.to_string(),
            phase: phase.to_string(),
            file_path: None,
            message: None,
            is_indeterminate: true,
        }
    }
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
        if lower.contains("facebook.com") || lower.contains("fb.watch") || lower.contains("fb.com") {
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
        if lower.contains("reddit.com") || lower.contains("redd.it") || lower.contains("v.redd.it") {
            return Some("reddit");
        }
        if lower.contains("threads.net") {
            return Some("threads");
        }
        if lower.contains("linkedin.com") {
            return Some("linkedin");
        }
        if lower.contains("bilibili.com") || lower.contains("b23.tv") {
            return Some("bilibili");
        }
        if lower.contains("soundcloud.com") {
            return Some("soundcloud");
        }
        if lower.contains("pixiv.net") || lower.contains("pixiv.me") {
            return Some("pixiv");
        }
        if lower.contains("douyin.com") {
            return Some("douyin");
        }
        if lower.contains("tumblr.com") {
            return Some("tumblr");
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
        cmd.arg("--progress-template").arg(
            "download-progress:%(progress._percent_str)s|%(progress._speed_str)s|%(progress._eta_str)s|%(info.format_id)s",
        );
        // In ra đường dẫn cuối cùng sau khi merge/post-process để UI luôn biết file đã lưu ở đâu.
        cmd.arg("--print").arg("after_move:filepath");
        // `--print` ngầm bật `--quiet`, khiến yt-dlp NUỐT toàn bộ dòng tiến trình và
        // dòng "[download] Destination:". Đó là lý do thanh tiến trình đứng im rồi
        // nhảy phịch lên 100%. Hai cờ dưới đây bật lại các dòng đó.
        cmd.arg("--no-quiet");
        cmd.arg("--progress");

        // Tự động dùng Node.js runtime cho YouTube n-sig challenge nếu có
        if let Some(node_path) = BinaryManager::find_binary("node") {
            cmd.arg("--js-runtimes").arg(format!("node:{}", node_path.to_string_lossy()));
        }

        // Tải song song nhiều mảnh (HLS/DASH) — giữ trong khoảng an toàn
        let frags = opts.concurrent_fragments.unwrap_or(8).clamp(1, 16);
        cmd.arg("-N").arg(frags.to_string());

        // aria2c chỉ bật khi người dùng yêu cầu: nó không xuất tiến trình theo
        // --progress-template, nên thanh tiến trình sẽ đứng im 0% tới lúc tải xong.
        if opts.use_aria2c {
            if BinaryManager::has_binary("aria2c") {
                info!("Bật bộ tải ngoài aria2c theo yêu cầu (thanh tiến trình sẽ không chi tiết)");
                cmd.arg("--downloader").arg("aria2c");
                cmd.arg("--downloader-args")
                    .arg("aria2c:-s 16 -x 16 -k 1M --summary-interval=1");
            } else {
                warn!("Đã yêu cầu aria2c nhưng chưa cài trên máy — dùng bộ tải mặc định");
            }
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

        if let Some(ref referer) = opts.referer {
            if !referer.trim().is_empty() {
                cmd.arg("--referer").arg(referer.trim());
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

        // Cookies — ưu tiên: 1) file cookie thủ công đã lưu, 2) cookie-from-browser
        {
            // Luôn thử dùng file cookie thủ công theo platform (bất kể browser có được chọn hay không)
            let platform = Self::detect_platform_from_url(&opts.url);
            let manual_cookie = platform.and_then(|p| CookieService::get_cookie_file_path(p));

            if let Some(ref cookie_path) = manual_cookie {
                info!("Dùng cookie file thủ công đã lưu: {:?}", cookie_path);
                cmd.arg("--cookies").arg(cookie_path);
            } else if let Some(ref b) = opts.browser {
                // Fallback: dùng cookie từ trình duyệt hệ thống nếu được chỉ định
                if !b.trim().is_empty() && b != "none" {
                    cmd.arg("--cookies-from-browser").arg(b);
                }
            }
        }

        cmd.stdout(Stdio::piped()).stderr(Stdio::piped());

        // Mã tác vụ: UI lọc sự kiện tiến trình theo mã này nên nhiều tệp tải
        // song song không còn ghi đè lên nhau trên cùng một thanh tiến trình.
        let task_id = opts
            .task_id
            .clone()
            .filter(|s| !s.trim().is_empty())
            .unwrap_or_else(|| uuid::Uuid::new_v4().to_string());

        info!("Bắt đầu tải tệp với yt-dlp [{task_id}]: {}", opts.url);
        let mut child = cmd.spawn().map_err(|e| format!("Không thể khởi chạy yt-dlp: {e}"))?;

        let stdout = child.stdout.take().ok_or("Không thể đọc stdout của yt-dlp")?;
        let stderr = child.stderr.take().ok_or("Không thể đọc stderr của yt-dlp")?;

        // stderr PHẢI được đọc song song. Trước đây ống stderr được mở nhưng không
        // ai đọc: yt-dlp ghi đầy bộ đệm ~64KB rồi bị chặn vĩnh viễn — đây là
        // nguyên nhân các luồng tải "đứng hình" và cuối cùng báo lỗi.
        let stderr_lines: Arc<AsyncMutex<Vec<String>>> = Arc::new(AsyncMutex::new(Vec::new()));
        let stderr_sink = Arc::clone(&stderr_lines);
        let stderr_task = tokio::spawn(async move {
            let mut lines = BufReader::new(stderr).lines();
            while let Ok(Some(line)) = lines.next_line().await {
                let trimmed = line.trim().to_string();
                if trimmed.is_empty() {
                    continue;
                }
                let mut buf = stderr_sink.lock().await;
                // Chỉ giữ phần cuối để thông báo lỗi ngắn gọn và không phình bộ nhớ
                if buf.len() >= 60 {
                    buf.remove(0);
                }
                buf.push(trimmed);
            }
        });

        let mut reader = BufReader::new(stdout).lines();
        let percent_regex = Regex::new(r"([\d\.]+)%").map_err(|e| e.to_string())?;

        // yt-dlp in "Downloading N format(s): 137+140" trước khi tải, nhờ đó biết
        // chính xác sẽ có mấy lượt tải để quy đổi ra phần trăm tổng thể.
        let formats_regex = Regex::new(r"Downloading \d+ format\(s\): (\S+)").map_err(|e| e.to_string())?;

        let progress_emit = |payload: DownloadProgressPayload| {
            let _ = app_handle.emit("download-progress", payload);
        };

        if opts.use_aria2c {
            progress_emit(DownloadProgressPayload::indeterminate(
                &task_id,
                "processing",
                "Đang tải qua Aria2c (không hiển thị %)...",
            ));
        } else {
            progress_emit(DownloadProgressPayload::new(
                &task_id,
                0.0,
                "preparing",
                "Đang lấy thông tin tệp...",
            ));
        }

        // Phần trăm chỉ đi tiến, không bao giờ lùi: trước đây mỗi luồng (video rồi
        // audio) đều chạy 0→100% nên thanh tiến trình tụt về 0 giữa chừng.
        let mut overall_percent = 0.0_f64;
        let mut expected_passes = 1usize;
        let mut pass_index = 0usize;
        let mut current_format: Option<String> = None;
        let mut downloaded_file_path: Option<String> = None;
        let mut printed_final_path: Option<String> = None;

        // Tối đa 96% dành cho giai đoạn tải, 4% còn lại cho ghép/hậu xử lý
        const DOWNLOAD_SHARE: f64 = 96.0;

        while let Ok(Some(line)) = reader.next_line().await {
            if let Some(rest) = line.strip_prefix("download-progress:") {
                let parts: Vec<&str> = rest.split('|').collect();
                let percent_str = parts.first().copied().unwrap_or("").trim();
                // yt-dlp trả "Unknown B/s" / "Unknown" lúc mới khởi động — đừng
                // hiển thị nguyên văn, để UI tự rơi về dấu "--".
                let clean = |v: &str| {
                    let t = v.trim();
                    if t.is_empty() || t.starts_with("Unknown") || t == "N/A" || t == "NA" {
                        String::new()
                    } else {
                        t.to_string()
                    }
                };
                let speed = clean(parts.get(1).copied().unwrap_or(""));
                let eta = clean(parts.get(2).copied().unwrap_or(""));
                let format_id = parts.get(3).copied().unwrap_or("").trim().to_string();

                // Đổi format_id nghĩa là yt-dlp đã chuyển sang lượt tải kế tiếp
                if !format_id.is_empty() && format_id != "NA" {
                    match current_format {
                        Some(ref f) if f == &format_id => {}
                        Some(_) => {
                            pass_index = (pass_index + 1).min(expected_passes.saturating_sub(1));
                            current_format = Some(format_id.clone());
                        }
                        None => current_format = Some(format_id.clone()),
                    }
                }

                let pass_percent = percent_regex
                    .captures(percent_str)
                    .and_then(|c| c.get(1))
                    .and_then(|m| m.as_str().parse::<f64>().ok())
                    .unwrap_or(0.0)
                    .clamp(0.0, 100.0);

                let raw = Self::weighted_percent(pass_index, expected_passes, pass_percent);
                overall_percent = overall_percent.max(raw).clamp(0.0, DOWNLOAD_SHARE);

                let phase = if expected_passes > 1 {
                    format!("Đang tải luồng {}/{}", pass_index + 1, expected_passes)
                } else {
                    "Đang tải dữ liệu".to_string()
                };

                progress_emit(DownloadProgressPayload {
                    id: task_id.clone(),
                    percent: overall_percent,
                    speed,
                    eta,
                    status: "downloading".to_string(),
                    phase,
                    file_path: None,
                    message: None,
                    is_indeterminate: false,
                });
                continue;
            }

            if let Some(n) = Self::parse_expected_passes(&formats_regex, &line) {
                expected_passes = n;
                pass_index = 0;
                continue;
            }

            if let Some(rest) = line.strip_prefix("[download] Destination: ") {
                downloaded_file_path = Some(rest.trim().trim_matches('"').to_string());
                continue;
            }

            if let Some(rest) = line.strip_prefix("[ExtractAudio] Destination: ") {
                downloaded_file_path = Some(rest.trim().trim_matches('"').to_string());
                continue;
            }

            if let Some(rest) = line.strip_prefix("[Merger] Merging formats into ") {
                downloaded_file_path = Some(rest.trim().trim_matches('"').to_string());
                overall_percent = overall_percent.max(DOWNLOAD_SHARE);
                progress_emit(DownloadProgressPayload {
                    id: task_id.clone(),
                    percent: overall_percent,
                    speed: String::new(),
                    eta: String::new(),
                    status: "processing".to_string(),
                    phase: "Đang ghép hình và tiếng qua FFmpeg...".to_string(),
                    file_path: None,
                    message: None,
                    is_indeterminate: opts.use_aria2c,
                });
                continue;
            }

            // Các bước hậu xử lý còn lại — báo đúng trạng thái thay vì đoán theo %
            if let Some(phase) = Self::postprocess_phase(&line) {
                overall_percent = overall_percent.max(DOWNLOAD_SHARE);
                progress_emit(DownloadProgressPayload {
                    id: task_id.clone(),
                    percent: overall_percent,
                    speed: String::new(),
                    eta: String::new(),
                    status: "processing".to_string(),
                    phase: phase.to_string(),
                    file_path: None,
                    message: None,
                    is_indeterminate: opts.use_aria2c,
                });
                continue;
            }

            if line.contains("Writing video thumbnail")
                || line.contains("Writing video subtitles to:")
                || line.contains("[Thumbnails] Writing thumbnail to:")
            {
                if let Some(pos) = line.rfind(": ") {
                    let path = line[pos + 2..].trim().trim_matches('"').to_string();
                    if !path.is_empty() {
                        downloaded_file_path = Some(path);
                    }
                }
                continue;
            }

            // Dòng trần còn lại là kết quả của `--print after_move:filepath`,
            // tức đường dẫn cuối cùng sau khi đã merge/remux xong.
            let candidate = line.trim().trim_matches('"');
            if !candidate.is_empty() && Path::new(candidate).is_absolute() {
                printed_final_path = Some(candidate.to_string());
            }
        }

        let status = child.wait().await.map_err(|e| format!("Lỗi chờ yt-dlp: {e}"))?;
        let _ = stderr_task.await;
        let collected_stderr = stderr_lines.lock().await.clone();

        if !status.success() {
            // Báo đúng nguyên nhân từ yt-dlp thay vì một câu chung chung
            let detail = Self::summarize_ytdlp_error(&collected_stderr);
            let err_msg = if detail.is_empty() {
                format!("Tải thất bại (yt-dlp kết thúc với mã {})", status.code().unwrap_or(-1))
            } else {
                detail
            };

            progress_emit(DownloadProgressPayload {
                id: task_id.clone(),
                percent: overall_percent,
                speed: String::new(),
                eta: String::new(),
                status: "error".to_string(),
                phase: "Tải thất bại".to_string(),
                file_path: None,
                message: Some(err_msg.clone()),
                is_indeterminate: false,
            });

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
                opts.client_ip.as_deref(),
            ).await;

            return Err(err_msg);
        }

        // `after_move:filepath` là đường dẫn chuẩn nhất; chỉ lùi về tên file tạm
        // khi yt-dlp không in ra (ví dụ khi dùng --skip-download).
        let final_path = printed_final_path
            .or(downloaded_file_path)
            .unwrap_or_else(|| dest_folder.to_string_lossy().to_string());
        let file_name = Path::new(&final_path)
            .file_name()
            .map(|f| f.to_string_lossy().to_string())
            .unwrap_or_else(|| "media_download".to_string());
        let file_size = std::fs::metadata(&final_path).map(|m| m.len() as i64).ok();

        // Chỉ báo hoàn tất sau khi tiến trình đã kết thúc thành công và có đường dẫn
        progress_emit(DownloadProgressPayload {
            id: task_id.clone(),
            percent: 100.0,
            speed: String::new(),
            eta: String::new(),
            status: "completed".to_string(),
            phase: "Hoàn tất".to_string(),
            file_path: Some(final_path.clone()),
            message: None,
            is_indeterminate: false,
        });

        // Ghi nhận lịch sử tải thành công vào database PostgreSQL
        db.record_download_history(
            &opts.device_id,
            opts.title.as_deref().unwrap_or(&file_name),
            &file_name,
            "auto",
            file_size,
            None,
            "success",
            None,
            opts.client_ip.as_deref(),
        ).await;

        Ok(DownloadResult {
            success: true,
            file_path: Some(final_path),
            file_name: Some(file_name),
            message: "Tải xuống thành công và lưu trực tiếp vào máy tính!".to_string(),
        })
    }

    /// Đọc dòng `[info] ...: Downloading N format(s): 137+140` để biết yt-dlp sẽ
    /// chạy mấy lượt tải. Nhờ đó phần trăm tổng thể phản ánh đúng thực tế thay vì
    /// đoán mò từ tuỳ chọn định dạng.
    fn parse_expected_passes(re: &Regex, line: &str) -> Option<usize> {
        let spec = re.captures(line)?.get(1)?.as_str();
        Some(spec.split('+').filter(|s| !s.is_empty()).count().max(1))
    }

    /// Quy đổi phần trăm của một lượt tải thành phần trăm tổng thể.
    ///
    /// yt-dlp tải video rồi tải audio, mỗi lượt chạy 0→100%. Nếu đưa thẳng lên UI
    /// thì thanh tiến trình đầy rồi tụt về 0. Hàm này trải các lượt lên một dải
    /// chung 0..DOWNLOAD_SHARE, phần còn lại dành cho bước ghép tệp.
    fn weighted_percent(pass_index: usize, expected_passes: usize, pass_percent: f64) -> f64 {
        const DOWNLOAD_SHARE: f64 = 96.0;
        let passes = expected_passes.max(1) as f64;
        let index = (pass_index as f64).min(passes - 1.0);
        let fraction = pass_percent.clamp(0.0, 100.0) / 100.0;
        ((index + fraction) / passes * DOWNLOAD_SHARE).clamp(0.0, DOWNLOAD_SHARE)
    }

    /// Nhận diện bước hậu xử lý của yt-dlp để UI hiển thị đúng việc đang chạy
    fn postprocess_phase(line: &str) -> Option<&'static str> {
        const PHASES: &[(&str, &str)] = &[
            ("[ExtractAudio]", "Đang tách âm thanh..."),
            ("[VideoConvertor]", "Đang chuyển đổi định dạng video..."),
            ("[VideoRemuxer]", "Đang đóng gói lại container..."),
            ("[EmbedThumbnail]", "Đang nhúng ảnh bìa..."),
            ("[Metadata]", "Đang ghi metadata..."),
            ("[EmbedSubtitle]", "Đang nhúng phụ đề..."),
            ("[SponsorBlock]", "Đang xử lý SponsorBlock..."),
            ("[ModifyChapters]", "Đang cắt bỏ đoạn được đánh dấu..."),
            ("[SplitChapters]", "Đang tách theo chương..."),
            ("[FixupM3u8]", "Đang sửa luồng M3U8..."),
            ("[FixupM4a]", "Đang sửa container M4A..."),
            ("[Fixup", "Đang sửa lỗi container..."),
        ];
        PHASES
            .iter()
            .find(|(prefix, _)| line.starts_with(prefix))
            .map(|(_, phase)| *phase)
    }

    /// Rút gọn stderr của yt-dlp thành một thông báo lỗi người dùng hiểu được
    fn summarize_ytdlp_error(stderr_lines: &[String]) -> String {
        let error_line = stderr_lines
            .iter()
            .rev()
            .find(|l| l.starts_with("ERROR:") || l.contains("ERROR:"))
            .or_else(|| stderr_lines.last());

        let raw = match error_line {
            Some(l) => l.trim_start_matches("ERROR:").trim().to_string(),
            None => return String::new(),
        };

        let lower = raw.to_lowercase();
        if lower.contains("login") || lower.contains("sign in") || lower.contains("cookies")
            || lower.contains("private") || lower.contains("403") || lower.contains("401")
        {
            return format!(
                "Nội dung yêu cầu đăng nhập hoặc bị chặn. Hãy mở Cookie Manager (🍪) \
                 để lưu cookie cho nền tảng này rồi thử lại. (Chi tiết: {raw})"
            );
        }
        if lower.contains("ffmpeg") {
            return format!("Thiếu hoặc lỗi ffmpeg khi ghép tệp. (Chi tiết: {raw})");
        }
        if lower.contains("unsupported url") || lower.contains("no video formats") {
            return format!("Liên kết này không có luồng tải được. (Chi tiết: {raw})");
        }
        if raw.chars().count() > 300 {
            return format!("{}...", raw.chars().take(300).collect::<String>());
        }
        raw
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
        client_ip: Option<&str>,
        db: Arc<Database>,
    ) -> Result<DownloadResult, String> {
        let dest_folder = dest_dir
            .map(PathBuf::from)
            .unwrap_or_else(Self::get_default_download_dir);

        if !dest_folder.exists() {
            let _ = tokio::fs::create_dir_all(&dest_folder).await;
        }

        let ext = Self::guess_extension(url);
        let clean_name = file_name
            .map(|f| Self::sanitize_file_name(f, ""))
            .filter(|f| !f.is_empty())
            .map(|f| {
                if Path::new(&f).extension().is_some() {
                    f
                } else {
                    format!("{f}.{ext}")
                }
            })
            .unwrap_or_else(|| format!("media_{}.{ext}", chrono::Utc::now().timestamp()));

        let target_path = dest_folder.join(&clean_name);
        let referer_header = Self::get_referer_for_url(url, referer);

        info!("Đang tải tệp trực tiếp qua curl: {url} -> {:?}", target_path);
        if !Self::curl_to_file(url, &referer_header, &target_path).await {
            return Err(
                "Tải tệp thất bại — liên kết có thể đã hết hạn hoặc bị chặn. Hãy quét lại rồi thử."
                    .to_string(),
            );
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
            client_ip,
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
        app_handle: AppHandle,
        items: Vec<DirectFileItem>,
        album_name: Option<&str>,
        dest_dir: Option<&str>,
        as_zip: bool,
        device_id: &str,
        task_id: Option<String>,
        client_ip: Option<&str>,
        db: Arc<Database>,
    ) -> Result<DownloadResult, String> {
        let base_dest = dest_dir
            .map(PathBuf::from)
            .unwrap_or_else(Self::get_default_download_dir);

        let raw_title = album_name.unwrap_or("Album_Media");
        let clean_title = Self::sanitize_file_name(raw_title, "Album_Media");
        let target_dir = base_dest.join(&clean_title);

        if !target_dir.exists() {
            tokio::fs::create_dir_all(&target_dir)
                .await
                .map_err(|e| format!("Không tạo được thư mục album: {e}"))?;
        }

        let task_id = task_id
            .filter(|s| !s.trim().is_empty())
            .unwrap_or_else(|| uuid::Uuid::new_v4().to_string());

        let total = items.len();
        if total == 0 {
            return Err("Danh sách tệp cần tải đang trống".to_string());
        }

        let emit = |percent: f64, status: &str, phase: String, file_path: Option<String>| {
            let _ = app_handle.emit(
                "download-progress",
                DownloadProgressPayload {
                    id: task_id.clone(),
                    percent,
                    speed: String::new(),
                    eta: String::new(),
                    status: status.to_string(),
                    phase,
                    file_path,
                    message: None,
                    is_indeterminate: false,
                },
            );
        };

        emit(0.0, "downloading", format!("Chuẩn bị tải {total} tệp..."), None);

        // Tải song song có giới hạn. Trước đây các tệp tải tuần tự nên một album
        // vài chục ảnh mất rất lâu và không hề có phản hồi tiến trình.
        const MAX_PARALLEL: usize = 5;
        let semaphore = Arc::new(tokio::sync::Semaphore::new(MAX_PARALLEL));
        let completed = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let mut handles = Vec::with_capacity(total);

        for (idx, item) in items.into_iter().enumerate() {
            let permit_src = Arc::clone(&semaphore);
            let counter = Arc::clone(&completed);
            let dir = target_dir.clone();
            let album = clean_title.clone();

            handles.push(tokio::spawn(async move {
                let _permit = match permit_src.acquire_owned().await {
                    Ok(p) => p,
                    Err(_) => return None,
                };

                let ext = Self::guess_extension(&item.url);
                let raw_fname = item.filename.as_deref().unwrap_or("");
                let fname = if raw_fname.trim().is_empty() {
                    format!("{album}_{:03}.{ext}", idx + 1)
                } else {
                    let safe = Self::sanitize_file_name(raw_fname, &format!("media_{}", idx + 1));
                    // Chỉ bổ sung đuôi khi tên chưa có — không nối chồng thành "anh.jpg.heic"
                    if Path::new(&safe).extension().is_some() {
                        safe
                    } else {
                        format!("{safe}.{ext}")
                    }
                };

                let dest_file = dir.join(&fname);
                let referer_header = Self::get_referer_for_url(&item.url, item.referer.as_deref());

                let ok = Self::curl_to_file(&item.url, &referer_header, &dest_file).await;
                counter.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                if ok {
                    Some(dest_file)
                } else {
                    warn!("Không tải được tệp album: {}", item.url);
                    None
                }
            }));
        }

        let mut downloaded_files = Vec::new();
        for handle in handles {
            if let Ok(Some(path)) = handle.await {
                downloaded_files.push(path);
            }
            let done = completed.load(std::sync::atomic::Ordering::SeqCst);
            let percent = (done as f64 / total as f64) * if as_zip { 90.0 } else { 100.0 };
            emit(
                percent,
                "downloading",
                format!("Đã tải {done}/{total} tệp"),
                None,
            );
        }

        if downloaded_files.is_empty() {
            emit(0.0, "error", "Không tải được tệp nào".to_string(), None);
            return Err(format!(
                "Không tải được tệp nào trong {total} tệp. Liên kết có thể đã hết hạn — hãy quét lại tài khoản."
            ));
        }

        let failed = total - downloaded_files.len();

        if as_zip {
            emit(93.0, "processing", "Đang nén thành tệp ZIP...".to_string(), None);
            let zip_filename = format!("{clean_title}.zip");
            let zip_path = base_dest.join(&zip_filename);

            let target_dir_clone = target_dir.clone();
            let zip_path_clone = zip_path.clone();

            let zip_status = tokio::task::spawn_blocking(move || {
                let res = Self::compress_dir_to_zip(&target_dir_clone, &zip_path_clone);
                if res.is_err() {
                    let _ = std::fs::remove_file(&zip_path_clone);
                }
                res
            })
            .await;

            match zip_status {
                Ok(Ok(())) => {
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
                        client_ip,
                    ).await;

                    emit(100.0, "completed", "Hoàn tất".to_string(), Some(zip_str.clone()));

                    return Ok(DownloadResult {
                        success: true,
                        file_path: Some(zip_str),
                        file_name: Some(zip_filename),
                        message: Self::batch_summary(downloaded_files.len(), failed, "vào tệp ZIP"),
                    });
                }
                Ok(Err(err)) => {
                    warn!("Nén ZIP thất bại: {err} — giữ nguyên thư mục ảnh đã tải");
                }
                Err(join_err) => {
                    warn!("Tiến trình nén ZIP bị gián đoạn: {join_err} — giữ nguyên thư mục ảnh đã tải");
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
            client_ip,
        ).await;

        emit(100.0, "completed", "Hoàn tất".to_string(), Some(dir_str.clone()));

        Ok(DownloadResult {
            success: true,
            file_path: Some(dir_str),
            file_name: Some(clean_title),
            message: Self::batch_summary(downloaded_files.len(), failed, "vào thư mục album"),
        })
    }

    fn batch_summary(ok: usize, failed: usize, where_to: &str) -> String {
        if failed > 0 {
            format!("Đã tải {ok} tệp {where_to}, {failed} tệp thất bại.")
        } else {
            format!("Đã tải thành công {ok} tệp {where_to}!")
        }
    }

    /// Loại bỏ ký tự không hợp lệ trong tên tệp trên Linux
    fn sanitize_file_name(raw: &str, fallback: &str) -> String {
        let cleaned: String = raw
            .replace(['/', '\\', '?', '%', '*', ':', '|', '"', '<', '>', '\n', '\r', '\t'], "_")
            .trim()
            .trim_matches('.')
            .chars()
            .take(120)
            .collect();
        if cleaned.is_empty() {
            fallback.to_string()
        } else {
            cleaned
        }
    }

    /// Đoán phần mở rộng THẬT của tệp sẽ nhận được.
    ///
    /// CDN của Instagram/Facebook hay phục vụ URL đuôi `.heic` nhưng kèm tham số
    /// `stp=dst-jpg`, và thứ trả về là JPEG. Đặt tên theo đuôi URL sẽ tạo ra tệp
    /// `.heic` mà máy không mở được, nên phải ưu tiên chỉ dẫn `dst-<fmt>`.
    fn guess_extension(url: &str) -> &'static str {
        let lower = url.to_lowercase();
        let mut split = lower.splitn(2, '?');
        let path = split.next().unwrap_or("");
        let query = split.next().unwrap_or("");

        for (marker, ext) in [("dst-jpg", "jpg"), ("dst-png", "png"), ("dst-webp", "webp")] {
            if query.contains(marker) {
                return ext;
            }
        }

        const KNOWN: &[(&str, &str)] = &[
            (".mp4", "mp4"), (".webm", "webm"), (".mov", "mov"), (".m4v", "m4v"),
            (".mp3", "mp3"), (".m4a", "m4a"), (".png", "png"), (".webp", "webp"),
            (".gif", "gif"), (".heic", "heic"), (".jpeg", "jpg"), (".jpg", "jpg"),
        ];
        KNOWN
            .iter()
            .find(|(suffix, _)| path.ends_with(suffix))
            .map(|(_, ext)| *ext)
            .unwrap_or("jpg")
    }

    /// Tải 1 URL về đúng đường dẫn bằng curl, trả về true nếu tệp có nội dung
    async fn curl_to_file(url: &str, referer: &str, dest: &Path) -> bool {
        const USER_AGENT: &str = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36";
        let ok = Command::new("curl")
            .arg("-sSL")
            .arg("-f")
            .arg("--retry").arg("2")
            .arg("--retry-delay").arg("1")
            .arg("--connect-timeout").arg("20")
            .arg("--max-time").arg("300")
            .arg("-A").arg(USER_AGENT)
            .arg("-e").arg(referer)
            .arg(url)
            .arg("-o").arg(dest)
            .status()
            .await
            .map(|s| s.success())
            .unwrap_or(false);

        // curl -f vẫn có thể để lại tệp 0 byte khi kết nối đứt giữa chừng
        if ok && std::fs::metadata(dest).map(|m| m.len() > 0).unwrap_or(false) {
            true
        } else {
            let _ = std::fs::remove_file(dest);
            false
        }
    }

    /// Nén toàn bộ tệp và thư mục con trong `src_dir` thành tệp ZIP tại `zip_path` bằng crate native `zip`
    pub fn compress_dir_to_zip(src_dir: &Path, zip_path: &Path) -> Result<(), String> {
        let file = std::fs::File::create(zip_path)
            .map_err(|e| format!("Không thể tạo tệp zip: {e}"))?;
        let mut zip = zip::ZipWriter::new(std::io::BufWriter::new(file));
        let options = zip::write::SimpleFileOptions::default()
            .compression_method(zip::CompressionMethod::Deflated);

        let walker = walkdir::WalkDir::new(src_dir);
        for entry in walker.into_iter().filter_map(|e| e.ok()) {
            let path = entry.path();
            if path == src_dir {
                continue;
            }

            let relative_path = path
                .strip_prefix(src_dir)
                .map_err(|e| format!("Lỗi xác định đường dẫn tương đối: {e}"))?;

            let path_str = relative_path.to_string_lossy().replace('\\', "/");
            if path.is_dir() {
                zip.add_directory(&path_str, options)
                    .map_err(|e| format!("Lỗi thêm thư mục vào zip: {e}"))?;
            } else if path.is_file() {
                zip.start_file(&path_str, options)
                    .map_err(|e| format!("Lỗi tạo mục tệp trong zip: {e}"))?;
                let mut f = std::fs::File::open(path)
                    .map_err(|e| format!("Không thể mở tệp {}: {e}", path.display()))?;
                std::io::copy(&mut f, &mut zip)
                    .map_err(|e| format!("Lỗi ghi dữ liệu vào zip: {e}"))?;
            }
        }

        let mut writer = zip.finish()
            .map_err(|e| format!("Lỗi hoàn tất tệp zip: {e}"))?;
        std::io::Write::flush(&mut writer)
            .map_err(|e| format!("Lỗi lưu tệp zip: {e}"))?;

        Ok(())
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DirectFileItem {
    pub url: String,
    pub filename: Option<String>,
    pub referer: Option<String>,
}

#[cfg(test)]
mod tests {
    use super::DownloaderService as D;

    #[test]
    fn single_pass_maps_to_full_download_share() {
        assert_eq!(D::weighted_percent(0, 1, 0.0), 0.0);
        assert_eq!(D::weighted_percent(0, 1, 50.0), 48.0);
        assert_eq!(D::weighted_percent(0, 1, 100.0), 96.0);
    }

    #[test]
    fn two_passes_never_reach_full_before_the_second_one() {
        // Lượt 1 xong chỉ mới là nửa đường — không được nhảy lên 100%
        assert_eq!(D::weighted_percent(0, 2, 100.0), 48.0);
        assert_eq!(D::weighted_percent(1, 2, 0.0), 48.0);
        assert_eq!(D::weighted_percent(1, 2, 100.0), 96.0);
    }

    #[test]
    fn progress_is_monotonic_across_a_two_pass_download() {
        let script = [(0, 0.0), (0, 40.0), (0, 100.0), (1, 0.0), (1, 30.0), (1, 100.0)];
        let mut last = 0.0;
        for (pass, pct) in script {
            let now = D::weighted_percent(pass, 2, pct);
            assert!(now >= last, "tiến trình bị tụt: {last} -> {now}");
            last = now;
        }
        assert_eq!(last, 96.0);
    }

    #[test]
    fn out_of_range_inputs_stay_clamped() {
        assert_eq!(D::weighted_percent(0, 0, 200.0), 96.0);
        assert_eq!(D::weighted_percent(9, 2, -5.0), 48.0);
    }

    #[test]
    fn expected_passes_are_read_from_the_yt_dlp_info_line() {
        let re = regex::Regex::new(r"Downloading \d+ format\(s\): (\S+)").unwrap();
        assert_eq!(D::parse_expected_passes(&re, "[info] abc: Downloading 1 format(s): mp4"), Some(1));
        assert_eq!(D::parse_expected_passes(&re, "[info] abc: Downloading 1 format(s): 137+140"), Some(2));
        assert_eq!(D::parse_expected_passes(&re, "[info] abc: Downloading 1 format(s): hls-1080+hls-audio"), Some(2));
        assert_eq!(D::parse_expected_passes(&re, "[download] Destination: /tmp/a.mp4"), None);
    }

    #[test]
    fn postprocess_lines_are_recognised() {
        assert_eq!(
            D::postprocess_phase("[VideoRemuxer] Remuxing video from mp4 to mkv; Destination: /tmp/a.mkv"),
            Some("Đang đóng gói lại container...")
        );
        assert_eq!(D::postprocess_phase("[ExtractAudio] Destination: /tmp/a.mp3"), Some("Đang tách âm thanh..."));
        assert_eq!(D::postprocess_phase("[download] Destination: /tmp/a.mp4"), None);
        assert_eq!(D::postprocess_phase("/tmp/a.mkv"), None);
    }

    #[test]
    fn login_errors_get_an_actionable_message() {
        let msg = D::summarize_ytdlp_error(&[
            "[debug] something".to_string(),
            "ERROR: [instagram] Requested content is not available, rate-limit reached or login required".to_string(),
        ]);
        assert!(msg.contains("Cookie Manager"), "thiếu hướng dẫn: {msg}");
    }

    #[test]
    fn empty_stderr_gives_empty_summary() {
        assert_eq!(D::summarize_ytdlp_error(&[]), "");
    }

    #[test]
    fn utf8_truncation_handles_multibyte_characters() {
        let long_vn = "Tiếng Việt có dấu kiểm tra cắt chuỗi an toàn không bị panic khi gặp ký tự UTF-8 đa byte. ".repeat(10);
        let summary = D::summarize_ytdlp_error(&[format!("ERROR: {long_vn}")]);
        assert!(summary.ends_with("..."));
        assert_eq!(summary.chars().count(), 303);
    }

    #[test]
    fn extension_follows_the_real_url_not_a_hardcoded_guess() {
        assert_eq!(D::guess_extension("https://cdn/x/video.mp4?token=abc&x=1"), "mp4");
        // CDN Instagram tra ve JPEG du URL co duoi .heic
        assert_eq!(D::guess_extension("https://cdn/x/photo.heic?stp=dst-jpg_e35_tt6"), "jpg");
        assert_eq!(D::guess_extension("https://cdn/x/photo.heic"), "heic");
        assert_eq!(D::guess_extension("https://cdn/x/photo.JPEG"), "jpg");
        assert_eq!(D::guess_extension("https://cdn/x/nostem"), "jpg");
    }

    #[test]
    fn file_names_are_stripped_of_path_separators() {
        assert_eq!(D::sanitize_file_name("a/b:c*d", "fb"), "a_b_c_d");
        assert_eq!(D::sanitize_file_name("   ", "fb"), "fb");
        assert_eq!(D::sanitize_file_name("....", "fb"), "fb");
    }

    #[test]
    fn compress_dir_to_zip_creates_valid_zip() {
        use std::io::Read;

        let temp_dir = std::env::temp_dir().join(format!("test_zip_dir_{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&temp_dir).unwrap();

        let file1_path = temp_dir.join("image1.jpg");
        let sub_dir = temp_dir.join("subfolder");
        std::fs::create_dir_all(&sub_dir).unwrap();
        let file2_path = sub_dir.join("image2.png");

        std::fs::write(&file1_path, b"fake jpg content 12345").unwrap();
        std::fs::write(&file2_path, b"fake png content in subfolder 67890").unwrap();

        let zip_path = std::env::temp_dir().join(format!("test_album_{}.zip", uuid::Uuid::new_v4()));

        let result = D::compress_dir_to_zip(&temp_dir, &zip_path);
        assert!(result.is_ok(), "Nén zip phải thành công: {:?}", result.err());

        // Kiểm tra đọc lại file ZIP
        let zip_file = std::fs::File::open(&zip_path).unwrap();
        let mut archive = zip::ZipArchive::new(zip_file).unwrap();

        assert_eq!(archive.len(), 3); // image1.jpg, subfolder/, subfolder/image2.png

        {
            let mut file1 = archive.by_name("image1.jpg").unwrap();
            let mut content1 = String::new();
            file1.read_to_string(&mut content1).unwrap();
            assert_eq!(content1, "fake jpg content 12345");
        }

        {
            let mut file2 = archive.by_name("subfolder/image2.png").unwrap();
            let mut content2 = String::new();
            file2.read_to_string(&mut content2).unwrap();
            assert_eq!(content2, "fake png content in subfolder 67890");
        }

        // Clean up
        let _ = std::fs::remove_dir_all(&temp_dir);
        let _ = std::fs::remove_file(&zip_path);
    }

    #[test]
    fn download_progress_payload_handles_indeterminate_and_aria2c() {
        use super::DownloadProgressPayload;

        let payload = DownloadProgressPayload {
            id: "task-test".to_string(),
            percent: 0.0,
            speed: String::new(),
            eta: String::new(),
            status: "processing".to_string(),
            phase: "Đang tải qua Aria2c (không hiển thị %)...".to_string(),
            file_path: None,
            message: None,
            is_indeterminate: true,
        };

        let json = serde_json::to_string(&payload).unwrap();
        assert!(json.contains(r#""isIndeterminate":true"#));
        assert!(json.contains(r#""status":"processing""#));
        assert!(json.contains(r#""phase":"Đang tải qua Aria2c (không hiển thị %)...""#));

        let deserialized: DownloadProgressPayload = serde_json::from_str(&json).unwrap();
        assert!(deserialized.is_indeterminate);
        assert_eq!(deserialized.status, "processing");
        assert_eq!(deserialized.phase, "Đang tải qua Aria2c (không hiển thị %)...");

        // Khi JSON không có cờ isIndeterminate, mặc định phải là false
        let minimal_json = r#"{"id":"task-1","percent":45.5,"speed":"2MB/s","eta":"5s","status":"downloading","phase":"Đang tải"}"#;
        let normal_p: DownloadProgressPayload = serde_json::from_str(minimal_json).unwrap();
        assert!(!normal_p.is_indeterminate);
        assert_eq!(normal_p.percent, 45.5);

        // Kiểm tra helper constructor indeterminate
        let ind_payload = DownloadProgressPayload::indeterminate("task-2", "processing", "Đang xử lý...");
        assert!(ind_payload.is_indeterminate);
        assert_eq!(ind_payload.percent, 0.0);
    }
}
