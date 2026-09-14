/**
 * Binary Manager — Kiểm tra và quản lý các công cụ ngoài:
 * yt-dlp, gallery-dl, ffmpeg, python3.
 *
 * Thiết kế: chỉ check + báo cáo + update manual theo yêu cầu user.
 * Không tự động cập nhật khi start.
 */
use std::path::PathBuf;
use std::process::Stdio;
use log::info;
use serde::{Deserialize, Serialize};
use tokio::process::Command;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BinaryStatus {
    pub name: String,
    pub is_installed: bool,
    pub path: Option<String>,
    pub version: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AllBinaryStatus {
    pub ytdlp: BinaryStatus,
    pub gallery_dl: BinaryStatus,
    pub ffmpeg: BinaryStatus,
    pub python3: BinaryStatus,
}

pub struct BinaryManager;

impl BinaryManager {
    /// Tìm binary trong PATH và các vị trí phổ biến trên Linux
    fn find_binary(name: &str) -> Option<PathBuf> {
        // Tìm trong PATH
        if let Ok(p) = which::which(name) {
            return Some(p);
        }

        let home = dirs::home_dir().unwrap_or_else(|| PathBuf::from("/tmp"));
        let local_bin = home.join(".local/bin").join(name);
        if local_bin.exists() {
            return Some(local_bin);
        }

        let candidates = vec![
            PathBuf::from(format!("/usr/local/bin/{}", name)),
            PathBuf::from(format!("/usr/bin/{}", name)),
            PathBuf::from(format!("/snap/bin/{}", name)),
            PathBuf::from(format!("bin/{}", name)),
        ];
        for c in candidates {
            if c.exists() {
                return Some(c);
            }
        }
        None
    }

    /// Lấy version của binary bằng cách chạy `{binary} --version`
    async fn get_version(path: &PathBuf) -> Option<String> {
        let output = Command::new(path)
            .arg("--version")
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .output()
            .await
            .ok()?;

        let raw = String::from_utf8_lossy(&output.stdout).trim().to_string();
        if raw.is_empty() {
            // Một số tool in version ra stderr
            let from_err = String::from_utf8_lossy(&output.stderr).trim().to_string();
            if !from_err.is_empty() {
                return Some(from_err.lines().next().unwrap_or("").to_string());
            }
            return None;
        }
        Some(raw.lines().next().unwrap_or("").to_string())
    }

    /// Kiểm tra trạng thái của tất cả binaries
    pub async fn check_all() -> AllBinaryStatus {
        let ytdlp_path = Self::find_binary("yt-dlp");
        let gallery_path = Self::find_binary("gallery-dl");
        let ffmpeg_path = Self::find_binary("ffmpeg");
        let python_path = Self::find_binary("python3");

        let ytdlp_version = if let Some(ref p) = ytdlp_path {
            Self::get_version(p).await
        } else {
            None
        };

        let gallery_version = if let Some(ref p) = gallery_path {
            Self::get_version(p).await
        } else {
            None
        };

        let ffmpeg_version = if let Some(ref p) = ffmpeg_path {
            Self::get_version(p).await
        } else {
            None
        };

        let python_version = if let Some(ref p) = python_path {
            Self::get_version(p).await
        } else {
            None
        };

        AllBinaryStatus {
            ytdlp: BinaryStatus {
                name: "yt-dlp".to_string(),
                is_installed: ytdlp_path.is_some(),
                path: ytdlp_path.map(|p| p.to_string_lossy().to_string()),
                version: ytdlp_version,
            },
            gallery_dl: BinaryStatus {
                name: "gallery-dl".to_string(),
                is_installed: gallery_path.is_some(),
                path: gallery_path.map(|p| p.to_string_lossy().to_string()),
                version: gallery_version,
            },
            ffmpeg: BinaryStatus {
                name: "ffmpeg".to_string(),
                is_installed: ffmpeg_path.is_some(),
                path: ffmpeg_path.map(|p| p.to_string_lossy().to_string()),
                version: ffmpeg_version,
            },
            python3: BinaryStatus {
                name: "python3".to_string(),
                is_installed: python_path.is_some(),
                path: python_path.map(|p| p.to_string_lossy().to_string()),
                version: python_version,
            },
        }
    }

    /// Cập nhật yt-dlp lên version mới nhất (chạy: yt-dlp -U)
    pub async fn update_ytdlp() -> Result<String, String> {
        let ytdlp_path = Self::find_binary("yt-dlp")
            .ok_or_else(|| "yt-dlp chưa được cài đặt".to_string())?;

        info!("Đang cập nhật yt-dlp tại: {:?}", ytdlp_path);

        let output = Command::new(&ytdlp_path)
            .arg("-U")
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .output()
            .await
            .map_err(|e| format!("Không thể chạy yt-dlp -U: {e}"))?;

        let stdout = String::from_utf8_lossy(&output.stdout).to_string();
        let stderr = String::from_utf8_lossy(&output.stderr).to_string();

        if output.status.success() {
            let msg = if !stdout.is_empty() { stdout } else { stderr };
            Ok(msg.trim().to_string())
        } else {
            Err(format!("Lỗi cập nhật yt-dlp: {}", stderr.trim()))
        }
    }

    /// Cập nhật gallery-dl lên version mới nhất (pip install -U gallery-dl)
    pub async fn update_gallery_dl() -> Result<String, String> {
        let python = Self::find_binary("python3")
            .ok_or_else(|| "python3 chưa được cài đặt".to_string())?;

        info!("Đang cập nhật gallery-dl via pip...");

        let output = Command::new(&python)
            .args(["-m", "pip", "install", "-U", "gallery-dl", "--break-system-packages"])
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .output()
            .await
            .map_err(|e| format!("Không thể chạy pip install gallery-dl: {e}"))?;

        let stdout = String::from_utf8_lossy(&output.stdout).to_string();
        let stderr = String::from_utf8_lossy(&output.stderr).to_string();

        if output.status.success() {
            let msg = if !stdout.is_empty() { stdout } else { stderr };
            Ok(msg.trim().to_string())
        } else {
            Err(format!("Lỗi cập nhật gallery-dl: {}", stderr.trim()))
        }
    }
}
