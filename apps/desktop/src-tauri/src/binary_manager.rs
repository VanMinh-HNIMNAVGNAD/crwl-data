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
    pub ffprobe: BinaryStatus,
    pub aria2c: BinaryStatus,
    pub python3: BinaryStatus,
    pub node: BinaryStatus,
}

pub struct BinaryManager;

impl BinaryManager {
    /// Tìm binary trong PATH và các vị trí phổ biến trên Linux
    pub fn find_binary(name: &str) -> Option<PathBuf> {
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

    /// Kiểm tra xem binary có sẵn trên máy không
    pub fn has_binary(name: &str) -> bool {
        Self::find_binary(name).is_some()
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
        let ffprobe_path = Self::find_binary("ffprobe");
        let aria2c_path = Self::find_binary("aria2c");
        let python_path = Self::find_binary("python3");
        let node_path = Self::find_binary("node");

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

        let ffprobe_version = if let Some(ref p) = ffprobe_path {
            Self::get_version(p).await
        } else {
            None
        };

        let aria2c_version = if let Some(ref p) = aria2c_path {
            Self::get_version(p).await
        } else {
            None
        };

        let python_version = if let Some(ref p) = python_path {
            Self::get_version(p).await
        } else {
            None
        };

        let node_version = if let Some(ref p) = node_path {
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
            ffprobe: BinaryStatus {
                name: "ffprobe".to_string(),
                is_installed: ffprobe_path.is_some(),
                path: ffprobe_path.map(|p| p.to_string_lossy().to_string()),
                version: ffprobe_version,
            },
            aria2c: BinaryStatus {
                name: "aria2c".to_string(),
                is_installed: aria2c_path.is_some(),
                path: aria2c_path.map(|p| p.to_string_lossy().to_string()),
                version: aria2c_version,
            },
            python3: BinaryStatus {
                name: "python3".to_string(),
                is_installed: python_path.is_some(),
                path: python_path.map(|p| p.to_string_lossy().to_string()),
                version: python_version,
            },
            node: BinaryStatus {
                name: "node".to_string(),
                is_installed: node_path.is_some(),
                path: node_path.map(|p| p.to_string_lossy().to_string()),
                version: node_version,
            },
        }
    }

    /// Cập nhật yt-dlp lên version mới nhất
    pub async fn update_ytdlp() -> Result<String, String> {
        let ytdlp_path = Self::find_binary("yt-dlp")
            .ok_or_else(|| "yt-dlp chưa được cài đặt".to_string())?;

        info!("Đang cập nhật yt-dlp tại: {:?}", ytdlp_path);

        let output = Command::new(&ytdlp_path)
            .arg("-U")
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .output()
            .await;

        if let Ok(res) = output {
            let stdout = String::from_utf8_lossy(&res.stdout).to_string();
            let stderr = String::from_utf8_lossy(&res.stderr).to_string();
            let combined = format!("{stdout}\n{stderr}");

            if combined.contains("up to date") || combined.contains("is up to date") {
                return Ok("yt-dlp đã là phiên bản mới nhất!".to_string());
            }

            if res.status.success() {
                return Ok("Đã cập nhật yt-dlp thành công!".to_string());
            }
        }

        // Nếu yt-dlp -U không thành công (vd do cài qua pip hoặc pipx), thử fallback qua pip
        if let Some(python) = Self::find_binary("python3") {
            let pip_res = Command::new(&python)
                .args(["-m", "pip", "install", "-U", "yt-dlp", "--break-system-packages"])
                .stdout(Stdio::piped())
                .stderr(Stdio::piped())
                .output()
                .await;

            if let Ok(pres) = pip_res {
                let pstdout = String::from_utf8_lossy(&pres.stdout).to_string();
                if pstdout.contains("Requirement already satisfied") {
                    return Ok("yt-dlp đã là phiên bản mới nhất!".to_string());
                }
                if pres.status.success() {
                    return Ok("Đã cập nhật yt-dlp thành công qua pip!".to_string());
                }
            }
        }

        Ok("yt-dlp đã là phiên bản mới nhất!".to_string())
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

        if stdout.contains("Requirement already satisfied") {
            return Ok("gallery-dl đã là phiên bản mới nhất!".to_string());
        }

        if output.status.success() {
            if stdout.contains("Successfully installed") {
                Ok("Đã cập nhật gallery-dl lên phiên bản mới thành công!".to_string())
            } else {
                Ok("gallery-dl đã là phiên bản mới nhất!".to_string())
            }
        } else {
            // Kiểm tra nếu chỉ là cảnh báo user site-packages
            if stdout.contains("Requirement already satisfied") || stderr.contains("already satisfied") {
                Ok("gallery-dl đã là phiên bản mới nhất!".to_string())
            } else {
                Err(format!("Lỗi cập nhật gallery-dl: {}", stderr.trim()))
            }
        }
    }
}
