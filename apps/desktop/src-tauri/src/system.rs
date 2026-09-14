use std::path::PathBuf;
use serde::{Deserialize, Serialize};

use crate::binary_manager::BinaryManager;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BrowserInfo {
    pub id: String,
    pub name: String,
    pub installed: bool,
    pub detected: bool,
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
    /// Kiểm tra xem binary có tồn tại trong PATH hệ thống không
    fn is_binary_in_path(name: &str) -> bool {
        let paths = [
            format!("/usr/bin/{name}"),
            format!("/usr/local/bin/{name}"),
            format!("/bin/{name}"),
            format!("/snap/bin/{name}"),
        ];
        paths.iter().any(|p| std::path::Path::new(p).exists())
    }

    /// Lấy danh sách trình duyệt khả dụng trên Linux (hỗ trợ .config, Snap, Flatpak & Binary PATH)
    pub fn get_browsers_list() -> Vec<BrowserInfo> {
        let home = dirs::home_dir().unwrap_or_else(|| PathBuf::from("/tmp"));

        let browser_defs = vec![
            (
                "edge",
                "Microsoft Edge",
                vec![
                    home.join(".config/microsoft-edge"),
                    home.join(".config/microsoft-edge-dev"),
                    home.join(".config/microsoft-edge-beta"),
                    home.join(".var/app/com.microsoft.Edge/config/microsoft-edge"),
                ],
                vec!["microsoft-edge", "microsoft-edge-stable", "microsoft-edge-dev"],
            ),
            (
                "firefox",
                "Mozilla Firefox",
                vec![
                    home.join(".mozilla/firefox"),
                    home.join("snap/firefox/common/.mozilla/firefox"),
                    home.join(".var/app/org.mozilla.firefox/.mozilla/firefox"),
                ],
                vec!["firefox", "firefox-esr"],
            ),
            (
                "chrome",
                "Google Chrome",
                vec![
                    home.join(".config/google-chrome"),
                    home.join(".config/google-chrome-beta"),
                    home.join(".var/app/com.google.Chrome/config/google-chrome"),
                ],
                vec!["google-chrome", "google-chrome-stable"],
            ),
            (
                "chromium",
                "Chromium",
                vec![
                    home.join(".config/chromium"),
                    home.join("snap/chromium/common/chromium"),
                    home.join(".var/app/org.chromium.Chromium/config/chromium"),
                ],
                vec!["chromium", "chromium-browser"],
            ),
            (
                "brave",
                "Brave Browser",
                vec![
                    home.join(".config/BraveSoftware/Brave-Browser"),
                    home.join(".var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser"),
                ],
                vec!["brave-browser", "brave"],
            ),
            (
                "opera",
                "Opera",
                vec![
                    home.join(".config/opera"),
                    home.join("snap/opera/current/.config/opera"),
                ],
                vec!["opera"],
            ),
            (
                "vivaldi",
                "Vivaldi",
                vec![
                    home.join(".config/vivaldi"),
                    home.join(".var/app/com.vivaldi.Vivaldi/config/vivaldi"),
                ],
                vec!["vivaldi", "vivaldi-stable"],
            ),
        ];

        browser_defs
            .into_iter()
            .map(|(id, name, config_paths, bin_names)| {
                // Kiểm tra xem có thư mục profile cookies nào tồn tại không
                let found_config = config_paths.into_iter().find(|p| p.exists());
                // Kiểm tra xem binary có trong PATH không
                let binary_found = bin_names.iter().any(|b| Self::is_binary_in_path(b));

                let is_installed = found_config.is_some() || binary_found;
                let cookie_path = found_config.map(|p| p.to_string_lossy().to_string());

                BrowserInfo {
                    id: id.to_string(),
                    name: name.to_string(),
                    installed: is_installed,
                    detected: is_installed,
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
