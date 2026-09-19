use std::sync::Arc;
use serde_json::Value;
use tauri::{AppHandle, State};

use crate::binary_manager::{AllBinaryStatus, BinaryManager};
use crate::cookies::{CookieService, CookieStatusResult, SaveCookieResult};
use crate::db::{Database, DownloadHistoryRecord};
use crate::downloader::{DirectFileItem, DownloadOptions, DownloadResult, DownloaderService};
use crate::settings::{AppSettings, SettingsManager};
use crate::sidecar::SidecarManager;
use crate::system::{BrowserInfo, SystemHealthInfo, SystemService};

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
) -> Result<Value, String> {
    let res = state.sidecar.extract_media(&url, browser.as_deref()).await?;
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

#[tauri::command]
pub async fn select_download_directory() -> Option<String> {
    DownloaderService::select_directory().await
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

#[tauri::command]
pub async fn get_system_health() -> SystemHealthInfo {
    SystemService::get_health_info().await
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
    device_id: Option<String>,
) -> Result<DownloadResult, String> {
    let opts = DownloadOptions {
        url,
        format_id: Some("thumbnail".to_string()),
        title,
        browser,
        device_id: device_id.unwrap_or_default(),
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
    device_id: Option<String>,
) -> Result<DownloadResult, String> {
    let opts = DownloadOptions {
        url,
        format_id: Some(format!("subtitle:{}:{}", lang, format.as_deref().unwrap_or("vtt"))),
        title,
        browser,
        device_id: device_id.unwrap_or_default(),
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
// App Settings Commands (thay thế NestJS DownloaderConfig)
// ─────────────────────────────────────────────────────────────────────────────

#[tauri::command]
pub fn get_app_settings() -> AppSettings {
    SettingsManager::load()
}

#[tauri::command]
pub fn save_app_settings(settings: AppSettings) -> Result<(), String> {
    SettingsManager::save(&settings)
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
    client_ip: Option<String>,
) -> Result<DownloadResult, String> {
    let dev_id = device_id.unwrap_or_else(|| "desktop_default".to_string());
    DownloaderService::download_direct_file(
        &url,
        filename.as_deref(),
        referer.as_deref(),
        dest_dir.as_deref(),
        &dev_id,
        client_ip.as_deref(),
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
    as_zip: Option<bool>,
    device_id: Option<String>,
    task_id: Option<String>,
    client_ip: Option<String>,
) -> Result<DownloadResult, String> {
    let dev_id = device_id.unwrap_or_else(|| "desktop_default".to_string());
    DownloaderService::download_album_batch(
        app,
        items,
        album_name.as_deref(),
        dest_dir.as_deref(),
        as_zip.unwrap_or(false),
        &dev_id,
        task_id,
        client_ip.as_deref(),
        Arc::clone(&state.db),
    ).await
}
