use std::path::PathBuf;
use std::sync::Arc;
use log::{error, info, warn};
use serde::{Deserialize, Serialize};
use sqlx::sqlite::{SqliteConnectOptions, SqlitePoolOptions};
use sqlx::{Row, SqlitePool};
use tokio::sync::RwLock;

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
    pub pool: Arc<RwLock<Option<SqlitePool>>>,
}

impl Database {
    pub fn new() -> Self {
        Self {
            pool: Arc::new(RwLock::new(None)),
        }
    }

    #[allow(dead_code)]
    pub fn from_pool(pool: Option<SqlitePool>) -> Self {
        Self {
            pool: Arc::new(RwLock::new(pool)),
        }
    }

    pub async fn get_pool(&self) -> Option<SqlitePool> {
        self.pool.read().await.clone()
    }

    #[allow(dead_code)]
    pub async fn is_connected(&self) -> bool {
        self.pool.read().await.is_some()
    }

    /// Xác định đường dẫn file SQLite cục bộ tại thư mục cấu hình của hệ điều hành:
    /// - Linux: ~/.config/crwl/crwl.db
    /// - Windows: %APPDATA%/crwl/crwl.db
    pub fn resolve_db_path() -> PathBuf {
        let dir = dirs::config_dir()
            .unwrap_or_else(|| dirs::home_dir().unwrap_or_else(|| PathBuf::from(".")))
            .join("crwl");

        if !dir.exists() {
            let _ = std::fs::create_dir_all(&dir);
        }

        dir.join("crwl.db")
    }

    /// Khởi tạo kết nối tới SQLite Database cục bộ trong nền
    pub async fn init(&self) {
        let db_path = Self::resolve_db_path();
        info!("Đang khởi tạo SQLite database cục bộ tại: {:?}", db_path);

        if let Some(parent) = db_path.parent() {
            if !parent.exists() {
                let _ = std::fs::create_dir_all(parent);
            }
        }

        let connect_opts = SqliteConnectOptions::new()
            .filename(&db_path)
            .create_if_missing(true)
            .journal_mode(sqlx::sqlite::SqliteJournalMode::Wal)
            .synchronous(sqlx::sqlite::SqliteSynchronous::Normal);

        match SqlitePoolOptions::new()
            .max_connections(5)
            .acquire_timeout(std::time::Duration::from_secs(5))
            .connect_with(connect_opts)
            .await
        {
            Ok(pool) => {
                info!("✅ Kết nối SQLite database thành công!");
                if let Err(e) = Self::init_schema(&pool).await {
                    warn!("⚠️ Lỗi khi khởi tạo SQLite schema: {e}");
                }
                *self.pool.write().await = Some(pool);
            }
            Err(e) => {
                error!("❌ Không thể kết nối SQLite database: {e}. Ứng dụng sẽ hoạt động ở chế độ Offline (không lưu DB).");
                *self.pool.write().await = None;
            }
        }
    }

    /// Tự động khởi tạo và xác thực các bảng trong SQLite
    pub async fn init_schema(pool: &SqlitePool) -> Result<(), sqlx::Error> {
        let ddl = r#"
            PRAGMA foreign_keys = ON;

            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                role TEXT DEFAULT 'user',
                is_active INTEGER DEFAULT 1,
                device_id TEXT UNIQUE NOT NULL,
                ip_address TEXT,
                browser_name TEXT,
                user_agent TEXT,
                last_active_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
                title TEXT NOT NULL,
                job_type TEXT DEFAULT 'single',
                engine TEXT DEFAULT 'auto',
                status TEXT DEFAULT 'ready',
                options TEXT DEFAULT '{}',
                total_items INTEGER DEFAULT 1,
                extracted_items INTEGER DEFAULT 0,
                client_ip TEXT,
                device_id TEXT,
                started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                finished_at DATETIME
            );

            CREATE TABLE IF NOT EXISTS job_items (
                id TEXT PRIMARY KEY,
                job_id TEXT REFERENCES jobs(id) ON DELETE CASCADE,
                source_url TEXT NOT NULL,
                item_type TEXT DEFAULT 'profile',
                status TEXT DEFAULT 'completed',
                extracted_count INTEGER DEFAULT 0,
                started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                finished_at DATETIME
            );

            CREATE TABLE IF NOT EXISTS extracted_medias (
                id TEXT PRIMARY KEY,
                job_id TEXT REFERENCES jobs(id) ON DELETE CASCADE,
                job_item_id TEXT REFERENCES job_items(id) ON DELETE SET NULL,
                user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
                platform TEXT,
                media_type TEXT,
                original_url TEXT NOT NULL,
                title TEXT,
                author TEXT,
                author_url TEXT,
                thumbnail_url TEXT,
                duration_seconds INTEGER,
                view_count INTEGER,
                like_count INTEGER,
                formats TEXT DEFAULT '[]',
                download_status TEXT DEFAULT 'pending',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS download_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
                job_id TEXT REFERENCES jobs(id) ON DELETE SET NULL,
                extracted_media_id TEXT REFERENCES extracted_medias(id) ON DELETE SET NULL,
                platform TEXT,
                media_title TEXT,
                file_name TEXT,
                file_size_bytes INTEGER,
                client_type TEXT DEFAULT 'desktop',
                duration_ms INTEGER,
                status TEXT DEFAULT 'success',
                error_reason TEXT,
                client_ip TEXT,
                device_id TEXT,
                browser_name TEXT,
                downloaded_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_users_device_id ON users(device_id);
            CREATE INDEX IF NOT EXISTS idx_download_history_device_id ON download_history(device_id);
            CREATE INDEX IF NOT EXISTS idx_download_history_downloaded_at ON download_history(downloaded_at DESC);
        "#;

        sqlx::raw_sql(ddl).execute(pool).await?;
        info!("✅ Schema SQLite đã được xác thực/khởi tạo đồng bộ.");
        Ok(())
    }

    /// Lấy hoặc tạo user theo device_id
    pub async fn get_or_create_user(&self, device_id: &str) -> Option<uuid::Uuid> {
        let pool = self.get_pool().await?;

        // 1. Kiểm tra nếu user đã tồn tại
        let existing = sqlx::query("SELECT id FROM users WHERE device_id = ? LIMIT 1;")
            .bind(device_id)
            .fetch_optional(&pool)
            .await;

        if let Ok(Some(row)) = existing {
            if let Ok(id_str) = row.try_get::<String, _>("id") {
                let _ = sqlx::query("UPDATE users SET last_active_at = CURRENT_TIMESTAMP WHERE device_id = ?;")
                    .bind(device_id)
                    .execute(&pool)
                    .await;
                return uuid::Uuid::parse_str(&id_str).ok();
            }
        }

        // 2. Tạo mới user
        let user_uuid = uuid::Uuid::new_v4();
        let user_id_str = user_uuid.to_string();
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

        let insert_query = r#"
            INSERT INTO users (id, username, role, is_active, device_id, browser_name, user_agent, last_active_at, created_at)
            VALUES (?, ?, 'user', 1, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP);
        "#;

        match sqlx::query(insert_query)
            .bind(&user_id_str)
            .bind(&username)
            .bind(device_id)
            .bind(browser_name)
            .bind(user_agent)
            .execute(&pool)
            .await
        {
            Ok(_) => Some(user_uuid),
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

        let user_id_str = user_id.map(|u| u.to_string());
        let now = chrono::Utc::now();

        let query = r#"
            INSERT INTO download_history (
                user_id, platform, media_title, file_name, file_size_bytes,
                client_type, duration_ms, status, error_reason,
                client_ip, device_id, browser_name, downloaded_at
            ) VALUES (
                ?, ?, ?, ?, ?, 'desktop', ?, ?, ?, ?, ?, ?, ?
            );
        "#;

        match sqlx::query(query)
            .bind(user_id_str)
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
            .bind(now)
            .execute(&pool)
            .await
        {
            Ok(res) => Some(res.last_insert_rowid()),
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

        let scoped_device = device_id.filter(|d| !d.trim().is_empty());

        let rows = if let Some(dev_id) = scoped_device {
            let query = r#"
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
                WHERE dh.device_id = ?
                ORDER BY dh.downloaded_at DESC, dh.id DESC
                LIMIT ?;
            "#;
            sqlx::query(query)
                .bind(dev_id)
                .bind(limit)
                .fetch_all(&pool)
                .await
        } else {
            let query = r#"
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
                ORDER BY dh.downloaded_at DESC, dh.id DESC
                LIMIT ?;
            "#;
            sqlx::query(query)
                .bind(limit)
                .fetch_all(&pool)
                .await
        };

        let rows = match rows {
            Ok(r) => r,
            Err(e) => {
                error!("Lỗi khi đọc download_history: {e}");
                return Vec::new();
            }
        };

        rows.into_iter()
            .map(|r| {
                let downloaded_at: Option<chrono::DateTime<chrono::Utc>> = r
                    .try_get::<chrono::DateTime<chrono::Utc>, _>("downloaded_at")
                    .ok()
                    .or_else(|| {
                        r.try_get::<String, _>("downloaded_at").ok().and_then(|s| {
                            chrono::DateTime::parse_from_rfc3339(&s)
                                .ok()
                                .map(|dt| dt.with_timezone(&chrono::Utc))
                                .or_else(|| {
                                    chrono::NaiveDateTime::parse_from_str(&s, "%Y-%m-%d %H:%M:%S")
                                        .ok()
                                        .map(|ndt| ndt.and_utc())
                                })
                        })
                    });

                DownloadHistoryRecord {
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
                    downloaded_at,
                    username: r.try_get("username").unwrap_or_default(),
                }
            })
            .collect()
    }

    /// Xóa lịch sử tải của thiết bị
    pub async fn clear_download_history(&self, device_id: Option<&str>) -> bool {
        let pool = match self.get_pool().await {
            Some(p) => p,
            None => return false,
        };

        let result = if let Some(dev_id) = device_id.filter(|d| !d.trim().is_empty()) {
            sqlx::query("DELETE FROM download_history WHERE device_id = ?;")
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
        let user_id_str = user_id.map(|u| u.to_string());

        let title = data.get("title").and_then(|v| v.as_str()).unwrap_or("Untitled Media");
        let platform = data.get("platform").and_then(|v| v.as_str()).unwrap_or("auto");
        let media_type = data.get("type").and_then(|v| v.as_str()).unwrap_or("video");
        let author = data.get("author").and_then(|v| v.as_str());
        let author_url = data.get("authorUrl").and_then(|v| v.as_str());
        let thumb = data.get("thumbnail").and_then(|v| v.as_str());
        let formats = data.get("streams").or_else(|| data.get("images")).cloned().unwrap_or_else(|| serde_json::Value::Array(vec![]));
        let formats_str = serde_json::to_string(&formats).unwrap_or_else(|_| "[]".to_string());

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
        let job_uuid = uuid::Uuid::new_v4();
        let job_id_str = job_uuid.to_string();

        let job_query = r#"
            INSERT INTO jobs (
                id, user_id, title, job_type, engine, status, options,
                total_items, extracted_items, client_ip, device_id,
                started_at, finished_at
            ) VALUES (
                ?, ?, ?, 'single', 'auto', 'ready', '{}',
                1, 1, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            );
        "#;

        if let Err(e) = sqlx::query(job_query)
            .bind(&job_id_str)
            .bind(&user_id_str)
            .bind(title)
            .bind(client_ip)
            .bind(device_id)
            .execute(&pool)
            .await
        {
            error!("Lỗi khi tạo job cho single extraction: {e}");
            return None;
        }

        // 2. Tạo Extracted Media
        let media_uuid = uuid::Uuid::new_v4();
        let media_id_str = media_uuid.to_string();

        let media_query = r#"
            INSERT INTO extracted_medias (
                id, job_id, user_id, platform, media_type, original_url,
                title, author, author_url, thumbnail_url, duration_seconds,
                formats, download_status, created_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, 'pending', CURRENT_TIMESTAMP
            );
        "#;

        if let Err(e) = sqlx::query(media_query)
            .bind(&media_id_str)
            .bind(&job_id_str)
            .bind(&user_id_str)
            .bind(platform)
            .bind(media_type)
            .bind(original_url)
            .bind(title)
            .bind(author)
            .bind(author_url)
            .bind(thumb)
            .bind(duration_seconds)
            .bind(formats_str)
            .execute(&pool)
            .await
        {
            error!("Lỗi khi tạo extracted_media: {e}");
            return None;
        }

        info!("✅ Đã ghi nhận single extraction vào SQLite: Job {job_uuid}, Media {media_uuid}");
        Some((job_uuid, media_uuid))
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
        let user_id_str = user_id.map(|u| u.to_string());

        let platform = data.get("platform").and_then(|v| v.as_str()).unwrap_or("auto");
        let empty_vec = vec![];
        let media_list = data.get("media").and_then(|m| m.as_array()).unwrap_or(&empty_vec);
        let total_items = media_list.len() as i32;

        let options = serde_json::json!({
            "url": source_url,
            "total_items": total_items,
        });
        let options_str = serde_json::to_string(&options).unwrap_or_else(|_| "{}".to_string());

        // 1. Tạo Job
        let job_uuid = uuid::Uuid::new_v4();
        let job_id_str = job_uuid.to_string();
        let job_title = format!("Crawl: {source_url}");

        let job_query = r#"
            INSERT INTO jobs (
                id, user_id, title, job_type, engine, status, options,
                total_items, extracted_items, client_ip, device_id,
                started_at, finished_at
            ) VALUES (
                ?, ?, ?, 'profile', 'auto', 'ready', ?,
                ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            );
        "#;

        if let Err(e) = sqlx::query(job_query)
            .bind(&job_id_str)
            .bind(&user_id_str)
            .bind(&job_title)
            .bind(&options_str)
            .bind(total_items)
            .bind(total_items)
            .bind(client_ip)
            .bind(device_id)
            .execute(&pool)
            .await
        {
            error!("Lỗi khi tạo job cho profile crawl: {e}");
            return None;
        }

        // 2. Tạo Job Item
        let job_item_uuid = uuid::Uuid::new_v4();
        let job_item_id_str = job_item_uuid.to_string();

        let item_query = r#"
            INSERT INTO job_items (
                id, job_id, source_url, item_type, status, extracted_count,
                started_at, finished_at
            ) VALUES (
                ?, ?, ?, 'profile', 'completed', ?,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            );
        "#;

        if let Err(e) = sqlx::query(item_query)
            .bind(&job_item_id_str)
            .bind(&job_id_str)
            .bind(source_url)
            .bind(total_items)
            .execute(&pool)
            .await
        {
            error!("Lỗi khi tạo job_item: {e}");
            return None;
        }

        // 3. Batch insert tối đa 50 item đầu tiên
        let top_items = media_list.iter().take(50);
        let mut inserted_count = 0;

        for item in top_items {
            let item_url = item.get("url").and_then(|u| u.as_str()).unwrap_or(source_url);
            let item_title = item.get("title").and_then(|t| t.as_str()).unwrap_or("Untitled");
            let item_author = item.get("author").and_then(|a| a.as_str());
            let item_thumb = item.get("thumb").or_else(|| item.get("thumbnail")).and_then(|th| th.as_str());
            let item_type = item.get("type").and_then(|ty| ty.as_str()).unwrap_or("video");

            let media_uuid = uuid::Uuid::new_v4();
            let media_id_str = media_uuid.to_string();

            let insert_sql = r#"
                INSERT INTO extracted_medias (
                    id, job_id, job_item_id, user_id, platform, media_type,
                    original_url, title, author, thumbnail_url,
                    formats, download_status, created_at
                ) VALUES (
                    ?, ?, ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    '[]', 'pending', CURRENT_TIMESTAMP
                );
            "#;

            if let Ok(_) = sqlx::query(insert_sql)
                .bind(&media_id_str)
                .bind(&job_id_str)
                .bind(&job_item_id_str)
                .bind(&user_id_str)
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

        info!("✅ Đã ghi nhận profile crawl vào SQLite: Job {job_uuid}, {inserted_count} extracted_medias");
        Some((job_uuid, media_list.len()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_database_new_and_from_pool() {
        let db = Database::new();
        assert!(!db.is_connected().await);
        assert!(db.get_pool().await.is_none());

        let db_from_none = Database::from_pool(None);
        assert!(!db_from_none.is_connected().await);
        assert!(db_from_none.get_pool().await.is_none());
    }

    #[tokio::test]
    async fn test_sqlite_in_memory_flow() {
        let connect_opts = SqliteConnectOptions::new()
            .filename(":memory:")
            .create_if_missing(true);

        let pool = SqlitePoolOptions::new()
            .max_connections(1)
            .connect_with(connect_opts)
            .await
            .expect("Failed to connect to in-memory SQLite");

        Database::init_schema(&pool)
            .await
            .expect("Failed to init SQLite schema");

        let db = Database::from_pool(Some(pool));
        assert!(db.is_connected().await);

        // Test ghi nhận lịch sử tải
        let dev_id = "test_device_123";
        let record_id = db
            .record_download_history(
                dev_id,
                "Test Video Title",
                "test_video.mp4",
                "youtube",
                Some(1024 * 1024),
                Some(1500),
                "success",
                None,
                Some("127.0.0.1"),
            )
            .await;

        assert!(record_id.is_some());
        let id = record_id.unwrap();
        assert!(id > 0);

        // Test truy vấn lịch sử
        let list = db.get_recent_downloads(10, Some(dev_id)).await;
        assert_eq!(list.len(), 1);
        assert_eq!(list[0].media_title.as_deref(), Some("Test Video Title"));
        assert_eq!(list[0].platform.as_deref(), Some("youtube"));
        assert_eq!(list[0].status, "success");

        // Test trích xuất media đơn
        let single_data = serde_json::json!({
            "title": "Single Extracted Video",
            "platform": "tiktok",
            "type": "video",
            "author": "creator",
            "thumbnail": "https://example.com/thumb.jpg",
            "duration": 65
        });
        let extraction = db
            .record_single_extraction(dev_id, "https://tiktok.com/@user/video/1", &single_data, None)
            .await;
        assert!(extraction.is_some());

        // Test xóa lịch sử
        let cleared = db.clear_download_history(Some(dev_id)).await;
        assert!(cleared);

        let empty_list = db.get_recent_downloads(10, Some(dev_id)).await;
        assert_eq!(empty_list.len(), 0);
    }
}
