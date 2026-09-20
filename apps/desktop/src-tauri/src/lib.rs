mod binary_manager;
mod commands;
mod cookies;
mod db;
mod downloader;
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

            // Khởi tạo Database pool trong nền (spawn) để không block việc khởi động và hiển thị cửa sổ
            let db = Arc::new(Database::new());
            let db_clone = Arc::clone(&db);
            tauri::async_runtime::spawn(async move {
                db_clone.init().await;
            });

            // Tìm đường dẫn Python CLI
            let res_dir = app.path().resource_dir().ok();
            let cli_path = SidecarManager::find_cli_path(res_dir.as_ref())
                .unwrap_or_else(|_| std::path::PathBuf::from("core/extractor_cli.py"));

            // Khởi động Python Sidecar IPC worker ở NỀN. Trước đây dùng block_on
            // nên cửa sổ chỉ hiện sau khi Python spawn xong; nếu Python lỗi/chậm
            // thì người dùng nhìn màn hình trắng. send_request() đã tự chờ worker
            // sẵn sàng nên việc hoãn khởi động là an toàn.
            let sidecar = Arc::new(SidecarManager::new_idle(cli_path));
            let sidecar_boot = Arc::clone(&sidecar);
            tauri::async_runtime::spawn(async move {
                sidecar_boot.start_worker().await;
            });

            app.manage(AppState { db, sidecar });
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            // ── Media Extraction (qua Python Sidecar IPC) ─────────────────
            commands::extract_media,
            commands::crawl_profile,
            commands::resolve_short_url,
            // ── Python Sidecar lifecycle ──────────────────────────────────
            commands::cancel_extraction,
            commands::get_sidecar_status,
            commands::restart_sidecar,
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
