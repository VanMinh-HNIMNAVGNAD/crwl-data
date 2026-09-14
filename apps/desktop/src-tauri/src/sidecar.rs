/**
 * Sidecar IPC Manager — Quản lý Python worker process sống ngầm.
 *
 * Protocol: Mỗi request/response là 1 dòng JSON (newline-delimited).
 * Request:  {"id": "<uuid>", "action": "extract"|"crawl"|"resolve", "url": "...", ...args}
 * Response: {"id": "<uuid>", "success": true|false, "data": {...} | "error": "..."}
 */
use std::collections::HashMap;
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;

use log::{error, info, warn};
use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, Command};
use tokio::sync::{oneshot, Mutex};

/// Counter tạo request ID tăng dần
static REQUEST_COUNTER: AtomicU64 = AtomicU64::new(1);

fn next_req_id() -> String {
    format!("req_{}", REQUEST_COUNTER.fetch_add(1, Ordering::SeqCst))
}

// ─────────────────────────────────────────────────────────────────────────────

/// Trạng thái nội bộ của sidecar — kênh giao tiếp với stdin writer task
struct SidecarInner {
    /// Kênh gửi (request_id, json_line, oneshot_reply_sender)
    tx: tokio::sync::mpsc::Sender<(String, String, oneshot::Sender<Result<Value, String>>)>,
    /// Handle của Python subprocess
    _child: Child,
}

/// Public handle — Clone-safe vì bọc trong Arc<Mutex>
#[derive(Clone)]
pub struct SidecarManager {
    inner: Arc<Mutex<Option<SidecarInner>>>,
    cli_path: PathBuf,
}

impl SidecarManager {
    /// Tạo SidecarManager và khởi động Python worker
    pub async fn new(cli_path: PathBuf) -> Self {
        let mgr = Self {
            inner: Arc::new(Mutex::new(None)),
            cli_path,
        };
        mgr.start_worker().await;
        mgr
    }

    /// Tìm đường dẫn extractor_cli.py
    pub fn find_cli_path(resource_dir: Option<&PathBuf>) -> Result<PathBuf, String> {
        // Thứ tự ưu tiên:
        // 1. Biến môi trường CRWL_CLI_PATH
        if let Ok(p) = std::env::var("CRWL_CLI_PATH") {
            let pb = PathBuf::from(&p);
            if pb.exists() {
                return Ok(pb);
            }
        }

        // 2. Resource dir từ Tauri AppHandle (khi đóng gói bundle deb / AppImage)
        if let Some(res) = resource_dir {
            let p1 = res.join("core/extractor_cli.py");
            if p1.exists() {
                return Ok(p1);
            }
            let p2 = res.join("extractor_cli.py");
            if p2.exists() {
                return Ok(p2);
            }
        }

        let candidates = vec![
            PathBuf::from("core/extractor_cli.py"),
            PathBuf::from("../../core/extractor_cli.py"),
            PathBuf::from("../../../core/extractor_cli.py"),
        ];

        // 3. Kiểm tra tương đối
        for path in &candidates {
            if path.exists() {
                return Ok(path.clone());
            }
        }

        // 4. Kiểm tra từ exe dir
        if let Ok(exe) = std::env::current_exe() {
            if let Some(parent) = exe.parent() {
                let exe_candidates = vec![
                    parent.join("core/extractor_cli.py"),
                    parent.join("resources/core/extractor_cli.py"),
                    parent.join("../lib/social-media-crawler/core/extractor_cli.py"),
                    parent.join("../lib/app/core/extractor_cli.py"),
                    parent.join("../share/social-media-crawler/core/extractor_cli.py"),
                ];
                for p in exe_candidates {
                    if p.exists() {
                        return Ok(p);
                    }
                }
            }
        }

        // 5. Kiểm tra từ cwd
        if let Ok(cwd) = std::env::current_dir() {
            for rel in &candidates {
                let abs = cwd.join(rel);
                if abs.exists() {
                    return Ok(abs);
                }
            }
        }

        Err("Không tìm thấy core/extractor_cli.py".to_string())
    }

    /// Khởi động Python subprocess với --stdin mode
    async fn start_worker(&self) {
        let cli = self.cli_path.clone();
        let inner_arc = self.inner.clone();

        let mut child = match Command::new("python3")
            .arg(&cli)
            .arg("--stdin")
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit()) // stderr ra console để debug
            .spawn()
        {
            Ok(c) => c,
            Err(e) => {
                error!("[Sidecar] Không thể khởi động Python worker: {e}");
                return;
            }
        };

        info!("[Sidecar] Python worker đã khởi động (PID: {:?})", child.id());

        let stdout = match child.stdout.take() {
            Some(s) => s,
            None => {
                error!("[Sidecar] Không lấy được stdout của Python worker");
                return;
            }
        };

        let mut stdin = match child.stdin.take() {
            Some(s) => s,
            None => {
                error!("[Sidecar] Không lấy được stdin của Python worker");
                return;
            }
        };

        // Kênh nội bộ: từ Rust command → stdin writer task
        let (tx, mut rx) = tokio::sync::mpsc::channel::<(
            String,
            String,
            oneshot::Sender<Result<Value, String>>,
        )>(32);

        // Map request_id → oneshot sender để route response về đúng caller
        let pending: Arc<Mutex<HashMap<String, oneshot::Sender<Result<Value, String>>>>> =
            Arc::new(Mutex::new(HashMap::new()));
        let pending_write = pending.clone();
        let pending_read = pending.clone();

        // Task 1: stdin writer — nhận từ kênh, ghi JSON line vào stdin Python
        tokio::spawn(async move {
            while let Some((req_id, line, reply_tx)) = rx.recv().await {
                {
                    let mut map = pending_write.lock().await;
                    map.insert(req_id, reply_tx);
                }
                if let Err(e) = stdin.write_all(line.as_bytes()).await {
                    error!("[Sidecar] Lỗi ghi stdin: {e}");
                    break;
                }
                if let Err(e) = stdin.write_all(b"\n").await {
                    error!("[Sidecar] Lỗi ghi newline stdin: {e}");
                    break;
                }
                let _ = stdin.flush().await;
            }
        });

        // Task 2: stdout reader — đọc JSON response, route về caller qua oneshot
        tokio::spawn(async move {
            let mut reader = BufReader::new(stdout).lines();
            while let Ok(Some(line)) = reader.next_line().await {
                let line = line.trim().to_string();
                if line.is_empty() {
                    continue;
                }

                match serde_json::from_str::<Value>(&line) {
                    Ok(val) => {
                        let req_id = val.get("id").and_then(|v| v.as_str()).unwrap_or("").to_string();
                        let mut map = pending_read.lock().await;
                        if let Some(reply_tx) = map.remove(&req_id) {
                            let success = val.get("success").and_then(|v| v.as_bool()).unwrap_or(false);
                            if success {
                                let data = val.get("data").cloned().unwrap_or(Value::Null);
                                let _ = reply_tx.send(Ok(data));
                            } else {
                                let err = val
                                    .get("error")
                                    .and_then(|v| v.as_str())
                                    .unwrap_or("Unknown sidecar error")
                                    .to_string();
                                let _ = reply_tx.send(Err(err));
                            }
                        }
                    }
                    Err(e) => {
                        warn!("[Sidecar] Lỗi parse JSON response: {e} — line: {line}");
                    }
                }
            }
            warn!("[Sidecar] Python worker stdout đã đóng.");
        });

        let mut lock = inner_arc.lock().await;
        *lock = Some(SidecarInner { tx, _child: child });
    }

    /// Gửi request IPC tới Python worker và đợi response (timeout 30s)
    async fn send_request(&self, payload: Value) -> Result<Value, String> {
        let req_id = next_req_id();
        let mut payload = payload;
        payload["id"] = Value::String(req_id.clone());

        let line = serde_json::to_string(&payload).map_err(|e| format!("Serialize error: {e}"))?;

        let (reply_tx, reply_rx) = oneshot::channel();

        {
            let lock = self.inner.lock().await;
            match lock.as_ref() {
                Some(inner) => {
                    inner.tx.send((req_id, line, reply_tx)).await.map_err(|_| {
                        "Sidecar worker không phản hồi — có thể đã bị crash".to_string()
                    })?;
                }
                None => {
                    return Err("Python sidecar chưa khởi động".to_string());
                }
            }
        }

        match tokio::time::timeout(Duration::from_secs(90), reply_rx).await {
            Ok(Ok(result)) => result,
            Ok(Err(_)) => Err("Sidecar reply channel đóng bất ngờ".to_string()),
            Err(_) => Err("Timeout: Python worker không phản hồi sau 90 giây".to_string()),
        }
    }

    /// Trích xuất thông tin media từ URL
    pub async fn extract_media(&self, url: &str, browser: Option<&str>) -> Result<Value, String> {
        let mut payload = json!({ "action": "extract", "url": url });
        if let Some(b) = browser {
            if !b.trim().is_empty() && b != "none" {
                payload["browser"] = Value::String(b.to_string());
            }
        }
        self.send_request(payload).await
    }

    /// Giải mã URL rút gọn
    pub async fn resolve_url(&self, url: &str, expected_platform: Option<&str>) -> Result<Value, String> {
        let mut payload = json!({ "action": "resolve", "url": url });
        if let Some(p) = expected_platform {
            if !p.trim().is_empty() {
                payload["expected"] = Value::String(p.to_string());
            }
        }
        self.send_request(payload).await
    }

    /// Quét profile / channel / playlist
    pub async fn crawl_profile(
        &self,
        url: &str,
        limit: Option<u32>,
        media_type: Option<&str>,
        platform: Option<&str>,
        browser: Option<&str>,
        range_start: Option<u32>,
        range_end: Option<u32>,
    ) -> Result<Value, String> {
        let mut payload = json!({
            "action": "crawl",
            "url": url,
            "limit": limit.unwrap_or(50),
        });
        if let Some(mt) = media_type {
            if !mt.is_empty() {
                payload["media_type"] = Value::String(mt.to_string());
            }
        }
        if let Some(p) = platform {
            if !p.is_empty() && p != "auto" {
                payload["platform"] = Value::String(p.to_string());
            }
        }
        if let Some(b) = browser {
            if !b.is_empty() && b != "none" {
                payload["browser"] = Value::String(b.to_string());
            }
        }
        if let Some(rs) = range_start {
            payload["range_start"] = Value::Number(rs.into());
        }
        if let Some(re) = range_end {
            payload["range_end"] = Value::Number(re.into());
        }

        self.send_request(payload).await
    }
}
