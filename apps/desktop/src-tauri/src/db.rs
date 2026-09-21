use std::env;
use std::path::PathBuf;
use std::sync::Arc;
use log::{error, info, warn};
use serde::{Deserialize, Serialize};
use sqlx::postgres::PgPoolOptions;
use sqlx::{PgPool, Row};
use tokio::sync::RwLock;

use crate::settings::SettingsManager;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DownloadHistoryRecord {
    pub id: i64,
    pub media_title: Option<String>,
    pub file_name: Option<String>,
    pub file_size_bytes: Option<i64>,
    pub platform: Option<String>,
    pub status: String,
    pub client_ip: Option<String>,
    pub device_id: Option<String>,
    pub browser_name: Option<String>,
    pub downloaded_at: Option<chrono::DateTime<chrono::Utc>>,
    pub username: Option<String>,
}

#[derive(Debug, Clone)]
pub struct Database {
    pub pool: Arc<RwLock<Option<PgPool>>>,
}

impl Database {
    pub fn new() -> Self {
        Self {
            pool: Arc::new(RwLock::new(None)),
        }
    }

    #[allow(dead_code)]
    pub fn from_pool(pool: Option<PgPool>) -> Self {
        Self {
            pool: Arc::new(RwLock::new(pool)),
        }
    }

    pub async fn get_pool(&self) -> Option<PgPool> {
        self.pool.read().await.clone()
    }

    #[allow(dead_code)]
    pub async fn is_connected(&self) -> bool {
        self.pool.read().await.is_some()
    }

    /// Kiểm tra xem URL có phải là giá trị mẫu / giả lập hay không
    pub fn is_placeholder_url(url: &str) -> bool {
        let u = url.trim();
        if u.is_empty() {
            return true;
        }
        let lower = u.to_lowercase();
        lower.contains("postgres.xxxx")
            || lower.contains("yourpassword")
            || lower.contains("<password>")
            || lower.contains("[password]")
            || lower.contains("user:password")
            || lower.contains("host:5432")
    }

    /// Tự động tìm chuỗi DATABASE_URL từ settings.json, biến môi trường hệ thống hoặc file .env
    pub fn resolve_database_url() -> Option<String> {
        // 1. Kiểm tra settings.database_url (từ settings.json của ứng dụng)
        let settings = SettingsManager::load();
        if let Some(url) = settings.database_url {
            let trimmed = url.trim();
            if !trimmed.is_empty() && !Self::is_placeholder_url(trimmed) {
                info!("Đã nạp DATABASE_URL từ settings.json");
                return Some(trimmed.to_string());
            }
        }

        // 2. Kiểm tra biến môi trường hệ thống trực tiếp
        if let Ok(url) = env::var("DATABASE_URL") {
            let trimmed = url.trim();
            if !trimmed.is_empty() && !Self::is_placeholder_url(trimmed) {
                info!("Đã nạp DATABASE_URL từ biến môi trường hệ thống");
                return Some(trimmed.to_string());
            }
        }

        // 3. Kiểm tra biến môi trường nhúng lúc biên dịch (GitHub Actions build secrets)
        if let Some(url) = option_env!("DATABASE_URL") {
            let trimmed = url.trim();
            if !trimmed.is_empty() && !Self::is_placeholder_url(trimmed) {
                info!("Đã nạp DATABASE_URL từ cấu hình đóng gói (compile-time env)");
                return Some(trimmed.to_string());
            }
        }

        // 4. Tìm theo thứ tự ưu tiên các file .env
        let home = dirs::home_dir().unwrap_or_else(|| PathBuf::from("/tmp"));
        let config_dir = dirs::config_dir().unwrap_or_else(|| home.join(".config"));

        let candidates = vec![
            config_dir.join("crwl").join(".env"),   // ~/.config/crwl/.env (primary)
            PathBuf::from(".env"),                   // cwd/.env
            PathBuf::from("../.env"),                // parent dir
            PathBuf::from("../../.env"),             // grandparent dir
        ];

        for candidate in candidates {
            if candidate.exists() {
                if let Ok(iter) = dotenvy::from_path_iter(&candidate) {
                    for item in iter.flatten() {
                        if item.0 == "DATABASE_URL" {
                            let url = item.1.trim().to_string();
                            if !url.is_empty() && !Self::is_placeholder_url(&url) {
                                info!("Đã nạp DATABASE_URL từ file: {:?}", candidate);
                                return Some(url);
                            }
                        }
                    }
                }
            }
        }

        None
    }

    /// Khởi tạo kết nối tới PostgreSQL trong nền
    pub async fn init(&self) {
        let database_url = Self::resolve_database_url();

        if let Some(ref url) = database_url {
            info!("Đang kết nối tới PostgreSQL database...");
            match PgPoolOptions::new()
                .max_connections(5)
                .acquire_timeout(std::time::Duration::from_secs(8))
                .connect(url)
                .await
            {
                Ok(pool) => {
                    info!("✅ Kết nối PostgreSQL thành công!");
                    if let Err(e) = Self::init_schema(&pool).await {
                        warn!("⚠️ Lỗi khi khởi tạo schema: {e}");
                    }
                    *self.pool.write().await = Some(pool);
                }
                Err(e) => {
                    error!("❌ Không thể kết nối PostgreSQL: {e}. Ứng dụng sẽ hoạt động ở chế độ Offline (không lưu DB).");
                    *self.pool.write().await = None;
                }
            }
        } else {
            info!("ℹ️ Không tìm thấy DATABASE_URL hợp lệ. Ứng dụng sẽ hoạt động ở chế độ Offline.");
            *self.pool.write().await = None;
        }
    }

    /// Tự động đảm bảo 5 bảng của schema.sql tồn tại
    async fn init_schema(pool: &PgPool) -> Result<(), sqlx::Error> {
        let ddl = r#"
            CREATE EXTENSION IF NOT EXISTS "pgcrypto";

            CREATE TABLE IF NOT EXISTS users (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                username VARCHAR(100) NOT NULL,
                role VARCHAR(50) DEFAULT 'user',
                is_active BOOLEAN DEFAULT TRUE,
                device_id VARCHAR(255) UNIQUE NOT NULL,
                ip_address VARCHAR(64),
                browser_name VARCHAR(100),
                user_agent TEXT,
                last_active_at TIMESTAMPTZ DEFAULT NOW(),
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS jobs (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                user_id UUID REFERENCES users(id) ON DELETE SET NULL,
                title TEXT NOT NULL,
                job_type VARCHAR(50) DEFAULT 'single',
                engine VARCHAR(50) DEFAULT 'auto',
                status VARCHAR(50) DEFAULT 'ready',
                options JSONB DEFAULT '{}'::jsonb,
                total_items INTEGER DEFAULT 1,
                extracted_items INTEGER DEFAULT 0,
                client_ip VARCHAR(64),
                device_id VARCHAR(255),
                started_at TIMESTAMPTZ DEFAULT NOW(),
                finished_at TIMESTAMPTZ
            );

            CREATE TABLE IF NOT EXISTS job_items (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                job_id UUID REFERENCES jobs(id) ON DELETE CASCADE,
                source_url TEXT NOT NULL,
                item_type VARCHAR(50) DEFAULT 'profile',
                status VARCHAR(50) DEFAULT 'completed',
                extracted_count INTEGER DEFAULT 0,
                started_at TIMESTAMPTZ DEFAULT NOW(),
                finished_at TIMESTAMPTZ
            );

            CREATE TABLE IF NOT EXISTS extracted_medias (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                job_id UUID REFERENCES jobs(id) ON DELETE CASCADE,
                job_item_id UUID REFERENCES job_items(id) ON DELETE SET NULL,
                user_id UUID REFERENCES users(id) ON DELETE SET NULL,
                platform VARCHAR(50),
                media_type VARCHAR(50),
                original_url TEXT NOT NULL,
                title TEXT,
                author TEXT,
                author_url TEXT,
                thumbnail_url TEXT,
                duration_seconds INTEGER,
                view_count BIGINT,
                like_count BIGINT,
                formats JSONB DEFAULT '[]'::jsonb,
                download_status VARCHAR(50) DEFAULT 'pending',
                created_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE TABLE IF NOT EXISTS download_history (
                id BIGSERIAL PRIMARY KEY,
                user_id UUID REFERENCES users(id) ON DELETE SET NULL,
                job_id UUID REFERENCES jobs(id) ON DELETE SET NULL,
                extracted_media_id UUID REFERENCES extracted_medias(id) ON DELETE SET NULL,
                platform VARCHAR(50),
                media_title TEXT,
                file_name TEXT,
                file_size_bytes BIGINT,
                client_type VARCHAR(50) DEFAULT 'desktop',
                duration_ms INTEGER,
                status VARCHAR(50) DEFAULT 'success',
                error_reason TEXT,
                client_ip VARCHAR(64),
                device_id VARCHAR(255),
                browser_name VARCHAR(100),
                downloaded_at TIMESTAMPTZ DEFAULT NOW()
            );

            CREATE INDEX IF NOT EXISTS idx_users_device_id ON users(device_id);
            CREATE INDEX IF NOT EXISTS idx_download_history_device_id ON download_history(device_id);
            CREATE INDEX IF NOT EXISTS idx_download_history_downloaded_at ON download_history(downloaded_at DESC);
        "#;

        sqlx::raw_sql(ddl).execute(pool).await?;
        info!("✅ Schema PostgreSQL đã được xác thực/khởi tạo đồng bộ.");
        Ok(())
    }

    /// Lấy hoặc tạo user theo device_id
    pub async fn get_or_create_user(&self, device_id: &str) -> Option<uuid::Uuid> {
        let pool = self.get_pool().await?;
        let username = format!("desktop_{}", &device_id.chars().take(10).collect::<String>());

        let browser_name = if cfg!(windows) {
            "Windows Desktop (Tauri)"
        } else if cfg!(target_os = "macos") {
            "macOS Desktop (Tauri)"
        } else {
            "Linux Desktop (Tauri)"
        };
        let user_agent = if cfg!(windows) {
            "Tauri 2 / WebView2"
        } else {
            "Tauri 2 / WebKitGTK"
        };

        let query = r#"
            INSERT INTO users (username, role, is_active, device_id, browser_name, user_agent, last_active_at)
            VALUES ($1, 'user', true, $2, $3, $4, NOW())
            ON CONFLICT (device_id) DO UPDATE SET last_active_at = NOW()
            RETURNING id;
        "#;

        match sqlx::query(query)
            .bind(&username)
            .bind(device_id)
            .bind(browser_name)
            .bind(user_agent)
            .fetch_one(&pool)
            .await
        {
            Ok(row) => row.try_get::<uuid::Uuid, _>("id").ok(),
            Err(e) => {
                error!("Lỗi khi get_or_create_user: {e}");
                None
            }
        }
    }

    /// Ghi nhận lịch sử tải xuống tệp vào download_history
    pub async fn record_download_history(
        &self,
        device_id: &str,
        media_title: &str,
        file_name: &str,
        platform: &str,
        file_size_bytes: Option<i64>,
        duration_ms: Option<i32>,
        status: &str,
        error_reason: Option<&str>,
        client_ip: Option<&str>,
    ) -> Option<i64> {
        let pool = self.get_pool().await?;
        let user_id = self.get_or_create_user(device_id).await;

        let browser_name = if cfg!(windows) {
            "Tauri 2 / Windows Desktop"
        } else if cfg!(target_os = "macos") {
            "Tauri 2 / macOS Desktop"
        } else {
            "Tauri 2 / Linux Desktop"
        };

        let query = r#"
            INSERT INTO download_history (
                user_id, platform, media_title, file_name, file_size_bytes,
                client_type, duration_ms, status, error_reason,
                client_ip, device_id, browser_name, downloaded_at
            ) VALUES (
                $1, $2, $3, $4, $5, 'desktop', $6, $7, $8, $9, $10, $11, NOW()
            ) RETURNING id;
        "#;

        match sqlx::query(query)
            .bind(user_id)
            .bind(platform)
            .bind(media_title)
            .bind(file_name)
            .bind(file_size_bytes)
            .bind(duration_ms)
            .bind(status)
            .bind(error_reason)
            .bind(client_ip)
            .bind(device_id)
            .bind(browser_name)
            .fetch_one(&pool)
            .await
        {
            Ok(row) => row.try_get::<i64, _>("id").ok(),
            Err(e) => {
                error!("Lỗi khi record_download_history: {e}");
                None
            }
        }
    }

    /// Lấy danh sách lịch sử tải gần nhất
    pub async fn get_recent_downloads(
        &self,
        limit: i64,
        device_id: Option<&str>,
    ) -> Vec<DownloadHistoryRecord> {
        let pool = match self.get_pool().await {
            Some(p) => p,
            None => return Vec::new(),
        };

        let mut query = String::from(
            r#"
            SELECT
                dh.id,
                dh.media_title,
                dh.file_name,
                dh.file_size_bytes,
                dh.platform,
                dh.status,
                dh.client_ip,
                dh.device_id,
                dh.browser_name,
                dh.downloaded_at,
                u.username
            FROM download_history dh
            LEFT JOIN users u ON dh.user_id = u.id
            "#,
        );

        let scoped_device = device_id.filter(|d| !d.trim().is_empty());

        let rows = if let Some(dev_id) = scoped_device {
            query.push_str(" WHERE dh.device_id = $2 ORDER BY dh.downloaded_at DESC LIMIT $1;");
            sqlx::query(&query)
                .bind(limit)
                .bind(dev_id)
                .fetch_all(&pool)
                .await
        } else {
            query.push_str(" ORDER BY dh.downloaded_at DESC LIMIT $1;");
            sqlx::query(&query).bind(limit).fetch_all(&pool).await
        };

        let rows = match rows {
            Ok(r) => r,
            Err(e) => {
                error!("Lỗi khi đọc download_history: {e}");
                return Vec::new();
            }
        };

        // `Row::get` panic khi cột NULL hoặc sai kiểu. Một panic trong Tauri command
        // khiến promise phía UI không bao giờ resolve — modal Lịch sử quay vô tận.
        // Dùng `try_get` để hàng dữ liệu lỗi chỉ bị bỏ qua thay vì treo cả tính năng.
        rows.into_iter()
            .map(|r| DownloadHistoryRecord {
                id: r.try_get("id").unwrap_or_default(),
                media_title: r.try_get("media_title").unwrap_or_default(),
                file_name: r.try_get("file_name").unwrap_or_default(),
                file_size_bytes: r.try_get("file_size_bytes").unwrap_or_default(),
                platform: r.try_get("platform").unwrap_or_default(),
                status: r
                    .try_get::<Option<String>, _>("status")
                    .unwrap_or_default()
                    .unwrap_or_else(|| "unknown".to_string()),
                client_ip: r.try_get("client_ip").unwrap_or_default(),
                device_id: r.try_get("device_id").unwrap_or_default(),
                browser_name: r.try_get("browser_name").unwrap_or_default(),
                downloaded_at: r.try_get("downloaded_at").unwrap_or_default(),
                username: r.try_get("username").unwrap_or_default(),
            })
            .collect()
    }

    /// Xóa lịch sử tải của thiết bị
    pub async fn clear_download_history(&self, device_id: Option<&str>) -> bool {
        let pool = match self.get_pool().await {
            Some(p) => p,
            None => return false,
        };

        let result = if let Some(dev_id) = device_id {
            sqlx::query("DELETE FROM download_history WHERE device_id = $1;")
                .bind(dev_id)
                .execute(&pool)
                .await
        } else {
            sqlx::query("DELETE FROM download_history;").execute(&pool).await
        };

        match result {
            Ok(_) => true,
            Err(e) => {
                error!("Lỗi khi clear_download_history: {e}");
                false
            }
        }
    }

    /// Ghi nhận phiên trích xuất media đơn vào jobs và extracted_medias
    pub async fn record_single_extraction(
        &self,
        device_id: &str,
        original_url: &str,
        data: &serde_json::Value,
        client_ip: Option<&str>,
    ) -> Option<(uuid::Uuid, uuid::Uuid)> {
        let pool = self.get_pool().await?;
        let user_id = self.get_or_create_user(device_id).await;

        let title = data.get("title").and_then(|v| v.as_str()).unwrap_or("Untitled Media");
        let platform = data.get("platform").and_then(|v| v.as_str()).unwrap_or("auto");
        let media_type = data.get("type").and_then(|v| v.as_str()).unwrap_or("video");
        let author = data.get("author").and_then(|v| v.as_str());
        let author_url = data.get("authorUrl").and_then(|v| v.as_str());
        let thumb = data.get("thumbnail").and_then(|v| v.as_str());
        let formats = data.get("streams").or_else(|| data.get("images")).cloned().unwrap_or_else(|| serde_json::Value::Array(vec![]));

        // Parse duration seconds if number or string
        let duration_seconds = data.get("duration").and_then(|d| {
            if let Some(n) = d.as_i64() {
                Some(n as i32)
            } else if let Some(s) = d.as_str() {
                let parts: Vec<&str> = s.split(':').collect();
                if parts.len() == 2 {
                    let m = parts[0].parse::<i32>().unwrap_or(0);
                    let sec = parts[1].parse::<i32>().unwrap_or(0);
                    Some(m * 60 + sec)
                } else if parts.len() == 3 {
                    let h = parts[0].parse::<i32>().unwrap_or(0);
                    let m = parts[1].parse::<i32>().unwrap_or(0);
                    let sec = parts[2].parse::<i32>().unwrap_or(0);
                    Some(h * 3600 + m * 60 + sec)
                } else {
                    None
                }
            } else {
                None
            }
        });

        // 1. Tạo Job
        let job_query = r#"
            INSERT INTO jobs (
                user_id, title, job_type, engine, status, options,
                total_items, extracted_items, client_ip, device_id,
                started_at, finished_at
            ) VALUES (
                $1, $2, 'single', 'auto', 'ready', '{}'::jsonb,
                1, 1, $3, $4, NOW(), NOW()
            ) RETURNING id;
        "#;

        let job_id = match sqlx::query(job_query)
            .bind(user_id)
            .bind(title)
            .bind(client_ip)
            .bind(device_id)
            .fetch_one(&pool)
            .await
        {
            Ok(row) => row.try_get::<uuid::Uuid, _>("id").ok()?,
            Err(e) => {
                error!("Lỗi khi tạo job cho single extraction: {e}");
                return None;
            }
        };

        // 2. Tạo Extracted Media
        let media_query = r#"
            INSERT INTO extracted_medias (
                job_id, user_id, platform, media_type, original_url,
                title, author, author_url, thumbnail_url, duration_seconds,
                formats, download_status, created_at
            ) VALUES (
                $1, $2, $3, $4, $5,
                $6, $7, $8, $9, $10,
                $11, 'pending', NOW()
            ) RETURNING id;
        "#;

        let media_id = match sqlx::query(media_query)
            .bind(job_id)
            .bind(user_id)
            .bind(platform)
            .bind(media_type)
            .bind(original_url)
            .bind(title)
            .bind(author)
            .bind(author_url)
            .bind(thumb)
            .bind(duration_seconds)
            .bind(formats)
            .fetch_one(&pool)
            .await
        {
            Ok(row) => row.try_get::<uuid::Uuid, _>("id").ok()?,
            Err(e) => {
                error!("Lỗi khi tạo extracted_media: {e}");
                return None;
            }
        };

        info!("✅ Đã ghi nhận single extraction vào DB: Job {job_id}, Media {media_id}");
        Some((job_id, media_id))
    }

    /// Ghi nhận phiên quét profile vào jobs, job_items và extracted_medias
    pub async fn record_profile_crawl(
        &self,
        device_id: &str,
        source_url: &str,
        data: &serde_json::Value,
        client_ip: Option<&str>,
    ) -> Option<(uuid::Uuid, usize)> {
        let pool = self.get_pool().await?;
        let user_id = self.get_or_create_user(device_id).await;

        let platform = data.get("platform").and_then(|v| v.as_str()).unwrap_or("auto");
        let empty_vec = vec![];
        let media_list = data.get("media").and_then(|m| m.as_array()).unwrap_or(&empty_vec);
        let total_items = media_list.len() as i32;

        let options = serde_json::json!({
            "url": source_url,
            "total_items": total_items,
        });

        // 1. Tạo Job
        let job_query = r#"
            INSERT INTO jobs (
                user_id, title, job_type, engine, status, options,
                total_items, extracted_items, client_ip, device_id,
                started_at, finished_at
            ) VALUES (
                $1, $2, 'profile', 'auto', 'ready', $3,
                $4, $4, $5, $6, NOW(), NOW()
            ) RETURNING id;
        "#;

        let job_title = format!("Crawl: {source_url}");
        let job_id = match sqlx::query(job_query)
            .bind(user_id)
            .bind(&job_title)
            .bind(options)
            .bind(total_items)
            .bind(client_ip)
            .bind(device_id)
            .fetch_one(&pool)
            .await
        {
            Ok(row) => row.try_get::<uuid::Uuid, _>("id").ok()?,
            Err(e) => {
                error!("Lỗi khi tạo job cho profile crawl: {e}");
                return None;
            }
        };

        // 2. Tạo Job Item
        let item_query = r#"
            INSERT INTO job_items (
                job_id, source_url, item_type, status, extracted_count,
                started_at, finished_at
            ) VALUES (
                $1, $2, 'profile', 'completed', $3,
                NOW(), NOW()
            ) RETURNING id;
        "#;

        let job_item_id = match sqlx::query(item_query)
            .bind(job_id)
            .bind(source_url)
            .bind(total_items)
            .fetch_one(&pool)
            .await
        {
            Ok(row) => row.try_get::<uuid::Uuid, _>("id").ok()?,
            Err(e) => {
                error!("Lỗi khi tạo job_item: {e}");
                return None;
            }
        };

        // 3. Batch insert tối đa 50 item đầu tiên
        let top_items = media_list.iter().take(50);
        let mut inserted_count = 0;

        for item in top_items {
            let item_url = item.get("url").and_then(|u| u.as_str()).unwrap_or(source_url);
            let item_title = item.get("title").and_then(|t| t.as_str()).unwrap_or("Untitled");
            let item_author = item.get("author").and_then(|a| a.as_str());
            let item_thumb = item.get("thumb").or_else(|| item.get("thumbnail")).and_then(|th| th.as_str());
            let item_type = item.get("type").and_then(|ty| ty.as_str()).unwrap_or("video");

            let insert_sql = r#"
                INSERT INTO extracted_medias (
                    job_id, job_item_id, user_id, platform, media_type,
                    original_url, title, author, thumbnail_url,
                    formats, download_status, created_at
                ) VALUES (
                    $1, $2, $3, $4, $5,
                    $6, $7, $8, $9,
                    '[]'::jsonb, 'pending', NOW()
                );
            "#;

            if let Ok(_) = sqlx::query(insert_sql)
                .bind(job_id)
                .bind(job_item_id)
                .bind(user_id)
                .bind(platform)
                .bind(item_type)
                .bind(item_url)
                .bind(item_title)
                .bind(item_author)
                .bind(item_thumb)
                .execute(&pool)
                .await
            {
                inserted_count += 1;
            }
        }

        info!("✅ Đã ghi nhận profile crawl vào DB: Job {job_id}, {inserted_count} extracted_medias");
        Some((job_id, media_list.len()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_is_placeholder_url() {
        assert!(Database::is_placeholder_url(""));
        assert!(Database::is_placeholder_url("   "));
        assert!(Database::is_placeholder_url(
            "postgresql://postgres.xxxx:yourpassword@aws-0-ap-northeast-2.pooler.supabase.com:5432/postgres"
        ));
        assert!(Database::is_placeholder_url("postgresql://user:yourpassword@localhost:5432/db"));
        assert!(Database::is_placeholder_url("postgresql://user:<password>@localhost:5432/db"));
        assert!(Database::is_placeholder_url("postgresql://user:[PASSWORD]@localhost:5432/db"));
        assert!(!Database::is_placeholder_url(
            "postgresql://postgres.realref:MySecret123@aws-0-ap-northeast-2.pooler.supabase.com:5432/postgres"
        ));
    }

    #[tokio::test]
    async fn test_database_new_and_from_pool() {
        let db = Database::new();
        assert!(!db.is_connected().await);
        assert!(db.get_pool().await.is_none());

        let db_from_none = Database::from_pool(None);
        assert!(!db_from_none.is_connected().await);
        assert!(db_from_none.get_pool().await.is_none());
    }
}
