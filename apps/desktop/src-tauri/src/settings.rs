/**
 * Settings Manager — Đọc/ghi cấu hình app vào ~/.config/crwl/settings.json.
 * Thay thế NestJS DownloaderConfig và AppConfig.
 */
use std::path::PathBuf;
use log::{info, warn};
use serde::{Deserialize, Serialize};

fn settings_path() -> PathBuf {
    dirs::config_dir()
        .unwrap_or_else(|| PathBuf::from("/tmp"))
        .join("crwl")
        .join("settings.json")
}

fn ensure_config_dir() -> std::io::Result<()> {
    let dir = settings_path().parent().unwrap().to_path_buf();
    if !dir.exists() {
        std::fs::create_dir_all(&dir)?;
    }
    Ok(())
}

/// `~/x` → `<home>/x`. Hộp nhập ở màn hình Công cụ là ô chữ tự do, và PathBuf
/// không tự hiểu dấu `~` nên đường dẫn kiểu đó chưa bao giờ dùng được.
fn expand_home(raw: &str) -> Option<PathBuf> {
    if raw == "~" {
        return dirs::home_dir();
    }
    if let Some(rest) = raw.strip_prefix("~/").or_else(|| raw.strip_prefix("~\\")) {
        return dirs::home_dir().map(|home| home.join(rest));
    }
    Some(PathBuf::from(raw))
}

/// Đường dẫn công cụ do người dùng nhập phải là một tệp chạy được.
fn validate_executable(label: &str, raw: &str) -> Result<String, String> {
    let path = expand_home(raw)
        .filter(|p| p.is_absolute())
        .ok_or_else(|| format!("Đường dẫn {label} phải là đường dẫn tuyệt đối: {raw}"))?;
    if !path.is_file() {
        return Err(format!("Không tìm thấy tệp {label} tại: {raw}"));
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = std::fs::metadata(&path).map(|m| m.permissions().mode()).unwrap_or(0);
        if mode & 0o111 == 0 {
            return Err(format!("Tệp {label} không có quyền thực thi: {raw} (chạy: chmod +x \"{raw}\")"));
        }
    }
    Ok(path.to_string_lossy().to_string())
}

/// Proxy hợp lệ cho cả yt-dlp, gallery-dl (requests), curl và Chromium.
/// Thiếu giao thức thì mặc định http:// như cách yt-dlp/curl tự hiểu.
pub(crate) fn normalize_proxy(raw: &str) -> Result<String, String> {
    let trimmed = raw.trim();
    let with_scheme = if trimmed.contains("://") {
        trimmed.to_string()
    } else {
        format!("http://{trimmed}")
    };
    let parsed = url::Url::parse(&with_scheme).map_err(|_| format!("Proxy không hợp lệ: {trimmed}"))?;
    if !matches!(parsed.scheme(), "http" | "https" | "socks4" | "socks4a" | "socks5" | "socks5h") {
        return Err(format!(
            "Proxy không hỗ trợ giao thức '{}' — dùng http, https, socks4 hoặc socks5",
            parsed.scheme()
        ));
    }
    if parsed.host_str().map(str::is_empty).unwrap_or(true) {
        return Err(format!("Proxy thiếu địa chỉ máy chủ: {trimmed}"));
    }
    Ok(with_scheme)
}

/// Giới hạn hợp lệ của "tự động xoá lịch sử sau N ngày" (tối đa ~10 năm).
pub const MAX_HISTORY_RETENTION_DAYS: u32 = 3650;

pub(crate) fn validate_retention_days(days: u32) -> Result<u32, String> {
    if (1..=MAX_HISTORY_RETENTION_DAYS).contains(&days) {
        Ok(days)
    } else {
        Err(format!(
            "Số ngày giữ lịch sử phải từ 1 đến {MAX_HISTORY_RETENTION_DAYS} (đang nhập: {days})"
        ))
    }
}

// ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct AppSettings {
    /// Thư mục tải về mặc định
    #[serde(skip_serializing_if = "Option::is_none")]
    pub download_dir: Option<String>,

    /// Đường dẫn tùy chỉnh tới yt-dlp binary
    #[serde(skip_serializing_if = "Option::is_none")]
    pub ytdlp_path: Option<String>,

    /// Đường dẫn tùy chỉnh tới gallery-dl binary
    #[serde(skip_serializing_if = "Option::is_none")]
    pub gallery_dl_path: Option<String>,

    /// Proxy dùng cho cả bóc tách lẫn tải (vd. http://127.0.0.1:8080, socks5://...)
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub proxy: Option<String>,

    /// Tự động xoá lịch sử tải cũ hơn số ngày này. None = giữ mãi.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub history_retention_days: Option<u32>,

    /// Phiên bản settings schema (để migrate sau này)
    #[serde(default = "default_version")]
    pub schema_version: u32,
}

fn default_version() -> u32 {
    1
}

impl Default for AppSettings {
    fn default() -> Self {
        Self {
            download_dir: None,
            ytdlp_path: None,
            gallery_dl_path: None,
            proxy: None,
            history_retention_days: None,
            schema_version: 1,
        }
    }
}

// ─────────────────────────────────────────────────────────────────────────────

pub struct SettingsManager;

impl SettingsManager {
    /// Đường dẫn binary do người dùng chỉ định, chỉ trả về khi tệp tồn tại và
    /// có quyền thực thi — cấu hình sai không được làm hỏng luồng tải.
    pub fn custom_binary_path(which: &str) -> Option<PathBuf> {
        let s = Self::load();
        let raw = match which {
            "yt-dlp" => s.ytdlp_path,
            "gallery-dl" => s.gallery_dl_path,
            _ => None,
        }?;
        let trimmed = raw.trim();
        if trimmed.is_empty() {
            return None;
        }
        let p = PathBuf::from(trimmed);
        if p.is_file() {
            Some(p)
        } else {
            warn!("[Settings] Bỏ qua đường dẫn {which} không hợp lệ: {trimmed}");
            None
        }
    }

    /// Thư mục tải mặc định do người dùng đặt trong cấu hình
    pub fn custom_download_dir() -> Option<PathBuf> {
        let raw = Self::load().download_dir?;
        let trimmed = raw.trim();
        if trimmed.is_empty() {
            return None;
        }
        let p = PathBuf::from(trimmed);
        if p.is_dir() {
            Some(p)
        } else {
            warn!("[Settings] Bỏ qua thư mục tải không hợp lệ: {trimmed}");
            None
        }
    }

    /// Số ngày giữ lịch sử tải (None = không tự xoá). Giá trị sai trong tệp cấu
    /// sửa tay bị bỏ qua thay vì xoá nhầm cả lịch sử.
    pub fn history_retention_days() -> Option<u32> {
        Self::load()
            .history_retention_days
            .and_then(|days| validate_retention_days(days).ok())
    }

    /// Bật / tắt tự động xoá lịch sử mà KHÔNG đụng tới các mục cấu hình khác.
    pub fn set_history_retention_days(days: Option<u32>) -> Result<(), String> {
        let days = days.map(validate_retention_days).transpose()?;
        let mut settings = Self::load();
        settings.history_retention_days = days;
        Self::save(&settings)
    }

    /// Proxy người dùng cấu hình (đã bỏ khoảng trắng), None nếu để trống.
    pub fn proxy() -> Option<String> {
        Self::load()
            .proxy
            .map(|p| p.trim().to_string())
            .filter(|p| !p.is_empty())
    }

    /// Chuẩn hoá & kiểm tra cấu hình trước khi lưu.
    ///
    /// Trước đây mọi giá trị đều được lưu và báo "Đã lưu", kể cả thư mục không tồn
    /// tại, đường dẫn công cụ gõ nhầm hay proxy sai — rồi chúng bị bỏ qua trong im
    /// lặng: tệp vẫn tải về ~/Downloads, engine vẫn dùng yt-dlp cũ, còn proxy hỏng
    /// làm mọi lượt bóc tách/tải thất bại mà không rõ lý do.
    pub fn validate(settings: AppSettings) -> Result<AppSettings, String> {
        let clean = |v: Option<String>| v.map(|s| s.trim().to_string()).filter(|s| !s.is_empty());

        let download_dir = match clean(settings.download_dir) {
            Some(raw) => {
                let dir = expand_home(&raw)
                    .filter(|p| p.is_absolute())
                    .ok_or_else(|| format!("Thư mục tải mặc định phải là đường dẫn tuyệt đối: {raw}"))?;
                if !dir.is_dir() {
                    return Err(format!("Thư mục tải mặc định không tồn tại: {raw}"));
                }
                Some(dir.to_string_lossy().to_string())
            }
            None => None,
        };

        let ytdlp_path = clean(settings.ytdlp_path)
            .map(|raw| validate_executable("yt-dlp", &raw))
            .transpose()?;
        let gallery_dl_path = clean(settings.gallery_dl_path)
            .map(|raw| validate_executable("gallery-dl", &raw))
            .transpose()?;
        let proxy = clean(settings.proxy).map(|raw| normalize_proxy(&raw)).transpose()?;
        let history_retention_days = settings
            .history_retention_days
            .map(validate_retention_days)
            .transpose()?;

        Ok(AppSettings {
            download_dir,
            ytdlp_path,
            gallery_dl_path,
            proxy,
            history_retention_days,
            schema_version: settings.schema_version,
        })
    }

    /// Đọc settings từ file. Trả về default nếu file chưa tồn tại.
    pub fn load() -> AppSettings {
        let path = settings_path();
        if !path.exists() {
            log::debug!("[Settings] Chưa có file settings, dùng mặc định");
            return AppSettings::default();
        }

        match std::fs::read_to_string(&path) {
            Ok(content) => {
                match serde_json::from_str::<AppSettings>(&content) {
                    Ok(settings) => {
                        log::debug!("[Settings] Đã tải settings từ {:?}", path);
                        settings
                    }
                    Err(e) => {
                        warn!("[Settings] Lỗi parse settings.json ({e}), dùng mặc định");
                        AppSettings::default()
                    }
                }
            }
            Err(e) => {
                warn!("[Settings] Không đọc được settings.json ({e}), dùng mặc định");
                AppSettings::default()
            }
        }
    }

    /// Ghi settings vào file
    pub fn save(settings: &AppSettings) -> Result<(), String> {
        ensure_config_dir().map_err(|e| format!("Không tạo được thư mục config: {e}"))?;

        let path = settings_path();
        let content = serde_json::to_string_pretty(settings)
            .map_err(|e| format!("Không serialize được settings: {e}"))?;

        std::fs::write(&path, content)
            .map_err(|e| format!("Không ghi được settings.json: {e}"))?;

        info!("[Settings] Đã lưu settings vào {:?}", path);
        Ok(())
    }

    /// Tạo file .env mẫu tại ~/.config/crwl/.env nếu chưa tồn tại
    pub fn ensure_env_template() {
        let env_path = dirs::config_dir()
            .unwrap_or_else(|| PathBuf::from("/tmp"))
            .join("crwl")
            .join(".env");

        if env_path.exists() {
            return;
        }

        let _ = ensure_config_dir();
        let template = r#"# Crwl Desktop App — Configuration
# Ứng dụng sử dụng cơ sở dữ liệu SQLite cục bộ lưu tại:
# - Linux: ~/.config/crwl/crwl.db
# - Windows: %APPDATA%/crwl/crwl.db
# Không cần cấu hình thêm bất kỳ biến môi trường nào.
"#;

        if let Err(e) = std::fs::write(&env_path, template) {
            warn!("[Settings] Không tạo được .env mẫu: {e}");
        } else {
            info!("[Settings] Đã tạo .env mẫu tại {:?}", env_path);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn proxy_is_normalized_and_validated() {
        assert_eq!(normalize_proxy("127.0.0.1:8080").unwrap(), "http://127.0.0.1:8080");
        assert_eq!(normalize_proxy(" socks5://localhost:1080 ").unwrap(), "socks5://localhost:1080");
        assert_eq!(normalize_proxy("http://user:pass@proxy.lan:3128").unwrap(), "http://user:pass@proxy.lan:3128");
        assert!(normalize_proxy("ftp://example.com:21").is_err());
        assert!(normalize_proxy("http://").is_err());
        assert!(normalize_proxy("not a proxy").is_err());
    }

    #[test]
    fn invalid_values_are_rejected_instead_of_silently_ignored() {
        let tmp = std::env::temp_dir();
        let base = AppSettings::default();

        let missing = tmp.join(format!("crwl_missing_{}", uuid::Uuid::new_v4()));
        let err = SettingsManager::validate(AppSettings {
            download_dir: Some(missing.to_string_lossy().to_string()),
            ..base.clone()
        })
        .unwrap_err();
        assert!(err.contains("không tồn tại"), "{err}");

        assert!(SettingsManager::validate(AppSettings {
            download_dir: Some("relative/dir".into()),
            ..base.clone()
        })
        .is_err());

        let err = SettingsManager::validate(AppSettings {
            ytdlp_path: Some("/definitely/not/here/yt-dlp".into()),
            ..base.clone()
        })
        .unwrap_err();
        assert!(err.contains("yt-dlp"), "{err}");

        // Ô để trống (chỉ có khoảng trắng) phải thành None, không phải chuỗi rỗng
        let ok = SettingsManager::validate(AppSettings {
            download_dir: Some(format!("  {}  ", tmp.display())),
            proxy: Some("   ".into()),
            ..base
        })
        .unwrap();
        assert_eq!(ok.download_dir.as_deref(), Some(tmp.to_string_lossy().as_ref()));
        assert_eq!(ok.proxy, None);
    }

    #[test]
    fn history_retention_is_validated_and_round_trips() {
        assert_eq!(validate_retention_days(30), Ok(30));
        assert!(validate_retention_days(0).is_err());
        assert!(validate_retention_days(MAX_HISTORY_RETENTION_DAYS + 1).is_err());

        let ok = SettingsManager::validate(AppSettings {
            history_retention_days: Some(7),
            ..AppSettings::default()
        })
        .unwrap();
        assert_eq!(ok.history_retention_days, Some(7));
        assert!(SettingsManager::validate(AppSettings {
            history_retention_days: Some(0),
            ..AppSettings::default()
        })
        .is_err());

        // Tệp settings.json cũ (chưa có trường này) vẫn đọc được, mặc định là giữ mãi
        let old: AppSettings = serde_json::from_str(r#"{"downloadDir":"/tmp","schemaVersion":1}"#).unwrap();
        assert_eq!(old.history_retention_days, None);
        let json = serde_json::to_string(&AppSettings {
            history_retention_days: Some(30),
            ..AppSettings::default()
        })
        .unwrap();
        assert!(json.contains("\"historyRetentionDays\":30"), "{json}");
    }

    #[test]
    fn tilde_paths_are_expanded() {
        let home = dirs::home_dir().expect("cần HOME để chạy test này");
        let ok = SettingsManager::validate(AppSettings {
            download_dir: Some("~".into()),
            ..AppSettings::default()
        })
        .unwrap();
        assert_eq!(ok.download_dir.as_deref(), Some(home.to_string_lossy().as_ref()));
    }

    #[cfg(unix)]
    #[test]
    fn tool_paths_must_be_executable() {
        use std::os::unix::fs::PermissionsExt;
        let tool = std::env::temp_dir().join(format!("crwl_tool_{}", uuid::Uuid::new_v4()));
        std::fs::write(&tool, "#!/bin/sh\n").unwrap();
        std::fs::set_permissions(&tool, std::fs::Permissions::from_mode(0o644)).unwrap();
        let settings = AppSettings {
            gallery_dl_path: Some(tool.to_string_lossy().to_string()),
            ..AppSettings::default()
        };
        assert!(SettingsManager::validate(settings.clone()).unwrap_err().contains("quyền thực thi"));

        std::fs::set_permissions(&tool, std::fs::Permissions::from_mode(0o755)).unwrap();
        assert!(SettingsManager::validate(settings).is_ok());
        let _ = std::fs::remove_file(&tool);
    }
}
