mod binary_manager;
mod commands;
mod cookies;
mod db;
mod downloader;
mod extractor;
mod settings;
mod sidecar;
mod system;

use std::sync::Arc;
use commands::AppState;
use db::Database;
use log::LevelFilter;
use sidecar::SidecarManager;
use settings::SettingsManager;
use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(
            tauri_plugin_log::Builder::default()
                .level(LevelFilter::Info)
                .build(),
        )
        .setup(|app| {
            // Tạo thư mục config và .env mẫu nếu chưa có
            SettingsManager::ensure_env_template();

            // Khởi tạo Database pool
            let db = tauri::async_runtime::block_on(async { Database::init().await });

            // Tìm đường dẫn Python CLI
            let cli_path = SidecarManager::find_cli_path()
                .unwrap_or_else(|_| std::path::PathBuf::from("core/extractor_cli.py"));

            // Khởi động Python Sidecar IPC worker
            let sidecar = tauri::async_runtime::block_on(async {
                SidecarManager::new(cli_path).await
            });

            app.manage(AppState {
                db: Arc::new(db),
                sidecar: Arc::new(sidecar),
            });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            // ── Media Extraction (qua Python Sidecar IPC) ─────────────────
            commands::extract_media,
            commands::crawl_profile,
            commands::resolve_short_url,
            // ── Downloads (yt-dlp native Rust) ────────────────────────────
            commands::start_download,
            commands::download_thumbnail,
            commands::download_subtitle,
            // ── File System ───────────────────────────────────────────────
            commands::select_download_directory,
            commands::get_default_download_directory,
            commands::open_download_folder,
            // ── History (PostgreSQL) ──────────────────────────────────────
            commands::get_download_history,
            commands::clear_download_history,
            // ── System ────────────────────────────────────────────────────
            commands::get_system_health,
            commands::get_browsers_list,
            // ── Cookie Manager (native file) ──────────────────────────────
            commands::save_platform_cookies,
            commands::get_cookie_status,
            commands::delete_platform_cookies,
            commands::get_supported_cookie_platforms,
            // ── Binary Manager ────────────────────────────────────────────
            commands::get_binary_status,
            commands::update_ytdlp,
            commands::update_gallery_dl,
            // ── App Settings ──────────────────────────────────────────────
            commands::get_app_settings,
            commands::save_app_settings,
            // ── Direct & Album Downloads ──────────────────────────────────
            commands::download_direct_file,
            commands::download_album_batch,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
