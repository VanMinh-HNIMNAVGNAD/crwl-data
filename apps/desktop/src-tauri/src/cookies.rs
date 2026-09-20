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
            "pixiv" | "pixiv_net" => ".pixiv.net",
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

        let default_domain = if platform.contains('.') {
            let p = platform.trim().to_lowercase();
            if p.starts_with('.') {
                p
            } else {
                format!(".{p}")
            }
        } else {
            Self::platform_to_domain(platform).to_string()
        };

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
                        let domain = obj.get("domain").and_then(|v| v.as_str()).unwrap_or(&default_domain);
                        let path = obj.get("path").and_then(|v| v.as_str()).unwrap_or("/");
                        let secure = if obj.get("secure").and_then(|v| v.as_bool()).unwrap_or(false) { "TRUE" } else { "FALSE" };
                        let item_exp = obj.get("expirationDate")
                            .and_then(|v| {
                                if let Some(n) = v.as_f64() {
                                    Some(n.trunc() as i64)
                                } else {
                                    v.as_i64()
                                }
                            })
                            .map(|e| e.to_string())
                            .unwrap_or_else(|| "0".to_string());

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
                    lines.push(format!("{default_domain}\tTRUE\t/\tFALSE\t0\t{n}\t{v}"));
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

    /// Chuẩn hóa tên platform hoặc domain về định danh platform chuẩn
    pub fn normalize_platform(raw: &str) -> Option<String> {
        let mut s = raw.trim().to_lowercase();
        if s.is_empty() {
            return None;
        }

        // Loại bỏ schema (http://, https://)
        if let Some(pos) = s.find("://") {
            s = s[pos + 3..].to_string();
        }

        // Bỏ path / query nếu có (vd: pixiv.net/artworks -> pixiv.net)
        if let Some(pos) = s.find('/') {
            s = s[..pos].to_string();
        }
        if let Some(pos) = s.find('?') {
            s = s[..pos].to_string();
        }

        // Loại bỏ www. và dấu chấm đầu
        let s = s.trim_start_matches('.').trim_start_matches("www.");

        // Bỏ đuôi .txt nếu người dùng nhập nhầm tên file
        let s = s.strip_suffix(".txt").unwrap_or(s).trim();

        match s {
            "instagram" | "instagram.com" | "instagr.am" | "ig" => Some("instagram".to_string()),
            "tiktok" | "tiktok.com" => Some("tiktok".to_string()),
            "facebook" | "facebook.com" | "fb.com" | "fb.watch" | "fb.me" | "fb" => Some("facebook".to_string()),
            "twitter" | "twitter.com" | "x.com" | "x" | "t.co" => Some("twitter".to_string()),
            "youtube" | "youtube.com" | "youtu.be" | "youtube-nocookie.com" | "yt" | "google" => Some("youtube".to_string()),
            "pinterest" | "pinterest.com" | "pin.it" => Some("pinterest".to_string()),
            "threads" | "threads.net" => Some("threads".to_string()),
            "pixiv" | "pixiv.net" | "pixiv.me" | "pixiv_net" => Some("pixiv".to_string()),
            "reddit" | "reddit.com" | "redd.it" | "v.redd.it" => Some("reddit".to_string()),
            "bilibili" | "bilibili.com" | "b23.tv" => Some("bilibili".to_string()),
            "douyin" | "douyin.com" => Some("douyin".to_string()),
            "soundcloud" | "soundcloud.com" | "on.soundcloud.com" => Some("soundcloud".to_string()),
            "tumblr" | "tumblr.com" => Some("tumblr".to_string()),
            "linkedin" | "linkedin.com" => Some("linkedin".to_string()),
            _ => {
                if s.ends_with(".pinterest.com") || s.contains("pinterest.") {
                    return Some("pinterest".to_string());
                }
                if s.ends_with(".instagram.com") {
                    return Some("instagram".to_string());
                }
                if s.ends_with(".tiktok.com") {
                    return Some("tiktok".to_string());
                }
                if s.ends_with(".facebook.com") {
                    return Some("facebook".to_string());
                }
                if s.ends_with(".twitter.com") || s.ends_with(".x.com") {
                    return Some("twitter".to_string());
                }
                if s.ends_with(".youtube.com") {
                    return Some("youtube".to_string());
                }
                if s.ends_with(".reddit.com") {
                    return Some("reddit".to_string());
                }
                if s.ends_with(".bilibili.com") {
                    return Some("bilibili".to_string());
                }
                if s.ends_with(".douyin.com") {
                    return Some("douyin".to_string());
                }
                if s.ends_with(".soundcloud.com") {
                    return Some("soundcloud".to_string());
                }
                if s.ends_with(".tumblr.com") {
                    return Some("tumblr".to_string());
                }
                if s.ends_with(".linkedin.com") {
                    return Some("linkedin".to_string());
                }
                if s.ends_with(".threads.net") {
                    return Some("threads".to_string());
                }
                if s.ends_with(".pixiv.net") {
                    return Some("pixiv".to_string());
                }

                let supported = Self::supported_platforms();
                if supported.contains(&s.to_string()) {
                    return Some(s.to_string());
                }

                None
            }
        }
    }

    /// Xác thực và chuẩn hóa tên platform theo `supported_platforms()`
    pub fn validate_and_normalize_platform(raw: &str) -> Result<String, String> {
        let trimmed = raw.trim();
        if trimmed.is_empty() {
            return Err("Tên nền tảng không được để trống".to_string());
        }

        let supported = Self::supported_platforms();
        match Self::normalize_platform(trimmed) {
            Some(norm) if supported.contains(&norm) => Ok(norm),
            _ => Err(format!(
                "Nền tảng '{}' không được hỗ trợ. Các nền tảng hỗ trợ: {}",
                trimmed,
                supported.join(", ")
            )),
        }
    }

    /// Dọn dẹp các file cookie định danh cũ/thừa sau khi lưu
    fn cleanup_legacy_cookie_files(normalized_platform: &str) {
        let dir = cookies_dir();
        if !dir.exists() {
            return;
        }
        let candidates: &[&str] = match normalized_platform {
            "pixiv" => &["pixiv.net.txt", "pixiv_net.txt"],
            "threads" => &["threads.net.txt"],
            "twitter" => &["x.com.txt", "twitter.com.txt", "x.txt"],
            "facebook" => &["facebook.com.txt", "fb.com.txt", "fb.txt"],
            "youtube" => &["youtube.com.txt", "youtu.be.txt", "google.txt"],
            "tiktok" => &["tiktok.com.txt"],
            "instagram" => &["instagram.com.txt"],
            "reddit" => &["reddit.com.txt"],
            "bilibili" => &["bilibili.com.txt"],
            "douyin" => &["douyin.com.txt"],
            "soundcloud" => &["soundcloud.com.txt"],
            "tumblr" => &["tumblr.com.txt"],
            "linkedin" => &["linkedin.com.txt"],
            "pinterest" => &["pinterest.com.txt"],
            _ => &[],
        };
        for legacy in candidates {
            let p = dir.join(legacy);
            if p.exists() {
                let _ = std::fs::remove_file(&p);
            }
        }
    }

    /// Tự động di chuyển các file cookie cũ (vd: pixiv.net.txt -> pixiv.txt) nếu file chuẩn chưa có
    pub fn migrate_legacy_cookie_files() {
        let dir = cookies_dir();
        if !dir.exists() {
            return;
        }
        let legacy_mappings = [
            ("pixiv.net.txt", "pixiv.txt"),
            ("pixiv_net.txt", "pixiv.txt"),
            ("threads.net.txt", "threads.txt"),
            ("x.com.txt", "twitter.txt"),
            ("twitter.com.txt", "twitter.txt"),
            ("facebook.com.txt", "facebook.txt"),
            ("youtube.com.txt", "youtube.txt"),
            ("tiktok.com.txt", "tiktok.txt"),
            ("instagram.com.txt", "instagram.txt"),
            ("reddit.com.txt", "reddit.txt"),
            ("bilibili.com.txt", "bilibili.txt"),
            ("douyin.com.txt", "douyin.txt"),
            ("soundcloud.com.txt", "soundcloud.txt"),
            ("tumblr.com.txt", "tumblr.txt"),
            ("linkedin.com.txt", "linkedin.txt"),
            ("pinterest.com.txt", "pinterest.txt"),
        ];

        for (legacy, standard) in legacy_mappings {
            let legacy_path = dir.join(legacy);
            let standard_path = dir.join(standard);
            if legacy_path.exists() {
                if !standard_path.exists() {
                    let _ = std::fs::rename(&legacy_path, &standard_path);
                } else {
                    let _ = std::fs::remove_file(&legacy_path);
                }
            }
        }
    }

    /// Lưu cookie string vào file cho một platform
    pub fn save_cookies(platform: &str, cookie_string: &str) -> Result<SaveCookieResult, String> {
        let normalized = Self::validate_and_normalize_platform(platform)?;
        ensure_cookies_dir().map_err(|e| format!("Không tạo được thư mục cookie: {e}"))?;

        let file_path = cookie_file_path(&normalized);

        // Validate: phải có nội dung hợp lệ
        let trimmed = cookie_string.trim();
        if trimmed.is_empty() {
            return Err("Cookie string không được để trống".to_string());
        }

        let (netscape_content, count) = Self::convert_to_netscape(&normalized, trimmed);

        std::fs::write(&file_path, netscape_content)
            .map_err(|e| format!("Không thể ghi file cookie: {e}"))?;

        Self::cleanup_legacy_cookie_files(&normalized);

        let path_str = file_path.to_string_lossy().to_string();
        info!("Đã lưu {count} cookie(s) cho platform '{normalized}' vào: {path_str}");

        Ok(SaveCookieResult {
            success: true,
            platform: normalized.clone(),
            file_path: path_str,
            message: format!("Đã lưu thành công {count} cookie cho '{normalized}' theo chuẩn Netscape"),
        })
    }

    /// Lấy trạng thái cookie của tất cả các platform được hỗ trợ
    pub fn get_cookie_status() -> CookieStatusResult {
        Self::migrate_legacy_cookie_files();
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
                let norm = Self::normalize_platform(p).unwrap_or_else(|| p.to_lowercase());
                let file_path = cookie_file_path(&norm);
                if file_path.exists() {
                    std::fs::remove_file(&file_path)
                        .map_err(|e| format!("Không thể xóa cookie file: {e}"))?;
                    info!("Đã xóa cookie cho platform: {}", norm);
                }
                Self::cleanup_legacy_cookie_files(&norm);
                let raw_path = cookie_file_path(p);
                if raw_path.exists() && raw_path != file_path {
                    let _ = std::fs::remove_file(&raw_path);
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
        let norm = Self::normalize_platform(platform).unwrap_or_else(|| platform.to_lowercase());
        let path = cookie_file_path(&norm);
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_convert_to_netscape_with_custom_domain_dot() {
        let (output, count) = CookieService::convert_to_netscape("pixiv.net", "test=123");
        assert_eq!(count, 1);
        assert!(output.contains(".pixiv.net\tTRUE\t/\tFALSE\t"));
        assert!(output.contains("\ttest\t123"));

        let (output_leading_dot, count_dot) = CookieService::convert_to_netscape(".pixiv.net", "test=123");
        assert_eq!(count_dot, 1);
        assert!(output_leading_dot.contains(".pixiv.net\tTRUE\t/\tFALSE\t"));
    }

    #[test]
    fn test_convert_to_netscape_with_platform_without_dot() {
        let (output, count) = CookieService::convert_to_netscape("pixiv", "test=123");
        assert_eq!(count, 1);
        assert!(output.contains(".pixiv.net\tTRUE\t/\tFALSE\t"));

        let (output_insta, count_insta) = CookieService::convert_to_netscape("instagram", "sessionid=abc");
        assert_eq!(count_insta, 1);
        assert!(output_insta.contains(".instagram.com\tTRUE\t/\tFALSE\t"));
    }

    #[test]
    fn test_convert_to_netscape_json_custom_domain() {
        let json = r#"[{"name": "session", "value": "xyz"}]"#;
        let (output, count) = CookieService::convert_to_netscape("pixiv.net", json);
        assert_eq!(count, 1);
        assert!(output.contains(".pixiv.net\tTRUE\t/\tFALSE\t"));
        assert!(output.contains("\tsession\txyz"));
    }

    #[test]
    fn test_convert_to_netscape_fallback_social_com() {
        let (output, count) = CookieService::convert_to_netscape("unknown_platform", "key=val");
        assert_eq!(count, 1);
        assert!(output.contains(".social.com\tTRUE\t/\tFALSE\t"));
    }

    #[test]
    fn test_normalize_platform() {
        assert_eq!(CookieService::normalize_platform("pixiv.net"), Some("pixiv".to_string()));
        assert_eq!(CookieService::normalize_platform(".pixiv.net"), Some("pixiv".to_string()));
        assert_eq!(CookieService::normalize_platform("https://www.pixiv.net/artworks/123"), Some("pixiv".to_string()));
        assert_eq!(CookieService::normalize_platform("pixiv.net.txt"), Some("pixiv".to_string()));
        assert_eq!(CookieService::normalize_platform("pixiv"), Some("pixiv".to_string()));
        assert_eq!(CookieService::normalize_platform("threads.net"), Some("threads".to_string()));
        assert_eq!(CookieService::normalize_platform("x.com"), Some("twitter".to_string()));
        assert_eq!(CookieService::normalize_platform("twitter.com"), Some("twitter".to_string()));
        assert_eq!(CookieService::normalize_platform("fb.com"), Some("facebook".to_string()));
        assert_eq!(CookieService::normalize_platform("facebook.com"), Some("facebook".to_string()));
        assert_eq!(CookieService::normalize_platform("youtu.be"), Some("youtube".to_string()));
        assert_eq!(CookieService::normalize_platform("b23.tv"), Some("bilibili".to_string()));
        assert_eq!(CookieService::normalize_platform("unknown-domain.xyz"), None);
        assert_eq!(CookieService::normalize_platform("   "), None);
    }

    #[test]
    fn test_validate_and_normalize_platform() {
        assert_eq!(CookieService::validate_and_normalize_platform("pixiv.net").unwrap(), "pixiv");
        assert_eq!(CookieService::validate_and_normalize_platform("threads").unwrap(), "threads");
        assert_eq!(CookieService::validate_and_normalize_platform("x.com").unwrap(), "twitter");

        let err_empty = CookieService::validate_and_normalize_platform("  ");
        assert!(err_empty.is_err());
        assert!(err_empty.unwrap_err().contains("không được để trống"));

        let err_unsupported = CookieService::validate_and_normalize_platform("invalidplatform.xyz");
        assert!(err_unsupported.is_err());
        assert!(err_unsupported.unwrap_err().contains("không được hỗ trợ"));
    }

    #[test]
    fn test_save_cookies_validation() {
        let res = CookieService::save_cookies("invalidplatform.xyz", "cookie=123");
        assert!(res.is_err());
        assert!(res.unwrap_err().contains("không được hỗ trợ"));

        let empty_res = CookieService::save_cookies("pixiv.net", "   ");
        assert!(empty_res.is_err());
        assert!(empty_res.unwrap_err().contains("không được để trống"));
    }

    #[test]
    fn test_cookie_expiry_semantics() {
        // Test 1: persistent cookie + expirationDate
        let json_persistent = r#"[{"name": "persist", "value": "1", "expirationDate": 1790000000}]"#;
        let (out_persist, _) = CookieService::convert_to_netscape("test.com", json_persistent);
        assert!(out_persist.contains("\t1790000000\tpersist\t1"));

        // Test 1: session cookie + no expirationDate
        let json_session = r#"[{"name": "sess", "value": "2"}]"#;
        let (out_sess, _) = CookieService::convert_to_netscape("test.com", json_session);
        assert!(out_sess.contains("\t0\tsess\t2"));
        
        // Test 1: expired cookie
        let json_expired = r#"[{"name": "exp", "value": "3", "expirationDate": 1200000000}]"#;
        let (out_exp, _) = CookieService::convert_to_netscape("test.com", json_expired);
        assert!(out_exp.contains("\t1200000000\texp\t3"));
        
        // Test 1: cookie header string -> session cookie
        let header_str = "cookie: sess=abc; another=123";
        let (out_hdr, _) = CookieService::convert_to_netscape("test.com", header_str);
        assert!(out_hdr.contains("\t0\tsess\tabc"));
        assert!(out_hdr.contains("\t0\tanother\t123"));
    }
}


