use std::path::PathBuf;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BrowserInfo {
    pub id: String,
    pub name: String,
    pub installed: bool,
    pub detected: bool,
    pub cookie_path: Option<String>,
}

pub struct SystemService;

impl SystemService {
    /// Kiểm tra xem binary có tồn tại trong PATH hệ thống không
    fn is_binary_in_path(name: &str) -> bool {
        if which::which(name).is_ok() {
            return true;
        }
        #[cfg(windows)]
        {
            if which::which(format!("{name}.exe")).is_ok() {
                return true;
            }
        }
        let paths = [
            format!("/usr/bin/{name}"),
            format!("/usr/local/bin/{name}"),
            format!("/bin/{name}"),
            format!("/snap/bin/{name}"),
        ];
        paths.iter().any(|p| std::path::Path::new(p).exists())
    }

    /// Lấy danh sách trình duyệt khả dụng trên Linux & Windows (hỗ trợ .config, LocalAppData, Snap, Flatpak & PATH)
    pub fn get_browsers_list() -> Vec<BrowserInfo> {
        let home = dirs::home_dir().unwrap_or_else(|| PathBuf::from("/tmp"));
        let local_app_data = dirs::data_local_dir();
        let app_data = dirs::config_dir();

        // 1. Edge
        let mut edge_paths = vec![
            home.join(".config/microsoft-edge"),
            home.join(".config/microsoft-edge-dev"),
            home.join(".config/microsoft-edge-beta"),
            home.join(".var/app/com.microsoft.Edge/config/microsoft-edge"),
        ];
        if let Some(ref l) = local_app_data {
            edge_paths.push(l.join("Microsoft").join("Edge").join("User Data"));
        }

        // 2. Firefox
        let mut firefox_paths = vec![
            home.join(".mozilla/firefox"),
            home.join("snap/firefox/common/.mozilla/firefox"),
            home.join(".var/app/org.mozilla.firefox/.mozilla/firefox"),
        ];
        if let Some(ref a) = app_data {
            firefox_paths.push(a.join("Mozilla").join("Firefox").join("Profiles"));
        }

        // 3. Chrome
        let mut chrome_paths = vec![
            home.join(".config/google-chrome"),
            home.join(".config/google-chrome-beta"),
            home.join(".var/app/com.google.Chrome/config/google-chrome"),
        ];
        if let Some(ref l) = local_app_data {
            chrome_paths.push(l.join("Google").join("Chrome").join("User Data"));
        }

        // 4. Chromium
        let chromium_paths = vec![
            home.join(".config/chromium"),
            home.join("snap/chromium/common/chromium"),
            home.join(".var/app/org.chromium.Chromium/config/chromium"),
        ];

        // 5. Brave
        let mut brave_paths = vec![
            home.join(".config/BraveSoftware/Brave-Browser"),
            home.join(".var/app/com.brave.Browser/config/BraveSoftware/Brave-Browser"),
        ];
        if let Some(ref l) = local_app_data {
            brave_paths.push(l.join("BraveSoftware").join("Brave-Browser").join("User Data"));
        }

        // 6. Opera
        let mut opera_paths = vec![
            home.join(".config/opera"),
            home.join("snap/opera/current/.config/opera"),
        ];
        if let Some(ref a) = app_data {
            opera_paths.push(a.join("Opera Software").join("Opera Stable"));
        }

        // 7. Vivaldi
        let mut vivaldi_paths = vec![
            home.join(".config/vivaldi"),
            home.join(".var/app/com.vivaldi.Vivaldi/config/vivaldi"),
        ];
        if let Some(ref l) = local_app_data {
            vivaldi_paths.push(l.join("Vivaldi").join("User Data"));
        }

        let browser_defs = vec![
            ("edge", "Microsoft Edge", edge_paths, vec!["microsoft-edge", "microsoft-edge-stable", "microsoft-edge-dev", "msedge"]),
            ("firefox", "Mozilla Firefox", firefox_paths, vec!["firefox", "firefox-esr"]),
            ("chrome", "Google Chrome", chrome_paths, vec!["google-chrome", "google-chrome-stable", "chrome"]),
            ("chromium", "Chromium", chromium_paths, vec!["chromium", "chromium-browser"]),
            ("brave", "Brave Browser", brave_paths, vec!["brave-browser", "brave"]),
            ("opera", "Opera", opera_paths, vec!["opera"]),
            ("vivaldi", "Vivaldi", vivaldi_paths, vec!["vivaldi", "vivaldi-stable"]),
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

}
