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

/// Giới hạn số lần restart liên tiếp khi worker bị crash liên tục
pub const MAX_CONSECUTIVE_RESTARTS: u32 = 3;

/// Ngưỡng thời gian mà worker chạy ổn định để coi là thành công (giây)
pub const STABILITY_THRESHOLD: Duration = Duration::from_secs(10);

/// Trạng thái vòng đời của Python worker
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum WorkerStatus {
    Uninitialized,
    Starting,
    Running,
    Failed(String),
    Stopped,
}

#[derive(Debug)]
struct LifecycleState {
    status: WorkerStatus,
    consecutive_crashes: u32,
    last_spawn_time: Option<std::time::Instant>,
    last_error: Option<String>,
}

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
    /// Ra lệnh cho supervisor kill tiến trình Python. Nếu chỉ xoá handle thì
    /// tiến trình con vẫn sống tiếp tới khi thoát hẳn ứng dụng.
    kill_tx: tokio::sync::mpsc::Sender<()>,
}

/// Public handle — Clone-safe vì bọc trong Arc<Mutex>
#[derive(Clone)]
pub struct SidecarManager {
    inner: Arc<Mutex<Option<SidecarInner>>>,
    cli_path: PathBuf,
    spawn_lock: Arc<Mutex<()>>,
    lifecycle: Arc<Mutex<LifecycleState>>,
}

impl SidecarManager {
    /// Dựng SidecarManager nhưng CHƯA khởi động worker.
    /// Dùng lúc app khởi động để không chặn thread chính chờ Python spawn xong.
    pub fn new_idle(cli_path: PathBuf) -> Self {
        Self {
            inner: Arc::new(Mutex::new(None)),
            cli_path,
            spawn_lock: Arc::new(Mutex::new(())),
            lifecycle: Arc::new(Mutex::new(LifecycleState {
                status: WorkerStatus::Uninitialized,
                consecutive_crashes: 0,
                last_spawn_time: None,
                last_error: None,
            })),
        }
    }

    /// Tạo SidecarManager và khởi động Python worker ngay
    #[cfg(test)]
    pub async fn new(cli_path: PathBuf) -> Self {
        let mgr = Self::new_idle(cli_path);
        mgr.start_worker().await;
        mgr
    }

    /// Lấy trạng thái vòng đời hiện tại của worker
    pub async fn status(&self) -> WorkerStatus {
        self.lifecycle.lock().await.status.clone()
    }

    /// Lấy số lần crash liên tiếp hiện tại
    pub async fn consecutive_crashes(&self) -> u32 {
        self.lifecycle.lock().await.consecutive_crashes
    }

    /// Lấy thông báo lỗi cuối cùng nếu có
    pub async fn last_error(&self) -> Option<String> {
        self.lifecycle.lock().await.last_error.clone()
    }

    /// Dừng worker: chuyển sang Stopped (không tự restart) VÀ kill tiến trình Python.
    #[allow(dead_code)]
    pub async fn stop(&self) {
        {
            let mut lc = self.lifecycle.lock().await;
            lc.status = WorkerStatus::Stopped;
        }
        let taken = self.inner.lock().await.take();
        if let Some(inner) = taken {
            // Supervisor đang chờ trên kill_rx sẽ gọi child.kill() giúp.
            let _ = inner.kill_tx.send(()).await;
        }
    }

    /// Reset bộ đếm lỗi và khởi động lại worker thủ công.
    /// Dọn sạch worker cũ trước để nút "Khởi động lại" luôn có tác dụng, kể cả khi
    /// `inner` còn sót handle của một tiến trình đã chết (start_worker thoát sớm
    /// khi `inner.is_some()`).
    pub async fn reset_and_start(&self) {
        let stale = self.inner.lock().await.take();
        if let Some(inner) = stale {
            let _ = inner.kill_tx.send(()).await;
        }
        {
            let mut lc = self.lifecycle.lock().await;
            lc.status = WorkerStatus::Uninitialized;
            lc.consecutive_crashes = 0;
            lc.last_error = None;
        }
        self.start_worker().await;
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

            // 1. Kiểm tra trạng thái vòng đời: không spawn lại nếu đã Failed hoặc Stopped
            {
                let lc = this.lifecycle.lock().await;
                if let WorkerStatus::Failed(err) = &lc.status {
                    warn!("[Sidecar] Worker đang ở trạng thái Failed, bỏ qua start_worker: {err}");
                    return;
                }
                if lc.status == WorkerStatus::Stopped {
                    info!("[Sidecar] Worker đang ở trạng thái Stopped, bỏ qua start_worker.");
                    return;
                }
            }

            // 2. Nếu đã có worker đang hoạt động thì không khởi động lại (tránh duplicate process)
            {
                let lock = this.inner.lock().await;
                if lock.is_some() {
                    return;
                }
            }

            // Đánh dấu trạng thái Starting
            {
                let mut lc = this.lifecycle.lock().await;
                lc.status = WorkerStatus::Starting;
                lc.last_spawn_time = Some(std::time::Instant::now());
            }

            let worker_id = WORKER_ID_COUNTER.fetch_add(1, Ordering::SeqCst);
            let cli = this.cli_path.clone();
            let inner_arc = this.inner.clone();
            let spawn_time = std::time::Instant::now();

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
                .stderr(Stdio::piped()); // Piped để vừa ghi log vừa chụp traceback lỗi khi crash

            let mut child = match cmd.spawn() {
                Ok(c) => c,
                Err(e) => {
                    let err_msg = format!("Không thể khởi động Python worker #{worker_id}: {e}");
                    error!("[Sidecar] {err_msg}");
                    let mut lc = this.lifecycle.lock().await;
                    lc.consecutive_crashes += 1;
                    lc.last_error = Some(err_msg.clone());
                    if lc.consecutive_crashes >= MAX_CONSECUTIVE_RESTARTS {
                        let fail_msg = format!(
                            "Không thể spawn Python worker {} lần liên tiếp. Chuyển sang trạng thái FAILED: {err_msg}",
                            lc.consecutive_crashes
                        );
                        error!("[Sidecar] {fail_msg}");
                        lc.status = WorkerStatus::Failed(fail_msg);
                    }
                    return;
                }
            };

            let child_id = child.id();
            info!("[Sidecar] Python worker #{worker_id} đã khởi động (PID: {:?})", child_id);

            let stdout = match child.stdout.take() {
                Some(s) => s,
                None => {
                    let err_msg = format!("Không lấy được stdout của Python worker #{worker_id}");
                    error!("[Sidecar] {err_msg}");
                    let mut lc = this.lifecycle.lock().await;
                    lc.consecutive_crashes += 1;
                    lc.last_error = Some(err_msg.clone());
                    if lc.consecutive_crashes >= MAX_CONSECUTIVE_RESTARTS {
                        lc.status = WorkerStatus::Failed(err_msg);
                    }
                    return;
                }
            };

            let mut stdin = match child.stdin.take() {
                Some(s) => s,
                None => {
                    let err_msg = format!("Không lấy được stdin của Python worker #{worker_id}");
                    error!("[Sidecar] {err_msg}");
                    let mut lc = this.lifecycle.lock().await;
                    lc.consecutive_crashes += 1;
                    lc.last_error = Some(err_msg.clone());
                    if lc.consecutive_crashes >= MAX_CONSECUTIVE_RESTARTS {
                        lc.status = WorkerStatus::Failed(err_msg);
                    }
                    return;
                }
            };

            // Capture stderr: log real-time và lưu rolling buffer 30 dòng gần nhất để debug khi crash
            let last_stderr = Arc::new(Mutex::new(Vec::<String>::new()));
            let last_stderr_writer = last_stderr.clone();
            if let Some(stderr) = child.stderr.take() {
                tokio::spawn(async move {
                    let mut lines = BufReader::new(stderr).lines();
                    while let Ok(Some(line)) = lines.next_line().await {
                        eprintln!("[Sidecar #{worker_id}:stderr] {line}");
                        warn!("[Sidecar #{worker_id}:stderr] {line}");
                        let mut buf = last_stderr_writer.lock().await;
                        if buf.len() >= 30 {
                            buf.remove(0);
                        }
                        buf.push(line);
                    }
                });
            }

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

            // Kênh báo hiệu stdout EOF tới supervisor task
            let (stdout_eof_tx, mut stdout_eof_rx) = tokio::sync::mpsc::channel::<()>(1);
            // Kênh ra lệnh kill từ stop() tới supervisor task
            let (kill_tx, mut kill_rx) = tokio::sync::mpsc::channel::<()>(1);
            let mgr_clone_for_read = this.clone();

            // Task 2: stdout reader — đọc JSON response, route về caller qua oneshot
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
                                            // Reset crash counter khi worker phục vụ request thành công
                                            {
                                                let mut lc = mgr_clone_for_read.lifecycle.lock().await;
                                                if lc.consecutive_crashes > 0 {
                                                    info!("[Sidecar] Worker #{worker_id} đã phản hồi thành công, reset crash counter về 0.");
                                                    lc.consecutive_crashes = 0;
                                                }
                                                lc.status = WorkerStatus::Running;
                                            }
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

                let _ = stdout_eof_tx.send(()).await;
            });

            // Đăng ký worker vào `inner` NGAY, TRƯỚC khi spawn supervisor.
            //
            // Trước đây việc này nằm ở cuối hàm, sau khi supervisor đã chạy. Nếu tiến
            // trình Python chết tức thì, supervisor có thể tới bước kiểm tra
            // `inner.worker_id == worker_id` khi `inner` vẫn còn None → nó thoát sớm,
            // KHÔNG tăng bộ đếm crash và KHÔNG lên lịch restart. Sau đó thân hàm mới
            // ghi đè `inner = Some(...)` trỏ tới một worker đã chết và đặt trạng thái
            // Running. Từ đó `inner.is_some()` khiến mọi lần start_worker (kể cả
            // reset_and_start) đều thoát sớm → bóc tách hỏng vĩnh viễn mà UI báo "đang chạy".
            {
                let mut lock = inner_arc.lock().await;
                *lock = Some(SidecarInner {
                    tx,
                    pending: pending.clone(),
                    worker_id,
                    kill_tx,
                });
            }

            // Task 3: Supervisor & Exit Handler — theo dõi tiến trình, reap zombie, xử lý crash và restart
            let mgr_clone = this.clone();
            let last_stderr_exit = last_stderr.clone();
            let pending_exit = pending.clone();

            tokio::spawn(async move {
                let wait_res = tokio::select! {
                    status = child.wait() => {
                        status
                    }
                    _ = stdout_eof_rx.recv() => {
                        match tokio::time::timeout(Duration::from_secs(2), child.wait()).await {
                            Ok(res) => res,
                            Err(_) => {
                                warn!("[Sidecar] Worker #{worker_id} (PID: {:?}) stdout EOF nhưng không thoát sau 2s, gửi kill...", child_id);
                                let _ = child.kill().await;
                                child.wait().await
                            }
                        }
                    }
                    _ = kill_rx.recv() => {
                        info!("[Sidecar] Nhận lệnh dừng — kill Python worker #{worker_id} (PID: {:?})", child_id);
                        let _ = child.kill().await;
                        child.wait().await
                    }
                };

                // Đợi 30ms để stderr reader kịp gom hết dòng cuối từ pipe
                tokio::time::sleep(Duration::from_millis(30)).await;

                let stderr_summary = {
                    let buf = last_stderr_exit.lock().await;
                    buf.join("\n").trim().to_string()
                };

                let (is_clean, exit_desc) = match &wait_res {
                    Ok(st) => {
                        let desc = format!("exit status: {st}");
                        (st.success(), desc)
                    }
                    Err(e) => (false, format!("wait error: {e}")),
                };

                // 1. Huỷ các pending requests đang chờ response từ worker này kèm thông tin nguyên nhân chi tiết
                {
                    let mut map = pending_exit.lock().await;
                    let count = map.len();
                    if count > 0 {
                        let err_detail = if !stderr_summary.is_empty() {
                            format!("Python worker đã dừng đột ngột ({exit_desc}). Stderr: {stderr_summary}")
                        } else {
                            format!("Python worker đã dừng đột ngột ({exit_desc})")
                        };
                        warn!("[Sidecar] Python worker #{worker_id} kết thúc: huỷ {count} pending request(s) — {err_detail}");
                        for (_id, reply_tx) in map.drain() {
                            let _ = reply_tx.send(Err(err_detail.clone()));
                        }
                    }
                }

                // 2. Chỉ xoá inner nếu worker hiện tại trong inner vẫn là worker này
                let should_consider_restart = {
                    let mut lock = mgr_clone.inner.lock().await;
                    if let Some(inner) = lock.as_ref() {
                        if inner.worker_id == worker_id {
                            *lock = None;
                            true
                        } else {
                            false
                        }
                    } else {
                        false
                    }
                };

                if !should_consider_restart {
                    return;
                }

                let uptime = spawn_time.elapsed();

                // 3. Kiểm tra vòng đời và quyết định restart
                let (should_restart, backoff_dur, attempt, max_restarts) = {
                    let mut lc = mgr_clone.lifecycle.lock().await;

                    if lc.status == WorkerStatus::Stopped {
                        info!("[Sidecar] Python worker #{worker_id} đã dừng (Stopped), không tự động restart.");
                        return;
                    }

                    // Nếu worker đã sống lâu hơn STABILITY_THRESHOLD trước khi thoát, reset crash counter
                    if uptime >= STABILITY_THRESHOLD {
                        info!(
                            "[Sidecar] Worker #{worker_id} đã chạy ổn định {:.1?}s trước khi dừng. Reset crash counter.",
                            uptime.as_secs_f64()
                        );
                        lc.consecutive_crashes = 0;
                    }

                    if is_clean && uptime >= STABILITY_THRESHOLD {
                        info!("[Sidecar] Python worker #{worker_id} (PID: {:?}) đã thoát sạch ({exit_desc}) sau khi chạy ổn định.", child_id);
                        (true, Duration::from_millis(500), 1, MAX_CONSECUTIVE_RESTARTS)
                    } else {
                        // Crash hoặc thoát non-zero, hoặc thoát ngay khi khởi động
                        lc.consecutive_crashes += 1;
                        let crash_reason = if !stderr_summary.is_empty() {
                            format!("Python worker #{worker_id} (PID: {:?}) crash với {exit_desc}. Stderr:\n{}", child_id, stderr_summary)
                        } else {
                            format!("Python worker #{worker_id} (PID: {:?}) crash với {exit_desc}", child_id)
                        };

                        error!("[Sidecar] {crash_reason}");
                        lc.last_error = Some(crash_reason.clone());

                        if lc.consecutive_crashes >= MAX_CONSECUTIVE_RESTARTS {
                            let fail_msg = format!(
                                "Python worker crash liên tục {} lần (vượt ngưỡng {}). Chuyển sang trạng thái FAILED, dừng tự động restart. Chi tiết: {}",
                                lc.consecutive_crashes, MAX_CONSECUTIVE_RESTARTS, crash_reason
                            );
                            error!("[Sidecar] {fail_msg}");
                            lc.status = WorkerStatus::Failed(fail_msg);
                            return;
                        }

                        let backoff_multiplier = 1u64 << lc.consecutive_crashes.saturating_sub(1).min(4);
                        let backoff = Duration::from_millis(500 * backoff_multiplier);
                        (true, backoff, lc.consecutive_crashes, MAX_CONSECUTIVE_RESTARTS)
                    }
                };

                if should_restart {
                    warn!(
                        "[Sidecar] Đang lên lịch khởi động lại Python worker sau {:?} (lần thử {}/{})...",
                        backoff_dur, attempt, max_restarts
                    );

                    tokio::time::sleep(backoff_dur).await;

                    // Kiểm tra lại trước khi restart: nếu status đã chuyển thành Stopped hoặc Failed thì huỷ
                    {
                        let lc = mgr_clone.lifecycle.lock().await;
                        if lc.status == WorkerStatus::Stopped || matches!(lc.status, WorkerStatus::Failed(_)) {
                            info!("[Sidecar] Huỷ lịch restart vì worker status hiện tại là {:?}", lc.status);
                            return;
                        }
                    }

                    info!("[Sidecar] Đang tự động khởi động lại Python worker...");
                    mgr_clone.start_worker().await;
                }
            });

            // Chỉ báo Running khi worker này VẪN là worker đang đăng ký. Nếu nó đã
            // chết và supervisor kịp dọn `inner` thì không được đè trạng thái lên.
            let still_current = {
                let lock = inner_arc.lock().await;
                lock.as_ref().map(|i| i.worker_id == worker_id).unwrap_or(false)
            };
            if still_current {
                let mut lc = this.lifecycle.lock().await;
                if !matches!(lc.status, WorkerStatus::Failed(_)) && lc.status != WorkerStatus::Stopped {
                    lc.status = WorkerStatus::Running;
                }
            }
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

    /// Mã request gửi xuống Python.
    ///
    /// Khi UI cung cấp task-id, dùng luôn nó (tiền tố `ui_`) để lệnh huỷ sau này
    /// tìm đúng request mà không cần bảng ánh xạ. Nếu id đó đang được dùng thì
    /// quay về bộ đếm nội bộ để hai request không đè lên nhau trong `pending`.
    async fn resolve_req_id(&self, client_task_id: Option<&str>) -> String {
        let candidate = match client_task_id.map(str::trim).filter(|t| !t.is_empty()) {
            Some(t) => format!("ui_{t}"),
            None => return next_req_id(),
        };
        let lock = self.inner.lock().await;
        if let Some(inner) = lock.as_ref() {
            if inner.pending.lock().await.contains_key(&candidate) {
                warn!("[Sidecar] task_id '{candidate}' đang được dùng — cấp id nội bộ thay thế");
                return next_req_id();
            }
        }
        candidate
    }

    /// Huỷ một request đang chạy: kill tiến trình con phía Python và trả lời
    /// caller ngay lập tức thay vì bắt UI chờ hết timeout.
    pub async fn cancel(&self, task_id: &str) -> Result<(), String> {
        let target = task_id.trim();
        if target.is_empty() {
            return Err("Thiếu mã tác vụ cần huỷ".to_string());
        }
        let target_id = format!("ui_{target}");

        let (tx, pending) = {
            let lock = self.inner.lock().await;
            match lock.as_ref() {
                Some(inner) => (inner.tx.clone(), inner.pending.clone()),
                None => return Err("Python worker chưa sẵn sàng".to_string()),
            }
        };

        // 1. Báo Python kill tiến trình con. Dùng oneshot bỏ đi vì không cần phản hồi.
        let cancel_id = next_req_id();
        let line = serde_json::to_string(&json!({
            "id": cancel_id,
            "action": "cancel",
            "target": target_id,
        }))
        .map_err(|e| format!("Serialize error: {e}"))?;
        let (throwaway_tx, _throwaway_rx) = oneshot::channel();
        tx.send((cancel_id, line, throwaway_tx))
            .await
            .map_err(|_| "Không gửi được lệnh huỷ tới Python worker".to_string())?;

        // 2. Giải phóng caller ngay. Python vẫn sẽ trả một response cho request này,
        //    lúc đó không còn ai trong `pending` nên nó bị bỏ qua — đúng như mong muốn.
        if let Some(reply_tx) = pending.lock().await.remove(&target_id) {
            let _ = reply_tx.send(Err("Đã huỷ theo yêu cầu.".to_string()));
        }

        info!("[Sidecar] Đã gửi lệnh huỷ cho '{target_id}'");
        Ok(())
    }

    /// Gửi request IPC tới Python worker và đợi response
    async fn send_request(&self, payload: Value, client_task_id: Option<&str>) -> Result<Value, String> {
        let req_id = self.resolve_req_id(client_task_id).await;
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
            // Nếu worker đã chuyển sang trạng thái Failed hoặc Stopped, dừng chờ ngay lập tức
            {
                let lc = self.lifecycle.lock().await;
                if let WorkerStatus::Failed(reason) = &lc.status {
                    return Err(format!(
                        "Python worker đã gặp sự cố và dừng hoạt động: {reason}\n\
                         Hãy mở 'Công cụ' (⚙) và bấm 'Khởi động lại' ở mục Python 3 sau khi đã khắc phục."
                    ));
                }
                if lc.status == WorkerStatus::Stopped {
                    return Err("Python worker đã dừng hoạt động (Stopped)".to_string());
                }
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }

        let tx = match tx_opt {
            Some(t) => t,
            None => {
                // Kiểm tra trạng thái lifecycle trước khi thử start_worker
                {
                    let lc = self.lifecycle.lock().await;
                    if let WorkerStatus::Failed(reason) = &lc.status {
                        return Err(format!(
                        "Python worker đã gặp sự cố và dừng hoạt động: {reason}\n\
                         Hãy mở 'Công cụ' (⚙) và bấm 'Khởi động lại' ở mục Python 3 sau khi đã khắc phục."
                    ));
                    }
                    if lc.status == WorkerStatus::Stopped {
                        return Err("Python worker đã dừng hoạt động (Stopped)".to_string());
                    }
                }

                // Thử kích hoạt start_worker nếu chưa có (chỉ khi chưa Failed)
                self.start_worker().await;
                let lock = self.inner.lock().await;
                match lock.as_ref() {
                    Some(inner) => inner.tx.clone(),
                    None => {
                        let lc = self.lifecycle.lock().await;
                        let detail = lc.last_error.as_deref().unwrap_or("Tiến trình Python worker không phản hồi");
                        return Err(format!("Python sidecar chưa khởi động hoặc không thể khởi động: {detail}"));
                    }
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
    pub async fn extract_media(
        &self,
        url: &str,
        browser: Option<&str>,
        task_id: Option<&str>,
    ) -> Result<Value, String> {
        let mut payload = json!({ "action": "extract", "url": url });
        if let Some(b) = browser {
            // Truyền xuống Python kể cả "none" — Python sẽ tự xử lý logic bỏ cookie
            if !b.trim().is_empty() {
                payload["browser"] = Value::String(b.to_string());
            }
        }
        self.send_request(payload, task_id).await
    }

    /// Giải mã URL rút gọn
    pub async fn resolve_url(&self, url: &str, expected_platform: Option<&str>) -> Result<Value, String> {
        let mut payload = json!({ "action": "resolve", "url": url });
        if let Some(p) = expected_platform {
            if !p.trim().is_empty() {
                payload["expected"] = Value::String(p.to_string());
            }
        }
        self.send_request(payload, None).await
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
        task_id: Option<&str>,
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

        self.send_request(payload, task_id).await
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
        let res = mgr.extract_media("https://example.com/test", None, None).await;
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

        // Đợi 700ms để Task 3 hoàn tất dọn dẹp và respawn worker mới (lần crash đầu backoff 500ms)
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
        let res = mgr.extract_media("https://example.com/echo", None, None).await;

        assert!(res.is_ok(), "Request phải thành công với mock worker bình thường");
        let data = res.unwrap();
        assert_eq!(data.get("title").and_then(|v| v.as_str()), Some("Mock Video"));

        let _ = std::fs::remove_file(&script_path);
    }

    #[tokio::test]
    async fn test_worker_immediate_crash_stops_at_max_retries() {
        let temp_dir = std::env::temp_dir();
        let script_path = temp_dir.join(format!(
            "test_sidecar_startup_crash_{}.py",
            REQUEST_COUNTER.fetch_add(1, Ordering::SeqCst)
        ));

        // Script giả lập: crash ngay lập tức khi khởi động (exit 1) kèm traceback trên stderr
        {
            let mut file = std::fs::File::create(&script_path).expect("Tạo file mock Python script");
            writeln!(
                file,
                "import sys\nsys.stderr.write('Fatal: Missing critical library fake_module\\n')\nsys.exit(1)\n"
            ).unwrap();
        }

        let mgr = SidecarManager::new(script_path.clone()).await;

        // Chờ thời gian cho 3 lần thử:
        // Lần 1: crash ngay -> backoff 500ms
        // Lần 2: crash ngay -> backoff 1000ms
        // Lần 3: crash ngay -> đạt MAX_CONSECUTIVE_RESTARTS (3) -> chuyển FAILED, dừng hoàn toàn.
        // Tổng thời gian chờ ~ 500 + 1000 + process overhead ≈ 1.6s. Cho ngủ 2.5s.
        tokio::time::sleep(Duration::from_millis(2500)).await;

        // 1. Kiểm tra trạng thái phải là Failed
        let status = mgr.status().await;
        match &status {
            WorkerStatus::Failed(reason) => {
                assert!(
                    reason.contains("crash liên tục") || reason.contains("vượt ngưỡng"),
                    "Lý do thất bại phải ghi rõ crash liên tục: {reason}"
                );
                assert!(
                    reason.contains("fake_module"),
                    "Lỗi phải chứa nội dung stderr từ tiến trình Python: {reason}"
                );
            }
            _ => panic!("Worker phải ở trạng thái Failed sau khi crash liên tục, nhưng đang là: {:?}", status),
        }

        // 2. Số lần crash phải chạm mốc tối đa
        assert_eq!(mgr.consecutive_crashes().await, 3);
        assert!(mgr.last_error().await.is_some(), "last_error phải ghi nhận lỗi crash cuối cùng");

        // 3. Không có worker nào đang hoạt động
        {
            let lock = mgr.inner.lock().await;
            assert!(lock.is_none(), "Inner phải là None khi worker ở trạng thái Failed");
        }

        // 4. Ngủ thêm 1 giây để kiểm tra worker KHÔNG tiếp tục respawn (chứng minh không bị loop vô hạn)
        tokio::time::sleep(Duration::from_millis(1000)).await;
        assert_eq!(
            mgr.consecutive_crashes().await,
            3,
            "Số lần crash không được tăng thêm (chứng minh không bị loop respawn vô hạn)"
        );

        // 5. Gửi request phải trả về lỗi ngay lập tức, không bị treo và không kích hoạt spawn lại
        let res = mgr.extract_media("https://example.com/test", None, None).await;
        assert!(res.is_err(), "Request phải trả về lỗi khi worker đã Failed");
        let err = res.unwrap_err();
        assert!(
            err.contains("đã gặp sự cố") || err.contains("FAILED"),
            "Error message phải báo rõ worker đã gặp sự cố: {err}"
        );

        let _ = std::fs::remove_file(&script_path);
    }

    #[tokio::test]
    async fn test_worker_recovery_after_reset() {
        let temp_dir = std::env::temp_dir();
        let script_path = temp_dir.join(format!(
            "test_sidecar_recovery_{}.py",
            REQUEST_COUNTER.fetch_add(1, Ordering::SeqCst)
        ));

        // Ban đầu script bị crash ngay
        {
            let mut file = std::fs::File::create(&script_path).expect("Tạo file mock Python script");
            writeln!(file, "import sys\nsys.exit(1)\n").unwrap();
        }

        let mgr = SidecarManager::new(script_path.clone()).await;
        tokio::time::sleep(Duration::from_millis(2500)).await;

        assert!(matches!(mgr.status().await, WorkerStatus::Failed(_)));

        // Sửa lại script hoạt động bình thường
        {
            let mut file = std::fs::File::create(&script_path).expect("Tạo lại file mock Python script");
            writeln!(
                file,
                "import sys, json\nfor line in sys.stdin:\n    req = json.loads(line.strip())\n    sys.stdout.write(json.dumps({{'id': req['id'], 'success': True, 'data': {{'title': 'Recovered Video'}}}})+'\\n')\n    sys.stdout.flush()\n"
            ).unwrap();
        }

        // Gọi reset_and_start để phục hồi
        mgr.reset_and_start().await;
        tokio::time::sleep(Duration::from_millis(300)).await;

        let res = mgr.extract_media("https://example.com/recovered", None, None).await;
        assert!(res.is_ok(), "Worker phải phục hồi thành công sau reset_and_start");
        let data = res.unwrap();
        assert_eq!(data.get("title").and_then(|v| v.as_str()), Some("Recovered Video"));

        let _ = std::fs::remove_file(&script_path);
    }

    #[tokio::test]
    async fn test_worker_stop_prevents_respawn() {
        let temp_dir = std::env::temp_dir();
        let script_path = temp_dir.join(format!(
            "test_sidecar_stop_{}.py",
            REQUEST_COUNTER.fetch_add(1, Ordering::SeqCst)
        ));

        {
            let mut file = std::fs::File::create(&script_path).expect("Tạo file mock Python script");
            writeln!(
                file,
                "import sys, json\nfor line in sys.stdin:\n    req = json.loads(line.strip())\n    sys.stdout.write(json.dumps({{'id': req['id'], 'success': True, 'data': {{'ok': True}}}})+'\\n')\n    sys.stdout.flush()\n"
            ).unwrap();
        }

        let mgr = SidecarManager::new(script_path.clone()).await;
        tokio::time::sleep(Duration::from_millis(200)).await;

        // Dừng worker
        mgr.stop().await;
        assert_eq!(mgr.status().await, WorkerStatus::Stopped);

        // Chờ và kiểm tra inner vẫn là None
        tokio::time::sleep(Duration::from_millis(700)).await;
        {
            let lock = mgr.inner.lock().await;
            assert!(lock.is_none(), "Worker đã Stopped không được tự động respawn");
        }

        let _ = std::fs::remove_file(&script_path);
    }

    /// `stop()` phải KILL tiến trình Python, không chỉ bỏ handle đi.
    /// Trước đây tiến trình con vẫn sống tới khi thoát hẳn ứng dụng.
    #[tokio::test]
    async fn test_stop_actually_kills_the_python_process() {
        let temp_dir = std::env::temp_dir();
        // uuid thay vì counter: counter reset về 1 mỗi lần chạy test nên file .pid
        // của lần chạy trước còn sót lại sẽ bị đọc nhầm.
        let uniq = uuid::Uuid::new_v4();
        let script_path = temp_dir.join(format!("test_sidecar_kill_{uniq}.py"));
        let pid_path = temp_dir.join(format!("test_sidecar_kill_{uniq}.pid"));
        let _ = std::fs::remove_file(&pid_path);

        // Worker ghi PID rồi bận xử lý dài (KHÔNG đọc stdin) — mô phỏng lúc đang
        // chạy một lệnh yt-dlp lâu. Đây chính là trường hợp mà việc chỉ đóng stdin
        // không đủ để kết thúc tiến trình.
        {
            let mut file = std::fs::File::create(&script_path).expect("Tạo file mock Python script");
            writeln!(
                file,
                "import os, time\nopen(r'{}', 'w').write(str(os.getpid()))\ntime.sleep(300)\n",
                pid_path.display()
            )
            .unwrap();
        }

        let mgr = SidecarManager::new(script_path.clone()).await;
        // Chờ worker ghi xong PID
        let mut pid: Option<i32> = None;
        for _ in 0..40 {
            if let Ok(txt) = std::fs::read_to_string(&pid_path) {
                if let Ok(v) = txt.trim().parse::<i32>() {
                    pid = Some(v);
                    break;
                }
            }
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
        let pid = pid.expect("Worker phải ghi được PID ra file");
        assert!(
            std::path::Path::new(&format!("/proc/{pid}")).exists(),
            "Tiến trình Python #{pid} phải đang chạy trước khi stop()"
        );

        mgr.stop().await;

        // Chờ supervisor kill và reap tiến trình
        let mut gone = false;
        for _ in 0..40 {
            if !std::path::Path::new(&format!("/proc/{pid}")).exists() {
                gone = true;
                break;
            }
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
        assert!(gone, "stop() phải kill tiến trình Python (PID {pid} vẫn còn sống)");
        assert_eq!(mgr.status().await, WorkerStatus::Stopped);

        let _ = std::fs::remove_file(&script_path);
        let _ = std::fs::remove_file(&pid_path);
    }

    /// #6 — Huỷ phải giải phóng caller NGAY và kill tiến trình con phía Python,
    /// thay vì để UI ngồi chờ hết timeout còn yt-dlp vẫn chạy.
    #[tokio::test]
    async fn test_cancel_releases_caller_and_kills_child_process() {
        let temp_dir = std::env::temp_dir();
        let uniq = uuid::Uuid::new_v4();
        let script_path = temp_dir.join(format!("test_sidecar_cancel_{uniq}.py"));
        let child_pid_path = temp_dir.join(format!("test_sidecar_cancel_{uniq}.childpid"));

        // Worker giả lập extractor thật: nhận request → sinh tiến trình con chạy lâu
        // (giống yt-dlp) và đăng ký nó với module cancellation.
        {
            let mut file = std::fs::File::create(&script_path).expect("Tạo mock script");
            let body = format!(
                r#"import sys, os, json, threading, subprocess
sys.path.insert(0, r'{root}')
from core.cancellation import begin_request, end_request, cancel_request, register_process, RequestCancelled

def serve(req):
    rid = req['id']
    begin_request(rid)
    try:
        p = subprocess.Popen(['sleep', '120'], start_new_session=True)
        register_process(p)
        open(r'{pidfile}', 'w').write(str(p.pid))
        p.communicate()
        out = {{'id': rid, 'success': True, 'data': {{}}}}
    except RequestCancelled:
        out = {{'id': rid, 'success': False, 'cancelled': True, 'error': 'huy'}}
    finally:
        end_request(rid)
    print(json.dumps(out), flush=True)

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    req = json.loads(line)
    if (req.get('action') or '') == 'cancel':
        cancel_request(req.get('target') or '')
        print(json.dumps({{'id': req['id'], 'success': True, 'data': {{}}}}), flush=True)
        continue
    threading.Thread(target=serve, args=(req,), daemon=True).start()
"#,
                root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                    .join("../../..")
                    .canonicalize()
                    .unwrap()
                    .display(),
                pidfile = child_pid_path.display(),
            );
            use std::io::Write as _;
            file.write_all(body.as_bytes()).unwrap();
        }

        let mgr = SidecarManager::new(script_path.clone()).await;
        let mgr_for_req = mgr.clone();

        // Gửi request kèm task_id của UI
        let req_handle = tokio::spawn(async move {
            let start = std::time::Instant::now();
            let res = mgr_for_req
                .extract_media("https://example.com/slow", None, Some("task-xyz"))
                .await;
            (res, start.elapsed())
        });

        // Chờ tiến trình con xuất hiện
        let mut child_pid: Option<i32> = None;
        for _ in 0..60 {
            if let Ok(t) = std::fs::read_to_string(&child_pid_path) {
                if let Ok(v) = t.trim().parse::<i32>() {
                    child_pid = Some(v);
                    break;
                }
            }
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
        let child_pid = child_pid.expect("Mock worker phải sinh được tiến trình con");
        assert!(std::path::Path::new(&format!("/proc/{child_pid}")).exists());

        mgr.cancel("task-xyz").await.expect("Gửi lệnh huỷ phải thành công");

        let (res, elapsed) = req_handle.await.unwrap();
        assert!(res.is_err(), "Request bị huỷ phải trả lỗi, không treo");
        assert!(
            res.unwrap_err().contains("huỷ"),
            "Thông báo phải nói rõ là đã huỷ"
        );
        assert!(
            elapsed < Duration::from_secs(10),
            "Caller phải được giải phóng ngay, không chờ hết timeout (mất {elapsed:?})"
        );

        // Tiến trình con phải bị kill, không sống tiếp 120s
        let mut gone = false;
        for _ in 0..60 {
            if !std::path::Path::new(&format!("/proc/{child_pid}")).exists() {
                gone = true;
                break;
            }
            tokio::time::sleep(Duration::from_millis(50)).await;
        }
        assert!(gone, "Tiến trình con {child_pid} phải bị kill khi huỷ");

        mgr.stop().await;
        let _ = std::fs::remove_file(&script_path);
        let _ = std::fs::remove_file(&child_pid_path);
    }

    #[tokio::test]
    async fn test_real_extractor_cli_startup_and_ipc() {
        // Tìm CLI thực tế của project
        let cli_res = SidecarManager::find_cli_path(None);
        if let Ok(cli_path) = cli_res {
            let mgr = SidecarManager::new(cli_path).await;
            tokio::time::sleep(Duration::from_millis(500)).await;

            // Kiểm tra trạng thái worker ban đầu
            assert_eq!(mgr.status().await, WorkerStatus::Running);
            assert_eq!(mgr.consecutive_crashes().await, 0);

            // Gửi lệnh resolve một URL để test IPC hai chiều thực tế
            let res = mgr.resolve_url("https://example.com", None).await;
            assert!(res.is_ok(), "Real extractor_cli phải xử lý resolve_url thành công: {:?}", res);
            let val = res.unwrap();
            assert_eq!(val.get("platform").and_then(|v| v.as_str()), Some("generic"));

            // Đảm bảo không spawn duplicate
            assert_eq!(mgr.consecutive_crashes().await, 0);
            assert_eq!(mgr.status().await, WorkerStatus::Running);

            mgr.stop().await;
        }
    }
}
