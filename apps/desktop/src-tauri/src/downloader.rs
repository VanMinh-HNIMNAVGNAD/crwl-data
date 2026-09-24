use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::{Arc, OnceLock};
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
use crate::settings::SettingsManager;

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
    /// preparing | downloading | processing | completed | error | cancelled
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

#[derive(Debug)]
pub struct ActiveTaskState {
    pub pids: Vec<u32>,
    pub dest_dir: Option<PathBuf>,
    pub known_paths: Vec<PathBuf>,
    pub known_dirs: Vec<PathBuf>,
    pub start_time: std::time::SystemTime,
    pub cancel_tx: Option<tokio::sync::oneshot::Sender<()>>,
}

static DOWNLOAD_REGISTRY: OnceLock<AsyncMutex<HashMap<String, ActiveTaskState>>> = OnceLock::new();
static CANCELLED_DOWNLOADS: OnceLock<AsyncMutex<HashSet<String>>> = OnceLock::new();

fn get_download_registry() -> &'static AsyncMutex<HashMap<String, ActiveTaskState>> {
    DOWNLOAD_REGISTRY.get_or_init(|| AsyncMutex::new(HashMap::new()))
}

fn get_cancelled_downloads() -> &'static AsyncMutex<HashSet<String>> {
    CANCELLED_DOWNLOADS.get_or_init(|| AsyncMutex::new(HashSet::new()))
}

fn kill_process_tree(pid: u32) {
    #[cfg(unix)]
    {
        let _ = std::process::Command::new("kill")
            .arg("-9")
            .arg(format!("-{pid}"))
            .output();
        let _ = std::process::Command::new("kill")
            .arg("-9")
            .arg(pid.to_string())
            .output();
    }
    #[cfg(windows)]
    {
        let _ = std::process::Command::new("taskkill")
            .args(&["/F", "/T", "/PID", &pid.to_string()])
            .output();
    }
}

pub fn cleanup_task_files(
    dest_dir: Option<&Path>,
    known_paths: &[PathBuf],
    known_dirs: &[PathBuf],
    start_time: std::time::SystemTime,
) {
    // 1. Xoá toàn bộ các thư mục album / thư mục tạm đã đăng ký
    for dir in known_dirs {
        if dir.exists() {
            info!("Hủy tải: Xoá thư mục album {:?}", dir);
            let _ = std::fs::remove_dir_all(dir);
        }
    }

    // 2. Xoá mọi file cụ thể đã được ghi nhận
    let mut candidate_stems: Vec<String> = Vec::new();

    for path in known_paths {
        if path.exists() {
            info!("Hủy tải: Xoá file đã biết {:?}", path);
            let _ = std::fs::remove_file(path);
        }
        let part_file = PathBuf::from(format!("{}.part", path.display()));
        if part_file.exists() {
            let _ = std::fs::remove_file(&part_file);
        }
        let ytdl_file = PathBuf::from(format!("{}.ytdl", path.display()));
        if ytdl_file.exists() {
            let _ = std::fs::remove_file(&ytdl_file);
        }
        let aria2_file = PathBuf::from(format!("{}.aria2", path.display()));
        if aria2_file.exists() {
            let _ = std::fs::remove_file(&aria2_file);
        }

        if let Some(stem) = path.file_stem().and_then(|s| s.to_str()) {
            let clean_stem = if let Some(idx) = stem.rfind(".f") {
                &stem[..idx]
            } else {
                stem
            };
            if !clean_stem.is_empty() && !candidate_stems.contains(&clean_stem.to_string()) {
                candidate_stems.push(clean_stem.to_string());
            }
        }
    }

    // 3. Quét dest_dir để dọn sạch bất kỳ tệp dở dang nào
    if let Some(dir) = dest_dir {
        if dir.exists() {
            if let Ok(entries) = std::fs::read_dir(dir) {
                for entry in entries.flatten() {
                    let entry_path = entry.path();
                    if !entry_path.is_file() {
                        continue;
                    }
                    let file_name = entry.file_name().to_string_lossy().to_string();

                    let matches_stem = candidate_stems.iter().any(|st| file_name.starts_with(st));

                    let is_partial_ext = file_name.ends_with(".part")
                        || file_name.ends_with(".ytdl")
                        || file_name.ends_with(".aria2")
                        || file_name.ends_with(".temp");

                    let is_recent = entry.metadata().ok()
                        .and_then(|m| m.modified().ok())
                        .map(|t| t >= start_time.checked_sub(std::time::Duration::from_secs(5)).unwrap_or(start_time))
                        .unwrap_or(false);

                    if matches_stem || (is_partial_ext && is_recent) {
                        info!("Hủy tải: Xoá tệp tạm/dở dang trong thư mục {:?}", entry_path);
                        let _ = std::fs::remove_file(&entry_path);
                    }
                }
            }
        }
    }
}

pub struct DownloaderService;

impl DownloaderService {
    /// Tìm vị trí binary yt-dlp
    pub fn find_ytdlp() -> Result<PathBuf, String> {
        // Đường dẫn tuỳ chỉnh trong settings được ưu tiên tuyệt đối
        if let Some(custom) = SettingsManager::custom_binary_path("yt-dlp") {
            return Ok(custom);
        }
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
        if let Some(custom) = SettingsManager::custom_download_dir() {
            return custom;
        }
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

    /// Nhận diện platform từ URL để chọn đúng cookie file.
    ///
    /// Dùng hostname-based matching (tương tự `dispatcher.py`) thay vì substring
    /// `contains()` để tránh nhầm lẫn giữa các tên miền:
    /// - `fox.com` / `vox.com` KHÔNG khớp với `x.com`
    /// - `evil.com/?ref=youtube.com` KHÔNG khớp với `youtube.com`
    fn detect_platform_from_url(url: &str) -> Option<&'static str> {
        /// Trích hostname từ URL, trả về lowercase không có trailing dot.
        fn hostname_of(url: &str) -> String {
            let raw = url.trim();
            let with_scheme = if raw.starts_with("http://") || raw.starts_with("https://") {
                raw.to_string()
            } else {
                format!("https://{raw}")
            };
            url::Url::parse(&with_scheme)
                .ok()
                .and_then(|u| u.host_str().map(|h| h.to_lowercase().trim_end_matches('.').to_string()))
                .unwrap_or_default()
        }

        /// hostname == domain hoặc là subdomain của domain.
        fn host_is(hostname: &str, domain: &str) -> bool {
            !hostname.is_empty() && (hostname == domain || hostname.ends_with(&format!(".{domain}")))
        }

        fn host_in(hostname: &str, domains: &[&str]) -> bool {
            domains.iter().any(|d| host_is(hostname, d))
        }

        let host = hostname_of(url);
        if host.is_empty() {
            return None;
        }

        if host_in(&host, &["instagram.com", "instagr.am"]) {
            return Some("instagram");
        }
        if host_is(&host, "tiktok.com") {
            return Some("tiktok");
        }
        if host_in(&host, &["facebook.com", "fb.watch", "fb.com", "fb.me"]) {
            return Some("facebook");
        }
        if host_in(&host, &["twitter.com", "x.com"]) {
            return Some("twitter");
        }
        if host_in(&host, &["youtube.com", "youtu.be"]) {
            return Some("youtube");
        }
        if host_in(&host, &["pinterest.com", "pin.it"]) {
            return Some("pinterest");
        }
        if host_in(&host, &["reddit.com", "redd.it"]) {
            return Some("reddit");
        }
        if host_is(&host, "threads.net") {
            return Some("threads");
        }
        if host_is(&host, "linkedin.com") {
            return Some("linkedin");
        }
        if host_in(&host, &["bilibili.com", "b23.tv"]) {
            return Some("bilibili");
        }
        if host_is(&host, "soundcloud.com") {
            return Some("soundcloud");
        }
        if host_in(&host, &["pixiv.net", "pixiv.me"]) {
            return Some("pixiv");
        }
        if host_is(&host, "douyin.com") {
            return Some("douyin");
        }
        if host_is(&host, "tumblr.com") {
            return Some("tumblr");
        }
        None
    }

    pub async fn register_task(
        task_id: &str,
        dest_dir: Option<PathBuf>,
        cancel_tx: tokio::sync::oneshot::Sender<()>,
    ) {
        let already_cancelled = {
            let cancelled = get_cancelled_downloads().lock().await;
            cancelled.iter().any(|parent| {
                task_id == parent || task_id.starts_with(&format!("{parent}_"))
            })
        };
        let mut reg = get_download_registry().lock().await;
        reg.insert(
            task_id.to_string(),
            ActiveTaskState {
                pids: Vec::new(),
                dest_dir,
                known_paths: Vec::new(),
                known_dirs: Vec::new(),
                start_time: std::time::SystemTime::now(),
                cancel_tx: Some(cancel_tx),
            },
        );
        if already_cancelled {
            if let Some(mut state) = reg.remove(task_id) {
                if let Some(tx) = state.cancel_tx.take() {
                    let _ = tx.send(());
                }
            }
        }
    }

    pub async fn is_download_cancelled(task_id: &str) -> bool {
        let cancelled = get_cancelled_downloads().lock().await;
        cancelled.iter().any(|parent| {
            task_id == parent || task_id.starts_with(&format!("{parent}_"))
        })
    }

    pub async fn register_pid(task_id: &str, pid: u32) {
        let mut reg = get_download_registry().lock().await;
        if let Some(state) = reg.get_mut(task_id) {
            if !state.pids.contains(&pid) {
                state.pids.push(pid);
            }
        }
    }

    pub async fn record_file_path(task_id: &str, path: PathBuf) {
        let mut reg = get_download_registry().lock().await;
        if let Some(state) = reg.get_mut(task_id) {
            if !state.known_paths.contains(&path) {
                state.known_paths.push(path);
            }
        }
    }

    pub async fn record_dir_path(task_id: &str, path: PathBuf) {
        let mut reg = get_download_registry().lock().await;
        if let Some(state) = reg.get_mut(task_id) {
            if !state.known_dirs.contains(&path) {
                state.known_dirs.push(path);
            }
        }
    }

    pub async fn unregister_task(task_id: &str) {
        let mut reg = get_download_registry().lock().await;
        reg.remove(task_id);
        // Dọn cờ huỷ: task đã kết thúc, không cần giữ trong set nữa.
        // Trước đây entry chỉ được insert mà không bao giờ remove, gây rò rỉ
        // bộ nhớ chậm và làm is_download_cancelled() chậm dần theo thời gian.
        drop(reg); // Nhả lock registry trước khi lấy lock cancelled
        get_cancelled_downloads().lock().await.remove(task_id);
    }

    pub async fn cancel_download(task_id: &str) -> Result<bool, String> {
        info!("Yêu cầu hủy tác vụ tải xuống: {task_id}");
        get_cancelled_downloads().lock().await.insert(task_id.to_string());
        let mut reg = get_download_registry().lock().await;
        let matching_keys: Vec<String> = reg
            .keys()
            .filter(|k| *k == task_id || k.starts_with(&format!("{task_id}_")))
            .cloned()
            .collect();

        if matching_keys.is_empty() {
            info!("Đánh dấu hủy [{task_id}] trong lúc tác vụ đang khởi tạo");
            return Ok(true);
        }

        for key in matching_keys {
            if let Some(mut state) = reg.remove(&key) {
                if let Some(tx) = state.cancel_tx.take() {
                    let _ = tx.send(());
                }
                for pid in &state.pids {
                    kill_process_tree(*pid);
                }
                cleanup_task_files(
                    state.dest_dir.as_deref(),
                    &state.known_paths,
                    &state.known_dirs,
                    state.start_time,
                );
            }
        }
        Ok(true)
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

        // Giới hạn cả title và id ngay trong template: yt-dlp mở file trước khi
        // backend có cơ hội đổi tên sau đó, nên chỉ sanitize ở Rust là chưa đủ.
        let output_template = dest_folder
            .join("%(title).60s [%(id).50s].%(ext)s")
            .to_string_lossy()
            .to_string();

        let mut cmd = Command::new(&ytdlp_path);
        #[cfg(windows)]
        {
            #[allow(unused_imports)]
            use std::os::windows::process::CommandExt;
            cmd.creation_flags(0x08000000);
        }
        cmd.arg(&opts.url);
        cmd.arg("-o").arg(&output_template);
        cmd.arg("--no-playlist");
        cmd.arg("--trim-filenames").arg("140");
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

        // Giữ số kết nối thấp để tránh làm nghẽn CPU, RAM và băng thông trên máy yếu.
        // Người dùng vẫn có thể tăng explicit qua DownloadOptions khi cần.
        let frags = opts.concurrent_fragments.unwrap_or(2).clamp(1, 8);
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
            } else if fid.contains('+') || fid.contains('/') {
                // Biểu thức chọn format phức tạp (như bestvideo[height<=1080]+bestaudio/best[height<=1080])
                cmd.arg("-f").arg(fid);
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
        #[cfg(unix)]
        {
            cmd.process_group(0);
        }
        cmd.kill_on_drop(true);

        // Mã tác vụ: UI lọc sự kiện tiến trình theo mã này nên nhiều tệp tải
        // song song không còn ghi đè lên nhau trên cùng một thanh tiến trình.
        let task_id = opts
            .task_id
            .clone()
            .filter(|s| !s.trim().is_empty())
            .unwrap_or_else(|| uuid::Uuid::new_v4().to_string());

        let (cancel_tx, mut cancel_rx) = tokio::sync::oneshot::channel();
        Self::register_task(&task_id, Some(dest_folder.clone()), cancel_tx).await;
        if Self::is_download_cancelled(&task_id).await {
            Self::unregister_task(&task_id).await;
            return Err("Tác vụ tải đã bị hủy trong lúc khởi tạo".to_string());
        }
        let start_time = std::time::SystemTime::now();

        info!("Bắt đầu tải tệp với yt-dlp [{task_id}]: {}", opts.url);
        let mut child = match cmd.spawn() {
            Ok(c) => c,
            Err(e) => {
                Self::unregister_task(&task_id).await;
                return Err(format!("Không thể khởi chạy yt-dlp: {e}"));
            }
        };

        if let Some(pid) = child.id() {
            Self::register_pid(&task_id, pid).await;
        }

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
        let mut is_cancelled = false;

        // Tối đa 96% dành cho giai đoạn tải, 4% còn lại cho ghép/hậu xử lý
        const DOWNLOAD_SHARE: f64 = 96.0;

        loop {
            tokio::select! {
                _ = &mut cancel_rx => {
                    info!("Nhận được lệnh hủy tác vụ yt-dlp [{task_id}]");
                    is_cancelled = true;
                    break;
                }
                line_res = reader.next_line() => {
                    let line = match line_res {
                        Ok(Some(l)) => l,
                        Ok(None) => break,
                        Err(e) => {
                            warn!("Lỗi đọc stdout của yt-dlp: {e}");
                            break;
                        }
                    };

                    if let Some(rest) = line.strip_prefix("download-progress:") {
                        let parts: Vec<&str> = rest.split('|').collect();
                        let percent_str = parts.first().copied().unwrap_or("").trim();
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
                        let path_str = rest.trim().trim_matches('"').to_string();
                        Self::record_file_path(&task_id, PathBuf::from(&path_str)).await;
                        downloaded_file_path = Some(path_str);
                        continue;
                    }

                    if let Some(rest) = line.strip_prefix("[ExtractAudio] Destination: ") {
                        let path_str = rest.trim().trim_matches('"').to_string();
                        Self::record_file_path(&task_id, PathBuf::from(&path_str)).await;
                        downloaded_file_path = Some(path_str);
                        continue;
                    }

                    if let Some(rest) = line.strip_prefix("[Merger] Merging formats into ") {
                        let path_str = rest.trim().trim_matches('"').to_string();
                        Self::record_file_path(&task_id, PathBuf::from(&path_str)).await;
                        downloaded_file_path = Some(path_str);
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
                                Self::record_file_path(&task_id, PathBuf::from(&path)).await;
                                downloaded_file_path = Some(path);
                            }
                        }
                        continue;
                    }

                    // Dòng trần còn lại là kết quả của `--print after_move:filepath`,
                    // tức đường dẫn cuối cùng sau khi đã merge/remux xong.
                    let candidate = line.trim().trim_matches('"');
                    if !candidate.is_empty() && Path::new(candidate).is_absolute() {
                        Self::record_file_path(&task_id, PathBuf::from(candidate)).await;
                        printed_final_path = Some(candidate.to_string());
                    }
                }
            }
        }

        let status = if is_cancelled {
            None
        } else {
            tokio::select! {
                _ = &mut cancel_rx => {
                    is_cancelled = true;
                    None
                }
                res = child.wait() => {
                    Some(res.map_err(|e| format!("Lỗi chờ yt-dlp: {e}"))?)
                }
            }
        };

        if is_cancelled {
            info!("Hủy tác vụ tải yt-dlp [{task_id}], dừng tiến trình và xoá sạch tệp dở dang...");
            if let Some(pid) = child.id() {
                kill_process_tree(pid);
            }
            let _ = child.kill().await;
            let _ = child.wait().await;
            let _ = stderr_task.await;

            let mut known = Vec::new();
            if let Some(ref p) = downloaded_file_path {
                known.push(PathBuf::from(p));
            }
            if let Some(ref p) = printed_final_path {
                known.push(PathBuf::from(p));
            }
            cleanup_task_files(Some(&dest_folder), &known, &[], start_time);

            progress_emit(DownloadProgressPayload {
                id: task_id.clone(),
                percent: 0.0,
                speed: String::new(),
                eta: String::new(),
                status: "cancelled".to_string(),
                phase: "Đã hủy tải xuống".to_string(),
                file_path: None,
                message: Some("Đã hủy tải xuống và xoá sạch tệp dở dang".to_string()),
                is_indeterminate: false,
            });

            db.record_download_history(
                &opts.device_id,
                opts.title.as_deref().unwrap_or("Untitled"),
                "cancelled_download",
                "auto",
                None,
                None,
                "cancelled",
                Some("Người dùng đã hủy tải xuống"),
                opts.client_ip.as_deref(),
            ).await;

            Self::unregister_task(&task_id).await;
            return Err("Tác vụ tải đã bị hủy".to_string());
        }

        let status = status.unwrap();
        let _ = stderr_task.await;
        let collected_stderr = stderr_lines.lock().await.clone();

        if !status.success() {
            Self::unregister_task(&task_id).await;
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

        // Ghi nhận lịch sử tải thành công vào SQLite cục bộ
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

        Self::unregister_task(&task_id).await;

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
        task_id: Option<String>,
        db: Arc<Database>,
    ) -> Result<DownloadResult, String> {
        let dest_folder = dest_dir
            .map(PathBuf::from)
            .unwrap_or_else(Self::get_default_download_dir);

        if !dest_folder.exists() {
            let _ = tokio::fs::create_dir_all(&dest_folder).await;
        }

        let ext = Self::determine_extension(url, None);
        let initial_name = Self::build_target_filename(url, file_name, None);

        let mut reserved_paths = std::collections::HashSet::new();
        let target_path = Self::resolve_collision_free_path(
            &dest_folder,
            &initial_name,
            ext,
            &mut reserved_paths,
        );
        let referer_header = Self::get_referer_for_url(url, referer);

        let mut cancel_rx_opt = None;
        if let Some(ref tid) = task_id {
            let (cancel_tx, cancel_rx) = tokio::sync::oneshot::channel();
            Self::register_task(tid, Some(dest_folder.clone()), cancel_tx).await;
            Self::record_file_path(tid, target_path.clone()).await;
            cancel_rx_opt = Some(cancel_rx);
        }

        info!("Đang tải tệp trực tiếp qua curl: {url} -> {:?}", target_path);
        let curl_fut = Self::curl_to_file_with_meta(url, &referer_header, &target_path);
        let (ok, content_type) = if let Some(mut cancel_rx) = cancel_rx_opt {
            tokio::select! {
                _ = &mut cancel_rx => {
                    info!("Hủy tải tệp trực tiếp: Xoá {:?}", target_path);
                    if target_path.exists() {
                        let _ = std::fs::remove_file(&target_path);
                    }
                    if let Some(ref tid) = task_id {
                        Self::unregister_task(tid).await;
                    }
                    return Err("Tác vụ tải tệp đã bị người dùng hủy và tệp tạm đã được xoá sạch".to_string());
                }
                res = curl_fut => res,
            }
        } else {
            curl_fut.await
        };

        if !ok {
            if let Some(ref tid) = task_id {
                Self::unregister_task(tid).await;
            }
            return Err(
                "Tải tệp thất bại — liên kết có thể đã hết hạn hoặc bị chặn. Hãy quét lại rồi thử."
                    .to_string(),
            );
        }

        // Priority 1: Nếu trước khi tải chưa có đuôi mở rộng, nhưng HTTP response trả về Content-Type cụ thể
        let mut final_path = target_path;
        if final_path.extension().is_none() {
            if let Some(ct) = content_type.as_deref() {
                if let Some(ct_ext) = Self::extension_from_content_type(ct) {
                    let candidate = final_path.with_extension(ct_ext);
                    if !candidate.exists() && std::fs::rename(&final_path, &candidate).is_ok() {
                        final_path = candidate;
                    }
                }
            }
        }

        let file_size = final_path.metadata().map(|m| m.len() as i64).ok();
        let path_str = final_path.to_string_lossy().to_string();
        let clean_name = final_path
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or(&initial_name)
            .to_string();

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

        if let Some(ref tid) = task_id {
            Self::unregister_task(tid).await;
        }

        Ok(DownloadResult {
            success: true,
            file_path: Some(path_str),
            file_name: Some(clean_name),
            message: "Tải tệp ảnh thành công!".to_string(),
        })
    }

    /// Tải album nhiều ảnh hoặc đóng gói thành file ZIP
    /// Cấp phát thư mục album MỚI, không dùng lại thư mục đã có nội dung.
    ///
    /// Trước đây hàm luôn dùng `base/<Tiêu đề>`: tải lại cùng một album (hoặc hai
    /// album trùng tên) sẽ đổ chung vào một chỗ, và bước nén ZIP `WalkDir` cả thư
    /// mục nên gói luôn tệp thừa của lần trước.
    fn allocate_album_dir(base_dest: &Path, clean_title: &str) -> PathBuf {
        let is_free = |p: &Path| -> bool {
            match std::fs::read_dir(p) {
                Ok(mut entries) => entries.next().is_none(), // tồn tại nhưng rỗng → dùng được
                Err(_) => !p.exists(),
            }
        };

        let first = base_dest.join(clean_title);
        if is_free(&first) {
            return first;
        }
        for counter in 1..10_000u32 {
            let cand = base_dest.join(format!("{clean_title}_{counter}"));
            if is_free(&cand) {
                return cand;
            }
        }
        first
    }

    #[allow(clippy::too_many_arguments)]
    pub async fn download_album_batch(
        app_handle: AppHandle,
        items: Vec<DirectFileItem>,
        album_name: Option<&str>,
        dest_dir: Option<&str>,
        album_dir: Option<&str>,
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

        // `album_dir` = thư mục do một lượt gọi trước trả về (luồng tải ảnh rồi
        // tải video rồi mới nén). Không có thì cấp phát thư mục mới, sạch.
        let target_dir = match album_dir.map(str::trim).filter(|d| !d.is_empty()) {
            Some(d) => PathBuf::from(d),
            None => Self::allocate_album_dir(&base_dest, &clean_title),
        };

        if !target_dir.exists() {
            tokio::fs::create_dir_all(&target_dir)
                .await
                .map_err(|e| format!("Không tạo được thư mục album: {e}"))?;
        }

        let task_id = task_id
            .filter(|s| !s.trim().is_empty())
            .unwrap_or_else(|| uuid::Uuid::new_v4().to_string());

        let (cancel_tx, mut cancel_rx) = tokio::sync::oneshot::channel();
        Self::register_task(&task_id, Some(base_dest.clone()), cancel_tx).await;
        Self::record_dir_path(&task_id, target_dir.clone()).await;

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

        let total = items.len();
        let mut downloaded_files = Vec::new();
        let mut failed = 0;

        if total == 0 {
            if !as_zip {
                Self::unregister_task(&task_id).await;
                let dir_str = target_dir.to_string_lossy().to_string();
                return Ok(DownloadResult {
                    success: true,
                    file_path: Some(dir_str),
                    file_name: Some(clean_title),
                    message: "Thư mục album đã sẵn sàng".to_string(),
                });
            }

            // as_zip = true: Thu thập các tệp đã có trong thư mục (ví dụ: video hoặc ảnh đã tải)
            let walker = walkdir::WalkDir::new(&target_dir);
            for entry in walker.into_iter().filter_map(|e| e.ok()) {
                if entry.file_type().is_file() {
                    downloaded_files.push(entry.into_path());
                }
            }

            if downloaded_files.is_empty() {
                Self::unregister_task(&task_id).await;
                return Err("Danh sách tệp cần tải đang trống".to_string());
            }
        } else {
            emit(0.0, "downloading", format!("Chuẩn bị tải {total} tệp..."), None);

            // Tải song song có giới hạn. Cấp phát trước đường dẫn độc nhất cho từng tệp
            // để tránh race condition và ghi đè khi nhiều tệp có cùng tên hoặc chạy concurrent.
            // Mỗi tác vụ là một process curl riêng; 5 process đồng thời gây giật
            // mạnh trên các máy 2 nhân khi tải album lớn.
            const MAX_PARALLEL: usize = 2;
            let semaphore = Arc::new(tokio::sync::Semaphore::new(MAX_PARALLEL));
            let completed = Arc::new(std::sync::atomic::AtomicUsize::new(0));
            let mut handles = Vec::with_capacity(total);

            let mut reserved_paths = std::collections::HashSet::new();
            let mut planned_items = Vec::with_capacity(total);

            for (idx, item) in items.into_iter().enumerate() {
                let ext = Self::guess_extension(&item.url);
                let raw_fname = item.filename.as_deref().unwrap_or("");
                let initial_fname = if raw_fname.trim().is_empty() {
                    if !ext.is_empty() {
                        format!("{clean_title}_{:03}.{ext}", idx + 1)
                    } else {
                        format!("{clean_title}_{:03}", idx + 1)
                    }
                } else {
                    let safe = Self::sanitize_file_name(raw_fname, &format!("media_{}", idx + 1));
                    // Chỉ bổ sung đuôi khi tên chưa có — không nối chồng thành "anh.jpg.heic"
                    if Path::new(&safe).extension().is_some() {
                        safe
                    } else if !ext.is_empty() {
                        format!("{safe}.{ext}")
                    } else {
                        safe
                    }
                };

                let dest_file = Self::resolve_collision_free_path(
                    &target_dir,
                    &initial_fname,
                    ext,
                    &mut reserved_paths,
                );
                planned_items.push((item, dest_file));
            }

            for (item, dest_file) in planned_items {
                let permit_src = Arc::clone(&semaphore);
                let counter = Arc::clone(&completed);

                handles.push(tokio::spawn(async move {
                    let _permit = match permit_src.acquire_owned().await {
                        Ok(p) => p,
                        Err(_) => return None,
                    };

                    let referer_header = Self::get_referer_for_url(&item.url, item.referer.as_deref());

                    let (ok, content_type) = Self::curl_to_file_with_meta(&item.url, &referer_header, &dest_file).await;
                    counter.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                    if ok {
                        let mut final_dest = dest_file;
                        if final_dest.extension().is_none() {
                            if let Some(ct) = content_type.as_deref() {
                                if let Some(ct_ext) = Self::extension_from_content_type(ct) {
                                    let candidate = final_dest.with_extension(ct_ext);
                                    if !candidate.exists() && std::fs::rename(&final_dest, &candidate).is_ok() {
                                        final_dest = candidate;
                                    }
                                }
                            }
                        }
                        Some(final_dest)
                    } else {
                        warn!("Không tải được tệp album: {}", item.url);
                        None
                    }
                }));
            }

            let mut is_cancelled = false;
            for handle in handles.iter_mut() {
                tokio::select! {
                    _ = &mut cancel_rx => {
                        is_cancelled = true;
                        break;
                    }
                    res = &mut *handle => {
                        if let Ok(Some(path)) = res {
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
                }
            }

            if is_cancelled {
                for handle in &mut handles {
                    handle.abort();
                }
                for handle in handles {
                    let _ = handle.await;
                }
                info!("Hủy album batch: Xoá sạch thư mục {:?}", target_dir);
                if target_dir.exists() {
                    let _ = tokio::fs::remove_dir_all(&target_dir).await;
                }
                emit(0.0, "cancelled", "Đã hủy tải album và xoá tệp".to_string(), None);
                Self::unregister_task(&task_id).await;
                return Err("Tác vụ tải album đã bị người dùng hủy và tệp tạm đã được xoá".to_string());
            }

            if downloaded_files.is_empty() {
                Self::unregister_task(&task_id).await;
                emit(0.0, "error", "Không tải được tệp nào".to_string(), None);
                return Err(format!(
                    "Không tải được tệp nào trong {total} tệp. Liên kết có thể đã hết hạn — hãy quét lại tài khoản."
                ));
            }

            failed = total - downloaded_files.len();
        }

        let mut zip_error: Option<String> = None;
        if as_zip {
            emit(93.0, "processing", "Đang nén thành tệp ZIP...".to_string(), None);
            // File::create() sẽ TRUNCATE tệp cũ: tải lại cùng album hoặc hai album
            // trùng tên từng âm thầm xoá mất ZIP trước đó. Cấp phát tên không đụng độ
            // giống như cách làm với từng tệp lẻ.
            let mut reserved = std::collections::HashSet::new();
            let zip_path = Self::resolve_collision_free_path(
                &base_dest,
                &format!("{clean_title}.zip"),
                "zip",
                &mut reserved,
            );
            let zip_filename = zip_path
                .file_name()
                .map(|f| f.to_string_lossy().to_string())
                .unwrap_or_else(|| format!("{clean_title}.zip"));

            Self::record_file_path(&task_id, zip_path.clone()).await;

            let target_dir_clone = target_dir.clone();
            let zip_path_clone = zip_path.clone();

            let zip_status = tokio::task::spawn_blocking(move || {
                let res = Self::compress_dir_to_zip(&target_dir_clone, &zip_path_clone);
                if res.is_err() {
                    let _ = std::fs::remove_file(&zip_path_clone);
                }
                res
            });

            let mut zip_cancelled = false;
            let zip_res = tokio::select! {
                _ = &mut cancel_rx => {
                    zip_cancelled = true;
                    Err("Cancelled".to_string())
                }
                res = zip_status => {
                    match res {
                        Ok(Ok(())) => Ok(()),
                        Ok(Err(err)) => Err(err),
                        Err(join_err) => Err(format!("tiến trình nén bị gián đoạn: {join_err}")),
                    }
                }
            };

            if zip_cancelled {
                info!("Hủy nén ZIP album: Xoá tệp ZIP và thư mục {:?}", target_dir);
                if zip_path.exists() {
                    let _ = std::fs::remove_file(&zip_path);
                }
                if target_dir.exists() {
                    let _ = tokio::fs::remove_dir_all(&target_dir).await;
                }
                emit(0.0, "cancelled", "Đã hủy nén ZIP và xoá tệp".to_string(), None);
                Self::unregister_task(&task_id).await;
                return Err("Tác vụ nén ZIP đã bị người dùng hủy và tệp tạm đã được xoá".to_string());
            }

            match zip_res {
                Ok(()) => {
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
                    Self::unregister_task(&task_id).await;

                    return Ok(DownloadResult {
                        success: true,
                        file_path: Some(zip_str),
                        file_name: Some(zip_filename),
                        message: Self::batch_summary(downloaded_files.len(), failed, "vào tệp ZIP"),
                    });
                }
                Err(err) => {
                    warn!("Nén ZIP thất bại: {err} — giữ nguyên thư mục ảnh đã tải");
                    zip_error = Some(err);
                }
            }
        }

        let dir_str = target_dir.to_string_lossy().to_string();

        // Người dùng yêu cầu ZIP nhưng nén hỏng: các tệp vẫn còn nguyên trong thư
        // mục, nhưng TUYỆT ĐỐI không được báo "Hoàn tất" như thể đã có ZIP.
        if let Some(reason) = zip_error {
            let msg = format!(
                "Đã tải {} tệp nhưng KHÔNG nén được ZIP ({reason}). Các tệp vẫn nằm trong thư mục: {dir_str}",
                downloaded_files.len()
            );

            db.record_download_history(
                device_id,
                &format!("Album: {clean_title}"),
                &clean_title,
                "album",
                None,
                None,
                "failed",
                Some(&msg),
                client_ip,
            ).await;

            let _ = app_handle.emit(
                "download-progress",
                DownloadProgressPayload {
                    id: task_id.clone(),
                    percent: 100.0,
                    speed: String::new(),
                    eta: String::new(),
                    status: "error".to_string(),
                    phase: "Nén ZIP thất bại".to_string(),
                    file_path: Some(dir_str.clone()),
                    message: Some(msg.clone()),
                    is_indeterminate: false,
                },
            );

            Self::unregister_task(&task_id).await;

            return Ok(DownloadResult {
                success: false,
                file_path: Some(dir_str),
                file_name: Some(clean_title),
                message: msg,
            });
        }

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
        Self::unregister_task(&task_id).await;

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

    /// Loại bỏ ký tự không hợp lệ trong tên tệp trên Linux và ngăn chặn path traversal
    pub fn sanitize_file_name(raw: &str, fallback: &str) -> String {
        const MAX_FILENAME_BYTES: usize = 120;
        let cleaned: String = raw
            .replace(
                [
                    '/', '\\', '\0', '?', '%', '*', ':', '|', '"', '<', '>', '\n', '\r', '\t',
                    ';', '&', '$', '`', '!',
                ],
                "_",
            )
            .trim()
            .trim_matches('.')
            .chars()
            .filter(|c| !c.is_control())
            .collect();
        let cleaned = cleaned.replace("..", "_");
        let trimmed = cleaned.trim().trim_matches('.');
        if trimmed.is_empty() {
            Self::truncate_utf8_bytes(fallback, MAX_FILENAME_BYTES)
        } else {
            Self::truncate_utf8_bytes(trimmed, MAX_FILENAME_BYTES)
        }
    }

    fn truncate_utf8_bytes(value: &str, max_bytes: usize) -> String {
        if value.len() <= max_bytes {
            return value.to_string();
        }

        let mut end = max_bytes;
        while end > 0 && !value.is_char_boundary(end) {
            end -= 1;
        }
        value[..end].trim_end_matches('.').to_string()
    }

    /// Cấp phát đường dẫn đích duy nhất trong thư mục để tránh ghi đè (collision-free & race-safe)
    /// - Không path traversal (chỉ dùng filename)
    /// - Không ghi đè file có sẵn trên đĩa hoặc file đã được cấp phát trong cùng batch
    /// - Nếu trùng tên, tự động đánh số _1, _2, ... trước phần mở rộng
    pub fn resolve_collision_free_path(
        dir: &Path,
        initial_fname: &str,
        default_ext: &str,
        reserved_paths: &mut std::collections::HashSet<PathBuf>,
    ) -> PathBuf {
        let file_name_only = Path::new(initial_fname)
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or(initial_fname);

        let safe_name = Self::sanitize_file_name(file_name_only, "media");
        let p = Path::new(&safe_name);
        let stem = p
            .file_stem()
            .and_then(|s| s.to_str())
            .unwrap_or("media");
        let ext = p
            .extension()
            .and_then(|e| e.to_str())
            .unwrap_or(default_ext);

        let initial_cand = if !ext.is_empty() {
            format!("{stem}.{ext}")
        } else {
            stem.to_string()
        };
        let mut cand_path = dir.join(&initial_cand);

        if !reserved_paths.contains(&cand_path) && !cand_path.exists() {
            reserved_paths.insert(cand_path.clone());
            return cand_path;
        }

        let mut counter = 1;
        loop {
            let next_cand = if !ext.is_empty() {
                format!("{stem}_{counter}.{ext}")
            } else {
                format!("{stem}_{counter}")
            };
            cand_path = dir.join(&next_cand);
            if !reserved_paths.contains(&cand_path) && !cand_path.exists() {
                reserved_paths.insert(cand_path.clone());
                return cand_path;
            }
            counter += 1;
        }
    }

    /// Ánh xạ Content-Type HTTP sang phần mở rộng tệp tương ứng
    pub fn extension_from_content_type(content_type: &str) -> Option<&'static str> {
        let mime = content_type
            .split(';')
            .next()
            .unwrap_or("")
            .trim()
            .to_lowercase();

        match mime.as_str() {
            "image/jpeg" => Some("jpg"),
            "image/png" => Some("png"),
            "image/webp" => Some("webp"),
            "image/avif" => Some("avif"),
            "image/gif" => Some("gif"),
            "image/svg+xml" => Some("svg"),
            "image/heic" | "image/heif" => Some("heic"),
            "image/bmp" | "image/x-ms-bmp" => Some("bmp"),
            "image/tiff" => Some("tiff"),
            "video/mp4" => Some("mp4"),
            "video/webm" => Some("webm"),
            "video/quicktime" => Some("mov"),
            "video/x-matroska" => Some("mkv"),
            "video/x-m4v" => Some("m4v"),
            "video/mp2t" => Some("ts"),
            "video/iso.segment" => Some("m4s"),
            "application/vnd.apple.mpegurl" | "application/x-mpegurl" | "audio/x-mpegurl" => Some("m3u8"),
            "application/dash+xml" => Some("mpd"),
            "audio/mpeg" | "audio/mp3" => Some("mp3"),
            "audio/mp4" | "audio/x-m4a" => Some("m4a"),
            "audio/ogg" | "application/ogg" => Some("ogg"),
            "audio/wav" | "audio/x-wav" => Some("wav"),
            "audio/flac" | "audio/x-flac" => Some("flac"),
            "audio/aac" => Some("aac"),
            _ => None,
        }
    }

    /// Xác định phần mở rộng theo thứ tự ưu tiên:
    /// 1. Content-Type nếu đã có từ HTTP response
    /// 2. URL extension nếu đáng tin cậy
    /// 3. Fallback: không có đuôi (""), tuyệt đối không mặc định ép thành "jpg"
    pub fn determine_extension(url: &str, content_type: Option<&str>) -> &'static str {
        if let Some(ct) = content_type {
            if let Some(ext) = Self::extension_from_content_type(ct) {
                return ext;
            }
        }
        Self::guess_extension(url)
    }

    /// Tạo tên tệp đích an toàn dựa trên URL, tên tuỳ chọn và Content-Type
    pub fn build_target_filename(url: &str, file_name: Option<&str>, content_type: Option<&str>) -> String {
        let ext = Self::determine_extension(url, content_type);
        file_name
            .map(|f| Self::sanitize_file_name(f, ""))
            .filter(|f| !f.is_empty())
            .map(|f| {
                if Path::new(&f).extension().is_some() {
                    f
                } else if !ext.is_empty() {
                    format!("{f}.{ext}")
                } else {
                    f
                }
            })
            .unwrap_or_else(|| {
                if !ext.is_empty() {
                    format!("media_{}.{ext}", chrono::Utc::now().timestamp())
                } else {
                    format!("media_{}", chrono::Utc::now().timestamp())
                }
            })
    }

    /// Đoán phần mở rộng THẬT của tệp sẽ nhận được từ URL.
    ///
    /// CDN của Instagram/Facebook hay phục vụ URL đuôi `.heic` nhưng kèm tham số
    /// `stp=dst-jpg`, và thứ trả về là JPEG. Đặt tên theo đuôi URL sẽ tạo ra tệp
    /// `.heic` mà máy không mở được, nên phải ưu tiên chỉ dẫn `dst-<fmt>`.
    ///
    /// Nếu không thể nhận diện được phần mở rộng, trả về "" (không có đuôi).
    /// Tuyệt đối không mặc định ép thành "jpg" để tránh làm sai loại file.
    pub fn guess_extension(url: &str) -> &'static str {
        let lower = url.to_lowercase();
        let without_hash = lower.split('#').next().unwrap_or("");
        let mut split = without_hash.splitn(2, '?');
        let path = split.next().unwrap_or("");
        let query = split.next().unwrap_or("");

        for (marker, ext) in [
            ("dst-jpg", "jpg"),
            ("dst-jpeg", "jpg"),
            ("dst-png", "png"),
            ("dst-webp", "webp"),
            ("dst-avif", "avif"),
            ("dst-heic", "heic"),
        ] {
            if query.contains(marker) {
                return ext;
            }
        }

        const KNOWN: &[(&str, &str)] = &[
            (".mp4", "mp4"),
            (".webm", "webm"),
            (".m4s", "m4s"),
            (".ts", "ts"),
            (".m3u8", "m3u8"),
            (".mpd", "mpd"),
            (".mov", "mov"),
            (".m4v", "m4v"),
            (".mkv", "mkv"),
            (".avi", "avi"),
            (".mp3", "mp3"),
            (".m4a", "m4a"),
            (".aac", "aac"),
            (".wav", "wav"),
            (".flac", "flac"),
            (".ogg", "ogg"),
            (".opus", "opus"),
            (".png", "png"),
            (".webp", "webp"),
            (".avif", "avif"),
            (".gif", "gif"),
            (".svg", "svg"),
            (".heic", "heic"),
            (".heif", "heif"),
            (".jpeg", "jpg"),
            (".jpg", "jpg"),
            (".bmp", "bmp"),
            (".tiff", "tiff"),
            (".tif", "tiff"),
            (".ico", "ico"),
        ];
        KNOWN
            .iter()
            .find(|(suffix, _)| path.ends_with(suffix))
            .map(|(_, ext)| *ext)
            .unwrap_or("")
    }

    /// Kiểm tra xem chuỗi byte có khớp với chữ ký (magic bytes) của các định dạng media phổ biến không
    pub(crate) fn is_known_media_bytes(buf: &[u8]) -> bool {
        if buf.len() < 2 {
            return false;
        }

        // JPEG: FF D8 FF
        if buf.len() >= 3 && buf[0] == 0xFF && buf[1] == 0xD8 && buf[2] == 0xFF {
            return true;
        }

        // PNG: 89 50 4E 47 0D 0A 1A 0A
        if buf.starts_with(&[0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A]) {
            return true;
        }

        // GIF: GIF87a hoặc GIF89a
        if buf.starts_with(b"GIF87a") || buf.starts_with(b"GIF89a") {
            return true;
        }

        // WebP: RIFF....WEBP
        if buf.len() >= 12 && &buf[0..4] == b"RIFF" && &buf[8..12] == b"WEBP" {
            return true;
        }

        // MP4 / MOV / M4V / M4A / HEIC / AVIF (ISO Base Media File Format box)
        if buf.len() >= 8 {
            let tag = &buf[4..8];
            if tag == b"ftyp" || tag == b"moov" || tag == b"mdat" || tag == b"wide" || tag == b"free" || tag == b"skip" {
                return true;
            }
        }
        if buf.len() >= 4 && (buf.starts_with(b"moov") || buf.starts_with(b"mdat")) {
            return true;
        }

        // WebM / MKV: 1A 45 DF A3 (EBML)
        if buf.starts_with(&[0x1A, 0x45, 0xDF, 0xA3]) {
            return true;
        }

        // MP3: ID3 header hoặc frame sync 0xFF 0xFB/0xF3/0xF2
        if buf.starts_with(b"ID3") {
            return true;
        }
        if buf.len() >= 2 && buf[0] == 0xFF && (buf[1] & 0xE0) == 0xE0 {
            return true;
        }

        // Ogg: OggS
        if buf.starts_with(b"OggS") {
            return true;
        }

        // FLAC: fLaC
        if buf.starts_with(b"fLaC") {
            return true;
        }

        // WAV / AVI: RIFF....WAVE / RIFF....AVI
        if buf.len() >= 12 && &buf[0..4] == b"RIFF" && (&buf[8..12] == b"WAVE" || &buf[8..12] == b"AVI ") {
            return true;
        }

        // BMP: BM
        if buf.starts_with(b"BM") {
            return true;
        }

        // TIFF
        if buf.starts_with(&[0x49, 0x49, 0x2A, 0x00]) || buf.starts_with(&[0x4D, 0x4D, 0x00, 0x2A]) {
            return true;
        }

        false
    }

    /// Xác thực tệp đã tải về: HTTP status, Content-Type, nội dung phản hồi và extension
    pub(crate) fn validate_downloaded_media(
        dest: &Path,
        http_status: u16,
        content_type: &str,
        url: &str,
    ) -> Result<(), String> {
        // 1. Kiểm tra HTTP Status
        if http_status != 0 && !(200..=299).contains(&http_status) {
            return Err(format!("Mã HTTP phản hồi không hợp lệ ({http_status}) từ {url}"));
        }

        // 2. Kiểm tra Content-Type
        let mime = content_type
            .split(';')
            .next()
            .unwrap_or("")
            .trim()
            .to_lowercase();

        let is_non_media_mime = mime == "text/html"
            || mime == "application/xhtml+xml"
            || mime == "application/json"
            || mime == "text/json"
            || mime.ends_with("+json")
            || mime == "application/xml"
            || mime == "text/xml"
            || mime == "application/javascript"
            || mime == "text/javascript"
            || mime == "application/x-javascript"
            || mime == "text/css";

        if is_non_media_mime {
            return Err(format!(
                "Content-Type không hợp lệ cho tệp media: '{mime}' (server trả về tài liệu HTML/JSON/script thay vì media) từ {url}"
            ));
        }

        // 3. Kiểm tra sự tồn tại và kích thước tệp
        let metadata = std::fs::metadata(dest)
            .map_err(|e| format!("Không thể kiểm tra tệp tải về {:?}: {e}", dest))?;

        let file_size = metadata.len();
        if file_size == 0 {
            return Err(format!("Tệp tải về rỗng (0 byte) từ {url}"));
        }

        // 4. Đọc mẫu đầu tệp để phân tích nội dung
        let sample = {
            use std::io::Read;
            let f = std::fs::File::open(dest)
                .map_err(|e| format!("Không thể mở tệp {:?} để kiểm tra: {e}", dest))?;
            let mut buf = Vec::with_capacity(8192);
            let mut take = f.take(8192);
            take.read_to_end(&mut buf)
                .map_err(|e| format!("Không thể đọc nội dung tệp {:?}: {e}", dest))?;
            buf
        };

        // Bỏ qua BOM và khoảng trắng đầu dòng
        let mut trimmed = sample.as_slice();
        if trimmed.starts_with(&[0xEF, 0xBB, 0xBF]) {
            trimmed = &trimmed[3..];
        }
        while let Some((first, rest)) = trimmed.split_first() {
            if first.is_ascii_whitespace() {
                trimmed = rest;
            } else {
                break;
            }
        }

        // Kiểm tra HTML/XML tag ở đầu nội dung
        let trimmed_lower_slice = if trimmed.len() > 100 {
            &trimmed[..100]
        } else {
            trimmed
        };
        let trimmed_lower_str = String::from_utf8_lossy(trimmed_lower_slice).to_lowercase();

        if trimmed_lower_str.starts_with("<!doctype")
            || trimmed_lower_str.starts_with("<html")
            || trimmed_lower_str.starts_with("<head")
            || trimmed_lower_str.starts_with("<body")
            || trimmed_lower_str.starts_with("<script")
            || trimmed_lower_str.starts_with("<?xml")
            || trimmed_lower_str.starts_with("<!--")
        {
            return Err(format!(
                "Nội dung tệp là trang web HTML/XML thay vì dữ liệu media từ {url}"
            ));
        }

        // Kiểm tra các dấu hiệu Cloudflare Challenge, anti-bot, WAF, trang đăng nhập hoặc lỗi HTML
        let sample_text = String::from_utf8_lossy(&sample).to_lowercase();

        let challenge_markers = [
            "cloudflare",
            "cf-browser-verification",
            "challenge-platform",
            "cf-turnstile",
            "__cf_chl",
            "cf-chl-",
            "just a moment...",
            "checking your browser",
            "verify you are human",
            "verifying you are human",
            "ddos-guard",
            "bot detection",
            "access denied",
            "attention required!",
            "<title>error",
            "<title>40",
            "<title>50",
            "<title>login",
            "<title>sign in",
            "<title>just a moment",
            "<title>attention required",
            "<title>security check",
            "please enable javascript",
            "g-recaptcha",
            "hcaptcha",
            "window._cf_chl_opt",
        ];

        if sample_text.contains("<html") || sample_text.contains("<!doctype") {
            return Err(format!(
                "Phát hiện cấu trúc HTML trong nội dung tải về từ {url}"
            ));
        }

        for marker in challenge_markers {
            if sample_text.contains(marker) {
                return Err(format!(
                    "Phát hiện nội dung lỗi / chống bot / challenge ('{marker}') từ {url}"
                ));
            }
        }

        // Kiểm tra phản hồi JSON
        if trimmed.starts_with(b"{") || trimmed.starts_with(b"[") {
            if file_size <= 65536 {
                if let Ok(full_bytes) = std::fs::read(dest) {
                    if serde_json::from_slice::<serde_json::Value>(&full_bytes).is_ok() {
                        return Err(format!(
                            "Nội dung tệp là dữ liệu JSON thay vì media từ {url}"
                        ));
                    }
                }
            }
            if sample_text.contains("\"error\"")
                || sample_text.contains("\"message\"")
                || sample_text.contains("\"status\"")
                || sample_text.contains("\"code\"")
                || sample_text.contains("\"detail\"")
            {
                return Err(format!(
                    "Nội dung tệp chứa cấu trúc JSON thông báo lỗi từ {url}"
                ));
            }
        }

        // Kiểm tra thông báo lỗi dạng văn bản thuần
        let is_pure_ascii_text = sample.iter().all(|&b| b == b'\r' || b == b'\n' || b == b'\t' || (32..=126).contains(&b));
        if is_pure_ascii_text && !sample.is_empty() {
            let text_lower = sample_text.trim();
            if text_lower.starts_with("error")
                || text_lower.starts_with("unauthorized")
                || text_lower.starts_with("forbidden")
                || text_lower.starts_with("access denied")
                || text_lower.contains("rate limit")
            {
                return Err(format!(
                    "Nội dung tệp là thông báo lỗi dạng văn bản ('{text_lower}') từ {url}"
                ));
            }
        }

        // 5. Kiểm tra chữ ký số Media và extension
        let has_known_media_magic = Self::is_known_media_bytes(&sample);
        if has_known_media_magic {
            return Ok(());
        }

        let is_media_content_type = mime.starts_with("image/")
            || mime.starts_with("video/")
            || mime.starts_with("audio/");

        let ext = dest
            .extension()
            .and_then(|e| e.to_str())
            .unwrap_or("")
            .to_lowercase();

        const STRICT_MEDIA_EXTS: &[&str] = &[
            "jpg", "jpeg", "png", "gif", "webp", "mp4", "webm", "mov", "m4v", "mp3", "m4a", "heic",
        ];

        if STRICT_MEDIA_EXTS.contains(&ext.as_str()) {
            return Err(format!(
                "Tệp có đuôi .{ext} nhưng nội dung không khớp với bất kỳ chữ ký (magic bytes) media nào từ {url}"
            ));
        }

        if !is_media_content_type {
            return Err(format!(
                "Tệp không có Content-Type media (nhận được '{mime}') và không khớp chữ ký media từ {url}"
            ));
        }

        Ok(())
    }

    /// Tải 1 URL về đúng đường dẫn bằng curl, kiểm tra tính hợp lệ của media và trả về true nếu thành công
    #[allow(dead_code)]
    pub(crate) async fn curl_to_file(url: &str, referer: &str, dest: &Path) -> bool {
        Self::curl_to_file_with_meta(url, referer, dest).await.0
    }

    /// Tải 1 URL về đường dẫn bằng curl, trả về (kết_quả, Content-Type_nhận_được)
    pub(crate) async fn curl_to_file_with_meta(
        url: &str,
        referer: &str,
        dest: &Path,
    ) -> (bool, Option<String>) {
        const USER_AGENT: &str = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36";
        let mut cmd = Command::new("curl");
        #[cfg(windows)]
        {
            #[allow(unused_imports)]
            use std::os::windows::process::CommandExt;
            cmd.creation_flags(0x08000000);
        }
        let output = match cmd
            .arg("-sSL")
            .arg("-f")
            .arg("--retry").arg("2")
            .arg("--retry-delay").arg("1")
            .arg("--connect-timeout").arg("20")
            .arg("--max-time").arg("300")
            .arg("-A").arg(USER_AGENT)
            .arg("-e").arg(referer)
            .arg("-w").arg("\n---CURL_META---\n%{http_code}\n%{content_type}\n")
            .arg(url)
            .arg("-o").arg(dest)
            .output()
            .await
        {
            Ok(o) => o,
            Err(e) => {
                warn!("Không thể thực thi lệnh curl cho {url}: {e}");
                let _ = std::fs::remove_file(dest);
                return (false, None);
            }
        };

        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            warn!("curl thất bại khi tải {url} (code {:?}): {}", output.status.code(), stderr.trim());
            let _ = std::fs::remove_file(dest);
            return (false, None);
        }

        let stdout_str = String::from_utf8_lossy(&output.stdout);
        let mut http_status = 0u16;
        let mut content_type = String::new();

        if let Some(pos) = stdout_str.rfind("---CURL_META---") {
            let meta_part = &stdout_str[pos + "---CURL_META---".len()..];
            let mut lines = meta_part.lines().filter(|l| !l.trim().is_empty());
            if let Some(code_line) = lines.next() {
                http_status = code_line.trim().parse::<u16>().unwrap_or(0);
            }
            if let Some(ct_line) = lines.next() {
                content_type = ct_line.trim().to_string();
            }
        }

        if let Err(reason) = Self::validate_downloaded_media(dest, http_status, &content_type, url) {
            warn!("Tải tệp media không hợp lệ: {reason}");
            let _ = std::fs::remove_file(dest);
            return (false, None);
        }

        let opt_ct = if content_type.is_empty() { None } else { Some(content_type) };
        (true, opt_ct)
    }

    /// Nén toàn bộ tệp và thư mục con trong `src_dir` thành tệp ZIP tại `zip_path` bằng crate native `zip`
    pub fn compress_dir_to_zip(src_dir: &Path, zip_path: &Path) -> Result<(), String> {
        let file = std::fs::File::create(zip_path)
            .map_err(|e| format!("Không thể tạo tệp zip: {e}"))?;
        let mut zip = zip::ZipWriter::new(std::io::BufWriter::new(file));
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
                let options = zip::write::SimpleFileOptions::default()
                    .compression_method(zip::CompressionMethod::Stored);
                zip.add_directory(&path_str, options)
                    .map_err(|e| format!("Lỗi thêm thư mục vào zip: {e}"))?;
            } else if path.is_file() {
                // Media phổ biến đã được nén sẵn; Deflate lại video/ảnh chỉ làm
                // tăng CPU và thời gian đóng gói mà hầu như không giảm kích thước.
                let extension = path
                    .extension()
                    .and_then(|value| value.to_str())
                    .map(|value| value.to_ascii_lowercase());
                let method = match extension.as_deref() {
                    Some("jpg" | "jpeg" | "png" | "gif" | "webp" | "heic" | "avif" | "mp4" | "mkv" | "mov" | "webm" | "m4v" | "mp3" | "m4a" | "flac" | "wav" | "aac" | "ogg" | "opus" | "zip" | "7z" | "rar") => {
                        zip::CompressionMethod::Stored
                    }
                    _ => zip::CompressionMethod::Deflated,
                };
                let options = zip::write::SimpleFileOptions::default()
                    .compression_method(method);
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
        assert_eq!(D::guess_extension("https://cdn/x/video.m4s"), "m4s");
        assert_eq!(D::guess_extension("https://cdn/x/video.ts"), "ts");
        assert_eq!(D::guess_extension("https://cdn/x/manifest.m3u8"), "m3u8");
        assert_eq!(D::guess_extension("https://cdn/x/image.avif"), "avif");
        assert_eq!(D::guess_extension("https://cdn/x/image.svg"), "svg");
        assert_eq!(D::guess_extension("https://cdn/x/nostem"), "");
    }

    #[test]
    fn file_names_are_stripped_of_path_separators() {
        assert_eq!(D::sanitize_file_name("a/b:c*d", "fb"), "a_b_c_d");
        assert_eq!(D::sanitize_file_name("   ", "fb"), "fb");
        assert_eq!(D::sanitize_file_name("....", "fb"), "fb");
        assert_eq!(D::sanitize_file_name("../../etc/passwd", "fb"), "___etc_passwd");
        assert_eq!(D::sanitize_file_name("a\0b", "fb"), "a_b");
    }

    #[test]
    fn long_utf8_file_names_are_truncated_by_bytes_without_breaking_characters() {
        let name = "Ảnh mùa thu Việt Nam ".repeat(30);
        let safe = D::sanitize_file_name(&name, "media");

        assert!(safe.len() <= 120);
        assert!(safe.is_char_boundary(safe.len()));
        assert!(!safe.ends_with('.'));
        assert!(!safe.is_empty());
    }

    #[tokio::test]
    async fn test_batch_collision_same_title_no_overwrite_and_unique_titles() {
        let temp_dir = std::env::temp_dir().join(format!("test_batch_collision_{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&temp_dir).unwrap();

        // Part A: 3 media có CÙNG title "image"
        let mut reserved = std::collections::HashSet::new();
        let items_same_title = vec![
            ("image", "jpg", "content 1"),
            ("image", "jpg", "content 2"),
            ("image", "jpg", "content 3"),
        ];

        let mut paths_same_title = Vec::new();
        for (title, ext, content) in &items_same_title {
            let path = D::resolve_collision_free_path(&temp_dir, title, ext, &mut reserved);
            std::fs::write(&path, content.as_bytes()).unwrap();
            paths_same_title.push(path);
        }

        // Kiểm tra tất cả file đều tồn tại, có đường dẫn riêng biệt và KHÔNG bị overwrite
        assert_eq!(paths_same_title.len(), 3);
        assert_ne!(paths_same_title[0], paths_same_title[1]);
        assert_ne!(paths_same_title[1], paths_same_title[2]);
        assert_ne!(paths_same_title[0], paths_same_title[2]);

        for (idx, path) in paths_same_title.iter().enumerate() {
            assert!(path.exists(), "Tệp phải tồn tại: {:?}", path);
            let read_content = std::fs::read_to_string(path).unwrap();
            assert_eq!(read_content, format!("content {}", idx + 1), "Nội dung tệp không được bị ghi đè");
        }

        // Part B: Media có normal unique titles
        let items_unique = vec![
            ("sunset_101", "jpg", "sunset bytes"),
            ("portrait_102", "jpg", "portrait bytes"),
        ];
        let mut paths_unique = Vec::new();
        for (title, ext, content) in &items_unique {
            let path = D::resolve_collision_free_path(&temp_dir, title, ext, &mut reserved);
            std::fs::write(&path, content.as_bytes()).unwrap();
            paths_unique.push(path);
        }

        assert_eq!(paths_unique[0], temp_dir.join("sunset_101.jpg"));
        assert_eq!(paths_unique[1], temp_dir.join("portrait_102.jpg"));
        assert_eq!(std::fs::read_to_string(&paths_unique[0]).unwrap(), "sunset bytes");
        assert_eq!(std::fs::read_to_string(&paths_unique[1]).unwrap(), "portrait bytes");

        let _ = std::fs::remove_dir_all(&temp_dir);
        println!("TEST #1: PASS");
    }

    #[tokio::test]
    async fn test_batch_collision_clean_state_concurrent_race_conditions() {
        // Lặp lại test batch collision từ TRẠNG THÁI SẠCH
        let temp_dir = std::env::temp_dir().join(format!("test_race_condition_{}", uuid::Uuid::new_v4()));
        if temp_dir.exists() {
            let _ = std::fs::remove_dir_all(&temp_dir);
        }
        std::fs::create_dir_all(&temp_dir).unwrap();

        // Giả lập 8 media đều có cùng title "image" hoặc "image.jpg" tải concurrent
        let total_items = 8;
        let mut reserved = std::collections::HashSet::new();
        let mut planned = Vec::with_capacity(total_items);

        for i in 0..total_items {
            let path = D::resolve_collision_free_path(&temp_dir, "image.jpg", "jpg", &mut reserved);
            planned.push((i, path));
        }

        // Kiểm tra toàn bộ path đã cấp phát trước đều độc nhất
        let distinct_paths: std::collections::HashSet<_> = planned.iter().map(|(_, p)| p.clone()).collect();
        assert_eq!(distinct_paths.len(), total_items, "Tất cả các path được cấp phát phải độc nhất");

        // Chạy concurrent qua tokio::spawn mô phỏng MAX_PARALLEL tải cùng lúc để kiểm tra race condition
        let semaphore = std::sync::Arc::new(tokio::sync::Semaphore::new(5));
        let mut handles = Vec::new();

        for (item_id, target_file) in planned {
            let sem = std::sync::Arc::clone(&semaphore);
            handles.push(tokio::spawn(async move {
                let _permit = sem.acquire_owned().await.unwrap();
                tokio::time::sleep(tokio::time::Duration::from_millis(5 * (item_id as u64 % 3 + 1))).await;
                std::fs::write(&target_file, format!("unique payload data for item {item_id}")).unwrap();
                target_file
            }));
        }

        let mut completed_paths = Vec::new();
        for handle in handles {
            let path = handle.await.unwrap();
            completed_paths.push(path);
        }

        assert_eq!(completed_paths.len(), total_items);

        // Kiểm tra tất cả file đều tồn tại và nội dung nguyên vẹn, không bị race condition ghi đè
        for i in 0..total_items {
            let expected_name = if i == 0 {
                "image.jpg".to_string()
            } else {
                format!("image_{i}.jpg")
            };
            let expected_file = temp_dir.join(&expected_name);
            assert!(expected_file.exists(), "Tệp {:?} phải tồn tại sau khi tải concurrent", expected_file);
            let content = std::fs::read_to_string(&expected_file).unwrap();
            assert_eq!(content, format!("unique payload data for item {i}"));
        }

        let _ = std::fs::remove_dir_all(&temp_dir);
        println!("TEST #2: PASS");
    }

    /// #4 — mỗi album phải vào một thư mục SẠCH, không dùng lại thư mục còn tệp cũ
    /// (nếu không, bước nén ZIP sẽ gói lẫn tệp của lần tải trước).
    #[test]
    fn album_dir_is_never_reused_when_it_still_has_files() {
        let base = std::env::temp_dir().join(format!("test_album_alloc_{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&base).unwrap();

        // Lần 1: thư mục chưa tồn tại → dùng đúng tên album
        let first = D::allocate_album_dir(&base, "Album");
        assert_eq!(first, base.join("Album"));
        std::fs::create_dir_all(&first).unwrap();

        // Thư mục tồn tại nhưng RỖNG → vẫn dùng lại được
        assert_eq!(D::allocate_album_dir(&base, "Album"), first);

        // Có tệp cũ bên trong → phải cấp thư mục mới
        std::fs::write(first.join("anh_cu.jpg"), b"stale").unwrap();
        let second = D::allocate_album_dir(&base, "Album");
        assert_eq!(second, base.join("Album_1"));
        assert_ne!(second, first);

        std::fs::create_dir_all(&second).unwrap();
        std::fs::write(second.join("anh_moi.jpg"), b"new").unwrap();
        let third = D::allocate_album_dir(&base, "Album");
        assert_eq!(third, base.join("Album_2"));

        // Tệp cũ không bị đụng tới
        assert_eq!(std::fs::read_to_string(first.join("anh_cu.jpg")).unwrap(), "stale");

        let _ = std::fs::remove_dir_all(&base);
    }

    /// #5 — ZIP mới không được ghi đè ZIP cũ cùng tên
    #[test]
    fn zip_name_does_not_overwrite_an_existing_archive() {
        let base = std::env::temp_dir().join(format!("test_zip_name_{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&base).unwrap();

        let mut reserved = std::collections::HashSet::new();
        let p1 = D::resolve_collision_free_path(&base, "Album.zip", "zip", &mut reserved);
        assert_eq!(p1, base.join("Album.zip"));
        std::fs::write(&p1, b"zip cu").unwrap();

        let mut reserved2 = std::collections::HashSet::new();
        let p2 = D::resolve_collision_free_path(&base, "Album.zip", "zip", &mut reserved2);
        assert_ne!(p2, p1, "ZIP thứ hai phải có tên khác");
        assert_eq!(p2, base.join("Album_1.zip"));

        // Nội dung ZIP cũ còn nguyên
        assert_eq!(std::fs::read_to_string(&p1).unwrap(), "zip cu");

        let _ = std::fs::remove_dir_all(&base);
    }

    /// #3 — compress_dir_to_zip phải trả lỗi (không im lặng) khi không ghi được
    #[test]
    fn compress_dir_to_zip_reports_failure_instead_of_pretending_success() {
        let src = std::env::temp_dir().join(format!("test_zip_fail_src_{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&src).unwrap();
        std::fs::write(src.join("a.jpg"), b"x").unwrap();

        // Ghi ZIP vào một đường dẫn không thể tạo được (thư mục cha không tồn tại)
        let bad_zip = std::env::temp_dir()
            .join(format!("khong_ton_tai_{}", uuid::Uuid::new_v4()))
            .join("out.zip");

        let res = D::compress_dir_to_zip(&src, &bad_zip);
        assert!(res.is_err(), "Nén vào đường dẫn không hợp lệ phải trả Err");
        assert!(!bad_zip.exists());

        let _ = std::fs::remove_dir_all(&src);
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

    #[test]
    fn test_zip_packaging_images_only() {
        use std::io::Read;

        let temp_dir = std::env::temp_dir().join(format!("test_album_img_{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&temp_dir).unwrap();

        for i in 1..=5 {
            let file_path = temp_dir.join(format!("image_{i}.jpg"));
            std::fs::write(&file_path, format!("fake image content {i}").as_bytes()).unwrap();
        }

        let zip_path = std::env::temp_dir().join(format!("test_album_img_{}.zip", uuid::Uuid::new_v4()));
        let res = D::compress_dir_to_zip(&temp_dir, &zip_path);
        assert!(res.is_ok(), "Nén ZIP images only phải thành công: {:?}", res.err());

        // Dọn dẹp thư mục nguồn giống logic backend
        let _ = std::fs::remove_dir_all(&temp_dir);

        assert!(!temp_dir.exists(), "Thư mục tạm phải được xóa sau khi nén");
        assert!(zip_path.exists(), "Tệp ZIP phải tồn tại");

        let zip_file = std::fs::File::open(&zip_path).unwrap();
        let mut archive = zip::ZipArchive::new(zip_file).unwrap();
        assert_eq!(archive.len(), 5, "Số lượng tệp trong ZIP phải là 5");

        for i in 1..=5 {
            let mut f = archive.by_name(&format!("image_{i}.jpg")).unwrap();
            let mut content = String::new();
            f.read_to_string(&mut content).unwrap();
            assert_eq!(content, format!("fake image content {i}"));
        }

        let _ = std::fs::remove_file(&zip_path);
    }

    #[test]
    fn test_zip_packaging_videos_only() {
        use std::io::Read;

        let temp_dir = std::env::temp_dir().join(format!("test_album_vid_{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&temp_dir).unwrap();

        for i in 1..=3 {
            let file_path = temp_dir.join(format!("video_{i}.mp4"));
            std::fs::write(&file_path, format!("fake video mp4 stream {i}").as_bytes()).unwrap();
        }

        let zip_path = std::env::temp_dir().join(format!("test_album_vid_{}.zip", uuid::Uuid::new_v4()));
        let res = D::compress_dir_to_zip(&temp_dir, &zip_path);
        assert!(res.is_ok(), "Nén ZIP videos only phải thành công: {:?}", res.err());

        // Dọn dẹp thư mục nguồn
        let _ = std::fs::remove_dir_all(&temp_dir);

        assert!(!temp_dir.exists(), "Thư mục tạm phải được xóa sau khi nén");
        assert!(zip_path.exists(), "Tệp ZIP phải tồn tại");

        let zip_file = std::fs::File::open(&zip_path).unwrap();
        let mut archive = zip::ZipArchive::new(zip_file).unwrap();
        assert_eq!(archive.len(), 3, "Số lượng tệp trong ZIP phải là 3");

        for i in 1..=3 {
            let mut f = archive.by_name(&format!("video_{i}.mp4")).unwrap();
            let mut content = String::new();
            f.read_to_string(&mut content).unwrap();
            assert_eq!(content, format!("fake video mp4 stream {i}"));
        }

        let _ = std::fs::remove_file(&zip_path);
    }

    #[test]
    fn test_zip_packaging_mixed_images_and_videos() {
        use std::io::Read;

        let temp_dir = std::env::temp_dir().join(format!("test_album_mixed_{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&temp_dir).unwrap();

        // 5 images
        for i in 1..=5 {
            let file_path = temp_dir.join(format!("image_{i}.jpg"));
            std::fs::write(&file_path, format!("image bytes {i}").as_bytes()).unwrap();
        }

        // 3 videos
        for i in 1..=3 {
            let file_path = temp_dir.join(format!("video_{i}.mp4"));
            std::fs::write(&file_path, format!("video stream bytes {i}").as_bytes()).unwrap();
        }

        let zip_path = std::env::temp_dir().join(format!("test_album_mixed_{}.zip", uuid::Uuid::new_v4()));
        let res = D::compress_dir_to_zip(&temp_dir, &zip_path);
        assert!(res.is_ok(), "Nén ZIP mixed media phải thành công: {:?}", res.err());

        // Dọn dẹp thư mục nguồn
        let _ = std::fs::remove_dir_all(&temp_dir);

        // Kiểm tra không có tệp nào rải rác bên ngoài ZIP
        assert!(!temp_dir.exists(), "Thư mục nguồn phải bị xóa hoàn toàn");
        assert!(zip_path.exists(), "Tệp ZIP duy nhất phải tồn tại");

        let zip_file = std::fs::File::open(&zip_path).unwrap();
        let mut archive = zip::ZipArchive::new(zip_file).unwrap();
        assert_eq!(archive.len(), 8, "Số lượng tệp trong ZIP phải chính xác 8 (5 images + 3 videos)");

        for i in 1..=5 {
            let mut f = archive.by_name(&format!("image_{i}.jpg")).unwrap();
            let mut content = String::new();
            f.read_to_string(&mut content).unwrap();
            assert_eq!(content, format!("image bytes {i}"));
        }

        for i in 1..=3 {
            let mut f = archive.by_name(&format!("video_{i}.mp4")).unwrap();
            let mut content = String::new();
            f.read_to_string(&mut content).unwrap();
            assert_eq!(content, format!("video stream bytes {i}"));
        }

        let _ = std::fs::remove_file(&zip_path);
    }

    async fn run_mock_server() -> (String, tokio::sync::oneshot::Sender<()>) {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let (shutdown_tx, mut shutdown_rx) = tokio::sync::oneshot::channel::<()>();

        tokio::spawn(async move {
            loop {
                tokio::select! {
                    _ = &mut shutdown_rx => break,
                    Ok((mut socket, _)) = listener.accept() => {
                        tokio::spawn(async move {
                            use tokio::io::{AsyncReadExt, AsyncWriteExt};
                            let mut buf = [0u8; 1024];
                            let n = match socket.read(&mut buf).await {
                                Ok(n) if n > 0 => n,
                                _ => return,
                            };
                            let req = String::from_utf8_lossy(&buf[..n]);
                            let first_line = req.lines().next().unwrap_or("");
                            let path = first_line.split_whitespace().nth(1).unwrap_or("/");

                            let (status, content_type, body): (&str, &str, Vec<u8>) = match path {
                                "/valid_image.jpg" => (
                                    "200 OK",
                                    "image/jpeg",
                                    vec![0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, b'J', b'F', b'I', b'F', 0x00, 0x01, 0x01, 0x01, 0x00, 0x60, 0x00, 0x60, 0x00, 0x00, 0xFF, 0xD9],
                                ),
                                "/valid_video.mp4" => (
                                    "200 OK",
                                    "video/mp4",
                                    vec![0x00, 0x00, 0x00, 0x18, b'f', b't', b'y', b'p', b'm', b'p', b'4', b'2', 0x00, 0x00, 0x00, 0x00, b'm', b'p', b'4', b'2', b'i', b's', b'o', b'm'],
                                ),
                                "/challenge.html" => (
                                    "200 OK",
                                    "text/html; charset=utf-8",
                                    b"<!DOCTYPE html><html><head><title>Just a moment...</title></head><body>Checking your browser before accessing the website. Cloudflare challenge-platform.</body></html>".to_vec(),
                                ),
                                "/error.json" => (
                                    "200 OK",
                                    "application/json",
                                    b"{\"error\": \"Invalid authorization credentials\", \"code\": 401, \"status\": \"error\"}".to_vec(),
                                ),
                                _ => (
                                    "404 Not Found",
                                    "text/plain",
                                    b"Not found".to_vec(),
                                ),
                            };

                            let response = format!(
                                "HTTP/1.1 {status}\r\nContent-Type: {content_type}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                                body.len()
                            );
                            let _ = socket.write_all(response.as_bytes()).await;
                            let _ = socket.write_all(&body).await;
                            let _ = socket.flush().await;
                        });
                    }
                }
            }
        });

        (format!("http://{}", addr), shutdown_tx)
    }

    #[tokio::test]
    async fn test_red_04_download_validation_flow_test1_and_test2() {
        let (server_url, _shutdown) = run_mock_server().await;
        let temp_dir = std::env::temp_dir().join(format!("test_red04_{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&temp_dir).unwrap();

        for test_iteration in [1, 2] {
            println!("--- RUNNING TEST #{test_iteration} ---");

            // 1. Valid image (JPEG)
            let img_path = temp_dir.join(format!("image_run{}.jpg", test_iteration));
            let img_ok = D::curl_to_file(&format!("{server_url}/valid_image.jpg"), "", &img_path).await;
            assert!(img_ok, "Test #{test_iteration}: valid image phải tải thành công");
            assert!(img_path.exists(), "Test #{test_iteration}: file ảnh hợp lệ phải được lưu trên đĩa");
            assert!(img_path.metadata().unwrap().len() > 0);
            let _ = std::fs::remove_file(&img_path);

            // 2. Valid video (MP4)
            let vid_path = temp_dir.join(format!("video_run{}.mp4", test_iteration));
            let vid_ok = D::curl_to_file(&format!("{server_url}/valid_video.mp4"), "", &vid_path).await;
            assert!(vid_ok, "Test #{test_iteration}: valid video phải tải thành công");
            assert!(vid_path.exists(), "Test #{test_iteration}: file video hợp lệ phải được lưu trên đĩa");
            assert!(vid_path.metadata().unwrap().len() > 0);
            let _ = std::fs::remove_file(&vid_path);

            // 3. HTML / Cloudflare Challenge response
            let html_dest = temp_dir.join(format!("challenge_run{}.jpg", test_iteration));
            let html_ok = D::curl_to_file(&format!("{server_url}/challenge.html"), "", &html_dest).await;
            assert!(!html_ok, "Test #{test_iteration}: HTML response phải bị từ chối và trả failure");
            assert!(!html_dest.exists(), "Test #{test_iteration}: HTML response KHÔNG được để lại file rác");

            // 4. JSON Error response
            let json_dest = temp_dir.join(format!("error_run{}.mp4", test_iteration));
            let json_ok = D::curl_to_file(&format!("{server_url}/error.json"), "", &json_dest).await;
            assert!(!json_ok, "Test #{test_iteration}: JSON response phải bị từ chối và trả failure");
            assert!(!json_dest.exists(), "Test #{test_iteration}: JSON response KHÔNG được để lại file rác");

            println!("TEST #{test_iteration}: PASS");
        }

        let _ = std::fs::remove_dir_all(&temp_dir);
    }

    #[test]
    fn test_unit_validate_downloaded_media_cases() {
        let temp_dir = std::env::temp_dir().join(format!("test_val_cases_{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&temp_dir).unwrap();

        // 1. Valid JPEG with image/jpeg
        let jpg_path = temp_dir.join("photo.jpg");
        std::fs::write(&jpg_path, &[0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, b'J', b'F', b'I', b'F', 0x00]).unwrap();
        assert!(D::validate_downloaded_media(&jpg_path, 200, "image/jpeg", "http://test.com/photo.jpg").is_ok());

        // 2. Valid JPEG with application/octet-stream (CDN binary stream)
        assert!(D::validate_downloaded_media(&jpg_path, 200, "application/octet-stream", "http://test.com/photo.jpg").is_ok());

        // 3. Valid MP4 with video/mp4
        let mp4_path = temp_dir.join("clip.mp4");
        std::fs::write(&mp4_path, &[0x00, 0x00, 0x00, 0x18, b'f', b't', b'y', b'p', b'm', b'p', b'4', b'2']).unwrap();
        assert!(D::validate_downloaded_media(&mp4_path, 200, "video/mp4", "http://test.com/clip.mp4").is_ok());

        // 4. Valid PNG with image/png
        let png_path = temp_dir.join("image.png");
        std::fs::write(&png_path, &[0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00]).unwrap();
        assert!(D::validate_downloaded_media(&png_path, 200, "image/png", "http://test.com/image.png").is_ok());

        // 5. Valid WebP with application/octet-stream
        let webp_path = temp_dir.join("image.webp");
        let webp_bytes = b"RIFF....WEBPVP8 ".to_vec();
        std::fs::write(&webp_path, &webp_bytes).unwrap();
        assert!(D::validate_downloaded_media(&webp_path, 200, "application/octet-stream", "http://test.com/image.webp").is_ok());

        // 6. Rejected: text/html Content-Type
        let html_path = temp_dir.join("fake_img.jpg");
        std::fs::write(&html_path, b"<!DOCTYPE html><html><body>Error</body></html>").unwrap();
        let res_html = D::validate_downloaded_media(&html_path, 200, "text/html; charset=utf-8", "http://test.com/fake_img.jpg");
        assert!(res_html.is_err());

        // 7. Rejected: application/json Content-Type
        let json_path = temp_dir.join("fake_vid.mp4");
        std::fs::write(&json_path, b"{\"error\": \"forbidden\"}").unwrap();
        let res_json = D::validate_downloaded_media(&json_path, 200, "application/json", "http://test.com/fake_vid.mp4");
        assert!(res_json.is_err());

        // 8. Rejected: HTML content even if Content-Type is generic application/octet-stream
        let cf_path = temp_dir.join("cf_challenge.jpg");
        std::fs::write(&cf_path, b"<html><head><title>Just a moment...</title></head><body>cf-browser-verification</body></html>").unwrap();
        let res_cf = D::validate_downloaded_media(&cf_path, 200, "application/octet-stream", "http://test.com/cf_challenge.jpg");
        assert!(res_cf.is_err());

        // 9. Rejected: JSON content even if Content-Type is generic
        let json_octet = temp_dir.join("error_octet.mp4");
        std::fs::write(&json_octet, b"{\"message\": \"Resource expired\", \"code\": 403}").unwrap();
        let res_jo = D::validate_downloaded_media(&json_octet, 200, "application/octet-stream", "http://test.com/error_octet.mp4");
        assert!(res_jo.is_err());

        // 10. Rejected: 0-byte file
        let empty_path = temp_dir.join("empty.jpg");
        std::fs::write(&empty_path, b"").unwrap();
        let res_empty = D::validate_downloaded_media(&empty_path, 200, "image/jpeg", "http://test.com/empty.jpg");
        assert!(res_empty.is_err());

        // 11. Rejected: HTTP 403 status code
        assert!(D::validate_downloaded_media(&jpg_path, 403, "image/jpeg", "http://test.com/photo.jpg").is_err());

        // 12. Rejected: .jpg extension with non-media text content
        let text_path = temp_dir.join("bad_ext.jpg");
        std::fs::write(&text_path, b"random corrupted text without any magic bytes").unwrap();
        assert!(D::validate_downloaded_media(&text_path, 200, "application/octet-stream", "http://test.com/bad_ext.jpg").is_err());

        let _ = std::fs::remove_dir_all(&temp_dir);
    }

    #[test]
    fn test_red_15_filename_and_extension_detection_test1() {
        // Test #1: Test các URL có extension:
        // .jpg, .png, .webp, .mp4, .m4s, .ts, .m3u8 và URL không có extension.
        // Kiểm tra output filename.
        let cases = [
            ("https://cdn.example.com/media/photo.jpg", "jpg"),
            ("https://cdn.example.com/media/image.png", "png"),
            ("https://cdn.example.com/media/graphic.webp", "webp"),
            ("https://cdn.example.com/media/clip.mp4", "mp4"),
            ("https://cdn.example.com/media/segment.m4s", "m4s"),
            ("https://cdn.example.com/media/stream.ts", "ts"),
            ("https://cdn.example.com/media/playlist.m3u8", "m3u8"),
            ("https://cdn.example.com/media/vector.svg", "svg"),
            ("https://cdn.example.com/media/photo.avif", "avif"),
            ("https://cdn.example.com/media/movie.webm", "webm"),
            ("https://cdn.example.com/media/anim.gif", "gif"),
            ("https://cdn.example.com/media/nostem_media", ""),
            ("https://cdn.example.com/media/12345?token=xyz", ""),
            ("https://cdn.example.com/media/archive.unknown", ""),
        ];

        for (url, expected_ext) in cases {
            let guessed = D::guess_extension(url);
            assert_eq!(guessed, expected_ext, "URL {url} phải nhận diện extension là '{expected_ext}'");

            let fname_with_title = D::build_target_filename(url, Some("custom_media"), None);
            if expected_ext.is_empty() {
                assert_eq!(fname_with_title, "custom_media", "URL không có extension không được tự gán đuôi");
                assert!(!fname_with_title.ends_with(".jpg"), "URL không có extension tuyệt đối không được là .jpg");
                assert!(!fname_with_title.ends_with('.'), "Không được có dấu chấm thừa ở cuối");
            } else {
                assert_eq!(fname_with_title, format!("custom_media.{expected_ext}"));
            }

            let fname_auto = D::build_target_filename(url, None, None);
            if expected_ext.is_empty() {
                assert!(!fname_auto.ends_with(".jpg"), "media tự sinh cho unknown không được có đuôi .jpg");
                assert!(!fname_auto.ends_with('.'), "Không được có dấu chấm thừa ở cuối");
            } else {
                assert!(fname_auto.ends_with(&format!(".{expected_ext}")));
            }
        }

        println!("TEST #1: PASS");
    }

    #[tokio::test]
    async fn test_red_15_actual_downloader_flow_test2() {
        // Test #2: Lặp lại test với actual downloader flow.
        // Đặc biệt xác nhận: unknown ≠ jpg nếu không có evidence cho JPEG.
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let addr = listener.local_addr().unwrap();
        let (shutdown_tx, mut shutdown_rx) = tokio::sync::oneshot::channel::<()>();

        tokio::spawn(async move {
            loop {
                tokio::select! {
                    _ = &mut shutdown_rx => break,
                    Ok((mut socket, _)) = listener.accept() => {
                        tokio::spawn(async move {
                            use tokio::io::{AsyncReadExt, AsyncWriteExt};
                            let mut buf = [0u8; 1024];
                            let n = match socket.read(&mut buf).await {
                                Ok(n) if n > 0 => n,
                                _ => return,
                            };
                            let req = String::from_utf8_lossy(&buf[..n]);
                            let first_line = req.lines().next().unwrap_or("");
                            let path = first_line.split_whitespace().nth(1).unwrap_or("/");

                            let (status, content_type, body): (&str, &str, Vec<u8>) = match path {
                                "/sample.m4s" => (
                                    "200 OK",
                                    "video/iso.segment",
                                    vec![0x00, 0x00, 0x00, 0x18, b's', b't', b'y', b'p', b'm', b's', b'4', b's', 0x00, 0x00, 0x00, 0x00, b'm', b's', b'4', b's', b'i', b's', b'o', b'm'],
                                ),
                                "/sample.ts" => (
                                    "200 OK",
                                    "video/mp2t",
                                    vec![0x47, 0x40, 0x00, 0x10, 0x00, 0x00, 0x01, 0xBA, 0x44, 0x00, 0x04, 0x00, 0x04, 0x01, 0x01, 0x89],
                                ),
                                "/sample.m3u8" => (
                                    "200 OK",
                                    "application/vnd.apple.mpegurl",
                                    b"#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:10\n".to_vec(),
                                ),
                                "/photo.avif" => (
                                    "200 OK",
                                    "image/avif",
                                    vec![0x00, 0x00, 0x00, 0x1C, b'f', b't', b'y', b'p', b'a', b'v', b'i', b'f', 0x00, 0x00, 0x00, 0x00, b'a', b'v', b'i', b'f', b'm', b'i', b'a', b'f'],
                                ),
                                "/vector.svg" => (
                                    "200 OK",
                                    "image/svg+xml",
                                    b"<svg xmlns=\"http://www.w3.org/2000/svg\" width=\"10\" height=\"10\"><circle cx=\"5\" cy=\"5\" r=\"4\"/></svg>".to_vec(),
                                ),
                                "/nostem_with_ct_png" => (
                                    "200 OK",
                                    "image/png",
                                    vec![0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00],
                                ),
                                "/unknown_octet" => (
                                    "200 OK",
                                    "application/octet-stream",
                                    vec![0x00, 0x00, 0x00, 0x18, b'f', b't', b'y', b'p', b'm', b'p', b'4', b'2', 0x00, 0x00, 0x00, 0x00, b'm', b'p', b'4', b'2', b'i', b's', b'o', b'm'],
                                ),
                                _ => (
                                    "404 Not Found",
                                    "text/plain",
                                    b"Not found".to_vec(),
                                ),
                            };

                            let response = format!(
                                "HTTP/1.1 {status}\r\nContent-Type: {content_type}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                                body.len()
                            );
                            let _ = socket.write_all(response.as_bytes()).await;
                            let _ = socket.write_all(&body).await;
                            let _ = socket.flush().await;
                        });
                    }
                }
            }
        });

        let server_url = format!("http://{}", addr);
        let temp_dir = std::env::temp_dir().join(format!("test_red15_{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&temp_dir).unwrap();

        // 1. Flow test with .m4s URL
        let m4s_url = format!("{server_url}/sample.m4s");
        let initial_m4s = D::build_target_filename(&m4s_url, Some("stream_chunk"), None);
        assert_eq!(initial_m4s, "stream_chunk.m4s");
        let m4s_dest = temp_dir.join(&initial_m4s);
        let ok_m4s = D::curl_to_file(&m4s_url, "", &m4s_dest).await;
        assert!(ok_m4s);
        assert!(m4s_dest.exists());
        assert_eq!(m4s_dest.extension().and_then(|e| e.to_str()), Some("m4s"));

        // 2. Flow test with .ts URL
        let ts_url = format!("{server_url}/sample.ts");
        let initial_ts = D::build_target_filename(&ts_url, Some("stream_segment"), None);
        assert_eq!(initial_ts, "stream_segment.ts");
        let ts_dest = temp_dir.join(&initial_ts);
        let ok_ts = D::curl_to_file(&ts_url, "", &ts_dest).await;
        assert!(ok_ts);
        assert!(ts_dest.exists());
        assert_eq!(ts_dest.extension().and_then(|e| e.to_str()), Some("ts"));

        // 3. Flow test with .avif URL
        let avif_url = format!("{server_url}/photo.avif");
        let initial_avif = D::build_target_filename(&avif_url, Some("avatar"), None);
        assert_eq!(initial_avif, "avatar.avif");
        let avif_dest = temp_dir.join(&initial_avif);
        let ok_avif = D::curl_to_file(&avif_url, "", &avif_dest).await;
        assert!(ok_avif);
        assert!(avif_dest.exists());
        assert_eq!(avif_dest.extension().and_then(|e| e.to_str()), Some("avif"));

        // 4. Flow test with URL without extension but HTTP Content-Type image/png (Priority 1)
        let ct_url = format!("{server_url}/nostem_with_ct_png");
        let initial_ct = D::build_target_filename(&ct_url, Some("custom_png"), None);
        assert_eq!(initial_ct, "custom_png"); // Before download: no extension
        let ct_dest = temp_dir.join(&initial_ct);
        let (ok_ct, detected_ct) = D::curl_to_file_with_meta(&ct_url, "", &ct_dest).await;
        assert!(ok_ct);
        assert_eq!(detected_ct.as_deref(), Some("image/png"));
        // Priority 1: Rename based on Content-Type
        let ext_from_ct = D::extension_from_content_type(detected_ct.as_deref().unwrap()).unwrap();
        assert_eq!(ext_from_ct, "png");
        let final_ct_path = ct_dest.with_extension(ext_from_ct);
        std::fs::rename(&ct_dest, &final_ct_path).unwrap();
        assert!(final_ct_path.exists());
        assert_eq!(final_ct_path.extension().and_then(|e| e.to_str()), Some("png"));

        // 5. Flow test with unknown URL & generic octet-stream Content-Type (NO JPEG EVIDENCE)
        let unk_url = format!("{server_url}/unknown_octet");
        let initial_unk = D::build_target_filename(&unk_url, Some("raw_blob"), None);
        assert_eq!(initial_unk, "raw_blob"); // No extension!
        let unk_dest = temp_dir.join(&initial_unk);
        let (ok_unk, ct_unk) = D::curl_to_file_with_meta(&unk_url, "", &unk_dest).await;
        assert!(ok_unk);
        assert_eq!(ct_unk.as_deref(), Some("application/octet-stream"));
        assert_eq!(D::extension_from_content_type(ct_unk.as_deref().unwrap()), None);
        // CRITICAL CHECK: unknown ≠ jpg
        assert_ne!(unk_dest.extension().and_then(|e| e.to_str()), Some("jpg"), "unknown media KHÔNG được là .jpg");
        assert_eq!(unk_dest.extension(), None, "unknown media phải giữ nguyên no-extension");
        assert!(unk_dest.exists());

        let _ = shutdown_tx.send(());
        let _ = std::fs::remove_dir_all(&temp_dir);

        println!("TEST #2: PASS");
    }

    #[tokio::test]
    async fn test_cancellation_registry_and_partial_cleanup() {
        let temp_dir = std::env::temp_dir().join(format!("test_cancel_{}", std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()));
        std::fs::create_dir_all(&temp_dir).unwrap();

        let partial_file = temp_dir.join("sample_video.mp4.part");
        let ytdl_file = temp_dir.join("sample_video.mp4.ytdl");
        let direct_file = temp_dir.join("direct_download.jpg");
        let album_dir = temp_dir.join("Album_Test");
        std::fs::create_dir_all(&album_dir).unwrap();
        let album_inner = album_dir.join("photo_1.jpg");

        std::fs::write(&partial_file, b"partial video bytes").unwrap();
        std::fs::write(&ytdl_file, b"ytdl metadata").unwrap();
        std::fs::write(&direct_file, b"jpeg bytes").unwrap();
        std::fs::write(&album_inner, b"album inner bytes").unwrap();

        assert!(partial_file.exists());
        assert!(ytdl_file.exists());
        assert!(direct_file.exists());
        assert!(album_inner.exists());

        let task_id = format!("task_test_cleanup_{}", std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos());
        let (cancel_tx, _cancel_rx) = tokio::sync::oneshot::channel();
        let _state = D::register_task(
            &task_id,
            Some(temp_dir.clone()),
            cancel_tx,
        ).await;

        D::record_file_path(&task_id, direct_file.clone()).await;
        D::record_dir_path(&task_id, album_dir.clone()).await;

        // Cancel the task
        let cancelled = D::cancel_download(&task_id).await.unwrap();
        assert!(cancelled, "cancel_download should return true for registered task");

        // Verify that partial files, known files, and album dirs are wiped clean
        assert!(!partial_file.exists(), ".part file must be deleted");
        assert!(!ytdl_file.exists(), ".ytdl file must be deleted");
        assert!(!direct_file.exists(), "direct file must be deleted");
        assert!(!album_dir.exists(), "album directory must be deleted");

        // Clean up temp dir
        let _ = std::fs::remove_dir_all(&temp_dir);
    }

    #[tokio::test]
    async fn test_cancellation_batch_prefix_matching() {
        let temp_dir = std::env::temp_dir().join(format!("test_cancel_prefix_{}", std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()));
        std::fs::create_dir_all(&temp_dir).unwrap();

        let parent_id = format!("batch_{}", std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos());
        let sub1_id = format!("{}_vid_0", parent_id);
        let sub2_id = format!("{}_vid_1", parent_id);

        let sub1_file = temp_dir.join("video_1.mp4.part");
        let sub2_file = temp_dir.join("video_2.mp4.part");
        std::fs::write(&sub1_file, b"sub 1 bytes").unwrap();
        std::fs::write(&sub2_file, b"sub 2 bytes").unwrap();

        let (cancel_tx1, _rx1) = tokio::sync::oneshot::channel();
        let (cancel_tx2, _rx2) = tokio::sync::oneshot::channel();
        D::register_task(&sub1_id, Some(temp_dir.clone()), cancel_tx1).await;
        D::register_task(&sub2_id, Some(temp_dir.clone()), cancel_tx2).await;

        // Cancelling by parent_id should match and cancel all subtasks
        let cancelled = D::cancel_download(&parent_id).await.unwrap();
        assert!(cancelled, "Cancelling parent batch id must succeed");

        assert!(!sub1_file.exists(), "sub1 .part file must be deleted");
        assert!(!sub2_file.exists(), "sub2 .part file must be deleted");

        let _ = std::fs::remove_dir_all(&temp_dir);
    }

    #[tokio::test]
    async fn test_cancellation_catches_late_subtask_registration() {
        let parent_id = format!(
            "late_cancel_{}",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        );
        let child_id = format!("{parent_id}_vid_0");

        // Hủy ngay trong khoảng task con chưa kịp đăng ký.
        assert!(D::cancel_download(&parent_id).await.unwrap());

        let (cancel_tx, mut cancel_rx) = tokio::sync::oneshot::channel();
        D::register_task(&child_id, None, cancel_tx).await;

        assert!(D::is_download_cancelled(&child_id).await);
        assert!(cancel_rx.try_recv().is_ok());
    }
}
