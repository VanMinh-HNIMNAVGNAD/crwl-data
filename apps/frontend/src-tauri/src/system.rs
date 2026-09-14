use std::path::PathBuf;
use serde::{Deserialize, Serialize};

use crate::binary_manager::BinaryManager;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BrowserInfo {
    pub id: String,
    pub name: String,
    pub installed: bool,
    pub cookie_path: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SystemHealthInfo {
    pub ytdlp_installed: bool,
    pub ytdlp_path: Option<String>,
    pub ytdlp_version: Option<String>,
    pub gallery_dl_installed: bool,
    pub gallery_dl_path: Option<String>,
    pub ffmpeg_installed: bool,
    pub ffmpeg_path: Option<String>,
    pub python_installed: bool,
    pub python_path: Option<String>,
    pub python_version: Option<String>,
    pub os: String,
    pub arch: String,
}

pub struct SystemService;

impl SystemService {
    /// Lấy danh sách trình duyệt khả dụng trên Linux
    pub fn get_browsers_list() -> Vec<BrowserInfo> {
        let home = dirs::home_dir().unwrap_or_else(|| PathBuf::from("/tmp"));
        let checks = vec![
            ("edge", "Microsoft Edge", home.join(".config/microsoft-edge")),
            ("chrome", "Google Chrome", home.join(".config/google-chrome")),
            ("chromium", "Chromium", home.join(".config/chromium")),
            ("brave", "Brave Browser", home.join(".config/BraveSoftware/Brave-Browser")),
            ("firefox", "Mozilla Firefox", home.join(".mozilla/firefox")),
            ("opera", "Opera", home.join(".config/opera")),
            ("vivaldi", "Vivaldi", home.join(".config/vivaldi")),
        ];

        checks
            .into_iter()
            .map(|(id, name, path)| {
                let installed = path.exists();
                let cookie_path = if installed {
                    Some(path.to_string_lossy().to_string())
                } else {
                    None
                };
                BrowserInfo {
                    id: id.to_string(),
                    name: name.to_string(),
                    installed,
                    cookie_path,
                }
            })
            .collect()
    }

    /// Kiểm tra trạng thái các công cụ lõi — delegates to BinaryManager
    pub async fn get_health_info() -> SystemHealthInfo {
        let status = BinaryManager::check_all().await;
        SystemHealthInfo {
            ytdlp_installed: status.ytdlp.is_installed,
            ytdlp_path: status.ytdlp.path,
            ytdlp_version: status.ytdlp.version,
            gallery_dl_installed: status.gallery_dl.is_installed,
            gallery_dl_path: status.gallery_dl.path,
            ffmpeg_installed: status.ffmpeg.is_installed,
            ffmpeg_path: status.ffmpeg.path,
            python_installed: status.python3.is_installed,
            python_path: status.python3.path,
            python_version: status.python3.version,
            os: std::env::consts::OS.to_string(),
            arch: std::env::consts::ARCH.to_string(),
        }
    }
}
