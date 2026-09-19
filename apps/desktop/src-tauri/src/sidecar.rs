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
use tokio::process::Command;
use tokio::sync::{oneshot, Mutex};

/// Counter tạo request ID tăng dần
static REQUEST_COUNTER: AtomicU64 = AtomicU64::new(1);

/// Counter tạo worker ID tăng dần để phân biệt các lần spawn worker
static WORKER_ID_COUNTER: AtomicU64 = AtomicU64::new(1);

fn next_req_id() -> String {
    format!("req_{}", REQUEST_COUNTER.fetch_add(1, Ordering::SeqCst))
}

// ─────────────────────────────────────────────────────────────────────────────

/// Trạng thái nội bộ của sidecar — kênh giao tiếp với stdin writer task
struct SidecarInner {
    /// Kênh gửi (request_id, json_line, oneshot_reply_sender)
    tx: tokio::sync::mpsc::Sender<(String, String, oneshot::Sender<Result<Value, String>>)>,
    pending: Arc<Mutex<std::collections::HashMap<String, oneshot::Sender<Result<Value, String>>>>>,
    /// ID phiên bản worker để phân biệt khi worker cũ kết thúc
    worker_id: u64,
}

/// Public handle — Clone-safe vì bọc trong Arc<Mutex>
#[derive(Clone)]
pub struct SidecarManager {
    inner: Arc<Mutex<Option<SidecarInner>>>,
    cli_path: PathBuf,
    spawn_lock: Arc<Mutex<()>>,
}

impl SidecarManager {
    /// Tạo SidecarManager và khởi động Python worker
    pub async fn new(cli_path: PathBuf) -> Self {
        let mgr = Self {
            inner: Arc::new(Mutex::new(None)),
            cli_path,
            spawn_lock: Arc::new(Mutex::new(())),
        };
        mgr.start_worker().await;
        mgr
    }

    /// Tìm đường dẫn extractor_cli.py
    pub fn find_cli_path(resource_dir: Option<&PathBuf>) -> Result<PathBuf, String> {
        // 1. Biến môi trường CRWL_CLI_PATH
        if let Ok(p) = std::env::var("CRWL_CLI_PATH") {
            let pb = PathBuf::from(&p);
            if pb.exists() {
                return Ok(pb);
            }
        }

        // 2. Trong môi trường dev (cargo/tauri dev), ưu tiên code nguồn trực tiếp thay vì target/debug cũ
        #[cfg(debug_assertions)]
        {
            let dev_candidates = vec![
                PathBuf::from("../../core/extractor_cli.py"),
                PathBuf::from("core/extractor_cli.py"),
                PathBuf::from("../../../core/extractor_cli.py"),
            ];
            for path in &dev_candidates {
                if path.exists() {
                    if let Ok(canon) = path.canonicalize() {
                        return Ok(canon);
                    }
                    return Ok(path.clone());
                }
            }
        }

        // 3. Resource dir từ Tauri AppHandle (khi đóng gói bundle deb / AppImage)
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
    pub fn start_worker(&self) -> std::pin::Pin<Box<dyn std::future::Future<Output = ()> + Send + '_>> {
        let this = self.clone();
        Box::pin(async move {
            let _guard = this.spawn_lock.lock().await;

            // Nếu đã có worker đang hoạt động thì không khởi động lại
            {
                let lock = this.inner.lock().await;
                if lock.is_some() {
                    return;
                }
            }

            let worker_id = WORKER_ID_COUNTER.fetch_add(1, Ordering::SeqCst);
            let cli = this.cli_path.clone();
            let inner_arc = this.inner.clone();

        let mut cmd = Command::new("python3");
        cmd.arg(&cli).arg("--stdin");
        cmd.env("PYTHONUNBUFFERED", "1");
        if let Some(proj_root) = cli.parent().and_then(|p| p.parent()) {
            cmd.current_dir(proj_root);
            cmd.env("PYTHONPATH", proj_root);
        }
        cmd.kill_on_drop(true);
        cmd.stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::inherit()); // stderr ra console để debug

        let mut child = match cmd.spawn() {
            Ok(c) => c,
            Err(e) => {
                error!("[Sidecar] Không thể khởi động Python worker #{worker_id}: {e}");
                return;
            }
        };

        let child_id = child.id();
        info!("[Sidecar] Python worker #{worker_id} đã khởi động (PID: {:?})", child_id);

        let stdout = match child.stdout.take() {
            Some(s) => s,
            None => {
                error!("[Sidecar] Không lấy được stdout của Python worker #{worker_id}");
                return;
            }
        };

        let mut stdin = match child.stdin.take() {
            Some(s) => s,
            None => {
                error!("[Sidecar] Không lấy được stdin của Python worker #{worker_id}");
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
                    map.insert(req_id.clone(), reply_tx);
                }
                if let Err(e) = stdin.write_all(line.as_bytes()).await {
                    error!("[Sidecar] Lỗi ghi stdin: {e}");
                    let mut map = pending_write.lock().await;
                    if let Some(tx) = map.remove(&req_id) {
                        let _ = tx.send(Err(format!("Lỗi ghi stdin sidecar: {e}")));
                    }
                    break;
                }
                if let Err(e) = stdin.write_all(b"\n").await {
                    error!("[Sidecar] Lỗi ghi newline stdin: {e}");
                    let mut map = pending_write.lock().await;
                    if let Some(tx) = map.remove(&req_id) {
                        let _ = tx.send(Err(format!("Lỗi ghi stdin sidecar: {e}")));
                    }
                    break;
                }
                if let Err(e) = stdin.flush().await {
                    error!("[Sidecar] Lỗi flush stdin: {e}");
                    let mut map = pending_write.lock().await;
                    if let Some(tx) = map.remove(&req_id) {
                        let _ = tx.send(Err(format!("Lỗi flush stdin sidecar: {e}")));
                    }
                    break;
                }
            }
        });

        // Task 2: stdout reader — đọc JSON response, route về caller qua oneshot
        let mgr_clone = self.clone();
        tokio::spawn(async move {
            let mut reader = BufReader::new(stdout).lines();
            loop {
                match reader.next_line().await {
                    Ok(Some(line)) => {
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
                    Ok(None) => {
                        warn!("[Sidecar] Python worker #{worker_id} stdout đã đóng (EOF).");
                        break;
                    }
                    Err(e) => {
                        warn!("[Sidecar] Lỗi đọc stdout Python worker #{worker_id}: {e}");
                        break;
                    }
                }
            }

            // 1. Trả lỗi cho mọi pending request đang chờ response từ worker này
            {
                let mut map = pending_read.lock().await;
                let count = map.len();
                if count > 0 {
                    warn!("[Sidecar] Python worker #{worker_id} kết thúc: huỷ {count} pending request(s)");
                    for (_id, reply_tx) in map.drain() {
                        let _ = reply_tx.send(Err("Python worker đã dừng đột ngột (stdout EOF)".to_string()));
                    }
                }
            }

            // 2. Chỉ xoá inner nếu worker hiện tại trong inner vẫn là worker này
            let should_respawn = {
                let mut lock = mgr_clone.inner.lock().await;
                if let Some(inner) = lock.as_ref() {
                    if inner.worker_id == worker_id {
                        *lock = None;
                        true
                    } else {
                        false
                    }
                } else {
                    true
                }
            };

            // 3. Tự động respawn worker
            if should_respawn {
                tokio::time::sleep(Duration::from_millis(500)).await;
                info!("[Sidecar] Đang tự động khởi động lại Python worker...");
                mgr_clone.start_worker().await;
            }
        });

        // Task 3: Chờ process kết thúc để thu hồi zombie process (reap child)
        tokio::spawn(async move {
            match child.wait().await {
                Ok(status) => {
                    info!("[Sidecar] Python worker #{worker_id} (PID: {:?}) đã thoát: {status}", child_id);
                }
                Err(e) => {
                    warn!("[Sidecar] Lỗi chờ Python worker #{worker_id} (PID: {:?}): {e}", child_id);
                }
            }
        });

        let mut lock = inner_arc.lock().await;
        *lock = Some(SidecarInner {
            tx,
            pending,
            worker_id,
        });
        })
    }

    /// Thời gian chờ tối đa theo loại request.
    /// Quét profile (nhất là khi chọn "Tất cả") có thể mất vài phút, không thể
    /// dùng chung một mốc 55s với việc giải mã link rút gọn.
    fn timeout_for(payload: &Value) -> Duration {
        let action = payload.get("action").and_then(|v| v.as_str()).unwrap_or("extract");
        match action {
            "resolve" => Duration::from_secs(30),
            "crawl" => {
                let limit = payload.get("limit").and_then(|v| v.as_u64()).unwrap_or(50);
                // limit = 0 nghĩa là "Tất cả" → cho thời gian rộng nhất
                if limit == 0 {
                    Duration::from_secs(600)
                } else if limit > 100 {
                    Duration::from_secs(420)
                } else {
                    Duration::from_secs(240)
                }
            }
            _ => Duration::from_secs(150),
        }
    }

    /// Gửi request IPC tới Python worker và đợi response
    async fn send_request(&self, payload: Value) -> Result<Value, String> {
        let req_id = next_req_id();
        let mut payload = payload;
        payload["id"] = Value::String(req_id.clone());

        let line = serde_json::to_string(&payload).map_err(|e| format!("Serialize error: {e}"))?;

        let (reply_tx, reply_rx) = oneshot::channel();

        // 1. Lấy kênh gửi tx; nếu worker đang trong quá trình khởi động lại, đợi tối đa 3 giây
        let mut tx_opt = None;
        for _ in 0..30 {
            {
                let lock = self.inner.lock().await;
                if let Some(inner) = lock.as_ref() {
                    tx_opt = Some(inner.tx.clone());
                    break;
                }
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }

        let tx = match tx_opt {
            Some(t) => t,
            None => {
                // Thử kích hoạt start_worker nếu chưa có
                self.start_worker().await;
                let lock = self.inner.lock().await;
                match lock.as_ref() {
                    Some(inner) => inner.tx.clone(),
                    None => return Err("Python sidecar chưa khởi động hoặc không thể khởi động".to_string()),
                }
            }
        };

        tx.send((req_id.clone(), line, reply_tx)).await.map_err(|_| {
            "Sidecar worker không phản hồi — có thể đã bị crash".to_string()
        })?;

        let wait_for = Self::timeout_for(&payload);
        match tokio::time::timeout(wait_for, reply_rx).await {
            Ok(Ok(result)) => result,
            Ok(Err(_)) => Err("Sidecar reply channel đóng bất ngờ".to_string()),
            Err(_) => {
                let lock = self.inner.lock().await;
                if let Some(inner) = lock.as_ref() {
                    let mut map = inner.pending.lock().await;
                    map.remove(&req_id);
                }
                Err(format!(
                    "Timeout: máy chủ bóc tách không phản hồi sau {} giây. Hãy giảm số lượng cần quét rồi thử lại.",
                    wait_for.as_secs()
                ))
            }
        }
    }

    /// Trích xuất thông tin media từ URL
    pub async fn extract_media(&self, url: &str, browser: Option<&str>) -> Result<Value, String> {
        let mut payload = json!({ "action": "extract", "url": url });
        if let Some(b) = browser {
            // Truyền xuống Python kể cả "none" — Python sẽ tự xử lý logic bỏ cookie
            if !b.trim().is_empty() {
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
            // Truyền xuống Python kể cả "none" — Python sẽ tự xử lý logic bỏ cookie
            if !b.is_empty() {
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[test]
    fn test_timeout_for() {
        assert_eq!(
            SidecarManager::timeout_for(&json!({"action": "resolve"})),
            Duration::from_secs(30)
        );
        assert_eq!(
            SidecarManager::timeout_for(&json!({"action": "crawl", "limit": 0})),
            Duration::from_secs(600)
        );
        assert_eq!(
            SidecarManager::timeout_for(&json!({"action": "crawl", "limit": 150})),
            Duration::from_secs(420)
        );
        assert_eq!(
            SidecarManager::timeout_for(&json!({"action": "crawl", "limit": 50})),
            Duration::from_secs(240)
        );
        assert_eq!(
            SidecarManager::timeout_for(&json!({"action": "extract"})),
            Duration::from_secs(150)
        );
    }

    #[tokio::test]
    async fn test_worker_eof_cancels_pending_and_respawns() {
        let temp_dir = std::env::temp_dir();
        let script_path = temp_dir.join(format!(
            "test_sidecar_crash_{}.py",
            REQUEST_COUNTER.fetch_add(1, Ordering::SeqCst)
        ));

        // Script giả lập: đọc 1 dòng từ stdin rồi lập tức exit(1) (EOF trên stdout)
        {
            let mut file = std::fs::File::create(&script_path).expect("Tạo file mock Python script");
            writeln!(file, "import sys\nline = sys.stdin.readline()\nsys.exit(1)\n").unwrap();
        }

        let mgr = SidecarManager::new(script_path.clone()).await;

        // Gửi request. Khi Python worker crash (stdout EOF), request PHẢI trả lỗi ngay lập tức
        let start = std::time::Instant::now();
        let res = mgr.extract_media("https://example.com/test", None).await;
        let elapsed = start.elapsed();

        assert!(res.is_err(), "Request phải trả về lỗi khi worker crash");
        let err_msg = res.unwrap_err();
        assert!(
            err_msg.contains("stdout EOF") || err_msg.contains("dừng đột ngột") || err_msg.contains("crash"),
            "Thông báo lỗi phải thông báo worker đã dừng/crash: {err_msg}"
        );
        assert!(
            elapsed < Duration::from_secs(5),
            "Request không được treo quá lâu (mất: {:?})",
            elapsed
        );

        // Đợi 700ms để Task 2 hoàn tất dọn dẹp và respawn worker mới
        tokio::time::sleep(Duration::from_millis(700)).await;

        {
            let lock = mgr.inner.lock().await;
            assert!(lock.is_some(), "Worker phải được tự động respawn lại sau khi crash");
            if let Some(inner) = lock.as_ref() {
                let pending_map = inner.pending.lock().await;
                assert_eq!(pending_map.len(), 0, "Pending map phải trống sau khi worker dừng");
            }
        }

        let _ = std::fs::remove_file(&script_path);
    }

    #[tokio::test]
    async fn test_worker_normal_response() {
        let temp_dir = std::env::temp_dir();
        let script_path = temp_dir.join(format!(
            "test_sidecar_echo_{}.py",
            REQUEST_COUNTER.fetch_add(1, Ordering::SeqCst)
        ));

        // Script giả lập bình thường: đọc JSON từ stdin và trả về JSON success
        {
            let mut file = std::fs::File::create(&script_path).expect("Tạo file mock Python script");
            writeln!(
                file,
                "import sys, json\nfor line in sys.stdin:\n    req = json.loads(line.strip())\n    sys.stdout.write(json.dumps({{'id': req['id'], 'success': True, 'data': {{'title': 'Mock Video'}}}})+'\\n')\n    sys.stdout.flush()\n"
            ).unwrap();
        }

        let mgr = SidecarManager::new(script_path.clone()).await;
        let res = mgr.extract_media("https://example.com/echo", None).await;

        assert!(res.is_ok(), "Request phải thành công với mock worker bình thường");
        let data = res.unwrap();
        assert_eq!(data.get("title").and_then(|v| v.as_str()), Some("Mock Video"));

        let _ = std::fs::remove_file(&script_path);
    }
}

