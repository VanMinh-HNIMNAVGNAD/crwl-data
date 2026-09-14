#![allow(dead_code)]

/**
 * ExtractorService — DEPRECATED.
 * Tất cả logic extraction đã chuyển sang SidecarManager (sidecar.rs)
 * để dùng JSON IPC với Python worker sống ngầm thay vì subprocess-per-request.
 *
 * File này giữ lại như fallback reference.
 */

use std::path::PathBuf;
use std::process::Stdio;
use log::{error, info};
use serde_json::Value;
use tokio::process::Command;

pub struct ExtractorService;

impl ExtractorService {
    pub fn find_cli_path() -> Result<PathBuf, String> {
        let candidates = vec![
            PathBuf::from("core/extractor_cli.py"),
            PathBuf::from("../../core/extractor_cli.py"),
            PathBuf::from("../../../core/extractor_cli.py"),
        ];
        for path in &candidates {
            if path.exists() {
                return Ok(path.clone());
            }
        }
        if let Ok(exe_dir) = std::env::current_exe() {
            if let Some(parent) = exe_dir.parent() {
                let resource_path = parent.join("core/extractor_cli.py");
                if resource_path.exists() {
                    return Ok(resource_path);
                }
            }
        }
        let cwd = std::env::current_dir().map_err(|e| e.to_string())?;
        for path in &candidates {
            let abs = cwd.join(path);
            if abs.exists() {
                return Ok(abs);
            }
        }
        Err("Không tìm thấy core/extractor_cli.py".to_string())
    }

    pub async fn resolve_url(url: &str, expected_platform: Option<&str>) -> Result<Value, String> {
        let cli_path = Self::find_cli_path()?;
        let mut cmd = Command::new("python3");
        cmd.arg(&cli_path).arg("resolve").arg(url);
        if let Some(exp) = expected_platform {
            if !exp.trim().is_empty() {
                cmd.arg("--expected").arg(exp);
            }
        }
        cmd.stdout(Stdio::piped()).stderr(Stdio::piped());
        info!("(Fallback) Resolve URL qua Python CLI: {url}");
        let output = cmd.output().await.map_err(|e| format!("Lỗi khi chạy Python: {e}"))?;
        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            error!("Python resolve failed: {stderr}");
            return Err(format!("Lỗi giải mã URL: {stderr}"));
        }
        let stdout = String::from_utf8_lossy(&output.stdout);
        serde_json::from_str(&stdout).map_err(|e| format!("Lỗi parse JSON: {e}"))
    }

    pub async fn extract_media(url: &str, browser: Option<&str>) -> Result<Value, String> {
        let cli_path = Self::find_cli_path()?;
        let mut cmd = Command::new("python3");
        cmd.arg(&cli_path).arg("extract").arg(url);
        if let Some(b) = browser {
            if !b.trim().is_empty() && b != "none" {
                cmd.arg("--browser").arg(b);
            }
        }
        cmd.stdout(Stdio::piped()).stderr(Stdio::piped());
        info!("(Fallback) Extract media qua Python CLI: {url}");
        let output = cmd.output().await.map_err(|e| format!("Lỗi khi chạy Python: {e}"))?;
        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            error!("Python extract failed: {stderr}");
            return Err(format!("Lỗi trích xuất: {stderr}"));
        }
        let stdout = String::from_utf8_lossy(&output.stdout);
        serde_json::from_str(&stdout).map_err(|e| format!("Lỗi parse JSON: {e}"))
    }

    pub async fn crawl_profile(
        url: &str,
        limit: Option<u32>,
        media_type: Option<&str>,
        platform: Option<&str>,
        browser: Option<&str>,
        range_start: Option<u32>,
        range_end: Option<u32>,
    ) -> Result<Value, String> {
        let cli_path = Self::find_cli_path()?;
        let mut cmd = Command::new("python3");
        cmd.arg(&cli_path).arg("crawl").arg(url);
        if let Some(lim) = limit {
            cmd.arg("--limit").arg(lim.to_string());
        }
        if let Some(mt) = media_type {
            if !mt.trim().is_empty() { cmd.arg("--media-type").arg(mt); }
        }
        if let Some(plat) = platform {
            if !plat.trim().is_empty() && plat != "auto" { cmd.arg("--platform").arg(plat); }
        }
        if let Some(b) = browser {
            if !b.trim().is_empty() && b != "none" { cmd.arg("--browser").arg(b); }
        }
        if let Some(start) = range_start { cmd.arg("--range-start").arg(start.to_string()); }
        if let Some(end) = range_end { cmd.arg("--range-end").arg(end.to_string()); }
        cmd.stdout(Stdio::piped()).stderr(Stdio::piped());
        info!("(Fallback) Crawl profile qua Python CLI: {url}");
        let output = cmd.output().await.map_err(|e| format!("Lỗi khi chạy Python: {e}"))?;
        if !output.status.success() {
            let stderr = String::from_utf8_lossy(&output.stderr);
            error!("Python crawl failed: {stderr}");
            return Err(format!("Lỗi quét: {stderr}"));
        }
        let stdout = String::from_utf8_lossy(&output.stdout);
        serde_json::from_str(&stdout).map_err(|e| format!("Lỗi parse JSON: {e}"))
    }
}
