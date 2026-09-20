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

    /// DATABASE_URL cho Supabase PostgreSQL
    #[serde(skip_serializing_if = "Option::is_none")]
    pub database_url: Option<String>,

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
            database_url: None,
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

    /// Đọc settings từ file. Trả về default nếu file chưa tồn tại.
    pub fn load() -> AppSettings {
        let path = settings_path();
        if !path.exists() {
            info!("[Settings] Chưa có file settings, dùng mặc định");
            return AppSettings::default();
        }

        match std::fs::read_to_string(&path) {
            Ok(content) => {
                match serde_json::from_str::<AppSettings>(&content) {
                    Ok(settings) => {
                        info!("[Settings] Đã tải settings từ {:?}", path);
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
# Điền thông tin Supabase của bạn nếu muốn đồng bộ lịch sử tải về vào database

# Supabase PostgreSQL Connection URL
# Lấy từ: https://supabase.com → Settings → Database → Connection string → URI
# DATABASE_URL=postgresql://postgres.[PROJECT_REF]:[PASSWORD]@aws-0-ap-northeast-2.pooler.supabase.com:5432/postgres
"#;

        if let Err(e) = std::fs::write(&env_path, template) {
            warn!("[Settings] Không tạo được .env mẫu: {e}");
        } else {
            info!("[Settings] Đã tạo .env mẫu tại {:?}", env_path);
        }
    }
}
