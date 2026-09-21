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

use crate::settings::SettingsManager;

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
    /// Tìm binary trong PATH và các vị trí phổ biến trên hệ thống (Linux & Windows)
    pub fn find_binary(name: &str) -> Option<PathBuf> {
        // Đường dẫn do người dùng cấu hình được ưu tiên trước PATH
        if let Some(custom) = SettingsManager::custom_binary_path(name) {
            return Some(custom);
        }

        // 1. Tìm trực tiếp trong PATH
        if let Ok(p) = which::which(name) {
            return Some(p);
        }

        // 2. Xử lý đặc thù trên Windows
        #[cfg(windows)]
        {
            // Kiểm tra tên có đuôi .exe
            if !name.ends_with(".exe") {
                if let Ok(p) = which::which(format!("{}.exe", name)) {
                    return Some(p);
                }
            }

            // Với Python trên Windows: python3 thường không có, chỉ có python.exe hoặc py.exe
            if name == "python3" {
                if let Ok(p) = which::which("python") {
                    return Some(p);
                }
                if let Ok(p) = which::which("py") {
                    return Some(p);
                }

                // Dò tìm trong %LOCALAPPDATA%\Programs\Python\Python3*
                if let Some(local_app_data) = dirs::data_local_dir() {
                    let py_dir = local_app_data.join("Programs").join("Python");
                    if py_dir.exists() {
                        if let Ok(entries) = std::fs::read_dir(&py_dir) {
                            for entry in entries.flatten() {
                                let exe = entry.path().join("python.exe");
                                if exe.is_file() {
                                    return Some(exe);
                                }
                            }
                        }
                    }
                }
            }
        }

        // 3. Thư mục crwl/bin trong config của ứng dụng
        if let Some(config_dir) = dirs::config_dir() {
            let app_bin = config_dir.join("crwl").join("bin").join(name);
            if app_bin.exists() {
                return Some(app_bin);
            }
            #[cfg(windows)]
            {
                let app_bin_exe = config_dir.join("crwl").join("bin").join(format!("{}.exe", name));
                if app_bin_exe.exists() {
                    return Some(app_bin_exe);
                }
            }
        }

        // 4. Thư mục ~/.local/bin trên Linux/Unix
        let home = dirs::home_dir().unwrap_or_else(|| PathBuf::from("/tmp"));
        let local_bin = home.join(".local/bin").join(name);
        if local_bin.exists() {
            return Some(local_bin);
        }

        // 5. Các vị trí tiêu chuẩn trên Linux
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

    /// Tiện ích tìm Python executable đa nền tảng
    pub fn find_python() -> Option<PathBuf> {
        Self::find_binary("python3")
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

    pub(crate) fn evaluate_ytdlp_update(
        stdout: &str,
        stderr: &str,
        success: bool,
        code: Option<i32>,
    ) -> Result<String, String> {
        let combined = format!("{stdout}\n{stderr}");

        if combined.contains("up to date") || combined.contains("is up to date") {
            return Ok("yt-dlp đã là phiên bản mới nhất!".to_string());
        }

        if success {
            return Ok("Đã cập nhật yt-dlp thành công!".to_string());
        }

        let err = if !stderr.trim().is_empty() {
            stderr.trim().to_string()
        } else if !stdout.trim().is_empty() {
            stdout.trim().to_string()
        } else {
            format!("Mã thoát: {}", code.unwrap_or(-1))
        };
        Err(err)
    }

    /// Binary có nằm trong một venv của pipx không (~/.local/pipx/venvs/<tool>/bin/…)
    pub(crate) fn is_pipx_managed(path: &std::path::Path) -> bool {
        let p = path.to_string_lossy().replace('\\', "/");
        p.contains("/pipx/venvs/") || p.contains("/pipx/shared/")
    }

    /// Binary có nằm trong thư mục của người dùng không (cài kiểu `pip --user`)
    pub(crate) fn is_user_local(path: &std::path::Path) -> bool {
        match dirs::home_dir() {
            Some(home) => path.starts_with(home),
            None => false,
        }
    }

    /// Một lượt chạy pip: trả về (thành công, stdout + stderr gộp)
    async fn run_pip(python: &PathBuf, args: &[&str]) -> (bool, String) {
        match Command::new(python)
            .args(args)
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .output()
            .await
        {
            Ok(out) => {
                let combined = format!(
                    "{}\n{}",
                    String::from_utf8_lossy(&out.stdout),
                    String::from_utf8_lossy(&out.stderr)
                );
                (out.status.success(), combined)
            }
            Err(e) => (false, format!("Không chạy được pip: {e}")),
        }
    }

    /// Cài/nâng cấp một gói pip vào ĐÚNG môi trường đang chứa binary.
    ///
    /// Trước đây chỉ có duy nhất `pip install -U <pkg> --break-system-packages`:
    /// thiếu `--user` nên với binary cài kiểu `pip --user` (ví dụ `~/.local/bin/yt-dlp`)
    /// lệnh sẽ nhắm vào site-packages hệ thống — hoặc lỗi quyền, hoặc cài ra một
    /// prefix khác hẳn với binary đang thực sự được dùng, và `--break-system-packages`
    /// còn có nguy cơ đụng vào gói do distro quản lý.
    pub(crate) async fn pip_upgrade(pkg: &str, existing_binary: Option<&PathBuf>) -> Result<String, String> {
        // 1. pipx quản lý thì nâng cấp bằng pipx, tuyệt đối không đụng pip
        if let Some(bin) = existing_binary {
            if Self::is_pipx_managed(bin) {
                if let Some(pipx) = Self::find_binary("pipx") {
                    let out = Command::new(&pipx)
                        .args(["upgrade", pkg])
                        .stdout(Stdio::piped())
                        .stderr(Stdio::piped())
                        .output()
                        .await
                        .map_err(|e| format!("Không chạy được pipx: {e}"))?;
                    let combined = format!(
                        "{}\n{}",
                        String::from_utf8_lossy(&out.stdout),
                        String::from_utf8_lossy(&out.stderr)
                    );
                    if out.status.success() || combined.contains("already at latest version") {
                        return Ok(format!("Đã cập nhật {pkg} qua pipx."));
                    }
                    return Err(format!("pipx upgrade {pkg} thất bại: {}", combined.trim()));
                }
                return Err(format!(
                    "{pkg} được cài bằng pipx nhưng không tìm thấy lệnh pipx. Chạy thủ công: pipx upgrade {pkg}"
                ));
            }
        }

        let python = Self::find_binary("python3").ok_or_else(|| "python3 chưa được cài đặt".to_string())?;

        // 2. Cài vào đúng phạm vi của binary hiện có: ~/… → --user
        let prefer_user = existing_binary.map(|b| Self::is_user_local(b)).unwrap_or(true);

        let mut attempts: Vec<Vec<&str>> = Vec::new();
        if prefer_user {
            attempts.push(vec!["-m", "pip", "install", "-U", "--user", pkg]);
        }
        attempts.push(vec!["-m", "pip", "install", "-U", pkg]);

        let mut last = String::new();
        for args in &attempts {
            let (ok, out) = Self::run_pip(&python, args).await;
            if out.contains("Requirement already satisfied") || out.contains("already satisfied") {
                return Ok(format!("{pkg} đã là phiên bản mới nhất!"));
            }
            if ok {
                let how = if args.contains(&"--user") { " (--user)" } else { "" };
                return Ok(format!("Đã cập nhật {pkg} thành công qua pip{how}!"));
            }
            // venv đang hoạt động thì không dùng được --user → thử lại không cờ
            if out.contains("Can not perform a '--user' install") {
                last = out;
                continue;
            }
            // PEP 668: Python do distro quản lý. Chỉ khi ĐÃ thất bại mới dùng
            // --break-system-packages, và luôn kèm --user để không đụng gói hệ thống.
            if out.contains("externally-managed-environment") {
                let mut esc: Vec<&str> = args.clone();
                esc.push("--break-system-packages");
                if !esc.contains(&"--user") {
                    esc.push("--user");
                }
                let (ok2, out2) = Self::run_pip(&python, &esc).await;
                if out2.contains("already satisfied") {
                    return Ok(format!("{pkg} đã là phiên bản mới nhất!"));
                }
                if ok2 {
                    return Ok(format!(
                        "Đã cập nhật {pkg} qua pip (--user --break-system-packages)."
                    ));
                }
                last = out2;
                continue;
            }
            last = out;
        }

        Err(format!("Cập nhật {pkg} thất bại: {}", last.trim()))
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

        let ytdlp_err = match output {
            Ok(res) => {
                let stdout = String::from_utf8_lossy(&res.stdout);
                let stderr = String::from_utf8_lossy(&res.stderr);
                match Self::evaluate_ytdlp_update(&stdout, &stderr, res.status.success(), res.status.code()) {
                    Ok(msg) => return Ok(msg),
                    Err(e) => e,
                }
            }
            Err(e) => format!("Không thể chạy yt-dlp: {e}"),
        };

        // yt-dlp -U không xong (thường vì cài qua pip/pipx/distro) → nâng cấp
        // đúng môi trường đang chứa binary đó.
        match Self::pip_upgrade("yt-dlp", Some(&ytdlp_path)).await {
            Ok(msg) => Ok(msg),
            Err(pip_err) => Err(format!(
                "Lỗi cập nhật yt-dlp: {ytdlp_err} (Thử qua trình quản lý gói: {pip_err})"
            )),
        }
    }

    /// Cập nhật gallery-dl lên version mới nhất, vào đúng môi trường đang dùng
    pub async fn update_gallery_dl() -> Result<String, String> {
        let existing = Self::find_binary("gallery-dl");
        info!("Đang cập nhật gallery-dl (binary: {:?})...", existing);
        Self::pip_upgrade("gallery-dl", existing.as_ref()).await
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_evaluate_ytdlp_update_up_to_date() {
        let stdout = "Latest version: 2026.08.19\nyt-dlp is up to date";
        let res = BinaryManager::evaluate_ytdlp_update(stdout, "", false, Some(0));
        assert_eq!(res.unwrap(), "yt-dlp đã là phiên bản mới nhất!");
    }

    #[test]
    fn test_evaluate_ytdlp_update_success() {
        let stdout = "Updating to version 2026.09.01 ...\nUpdated yt-dlp to version 2026.09.01";
        let res = BinaryManager::evaluate_ytdlp_update(stdout, "", true, Some(0));
        assert_eq!(res.unwrap(), "Đã cập nhật yt-dlp thành công!");
    }

    #[test]
    fn pipx_managed_binaries_are_detected() {
        use std::path::Path;
        assert!(BinaryManager::is_pipx_managed(Path::new(
            "/home/u/.local/pipx/venvs/yt-dlp/bin/yt-dlp"
        )));
        assert!(BinaryManager::is_pipx_managed(Path::new(
            "/home/u/.local/pipx/shared/bin/yt-dlp"
        )));
        assert!(!BinaryManager::is_pipx_managed(Path::new("/home/u/.local/bin/yt-dlp")));
        assert!(!BinaryManager::is_pipx_managed(Path::new("/usr/bin/yt-dlp")));
    }

    /// Binary cài kiểu `pip --user` phải được nâng cấp bằng `--user`, không đụng
    /// site-packages của hệ thống.
    #[test]
    fn user_local_binaries_are_detected() {
        use std::path::Path;
        let home = dirs::home_dir().expect("cần HOME để chạy test này");
        assert!(BinaryManager::is_user_local(&home.join(".local/bin/yt-dlp")));
        assert!(!BinaryManager::is_user_local(Path::new("/usr/bin/yt-dlp")));
        assert!(!BinaryManager::is_user_local(Path::new("/usr/local/bin/yt-dlp")));
    }

    #[test]
    fn test_evaluate_ytdlp_update_error() {
        let stderr = "ERROR: yt-dlp was installed with a package manager";
        let res = BinaryManager::evaluate_ytdlp_update("", stderr, false, Some(1));
        assert_eq!(res.unwrap_err(), "ERROR: yt-dlp was installed with a package manager");
    }



}
