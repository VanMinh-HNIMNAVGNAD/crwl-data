use std::sync::Arc;
use serde_json::Value;
use tauri::{AppHandle, State};

use crate::binary_manager::{AllBinaryStatus, BinaryManager};
use crate::cookies::{CookieService, CookieStatusResult, SaveCookieResult};
use crate::db::{Database, DownloadHistoryRecord};
use crate::downloader::{DirectFileItem, DownloadOptions, DownloadResult, DownloaderService};
use crate::settings::{validate_retention_days, AppSettings, SettingsManager};
use crate::sidecar::{SidecarManager, WorkerStatus};
use crate::system::{BrowserInfo, SystemService};

pub struct AppState {
    pub db: Arc<Database>,
    pub sidecar: Arc<SidecarManager>,
}

#[tauri::command]
pub async fn extract_media(
    state: State<'_, AppState>,
    url: String,
    browser: Option<String>,
    device_id: Option<String>,
    client_ip: Option<String>,
    task_id: Option<String>,
) -> Result<Value, String> {
    let res = state
        .sidecar
        .extract_media(&url, browser.as_deref(), task_id.as_deref())
        .await?;
    let db = Arc::clone(&state.db);
    let dev_id = device_id.unwrap_or_else(|| "desktop_default".to_string());
    let url_clone = url.clone();
    let res_clone = res.clone();
    let ip_clone = client_ip;
    tokio::spawn(async move {
        db.record_single_extraction(&dev_id, &url_clone, &res_clone, ip_clone.as_deref()).await;
    });
    Ok(res)
}

#[tauri::command]
pub async fn crawl_profile(
    state: State<'_, AppState>,
    url: String,
    limit: Option<u32>,
    media_type: Option<String>,
    platform: Option<String>,
    browser: Option<String>,
    range_start: Option<u32>,
    range_end: Option<u32>,
    device_id: Option<String>,
    client_ip: Option<String>,
    task_id: Option<String>,
) -> Result<Value, String> {
    let res = state
        .sidecar
        .crawl_profile(
            &url,
            limit,
            media_type.as_deref(),
            platform.as_deref(),
            browser.as_deref(),
            range_start,
            range_end,
            task_id.as_deref(),
        )
        .await?;
    let db = Arc::clone(&state.db);
    let dev_id = device_id.unwrap_or_else(|| "desktop_default".to_string());
    let url_clone = url.clone();
    let res_clone = res.clone();
    let ip_clone = client_ip;
    tokio::spawn(async move {
        db.record_profile_crawl(&dev_id, &url_clone, &res_clone, ip_clone.as_deref()).await;
    });
    Ok(res)
}

#[tauri::command]
pub async fn resolve_short_url(
    state: State<'_, AppState>,
    url: String,
    expected_platform: Option<String>,
) -> Result<Value, String> {
    state
        .sidecar
        .resolve_url(&url, expected_platform.as_deref())
        .await
}

#[tauri::command]
pub async fn start_download(
    app: AppHandle,
    state: State<'_, AppState>,
    options: DownloadOptions,
) -> Result<DownloadResult, String> {
    DownloaderService::start_download(app, Arc::clone(&state.db), options).await
}

/// `remember = true`: lưu thư mục vừa chọn thành thư mục tải mặc định (nút 📁).
/// `remember = false`: chỉ dùng cho lượt tải này (chế độ "Hỏi trước khi tải").
#[tauri::command]
pub async fn select_download_directory(remember: Option<bool>) -> Result<Option<String>, String> {
    DownloaderService::select_directory(remember.unwrap_or(false)).await
}

#[tauri::command]
pub fn get_default_download_directory() -> String {
    DownloaderService::get_default_download_dir()
        .to_string_lossy()
        .to_string()
}

#[tauri::command]
pub fn open_download_folder(path: String) -> Result<(), String> {
    DownloaderService::open_folder(&path)
}

#[tauri::command]
pub async fn get_download_history(
    state: State<'_, AppState>,
    limit: Option<i64>,
    device_id: Option<String>,
) -> Result<Vec<DownloadHistoryRecord>, String> {
    let lim = limit.unwrap_or(30);
    Ok(state.db.get_recent_downloads(lim, device_id.as_deref()).await)
}

#[tauri::command]
pub async fn clear_download_history(
    state: State<'_, AppState>,
    device_id: Option<String>,
) -> Result<bool, String> {
    Ok(state.db.clear_download_history(device_id.as_deref()).await)
}

#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct HistoryRetentionResult {
    /// Số ngày giữ lịch sử đang áp dụng; None = đã tắt tự động xoá
    pub days: Option<u32>,
    /// Số mục lịch sử quá hạn đã bị xoá ngay khi áp dụng
    pub removed: u64,
}

/// Số mục lịch sử sẽ bị xoá ngay nếu bật "tự xoá sau `days` ngày" — để giao diện
/// hỏi xác nhận trước một thao tác không hoàn tác được.
#[tauri::command]
pub async fn preview_history_purge(state: State<'_, AppState>, days: u32) -> Result<i64, String> {
    let days = validate_retention_days(days)?;
    // Chưa kết nối được DB thì cũng chưa có lịch sử nào để xoá
    Ok(state.db.count_history_older_than(days).await.unwrap_or(0))
}

/// Bật (days = Some) / tắt (None) tự động xoá lịch sử tải. Khi bật, các mục đã
/// quá hạn bị xoá ngay; sau đó app tự dọn lúc khởi động và định kỳ (xem lib.rs).
#[tauri::command]
pub async fn set_history_retention(
    state: State<'_, AppState>,
    days: Option<u32>,
) -> Result<HistoryRetentionResult, String> {
    SettingsManager::set_history_retention_days(days)?;
    let removed = match days {
        Some(d) => state.db.purge_history_older_than(d).await.unwrap_or(0),
        None => 0,
    };
    Ok(HistoryRetentionResult { days, removed })
}

#[tauri::command]
pub fn get_browsers_list() -> Vec<BrowserInfo> {
    SystemService::get_browsers_list()
}

// ─────────────────────────────────────────────────────────────────────────────
// Cookie Manager Commands (thay thế NestJS /api/media/cookies)
// ─────────────────────────────────────────────────────────────────────────────

#[tauri::command]
pub fn save_platform_cookies(
    platform: String,
    cookie_string: String,
) -> Result<SaveCookieResult, String> {
    CookieService::save_cookies(&platform, &cookie_string)
}

#[tauri::command]
pub fn get_cookie_status() -> CookieStatusResult {
    CookieService::get_cookie_status()
}

#[tauri::command]
pub fn delete_platform_cookies(platform: Option<String>) -> Result<bool, String> {
    CookieService::delete_cookies(platform.as_deref())
}

#[tauri::command]
pub fn get_supported_cookie_platforms() -> Vec<String> {
    CookieService::supported_platforms()
}

// ─────────────────────────────────────────────────────────────────────────────
// Thumbnail Download Command (thay thế NestJS /api/media/download/thumbnail)
// ─────────────────────────────────────────────────────────────────────────────

#[tauri::command]
pub async fn download_thumbnail(
    app: AppHandle,
    state: State<'_, AppState>,
    url: String,
    title: Option<String>,
    browser: Option<String>,
    dest_dir: Option<String>,
    device_id: Option<String>,
    task_id: Option<String>,
) -> Result<DownloadResult, String> {
    let opts = DownloadOptions {
        url,
        format_id: Some("thumbnail".to_string()),
        title,
        browser,
        dest_dir,
        device_id: device_id.unwrap_or_else(|| "desktop_default".to_string()),
        task_id,
        ..Default::default()
    };
    DownloaderService::start_download(app, Arc::clone(&state.db), opts).await
}

// ─────────────────────────────────────────────────────────────────────────────
// Subtitle Download Command (thay thế NestJS /api/media/download/subtitle)
// ─────────────────────────────────────────────────────────────────────────────

#[tauri::command]
pub async fn download_subtitle(
    app: AppHandle,
    state: State<'_, AppState>,
    url: String,
    lang: String,
    format: Option<String>,
    title: Option<String>,
    browser: Option<String>,
    dest_dir: Option<String>,
    device_id: Option<String>,
    task_id: Option<String>,
) -> Result<DownloadResult, String> {
    let opts = DownloadOptions {
        url,
        format_id: Some(format!("subtitle:{}:{}", lang, format.as_deref().unwrap_or("vtt"))),
        title,
        browser,
        dest_dir,
        device_id: device_id.unwrap_or_else(|| "desktop_default".to_string()),
        task_id,
        ..Default::default()
    };
    DownloaderService::start_download(app, Arc::clone(&state.db), opts).await
}

// ─────────────────────────────────────────────────────────────────────────────
// Binary Manager Commands (thay thế NestJS BinaryManagerService)
// ─────────────────────────────────────────────────────────────────────────────

#[tauri::command]
pub async fn get_binary_status() -> AllBinaryStatus {
    BinaryManager::check_all().await
}

#[tauri::command]
pub async fn update_ytdlp() -> Result<String, String> {
    BinaryManager::update_ytdlp().await
}

#[tauri::command]
pub async fn update_gallery_dl() -> Result<String, String> {
    BinaryManager::update_gallery_dl().await
}

// ─────────────────────────────────────────────────────────────────────────────
// Python Sidecar Lifecycle — cho phép hồi phục khi worker chuyển sang FAILED
// ─────────────────────────────────────────────────────────────────────────────

#[derive(serde::Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SidecarStatusInfo {
    /// uninitialized | starting | running | failed | stopped
    pub state: String,
    pub healthy: bool,
    pub consecutive_crashes: u32,
    pub last_error: Option<String>,
}

/// Huỷ một tác vụ bóc tách / quét đang chạy.
/// Kill luôn tiến trình yt-dlp / gallery-dl phía Python thay vì chỉ bỏ qua kết quả.
#[tauri::command]
pub async fn cancel_extraction(state: State<'_, AppState>, task_id: String) -> Result<bool, String> {
    state.sidecar.cancel(&task_id).await?;
    Ok(true)
}

#[tauri::command]
pub async fn get_sidecar_status(state: State<'_, AppState>) -> Result<SidecarStatusInfo, String> {
    let status = state.sidecar.status().await;
    let name = match &status {
        WorkerStatus::Uninitialized => "uninitialized",
        WorkerStatus::Starting => "starting",
        WorkerStatus::Running => "running",
        WorkerStatus::Failed(_) => "failed",
        WorkerStatus::Stopped => "stopped",
    };
    Ok(SidecarStatusInfo {
        state: name.to_string(),
        healthy: matches!(status, WorkerStatus::Running | WorkerStatus::Starting),
        consecutive_crashes: state.sidecar.consecutive_crashes().await,
        last_error: state.sidecar.last_error().await,
    })
}

/// Khởi động lại Python worker sau khi nó đã chuyển sang trạng thái FAILED.
/// Không có lệnh này thì người dùng buộc phải thoát hẳn ứng dụng mới bóc tách lại được.
#[tauri::command]
pub async fn restart_sidecar(state: State<'_, AppState>) -> Result<String, String> {
    state.sidecar.reset_and_start().await;
    // Lỗi import / cú pháp khiến Python chết ngay sau khi khởi động: chờ một nhịp
    // ngắn rồi mới kết luận, thay vì báo "Đã khởi động lại" trong khi engine đã chết.
    tokio::time::sleep(std::time::Duration::from_millis(1500)).await;
    match state.sidecar.status().await {
        WorkerStatus::Failed(reason) => Err(format!("Không khởi động lại được Python worker: {reason}")),
        WorkerStatus::Stopped => Err("Python worker vẫn đang ở trạng thái dừng.".to_string()),
        _ if state.sidecar.consecutive_crashes().await > 0 => Err(format!(
            "Python worker dừng ngay sau khi khởi động: {}",
            state
                .sidecar
                .last_error()
                .await
                .unwrap_or_else(|| "không rõ nguyên nhân".to_string())
        )),
        _ => Ok("Đã khởi động lại Python worker.".to_string()),
    }
}

// ─────────────────────────────────────────────────────────────────────────────
// App Settings Commands (thay thế NestJS DownloaderConfig)
// ─────────────────────────────────────────────────────────────────────────────

#[tauri::command]
pub fn get_app_settings() -> AppSettings {
    SettingsManager::load()
}

/// Lưu cấu hình. Trả về true nếu engine bóc tách đã được khởi động lại để nhận
/// đường dẫn công cụ / proxy mới (các lượt bóc tách đang chạy sẽ bị ngắt).
#[tauri::command]
pub async fn save_app_settings(
    state: State<'_, AppState>,
    settings: AppSettings,
) -> Result<bool, String> {
    let before = SettingsManager::load();
    // Form "Công cụ" không quản lý mục tự xoá lịch sử (đặt ở modal Lịch sử): giữ
    // nguyên giá trị đang lưu, nếu không mỗi lần bấm "Lưu cấu hình" sẽ tắt nó.
    let settings = SettingsManager::validate(AppSettings {
        history_retention_days: before.history_retention_days,
        ..settings
    })?;
    SettingsManager::save(&settings)?;
    let sidecar_env_changed = before.ytdlp_path != settings.ytdlp_path
        || before.gallery_dl_path != settings.gallery_dl_path
        || before.proxy != settings.proxy;
    if sidecar_env_changed {
        state.sidecar.reset_and_start().await;
    }
    Ok(sidecar_env_changed)
}



// ─────────────────────────────────────────────────────────────────────────────
// Native Album & Direct File Download Commands (thay thế NestJS proxyService)
// ─────────────────────────────────────────────────────────────────────────────

#[tauri::command]
pub async fn download_direct_file(
    state: State<'_, AppState>,
    url: String,
    filename: Option<String>,
    referer: Option<String>,
    dest_dir: Option<String>,
    device_id: Option<String>,
    task_id: Option<String>,
    client_ip: Option<String>,
    platform: Option<String>,
) -> Result<DownloadResult, String> {
    let dev_id = device_id.unwrap_or_else(|| "desktop_default".to_string());
    DownloaderService::download_direct_file(
        &url,
        filename.as_deref(),
        referer.as_deref(),
        dest_dir.as_deref(),
        &dev_id,
        client_ip.as_deref(),
        platform.as_deref(),
        task_id,
        Arc::clone(&state.db),
    ).await
}

#[tauri::command]
pub async fn download_album_batch(
    app: AppHandle,
    state: State<'_, AppState>,
    items: Vec<DirectFileItem>,
    album_name: Option<String>,
    dest_dir: Option<String>,
    album_dir: Option<String>,
    as_zip: Option<bool>,
    device_id: Option<String>,
    task_id: Option<String>,
    client_ip: Option<String>,
    platform: Option<String>,
) -> Result<DownloadResult, String> {
    let dev_id = device_id.unwrap_or_else(|| "desktop_default".to_string());
    DownloaderService::download_album_batch(
        app,
        items,
        album_name.as_deref(),
        dest_dir.as_deref(),
        album_dir.as_deref(),
        as_zip.unwrap_or(false),
        &dev_id,
        task_id,
        client_ip.as_deref(),
        platform.as_deref(),
        Arc::clone(&state.db),
    ).await
}

/// Hủy một tác vụ tải xuống đang chạy (yt-dlp, curl, nén zip) và dọn dẹp sạch sẽ toàn bộ tệp tạm.
#[tauri::command]
pub async fn cancel_download(task_id: String) -> Result<bool, String> {
    DownloaderService::cancel_download(&task_id).await
}
