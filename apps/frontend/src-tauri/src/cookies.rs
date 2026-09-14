use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;
use std::path::PathBuf;
use log::{info, warn};

/// Thư mục lưu cookie file theo platform
fn cookies_dir() -> PathBuf {
    dirs::config_dir()
        .unwrap_or_else(|| PathBuf::from("/tmp"))
        .join("crwl")
        .join("cookies")
}

/// Đường dẫn file cookie cho từng platform
fn cookie_file_path(platform: &str) -> PathBuf {
    cookies_dir().join(format!("{}.txt", platform.to_lowercase()))
}

/// Đảm bảo thư mục cookies tồn tại
fn ensure_cookies_dir() -> std::io::Result<()> {
    let dir = cookies_dir();
    if !dir.exists() {
        std::fs::create_dir_all(&dir)?;
    }
    Ok(())
}

// ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PlatformCookieStatus {
    pub platform: String,
    pub has_cookies: bool,
    pub file_path: Option<String>,
    pub size_bytes: Option<u64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CookieStatusResult {
    pub platforms: Vec<PlatformCookieStatus>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SaveCookieResult {
    pub success: bool,
    pub platform: String,
    pub file_path: String,
    pub message: String,
}

// ─────────────────────────────────────────────────────────────────────────────

pub struct CookieService;

impl CookieService {
    /// Ánh xạ platform sang cookie domain gốc
    pub fn platform_to_domain(platform: &str) -> &'static str {
        match platform.to_lowercase().as_str() {
            "instagram" => ".instagram.com",
            "tiktok" => ".tiktok.com",
            "facebook" => ".facebook.com",
            "twitter" | "x" => ".x.com",
            "youtube" | "google" => ".youtube.com",
            "pinterest" => ".pinterest.com",
            "threads" => ".threads.net",
            "linkedin" => ".linkedin.com",
            "pixiv" => ".pixiv.net",
            "reddit" => ".reddit.com",
            "bilibili" => ".bilibili.com",
            "douyin" => ".douyin.com",
            "soundcloud" => ".soundcloud.com",
            "tumblr" => ".tumblr.com",
            _ => ".social.com",
        }
    }

    /// Chuyển đổi cookie string (Netscape, JSON hoặc Header key=val) sang chuẩn Netscape HTTP Cookie
    pub fn convert_to_netscape(platform: &str, raw: &str) -> (String, usize) {
        let trimmed = raw.trim();

        // 1. Đã là Netscape format
        if trimmed.starts_with("# Netscape HTTP Cookie File") || (trimmed.contains("\tTRUE\t") || trimmed.contains("\tFALSE\t")) {
            let count = trimmed.lines().filter(|l| !l.starts_with('#') && !l.trim().is_empty()).count();
            return (trimmed.to_string(), count);
        }

        let default_domain = Self::platform_to_domain(platform);
        let expiry = (chrono::Utc::now().timestamp() + 30 * 86400).to_string();

        // 2. Định dạng JSON (từ extension Cookie-Editor hoặc EditThisCookie)
        if trimmed.starts_with('[') && trimmed.ends_with(']') {
            if let Ok(Value::Array(items)) = serde_json::from_str::<Value>(trimmed) {
                let mut lines = vec![
                    "# Netscape HTTP Cookie File".to_string(),
                    format!("# Converted for {platform} from JSON"),
                    "".to_string(),
                ];
                let mut count = 0;
                for item in items {
                    if let Value::Object(obj) = item {
                        let name = obj.get("name").and_then(|v| v.as_str()).unwrap_or("");
                        let value = obj.get("value").and_then(|v| v.as_str()).unwrap_or("");
                        if name.is_empty() {
                            continue;
                        }
                        let domain = obj.get("domain").and_then(|v| v.as_str()).unwrap_or(default_domain);
                        let path = obj.get("path").and_then(|v| v.as_str()).unwrap_or("/");
                        let secure = if obj.get("secure").and_then(|v| v.as_bool()).unwrap_or(false) { "TRUE" } else { "FALSE" };
                        let item_exp = obj.get("expirationDate")
                            .and_then(|v| v.as_i64())
                            .map(|e| e.to_string())
                            .unwrap_or_else(|| expiry.clone());

                        let is_domain = if domain.starts_with('.') { "TRUE" } else { "FALSE" };
                        lines.push(format!("{domain}\t{is_domain}\t{path}\t{secure}\t{item_exp}\t{name}\t{value}"));
                        count += 1;
                    }
                }
                if count > 0 {
                    return (lines.join("\n"), count);
                }
            }
        }

        // 3. Định dạng chuỗi Header: "name=value; name2=value2"
        let clean_raw = trimmed.strip_prefix("cookie:").or_else(|| trimmed.strip_prefix("Cookie:")).unwrap_or(trimmed);
        let mut lines = vec![
            "# Netscape HTTP Cookie File".to_string(),
            format!("# Converted for {platform} from Key-Value string"),
            "".to_string(),
        ];
        let mut count = 0;

        for pair in clean_raw.split(';') {
            let p = pair.trim();
            if p.is_empty() {
                continue;
            }
            if let Some((name, val)) = p.split_once('=') {
                let n = name.trim();
                let v = val.trim();
                if !n.is_empty() {
                    lines.push(format!("{default_domain}\tTRUE\t/\tFALSE\t{expiry}\t{n}\t{v}"));
                    count += 1;
                }
            }
        }

        if count > 0 {
            (lines.join("\n"), count)
        } else {
            (trimmed.to_string(), 1)
        }
    }

    /// Lưu cookie string vào file cho một platform
    pub fn save_cookies(platform: &str, cookie_string: &str) -> Result<SaveCookieResult, String> {
        ensure_cookies_dir().map_err(|e| format!("Không tạo được thư mục cookie: {e}"))?;

        let file_path = cookie_file_path(platform);

        // Validate: phải có nội dung hợp lệ
        let trimmed = cookie_string.trim();
        if trimmed.is_empty() {
            return Err("Cookie string không được để trống".to_string());
        }

        let (netscape_content, count) = Self::convert_to_netscape(platform, trimmed);

        std::fs::write(&file_path, netscape_content)
            .map_err(|e| format!("Không thể ghi file cookie: {e}"))?;

        let path_str = file_path.to_string_lossy().to_string();
        info!("Đã lưu {count} cookie(s) cho platform '{platform}' vào: {path_str}");

        Ok(SaveCookieResult {
            success: true,
            platform: platform.to_string(),
            file_path: path_str,
            message: format!("Đã lưu thành công {count} cookie cho '{platform}' theo chuẩn Netscape"),
        })
    }

    /// Lấy trạng thái cookie của tất cả các platform được hỗ trợ
    pub fn get_cookie_status() -> CookieStatusResult {
        let supported = Self::supported_platforms();
        let mut statuses = Vec::new();

        for platform in &supported {
            let file_path = cookie_file_path(platform);
            let has_cookies = file_path.exists()
                && file_path
                    .metadata()
                    .map(|m| m.len() > 0)
                    .unwrap_or(false);

            let size_bytes = if has_cookies {
                file_path.metadata().map(|m| m.len()).ok()
            } else {
                None
            };

            statuses.push(PlatformCookieStatus {
                platform: platform.clone(),
                has_cookies,
                file_path: if has_cookies {
                    Some(file_path.to_string_lossy().to_string())
                } else {
                    None
                },
                size_bytes,
            });
        }

        CookieStatusResult { platforms: statuses }
    }

    /// Xóa cookie file của một platform (hoặc tất cả nếu domain = None)
    pub fn delete_cookies(platform: Option<&str>) -> Result<bool, String> {
        match platform {
            Some(p) => {
                let file_path = cookie_file_path(p);
                if file_path.exists() {
                    std::fs::remove_file(&file_path)
                        .map_err(|e| format!("Không thể xóa cookie file: {e}"))?;
                    info!("Đã xóa cookie cho platform: {}", p);
                }
                Ok(true)
            }
            None => {
                // Xóa tất cả cookie files trong thư mục
                let dir = cookies_dir();
                if !dir.exists() {
                    return Ok(true);
                }

                let entries = std::fs::read_dir(&dir)
                    .map_err(|e| format!("Không thể đọc thư mục cookie: {e}"))?;

                for entry in entries.flatten() {
                    let path = entry.path();
                    if path.extension().and_then(|e| e.to_str()) == Some("txt") {
                        if let Err(e) = std::fs::remove_file(&path) {
                            warn!("Không xóa được {}: {e}", path.display());
                        }
                    }
                }
                info!("Đã xóa toàn bộ cookie files");
                Ok(true)
            }
        }
    }

    /// Lấy đường dẫn cookie file cho yt-dlp (dùng --cookies flag)
    pub fn get_cookie_file_path(platform: &str) -> Option<PathBuf> {
        let path = cookie_file_path(platform);
        if path.exists() && path.metadata().map(|m| m.len() > 0).unwrap_or(false) {
            Some(path)
        } else {
            None
        }
    }

    /// Lấy map platform -> cookie_file_path cho tất cả platforms có cookie
    #[allow(dead_code)]
    pub fn get_all_cookie_paths() -> HashMap<String, PathBuf> {
        let mut map = HashMap::new();
        for platform in Self::supported_platforms() {
            if let Some(path) = Self::get_cookie_file_path(&platform) {
                map.insert(platform, path);
            }
        }
        map
    }

    /// Danh sách các platform hỗ trợ cookie
    pub fn supported_platforms() -> Vec<String> {
        vec![
            "instagram".to_string(),
            "tiktok".to_string(),
            "facebook".to_string(),
            "twitter".to_string(),
            "youtube".to_string(),
            "pinterest".to_string(),
            "threads".to_string(),
            "pixiv".to_string(),
            "reddit".to_string(),
            "bilibili".to_string(),
            "douyin".to_string(),
            "soundcloud".to_string(),
            "tumblr".to_string(),
            "linkedin".to_string(),
        ]
    }
}
