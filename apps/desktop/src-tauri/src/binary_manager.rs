/**
 * Binary Manager — Kiểm tra và quản lý các công cụ ngoài:
 * yt-dlp, gallery-dl, ffmpeg, python3.
 *
 * Thiết kế: chỉ check + báo cáo + update manual theo yêu cầu user.
 * Không tự động cập nhật khi start.
 */
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::OnceLock;
use std::time::{Duration, SystemTime};
use log::{info, warn};
use regex::Regex;
use serde::{Deserialize, Serialize};
use tokio::process::Command;

use crate::settings::SettingsManager;

/// `--version` của một binary hỏng/treo không được làm treo cả màn hình Công cụ.
const VERSION_TIMEOUT: Duration = Duration::from_secs(10);

/// Một lượt cập nhật (yt-dlp -U, pip, pipx). Không có mốc này, mạng treo khiến nút
/// "Cập nhật" quay mãi và khoá luôn mọi nút khác trong modal Công cụ.
const UPDATE_TIMEOUT: Duration = Duration::from_secs(300);

/// Chỉ một lượt cập nhật chạy tại một thời điểm: hai lượt pip/pipx song song trên
/// cùng một môi trường có thể làm hỏng môi trường đó.
fn update_lock() -> &'static tokio::sync::Mutex<()> {
    static LOCK: OnceLock<tokio::sync::Mutex<()>> = OnceLock::new();
    LOCK.get_or_init(|| tokio::sync::Mutex::new(()))
}

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

/// Môi trường đang chứa một binary — quyết định cách nâng cấp nó cho ĐÚNG chỗ.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum InstallKind {
    /// venv do pipx quản lý → `pipx upgrade`
    Pipx,
    /// venv thường (có `pyvenv.cfg`), hoặc thư mục `Scripts` của một bản cài Python
    /// trên Windows → pip của chính trình thông dịch đó (không `--user`)
    Venv(PathBuf),
    /// Script do `pip install --user` sinh ra → pip `--user` của interpreter đã cài nó
    PipUser(PathBuf),
    /// Thư mục hệ thống (apt/dnf, /usr/local/bin...) hoặc bản đóng gói sẵn:
    /// app không được cài đè lên.
    Unmanaged,
}

/// Kết quả một lệnh đã chạy xong
struct CmdOutput {
    success: bool,
    code: Option<i32>,
    stdout: String,
    stderr: String,
}

impl CmdOutput {
    fn combined(&self) -> String {
        format!("{}\n{}", self.stdout, self.stderr)
    }
}

/// Lệnh chạy ngầm: không stdin, gom stdout/stderr, bị kill khi future bị huỷ.
fn background_command(program: &Path) -> Command {
    let mut cmd = Command::new(program);
    // Ứng dụng GUI trên Windows: thiếu cờ này thì mỗi lần chạy `--version` hay pip
    // lại bật lên một cửa sổ console đen.
    #[cfg(windows)]
    {
        #[allow(unused_imports)]
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x08000000); // CREATE_NO_WINDOW
    }
    cmd.stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    cmd
}

/// Chạy lệnh tối đa `timeout`; quá hạn thì tiến trình bị kill (kill_on_drop).
async fn run_with_timeout(mut cmd: Command, timeout: Duration) -> Result<CmdOutput, String> {
    match tokio::time::timeout(timeout, cmd.output()).await {
        Err(_) => Err(format!(
            "quá {} giây chưa xong (mạng chậm hoặc bị chặn) nên đã dừng",
            timeout.as_secs()
        )),
        Ok(Err(e)) => Err(format!("không chạy được lệnh: {e}")),
        Ok(Ok(out)) => Ok(CmdOutput {
            success: out.status.success(),
            code: out.status.code(),
            stdout: String::from_utf8_lossy(&out.stdout).to_string(),
            stderr: String::from_utf8_lossy(&out.stderr).to_string(),
        }),
    }
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

    /// Trình thông dịch Python 3 CHẠY ĐƯỢC THẬT để khởi động engine bóc tách.
    ///
    /// Trên Windows, `python3.exe` trong PATH thường là "App execution alias" của
    /// Microsoft Store: khi chưa cài Python từ Store nó chỉ in "Python was not found"
    /// rồi thoát, kể cả khi máy đã cài Python từ python.org (bản này không tạo
    /// python3.exe). Trước đây app luôn chọn nó trước, nên engine chết 3 lần liên tiếp
    /// và chuyển FAILED. Trên Windows nay thử `py` (launcher của python.org) và
    /// `python` trước, mỗi ứng viên phải trả lời `--version` là Python 3.
    /// Linux/macOS giữ cách dò cũ (không tốn thêm một tiến trình).
    pub async fn find_working_python() -> Option<PathBuf> {
        if !cfg!(windows) {
            return Self::find_python();
        }
        let mut candidates: Vec<PathBuf> = ["py", "python", "python3"]
            .iter()
            .filter_map(|name| which::which(name).ok())
            .collect();
        if let Some(p) = Self::find_python() {
            candidates.push(p);
        }
        if let Some(local) = dirs::data_local_dir() {
            if let Ok(entries) = std::fs::read_dir(local.join("Programs").join("Python")) {
                candidates.extend(entries.flatten().map(|e| e.path().join("python.exe")).filter(|p| p.is_file()));
            }
        }
        let mut seen = std::collections::HashSet::new();
        candidates.retain(|p| seen.insert(p.clone()));

        for candidate in &candidates {
            let mut cmd = background_command(candidate);
            cmd.arg("--version");
            if let Ok(out) = run_with_timeout(cmd, VERSION_TIMEOUT).await {
                let text = format!("{}{}", out.stdout, out.stderr);
                if out.success && text.trim_start().starts_with("Python 3") {
                    return Some(candidate.clone());
                }
            }
            warn!("[Python] Bỏ qua {:?}: không phải trình thông dịch Python 3 chạy được", candidate);
        }
        // Không ứng viên nào chạy được: trả về ứng viên đầu để lỗi khởi động nêu rõ đường dẫn
        candidates.into_iter().next()
    }

    /// Bản yt-dlp này có tuỳ chọn `--js-runtimes` không.
    ///
    /// Tuỳ chọn chỉ có từ các bản yt-dlp cuối 2025; bản cũ (vd. gói apt của
    /// Ubuntu/Debian) gặp nó là thoát ngay với "no such option: --js-runtimes".
    /// Trước đây app luôn thêm cờ này khi máy có Node.js, nên với yt-dlp cũ thì MỌI
    /// lượt tải đều thất bại. Kết quả được nhớ theo (đường dẫn, mtime): cập nhật
    /// yt-dlp xong là được kiểm tra lại.
    pub async fn ytdlp_supports_js_runtimes(bin: &Path) -> bool {
        type Cache = std::sync::Mutex<HashMap<(PathBuf, Option<SystemTime>), bool>>;
        static CACHE: OnceLock<Cache> = OnceLock::new();
        let cache = CACHE.get_or_init(|| std::sync::Mutex::new(HashMap::new()));

        let key = (bin.to_path_buf(), std::fs::metadata(bin).and_then(|m| m.modified()).ok());
        if let Some(known) = cache.lock().ok().and_then(|c| c.get(&key).copied()) {
            return known;
        }
        let mut cmd = background_command(bin);
        cmd.arg("--help");
        match run_with_timeout(cmd, Duration::from_secs(20)).await {
            Ok(out) => {
                let supported = out.stdout.contains("--js-runtimes");
                if let Ok(mut c) = cache.lock() {
                    c.insert(key, supported);
                }
                supported
            }
            // Không đọc được trợ giúp (máy quá chậm...): lần này bỏ cờ cho chắc, lần
            // sau thử lại. Thiếu cờ chỉ làm YouTube kém đi; thêm nhầm thì hỏng hẳn.
            Err(_) => false,
        }
    }

    /// Kiểm tra xem binary có sẵn trên máy không
    pub fn has_binary(name: &str) -> bool {
        Self::find_binary(name).is_some()
    }

    /// Rút số phiên bản từ output của `--version`.
    ///
    /// Trước đây UI hiển thị nguyên dòng đầu kèm tiền tố "v", ra những nhãn như
    /// "vffmpeg version 8.0.1 Copyright (c) 2000-2025...", "vPython 3.14.4" hay
    /// "vv22.22.1".
    pub(crate) fn parse_version(raw: &str) -> Option<String> {
        static VERSION_RE: OnceLock<Regex> = OnceLock::new();
        let re = VERSION_RE.get_or_init(|| {
            Regex::new(r"\d+(?:\.\d+)+(?:[-+~][0-9A-Za-z][0-9A-Za-z._+~-]*)?").expect("regex phiên bản hợp lệ")
        });
        let line = raw.lines().map(str::trim).find(|l| !l.is_empty())?;
        let found = match re.find(line) {
            Some(m) => m.as_str().to_string(),
            None => {
                // Bản build git của ffmpeg: "ffmpeg version N-113478-g4d9a8d5 Copyright ..."
                let mut tokens = line.split_whitespace();
                tokens
                    .by_ref()
                    .find(|t| t.eq_ignore_ascii_case("version"))
                    .and_then(|_| tokens.next())
                    .unwrap_or(line)
                    .to_string()
            }
        };
        Some(found.chars().take(32).collect())
    }

    /// Lấy version của binary bằng cách chạy `{binary} --version`
    async fn get_version(path: &Path) -> Option<String> {
        let mut cmd = background_command(path);
        cmd.arg("--version");
        let out = run_with_timeout(cmd, VERSION_TIMEOUT).await.ok()?;
        // Một số tool in version ra stderr
        let text = if out.stdout.trim().is_empty() { &out.stderr } else { &out.stdout };
        Self::parse_version(text)
    }

    async fn status_of(name: &str) -> BinaryStatus {
        // Python: báo đúng trình thông dịch mà engine bóc tách thật sự dùng
        let path = if name == "python3" {
            Self::find_working_python().await
        } else {
            Self::find_binary(name)
        };
        let version = match path.as_deref() {
            Some(p) => Self::get_version(p).await,
            None => None,
        };
        BinaryStatus {
            name: name.to_string(),
            is_installed: path.is_some(),
            path: path.map(|p| p.to_string_lossy().to_string()),
            version,
        }
    }

    /// Kiểm tra trạng thái của tất cả binaries
    pub async fn check_all() -> AllBinaryStatus {
        // Chạy song song: tuần tự thì 7 lần `--version` (yt-dlp, gallery-dl khởi động
        // Python khá chậm) khiến modal Công cụ phải chờ vài giây mới có kết quả.
        let (ytdlp, gallery_dl, ffmpeg, ffprobe, aria2c, python3, node) = tokio::join!(
            Self::status_of("yt-dlp"),
            Self::status_of("gallery-dl"),
            Self::status_of("ffmpeg"),
            Self::status_of("ffprobe"),
            Self::status_of("aria2c"),
            Self::status_of("python3"),
            Self::status_of("node"),
        );
        AllBinaryStatus { ytdlp, gallery_dl, ffmpeg, ffprobe, aria2c, python3, node }
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
            Self::tail_lines(stderr)
        } else if !stdout.trim().is_empty() {
            Self::tail_lines(stdout)
        } else {
            format!("Mã thoát: {}", code.unwrap_or(-1))
        };
        Err(err)
    }

    /// Vài dòng cuối có nghĩa của output pip / yt-dlp: đủ biết lỗi gì mà không đổ
    /// cả trang log vào toast.
    pub(crate) fn tail_lines(text: &str) -> String {
        let lines: Vec<&str> = text.lines().map(str::trim).filter(|l| !l.is_empty()).collect();
        let errors: Vec<&str> = lines
            .iter()
            .copied()
            .filter(|l| l.starts_with("ERROR") || l.starts_with("error:"))
            .collect();
        let source = if errors.is_empty() { lines } else { errors };
        let joined = source[source.len().saturating_sub(3)..].join(" | ");
        if joined.chars().count() > 500 {
            format!("{}…", joined.chars().take(500).collect::<String>())
        } else {
            joined
        }
    }

    /// Binary có nằm trong một venv của pipx không (~/.local/pipx/venvs/<tool>/bin/…)
    pub(crate) fn is_pipx_managed(path: &std::path::Path) -> bool {
        let p = path.to_string_lossy().replace('\\', "/");
        p.contains("/pipx/venvs/") || p.contains("/pipx/shared/")
    }

    /// Binary có nằm trong thư mục của người dùng không (cài kiểu `pip --user`)
    pub(crate) fn is_user_local(path: &std::path::Path) -> bool {
        match dirs::home_dir() {
            Some(home) => {
                path.starts_with(&home)
                    || std::fs::canonicalize(&home).map(|h| path.starts_with(h)).unwrap_or(false)
            }
            None => false,
        }
    }

    /// pipx còn giữ venv cho gói này không (pipx trên Windows CHÉP .exe ra
    /// ~/.local/bin thay vì tạo symlink, nên không suy ra được từ đường dẫn).
    fn pipx_venv_exists(pkg: &str) -> bool {
        let mut roots: Vec<PathBuf> = Vec::new();
        if let Ok(custom) = std::env::var("PIPX_HOME") {
            roots.push(PathBuf::from(custom));
        }
        if let Some(home) = dirs::home_dir() {
            roots.push(home.join(".local").join("share").join("pipx"));
            roots.push(home.join(".local").join("pipx"));
            roots.push(home.join("pipx"));
        }
        if let Some(local) = dirs::data_local_dir() {
            roots.push(local.join("pipx").join("pipx"));
        }
        roots.iter().any(|root| root.join("venvs").join(pkg).is_dir())
    }

    /// `<venv>/bin/<tool>` (hoặc `<venv>\Scripts\<tool>.exe`) → python của venv đó.
    fn venv_python_of(real_bin: &Path) -> Option<PathBuf> {
        let venv = real_bin.parent()?.parent()?;
        if !venv.join("pyvenv.cfg").is_file() {
            return None;
        }
        [
            venv.join("bin").join("python"),
            venv.join("bin").join("python3"),
            venv.join("Scripts").join("python.exe"),
        ]
        .into_iter()
        .find(|p| p.exists())
    }

    /// `<python>\Scripts\<tool>.exe` trên Windows → `<python>\python.exe` đã cài nó.
    /// (Bản cài `pip --user` nằm ở %APPDATA%\Python\...\Scripts, không có python.exe kề bên.)
    fn owning_interpreter_of(real_bin: &Path) -> Option<PathBuf> {
        let scripts = real_bin.parent()?;
        if !scripts.file_name()?.to_string_lossy().eq_ignore_ascii_case("scripts") {
            return None;
        }
        let python = scripts.parent()?.join("python.exe");
        python.is_file().then_some(python)
    }

    /// Interpreter trong dòng shebang của một script do pip sinh ra.
    ///
    /// Trả None với bản đóng gói sẵn (ELF, zipapp có shebang...): loại đó chỉ tự
    /// cập nhật được (`yt-dlp -U`), cài pip đè lên sẽ thay mất nó.
    pub(crate) fn script_interpreter(path: &Path) -> Option<PathBuf> {
        use std::io::Read;
        let mut head = [0u8; 512];
        let n = std::fs::File::open(path).ok()?.read(&mut head).ok()?;
        let head = &head[..n];
        let line_end = head.iter().position(|&b| b == b'\n')?;
        let first_line = std::str::from_utf8(&head[..line_end]).ok()?.trim();
        let shebang = first_line.strip_prefix("#!")?.trim();
        // zipapp (bản phát hành chính thức của yt-dlp): ngay sau shebang là dữ liệu ZIP
        if head[line_end + 1..].starts_with(b"PK\x03\x04") {
            return None;
        }
        let mut parts = shebang.split_whitespace();
        let program = parts.next()?;
        let interpreter = if Path::new(program).file_name().map(|n| n == "env").unwrap_or(false) {
            // "#!/usr/bin/env python3" (có thể kèm cờ như "-S")
            let name = parts.find(|p| !p.starts_with('-'))?;
            which::which(name).ok()?
        } else {
            PathBuf::from(program)
        };
        let file_name = interpreter.file_name()?.to_string_lossy().to_lowercase();
        file_name.starts_with("python").then_some(interpreter)
    }

    /// Xác định môi trường đang chứa `bin` để nâng cấp đúng chỗ.
    pub(crate) fn classify_install(bin: &Path, pkg: &str) -> InstallKind {
        // ~/.local/bin/<tool> của pipx là SYMLINK trỏ vào venv. Phải xét đường dẫn
        // THẬT: trước đây chỉ xét đường dẫn symlink nên không nhận ra pipx, rồi
        // `pip install --user --break-system-packages` cài một bản thứ hai vào Python
        // hệ thống, trong khi bản pipx đang được dùng vẫn cũ nguyên.
        let real = std::fs::canonicalize(bin).unwrap_or_else(|_| bin.to_path_buf());
        if Self::is_pipx_managed(&real) || Self::is_pipx_managed(bin) {
            return InstallKind::Pipx;
        }
        if let Some(python) = Self::venv_python_of(&real) {
            return InstallKind::Venv(python);
        }
        if !Self::is_user_local(&real) {
            return InstallKind::Unmanaged;
        }
        if cfg!(windows) {
            if let Some(python) = Self::owning_interpreter_of(&real) {
                return InstallKind::Venv(python);
            }
            if Self::pipx_venv_exists(pkg) {
                return InstallKind::Pipx;
            }
            return Self::find_python().map(InstallKind::PipUser).unwrap_or(InstallKind::Unmanaged);
        }
        Self::script_interpreter(&real)
            .map(InstallKind::PipUser)
            .unwrap_or(InstallKind::Unmanaged)
    }

    fn install_hint(pkg: &str) -> String {
        if cfg!(windows) && pkg == "yt-dlp" {
            "Cài bằng: winget install yt-dlp.yt-dlp".to_string()
        } else {
            format!("Cài bằng: pipx install {pkg}")
        }
    }

    /// Hướng dẫn khi app không được phép tự cài đè (binary của hệ thống / đóng gói sẵn).
    fn unmanaged_hint(pkg: &str, bin: &Path, detail: &str) -> String {
        let lower = detail.to_lowercase();
        if lower.contains("unable to write") || lower.contains("permission denied") || lower.contains("administrator") {
            return if cfg!(windows) {
                format!("Hãy chạy \"{} -U\" bằng quyền Administrator.", bin.display())
            } else {
                format!("Hãy chạy: sudo {} -U", bin.display())
            };
        }
        if lower.contains("unable to obtain")
            || lower.contains("http error")
            || lower.contains("timed out")
            || lower.contains("giây chưa xong")
            || lower.contains("network")
        {
            return "Kiểm tra kết nối mạng / proxy rồi thử lại.".to_string();
        }
        if cfg!(windows) {
            format!(
                "{pkg} tại {} không do pip/pipx của bạn quản lý nên app không tự cài đè. \
                 Hãy cập nhật bằng công cụ đã dùng để cài nó.",
                bin.display()
            )
        } else {
            format!(
                "{pkg} tại {} do hệ điều hành quản lý (apt/dnf...) hoặc là bản đóng gói sẵn, nên app \
                 không tự cài đè để tránh làm hỏng hệ thống. Hãy chạy: sudo apt install --only-upgrade {pkg} \
                 — hoặc cài bản riêng cho bạn: pipx install {pkg}",
                bin.display()
            )
        }
    }

    async fn run_pip(python: &Path, args: &[&str]) -> Result<CmdOutput, String> {
        let mut cmd = background_command(python);
        cmd.args(["-m", "pip"])
            .args(args)
            .args(["--disable-pip-version-check", "--no-input"]);
        run_with_timeout(cmd, UPDATE_TIMEOUT).await.map_err(|e| format!("pip {e}"))
    }

    /// Diễn giải kết quả `pip install -U <pkg>`.
    pub(crate) fn pip_outcome(pkg: &str, success: bool, output: &str, how: &str) -> Result<String, String> {
        if success {
            // "Requirement already satisfied" cũng xuất hiện cho các gói PHỤ THUỘC khi
            // pip vừa nâng cấp xong — trước đây vì thế mà báo nhầm "đã là bản mới nhất".
            // Chỉ dòng "Successfully installed <pkg>-<phiên bản>" mới chắc chắn.
            let wanted = format!("{}-", pkg.to_lowercase().replace('_', "-"));
            let upgraded = output.lines().any(|line| {
                let l = line.trim().to_lowercase().replace('_', "-");
                l.starts_with("successfully installed") && l.split_whitespace().any(|w| w.starts_with(&wanted))
            });
            return Ok(if upgraded {
                format!("Đã cập nhật {pkg} qua pip{how}.")
            } else {
                format!("{pkg} đã là phiên bản mới nhất.")
            });
        }
        if output.contains("No module named pip") {
            return Err(format!(
                "Python đang chạy {pkg} chưa có pip. Hãy cài pip (vd. sudo apt install python3-pip) rồi thử lại."
            ));
        }
        Err(format!("Cập nhật {pkg} qua pip thất bại: {}", Self::tail_lines(output)))
    }

    async fn pipx_upgrade(pkg: &str) -> Result<String, String> {
        let Some(pipx) = Self::find_binary("pipx") else {
            return Err(format!(
                "{pkg} được cài bằng pipx nhưng không tìm thấy lệnh pipx. Chạy thủ công: pipx upgrade {pkg}"
            ));
        };
        let mut cmd = background_command(&pipx);
        cmd.args(["upgrade", pkg]);
        let out = run_with_timeout(cmd, UPDATE_TIMEOUT)
            .await
            .map_err(|e| format!("pipx upgrade {pkg} {e}"))?;
        let combined = out.combined();
        if combined.contains("already at latest version") {
            return Ok(format!("{pkg} đã là phiên bản mới nhất."));
        }
        if out.success {
            return Ok(format!("Đã cập nhật {pkg} qua pipx."));
        }
        Err(format!("pipx upgrade {pkg} thất bại: {}", Self::tail_lines(&combined)))
    }

    /// Nâng cấp gói trong ĐÚNG môi trường đang chứa binary.
    ///
    /// Trước đây luôn chạy `pip install -U <pkg>` của python3 hệ thống và leo thang
    /// lên `--break-system-packages`: binary của pipx/venv hay của apt không được
    /// nâng cấp, thay vào đó một bản thứ hai bị cài lạc vào site-packages của người dùng.
    async fn upgrade_in_place(pkg: &str, kind: &InstallKind) -> Result<String, String> {
        match kind {
            InstallKind::Pipx => Self::pipx_upgrade(pkg).await,
            InstallKind::Venv(python) => {
                let out = Self::run_pip(python, &["install", "-U", pkg]).await?;
                Self::pip_outcome(pkg, out.success, &out.combined(), "")
            }
            InstallKind::PipUser(python) => {
                let out = Self::run_pip(python, &["install", "-U", "--user", pkg]).await?;
                if !out.success && out.combined().contains("externally-managed-environment") {
                    // PEP 668: distro khoá Python hệ thống. Binary này vốn do `pip --user`
                    // cài, nên chỉ nâng cấp đúng trong site của người dùng — luôn kèm
                    // --user, không bao giờ đụng gói của hệ thống.
                    let retry = Self::run_pip(
                        python,
                        &["install", "-U", "--user", "--break-system-packages", pkg],
                    )
                    .await?;
                    return Self::pip_outcome(pkg, retry.success, &retry.combined(), " (--user)");
                }
                Self::pip_outcome(pkg, out.success, &out.combined(), " (--user)")
            }
            InstallKind::Unmanaged => Err(format!("{pkg} không do pip/pipx của người dùng quản lý")),
        }
    }

    /// Gắn phiên bản thực tế sau khi cập nhật vào thông báo.
    async fn with_version(message: String, bin: &Path) -> String {
        match Self::get_version(bin).await {
            Some(v) => format!("{message} Phiên bản hiện tại: {v}"),
            None => message,
        }
    }

    /// Cập nhật yt-dlp lên version mới nhất
    pub async fn update_ytdlp() -> Result<String, String> {
        let _guard = update_lock().lock().await;
        let ytdlp_path = Self::find_binary("yt-dlp")
            .ok_or_else(|| format!("yt-dlp chưa được cài đặt. {}", Self::install_hint("yt-dlp")))?;
        let kind = Self::classify_install(&ytdlp_path, "yt-dlp");
        info!("Đang cập nhật yt-dlp tại {:?} ({:?})", ytdlp_path, kind);

        let mut cmd = background_command(&ytdlp_path);
        cmd.arg("-U");
        let self_update_err = match run_with_timeout(cmd, UPDATE_TIMEOUT).await {
            Ok(out) => match Self::evaluate_ytdlp_update(&out.stdout, &out.stderr, out.success, out.code) {
                Ok(msg) => return Ok(Self::with_version(msg, &ytdlp_path).await),
                Err(e) => e,
            },
            Err(e) => format!("yt-dlp -U {e}"),
        };

        // `yt-dlp -U` chỉ tự cập nhật được bản đóng gói sẵn; bản cài qua pip/pipx/venv
        // báo "Use that to update". Khi đó nâng cấp đúng môi trường đang chứa nó —
        // còn bản do hệ điều hành quản lý thì chỉ hướng dẫn, không cài đè.
        if kind == InstallKind::Unmanaged {
            return Err(format!(
                "Không cập nhật được yt-dlp: {self_update_err}. {}",
                Self::unmanaged_hint("yt-dlp", &ytdlp_path, &self_update_err)
            ));
        }
        match Self::upgrade_in_place("yt-dlp", &kind).await {
            Ok(msg) => Ok(Self::with_version(msg, &ytdlp_path).await),
            Err(pip_err) => Err(format!(
                "Không cập nhật được yt-dlp: {pip_err} (yt-dlp -U: {self_update_err})"
            )),
        }
    }

    /// Cập nhật gallery-dl lên version mới nhất, vào đúng môi trường đang dùng
    pub async fn update_gallery_dl() -> Result<String, String> {
        let _guard = update_lock().lock().await;
        let gallery_path = Self::find_binary("gallery-dl")
            .ok_or_else(|| format!("gallery-dl chưa được cài đặt. {}", Self::install_hint("gallery-dl")))?;
        let kind = Self::classify_install(&gallery_path, "gallery-dl");
        info!("Đang cập nhật gallery-dl tại {:?} ({:?})", gallery_path, kind);

        // gallery-dl bản pip chỉ có `--update-check` (kiểm tra), không tự cập nhật được.
        if kind == InstallKind::Unmanaged {
            return Err(format!(
                "Không cập nhật được gallery-dl. {}",
                Self::unmanaged_hint("gallery-dl", &gallery_path, "")
            ));
        }
        let msg = Self::upgrade_in_place("gallery-dl", &kind).await?;
        Ok(Self::with_version(msg, &gallery_path).await)
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

    #[test]
    fn versions_are_extracted_from_version_banners() {
        let cases = [
            ("ffmpeg version 8.0.1-3ubuntu2 Copyright (c) 2000-2025 the FFmpeg developers", "8.0.1-3ubuntu2"),
            ("Python 3.14.4", "3.14.4"),
            ("v22.22.1", "22.22.1"),
            ("2026.08.19", "2026.08.19"),
            ("1.32.12-dev", "1.32.12-dev"),
            ("aria2 version 1.37.0\nCopyright (C) 2006, 2019 Tatsuhiro Tsujikawa", "1.37.0"),
            ("\n  ffprobe version 7.1 Copyright", "7.1"),
            ("ffmpeg version N-113478-g4d9a8d5ee9-20240301 Copyright", "N-113478-g4d9a8d5ee9-20240301"),
        ];
        for (raw, expected) in cases {
            assert_eq!(BinaryManager::parse_version(raw).as_deref(), Some(expected), "banner: {raw:?}");
        }
        assert_eq!(BinaryManager::parse_version("   \n  "), None);
    }

    /// pip in "Requirement already satisfied" cho các gói phụ thuộc kể cả khi vừa
    /// nâng cấp gói chính — không được báo nhầm là "đã là bản mới nhất".
    #[test]
    fn pip_outcome_distinguishes_upgrade_from_already_latest() {
        let upgraded = "Collecting gallery-dl\nRequirement already satisfied: requests>=2.11.0 in /x\n\
                        Installing collected packages: gallery-dl\nSuccessfully installed gallery-dl-1.32.13";
        assert_eq!(
            BinaryManager::pip_outcome("gallery-dl", true, upgraded, " (--user)").unwrap(),
            "Đã cập nhật gallery-dl qua pip (--user)."
        );
        let underscore = "Successfully installed gallery_dl-1.32.13";
        assert!(BinaryManager::pip_outcome("gallery-dl", true, underscore, "").unwrap().starts_with("Đã cập nhật"));

        let latest = "Requirement already satisfied: yt-dlp in /x (2026.9.1)\nRequirement already satisfied: requests in /x";
        assert_eq!(
            BinaryManager::pip_outcome("yt-dlp", true, latest, "").unwrap(),
            "yt-dlp đã là phiên bản mới nhất."
        );

        let only_dependency = "Successfully installed requests-2.33.0";
        assert_eq!(
            BinaryManager::pip_outcome("yt-dlp", true, only_dependency, "").unwrap(),
            "yt-dlp đã là phiên bản mới nhất."
        );

        let no_pip = BinaryManager::pip_outcome("yt-dlp", false, "/usr/bin/python3: No module named pip", "");
        assert!(no_pip.unwrap_err().contains("chưa có pip"));
    }

    #[test]
    fn long_tool_output_is_trimmed_to_the_relevant_lines() {
        let noisy = format!("{}ERROR: Unable to write to /usr/local/bin/yt-dlp; Try running as administrator\n", "noise line\n".repeat(200));
        assert_eq!(
            BinaryManager::tail_lines(&noisy),
            "ERROR: Unable to write to /usr/local/bin/yt-dlp; Try running as administrator"
        );
        let plain = "a\nb\n\nc\nd\n";
        assert_eq!(BinaryManager::tail_lines(plain), "b | c | d");
        assert!(BinaryManager::tail_lines(&"x".repeat(2000)).chars().count() <= 501);
    }

    /// yt-dlp cũ (vd. gói apt) thoát ngay với "no such option: --js-runtimes":
    /// chỉ được thêm cờ khi `--help` của chính binary đó có nó.
    #[cfg(unix)]
    #[tokio::test]
    async fn js_runtimes_flag_is_only_used_when_ytdlp_supports_it() {
        use std::os::unix::fs::PermissionsExt;
        let dir = scratch_dir("jsrt");
        let make = |name: &str, help: &str| {
            let path = dir.join(name);
            std::fs::write(&path, format!("#!/bin/sh\necho '{help}'\n")).unwrap();
            std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755)).unwrap();
            path
        };
        let new = make("yt-dlp-new", "    --js-runtimes RUNTIME[:PATH]    Additional JavaScript runtime");
        let old = make("yt-dlp-old", "    --cookies-from-browser BROWSER");
        assert!(BinaryManager::ytdlp_supports_js_runtimes(&new).await);
        assert!(!BinaryManager::ytdlp_supports_js_runtimes(&old).await);
        // Lần hỏi thứ hai lấy từ bộ nhớ đệm, kết quả không đổi
        assert!(BinaryManager::ytdlp_supports_js_runtimes(&new).await);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[tokio::test]
    async fn the_python_used_for_the_engine_actually_runs() {
        if let Some(python) = BinaryManager::find_working_python().await {
            let out = std::process::Command::new(&python).arg("--version").output().unwrap();
            let text = format!("{}{}", String::from_utf8_lossy(&out.stdout), String::from_utf8_lossy(&out.stderr));
            assert!(text.starts_with("Python 3"), "{python:?}: {text}");
        }
    }

    fn scratch_dir(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("crwl_binmgr_{tag}_{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn script_interpreter_only_accepts_pip_generated_python_scripts() {
        let dir = scratch_dir("shebang");

        let pip_script = dir.join("pip_script");
        std::fs::write(&pip_script, "#!/usr/bin/python3\nimport sys\nfrom yt_dlp import main\n").unwrap();
        assert_eq!(BinaryManager::script_interpreter(&pip_script), Some(PathBuf::from("/usr/bin/python3")));

        // zipapp chính thức của yt-dlp: shebang + dữ liệu ZIP → không phải script pip
        let zipapp = dir.join("zipapp");
        let mut bytes = b"#!/usr/bin/env python3\n".to_vec();
        bytes.extend_from_slice(b"PK\x03\x04\x14\x00rest-of-zip");
        std::fs::write(&zipapp, bytes).unwrap();
        assert_eq!(BinaryManager::script_interpreter(&zipapp), None);

        let elf = dir.join("elf");
        std::fs::write(&elf, b"\x7fELF\x02\x01\x01\x00binary").unwrap();
        assert_eq!(BinaryManager::script_interpreter(&elf), None);

        let shell = dir.join("shell");
        std::fs::write(&shell, "#!/bin/sh\necho hi\n").unwrap();
        assert_eq!(BinaryManager::script_interpreter(&shell), None);

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn venv_and_system_binaries_are_classified_without_touching_pip() {
        let dir = scratch_dir("classify");

        let venv = dir.join("myvenv");
        std::fs::create_dir_all(venv.join("bin")).unwrap();
        std::fs::write(venv.join("pyvenv.cfg"), "home = /usr/bin\n").unwrap();
        std::fs::write(venv.join("bin").join("python"), "").unwrap();
        let tool = venv.join("bin").join("gallery-dl");
        std::fs::write(&tool, "#!/x/myvenv/bin/python\n").unwrap();
        assert_eq!(
            BinaryManager::classify_install(&tool, "gallery-dl"),
            InstallKind::Venv(venv.join("bin").join("python"))
        );

        // Windows: <python>\Scripts\yt-dlp.exe thuộc về chính <python>\python.exe
        let py_home = dir.join("Python312");
        std::fs::create_dir_all(py_home.join("Scripts")).unwrap();
        std::fs::write(py_home.join("python.exe"), "").unwrap();
        std::fs::write(py_home.join("Scripts").join("yt-dlp.exe"), "MZ").unwrap();
        assert_eq!(
            BinaryManager::owning_interpreter_of(&py_home.join("Scripts").join("yt-dlp.exe")),
            Some(py_home.join("python.exe"))
        );
        assert_eq!(BinaryManager::owning_interpreter_of(&venv.join("bin").join("gallery-dl")), None);

        // Ngoài thư mục người dùng, không thuộc venv → do hệ thống quản lý
        let system_tool = dir.join("yt-dlp");
        std::fs::write(&system_tool, "#!/usr/bin/python3\n").unwrap();
        if !BinaryManager::is_user_local(&system_tool) {
            assert_eq!(BinaryManager::classify_install(&system_tool, "yt-dlp"), InstallKind::Unmanaged);
        }

        let _ = std::fs::remove_dir_all(&dir);
    }

    /// Lỗi thật trên máy người dùng: ~/.local/bin/gallery-dl là SYMLINK vào venv của
    /// pipx. Chỉ xét chuỗi đường dẫn symlink thì không thấy "/pipx/venvs/", và app
    /// đã cài lạc gallery-dl vào Python hệ thống bằng --break-system-packages.
    #[cfg(unix)]
    #[test]
    fn pipx_symlinked_binaries_are_upgraded_with_pipx() {
        let dir = scratch_dir("pipx");
        let venv_bin = dir.join("share").join("pipx").join("venvs").join("gallery-dl").join("bin");
        std::fs::create_dir_all(&venv_bin).unwrap();
        std::fs::write(venv_bin.parent().unwrap().join("pyvenv.cfg"), "home = /usr/bin\n").unwrap();
        std::fs::write(venv_bin.join("python"), "").unwrap();
        let real = venv_bin.join("gallery-dl");
        std::fs::write(&real, "#!/venv/bin/python\n").unwrap();

        let exposed_dir = dir.join("bin");
        std::fs::create_dir_all(&exposed_dir).unwrap();
        let exposed = exposed_dir.join("gallery-dl");
        std::os::unix::fs::symlink(&real, &exposed).unwrap();

        assert!(!BinaryManager::is_pipx_managed(&exposed), "đường dẫn symlink không chứa dấu hiệu pipx");
        assert_eq!(BinaryManager::classify_install(&exposed, "gallery-dl"), InstallKind::Pipx);

        let _ = std::fs::remove_dir_all(&dir);
    }
}
