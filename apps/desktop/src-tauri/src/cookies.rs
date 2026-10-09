use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::HashMap;
use std::path::PathBuf;
use log::{info, warn};
#[cfg(unix)]
use std::os::unix::fs::PermissionsExt;

/// Thư mục lưu cookie file theo platform
fn cookies_dir() -> PathBuf {
    dirs::config_dir()
        .unwrap_or_else(|| PathBuf::from("/tmp"))
        .join("crwl")
        .join("cookies")
}

/// Đường dẫn file cookie cho từng platform
fn cookie_file_path(platform: &str) -> PathBuf {
    cookies_dir().join(format!("{}.txt", platform.to_lowercase()))
}

/// Đảm bảo thư mục cookies tồn tại
fn ensure_cookies_dir() -> std::io::Result<()> {
    let dir = cookies_dir();
    std::fs::create_dir_all(&dir)?;
    #[cfg(unix)]
    {
        std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o700))?;
        for entry in std::fs::read_dir(&dir)? {
            let path = entry?.path();
            if path.is_file() {
                std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600))?;
            }
        }
    }
    Ok(())
}

#[cfg(unix)]
fn write_private_cookie_file(path: &std::path::Path, content: &str) -> std::io::Result<()> {
    use std::os::unix::fs::OpenOptionsExt;
    let mut options = std::fs::OpenOptions::new();
    options.write(true).create(true).truncate(true).mode(0o600);
    use std::io::Write;
    let mut file = options.open(path)?;
    file.write_all(content.as_bytes())?;
    file.set_permissions(std::fs::Permissions::from_mode(0o600))
}

#[cfg(not(unix))]
fn write_private_cookie_file(path: &std::path::Path, content: &str) -> std::io::Result<()> {
    std::fs::write(path, content)
}

const NETSCAPE_HEADER: &str = "# Netscape HTTP Cookie File";

/// Dòng đầu có khớp mẫu `#( Netscape)? HTTP Cookie File` mà http.cookiejar bắt buộc.
fn has_netscape_header(content: &str) -> bool {
    let first = content.lines().next().unwrap_or("").trim();
    first.starts_with(NETSCAPE_HEADER) || first.starts_with("# HTTP Cookie File")
}

/// Sửa tại chỗ tệp cookie đã lưu: bỏ BOM, thêm dòng đầu Netscape nếu thiếu.
///
/// Bản cũ lưu nguyên văn các dòng cookie người dùng dán (không có dòng đầu). yt-dlp
/// từ chối hẳn tệp đó ("does not look like a Netscape format cookies file") nên mọi
/// lượt tải của nền tảng tương ứng đều thất bại cho tới khi người dùng xoá cookie.
fn repair_cookie_file(path: &std::path::Path) {
    let Ok(content) = std::fs::read_to_string(path) else {
        return;
    };
    let stripped = content.trim_start_matches('\u{feff}');
    if stripped.len() == content.len() && has_netscape_header(stripped) {
        return;
    }
    let fixed = if has_netscape_header(stripped) {
        stripped.to_string()
    } else {
        format!("{NETSCAPE_HEADER}\n{stripped}")
    };
    match write_private_cookie_file(path, &fixed) {
        Ok(()) => info!("Đã bổ sung dòng đầu Netscape cho tệp cookie {:?}", path),
        Err(e) => warn!("Không sửa được tệp cookie {:?}: {e}", path),
    }
}

// ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PlatformCookieStatus {
    pub platform: String,
    pub has_cookies: bool,
    pub file_path: Option<String>,
    pub size_bytes: Option<u64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CookieStatusResult {
    pub platforms: Vec<PlatformCookieStatus>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SaveCookieResult {
    pub success: bool,
    pub platform: String,
    pub file_path: String,
    pub message: String,
}

// ─────────────────────────────────────────────────────────────────────────────

pub struct CookieService;

impl CookieService {
    /// Ánh xạ platform sang cookie domain gốc
    pub fn platform_to_domain(platform: &str) -> &'static str {
        match platform.to_lowercase().as_str() {
            "instagram" => ".instagram.com",
            "tiktok" => ".tiktok.com",
            "facebook" => ".facebook.com",
            "twitter" | "x" => ".x.com",
            "youtube" | "google" => ".youtube.com",
            "pinterest" => ".pinterest.com",
            "threads" => ".threads.net",
            "linkedin" => ".linkedin.com",
            "pixiv" | "pixiv_net" => ".pixiv.net",
            "reddit" => ".reddit.com",
            "bilibili" => ".bilibili.com",
            "douyin" => ".douyin.com",
            "soundcloud" => ".soundcloud.com",
            "tumblr" => ".tumblr.com",
            _ => ".social.com",
        }
    }

    /// Một dòng cookie Netscape mà tab đã bị đổi thành dấu cách (hay gặp khi copy từ
    /// trình xem văn bản): `domain flag path secure expiry name value`.
    fn netscape_line_from_spaces(line: &str) -> Option<String> {
        let parts: Vec<&str> = line.split_whitespace().collect();
        let is_flag = |s: &str| s.eq_ignore_ascii_case("TRUE") || s.eq_ignore_ascii_case("FALSE");
        if parts.len() < 6 || !is_flag(parts[1]) || !is_flag(parts[3]) || parts[4].parse::<i64>().is_err() {
            return None;
        }
        let value = parts.get(6..).map(|rest| rest.join(" ")).unwrap_or_default();
        Some(format!("{}\t{}\t{}\t{}\t{}\t{}\t{}", parts[0], parts[1], parts[2], parts[3], parts[4], parts[5], value))
    }

    /// Chuyển đổi cookie string (Netscape, JSON hoặc Header key=val) sang chuẩn Netscape HTTP Cookie.
    ///
    /// Trả về số cookie đọc được = 0 khi không nhận diện được gì. Trước đây khi đó
    /// nguyên văn chuỗi được lưu thành "1 cookie" — tệp hỏng này được ưu tiên số 1 cho
    /// mọi lượt tải, và yt-dlp từ chối nó ("does not look like a Netscape format
    /// cookies file") nên MỌI lượt tải của nền tảng đó đều thất bại.
    pub fn convert_to_netscape(platform: &str, raw: &str) -> (String, usize) {
        let trimmed = raw.trim();

        // 1. Đã là Netscape format
        if trimmed.starts_with("# Netscape HTTP Cookie File") || (trimmed.contains("\tTRUE\t") || trimmed.contains("\tFALSE\t")) {
            let count = trimmed
                .lines()
                .filter(|l| !l.trim().is_empty() && (!l.starts_with('#') || l.starts_with("#HttpOnly_")))
                .filter(|l| l.split('\t').count() >= 6)
                .count();
            // Người dùng thường chỉ copy các dòng cookie, không kèm dòng đầu. Lưu nguyên
            // văn như trước thì yt-dlp từ chối cả tệp (xem repair_cookie_file).
            let content = if has_netscape_header(trimmed) {
                trimmed.to_string()
            } else {
                format!("{NETSCAPE_HEADER}\n{trimmed}")
            };
            return (content, count);
        }

        // 1b. Netscape nhưng tab đã thành dấu cách
        let spaced: Vec<String> = trimmed
            .lines()
            .filter(|l| !l.trim().is_empty() && !l.trim_start().starts_with('#'))
            .filter_map(Self::netscape_line_from_spaces)
            .collect();
        if !spaced.is_empty() {
            let count = spaced.len();
            let mut lines = vec![
                "# Netscape HTTP Cookie File".to_string(),
                format!("# Converted for {platform} (tab đã được khôi phục)"),
                "".to_string(),
            ];
            lines.extend(spaced);
            return (lines.join("\n"), count);
        }

        let default_domain = if platform.contains('.') {
            let p = platform.trim().to_lowercase();
            if p.starts_with('.') {
                p
            } else {
                format!(".{p}")
            }
        } else {
            Self::platform_to_domain(platform).to_string()
        };

        // 2. Định dạng JSON (từ extension Cookie-Editor hoặc EditThisCookie). Đã là JSON
        //    thì KHÔNG rơi xuống bước tách "key=value" — bước đó sẽ băm chuỗi JSON
        //    thành những cookie rác.
        if trimmed.starts_with('[') || trimmed.starts_with('{') {
            let items = match serde_json::from_str::<Value>(trimmed) {
                Ok(Value::Array(items)) => items,
                // Một số tiện ích xuất dạng {"cookies": [...]}
                Ok(Value::Object(mut obj)) => match obj.remove("cookies") {
                    Some(Value::Array(items)) => items,
                    _ => Vec::new(),
                },
                _ => Vec::new(),
            };
            let mut lines = vec![
                "# Netscape HTTP Cookie File".to_string(),
                format!("# Converted for {platform} from JSON"),
                "".to_string(),
            ];
            let mut count = 0;
            for item in items {
                if let Value::Object(obj) = item {
                    let name = obj.get("name").and_then(|v| v.as_str()).unwrap_or("");
                    let value = obj.get("value").and_then(|v| v.as_str()).unwrap_or("");
                    if !Self::is_valid_cookie_pair(name, value) {
                        continue;
                    }
                    let domain = obj.get("domain").and_then(|v| v.as_str()).unwrap_or(&default_domain);
                    let path = obj.get("path").and_then(|v| v.as_str()).unwrap_or("/");
                    let secure = if obj.get("secure").and_then(|v| v.as_bool()).unwrap_or(false) { "TRUE" } else { "FALSE" };
                    let item_exp = obj.get("expirationDate")
                        .and_then(|v| {
                            if let Some(n) = v.as_f64() {
                                Some(n.trunc() as i64)
                            } else {
                                v.as_i64()
                            }
                        })
                        .map(|e| e.to_string())
                        .unwrap_or_else(|| "0".to_string());

                    let is_domain = if domain.starts_with('.') { "TRUE" } else { "FALSE" };
                    lines.push(format!("{domain}\t{is_domain}\t{path}\t{secure}\t{item_exp}\t{name}\t{value}"));
                    count += 1;
                }
            }
            return if count > 0 { (lines.join("\n"), count) } else { (String::new(), 0) };
        }

        // 3. Định dạng chuỗi Header: "name=value; name2=value2" — hoặc mỗi cookie
        //    một dòng như ô nhập gợi ý. Trước đây chỉ tách theo ';' nên dán nhiều
        //    dòng thì mọi cookie sau dòng đầu bị nhét vào GIÁ TRỊ của cookie đầu
        //    (vd. Facebook mất `xs` → không đăng nhập được).
        let mut lines = vec![
            "# Netscape HTTP Cookie File".to_string(),
            format!("# Converted for {platform} from Key-Value string"),
            "".to_string(),
        ];
        let mut count = 0;

        for pair in trimmed.split(|c| c == ';' || c == '\n' || c == '\r') {
            let mut p = pair.trim();
            for prefix in ["cookie:", "Cookie:", "COOKIE:"] {
                if let Some(rest) = p.strip_prefix(prefix) {
                    p = rest.trim();
                }
            }
            if p.is_empty() {
                continue;
            }
            if let Some((name, val)) = p.split_once('=') {
                let n = name.trim();
                let v = val.trim();
                if Self::is_valid_cookie_pair(n, v) {
                    lines.push(format!("{default_domain}\tTRUE\t/\tFALSE\t0\t{n}\t{v}"));
                    count += 1;
                }
            }
        }

        if count > 0 {
            (lines.join("\n"), count)
        } else {
            (String::new(), 0)
        }
    }

    /// Tên cookie hợp lệ theo RFC 6265 (không khoảng trắng / ký tự phân tách) và giá
    /// trị không chứa tab — nếu không, một câu văn có dấu "=" cũng thành cookie rác,
    /// còn tab trong giá trị làm vỡ dòng Netscape.
    fn is_valid_cookie_pair(name: &str, value: &str) -> bool {
        !name.is_empty()
            && !name
                .chars()
                .any(|c| c.is_whitespace() || c.is_control() || "()<>@,;:\\\"/[]?={}".contains(c))
            && !value.chars().any(|c| c == '\t' || c == '\n' || c == '\r')
    }

    /// Chuẩn hóa tên platform hoặc domain về định danh platform chuẩn
    pub fn normalize_platform(raw: &str) -> Option<String> {
        let mut s = raw.trim().to_lowercase();
        if s.is_empty() {
            return None;
        }

        // Loại bỏ schema (http://, https://)
        if let Some(pos) = s.find("://") {
            s = s[pos + 3..].to_string();
        }

        // Bỏ path / query nếu có (vd: pixiv.net/artworks -> pixiv.net)
        if let Some(pos) = s.find('/') {
            s = s[..pos].to_string();
        }
        if let Some(pos) = s.find('?') {
            s = s[..pos].to_string();
        }

        // Loại bỏ www. và dấu chấm đầu
        let s = s.trim_start_matches('.').trim_start_matches("www.");

        // Bỏ đuôi .txt nếu người dùng nhập nhầm tên file
        let s = s.strip_suffix(".txt").unwrap_or(s).trim();

        match s {
            "instagram" | "instagram.com" | "instagr.am" | "ig" => Some("instagram".to_string()),
            "tiktok" | "tiktok.com" => Some("tiktok".to_string()),
            "facebook" | "facebook.com" | "fb.com" | "fb.watch" | "fb.me" | "fb" => Some("facebook".to_string()),
            "twitter" | "twitter.com" | "x.com" | "x" | "t.co" => Some("twitter".to_string()),
            "youtube" | "youtube.com" | "youtu.be" | "youtube-nocookie.com" | "yt" | "google" => Some("youtube".to_string()),
            "pinterest" | "pinterest.com" | "pin.it" => Some("pinterest".to_string()),
            "threads" | "threads.net" => Some("threads".to_string()),
            "pixiv" | "pixiv.net" | "pixiv.me" | "pixiv_net" => Some("pixiv".to_string()),
            "reddit" | "reddit.com" | "redd.it" | "v.redd.it" => Some("reddit".to_string()),
            "bilibili" | "bilibili.com" | "b23.tv" => Some("bilibili".to_string()),
            "douyin" | "douyin.com" => Some("douyin".to_string()),
            "soundcloud" | "soundcloud.com" | "on.soundcloud.com" => Some("soundcloud".to_string()),
            "tumblr" | "tumblr.com" => Some("tumblr".to_string()),
            "linkedin" | "linkedin.com" => Some("linkedin".to_string()),
            _ => {
                if s.ends_with(".pinterest.com") || s.contains("pinterest.") {
                    return Some("pinterest".to_string());
                }
                if s.ends_with(".instagram.com") {
                    return Some("instagram".to_string());
                }
                if s.ends_with(".tiktok.com") {
                    return Some("tiktok".to_string());
                }
                if s.ends_with(".facebook.com") {
                    return Some("facebook".to_string());
                }
                if s.ends_with(".twitter.com") || s.ends_with(".x.com") {
                    return Some("twitter".to_string());
                }
                if s.ends_with(".youtube.com") {
                    return Some("youtube".to_string());
                }
                if s.ends_with(".reddit.com") {
                    return Some("reddit".to_string());
                }
                if s.ends_with(".bilibili.com") {
                    return Some("bilibili".to_string());
                }
                if s.ends_with(".douyin.com") {
                    return Some("douyin".to_string());
                }
                if s.ends_with(".soundcloud.com") {
                    return Some("soundcloud".to_string());
                }
                if s.ends_with(".tumblr.com") {
                    return Some("tumblr".to_string());
                }
                if s.ends_with(".linkedin.com") {
                    return Some("linkedin".to_string());
                }
                if s.ends_with(".threads.net") {
                    return Some("threads".to_string());
                }
                if s.ends_with(".pixiv.net") {
                    return Some("pixiv".to_string());
                }

                let supported = Self::supported_platforms();
                if supported.contains(&s.to_string()) {
                    return Some(s.to_string());
                }

                None
            }
        }
    }

    /// Xác thực và chuẩn hóa tên platform theo `supported_platforms()`
    pub fn validate_and_normalize_platform(raw: &str) -> Result<String, String> {
        let trimmed = raw.trim();
        if trimmed.is_empty() {
            return Err("Tên nền tảng không được để trống".to_string());
        }

        let supported = Self::supported_platforms();
        match Self::normalize_platform(trimmed) {
            Some(norm) if supported.contains(&norm) => Ok(norm),
            _ => Err(format!(
                "Nền tảng '{}' không được hỗ trợ. Các nền tảng hỗ trợ: {}",
                trimmed,
                supported.join(", ")
            )),
        }
    }

    /// Dọn dẹp các file cookie định danh cũ/thừa sau khi lưu
    fn cleanup_legacy_cookie_files(normalized_platform: &str) {
        let dir = cookies_dir();
        if !dir.exists() {
            return;
        }
        let candidates: &[&str] = match normalized_platform {
            "pixiv" => &["pixiv.net.txt", "pixiv_net.txt"],
            "threads" => &["threads.net.txt"],
            "twitter" => &["x.com.txt", "twitter.com.txt", "x.txt"],
            "facebook" => &["facebook.com.txt", "fb.com.txt", "fb.txt"],
            "youtube" => &["youtube.com.txt", "youtu.be.txt", "google.txt"],
            "tiktok" => &["tiktok.com.txt"],
            "instagram" => &["instagram.com.txt"],
            "reddit" => &["reddit.com.txt"],
            "bilibili" => &["bilibili.com.txt"],
            "douyin" => &["douyin.com.txt"],
            "soundcloud" => &["soundcloud.com.txt"],
            "tumblr" => &["tumblr.com.txt"],
            "linkedin" => &["linkedin.com.txt"],
            "pinterest" => &["pinterest.com.txt"],
            _ => &[],
        };
        for legacy in candidates {
            let p = dir.join(legacy);
            if p.exists() {
                let _ = std::fs::remove_file(&p);
            }
        }
    }

    /// Tự động di chuyển các file cookie cũ (vd: pixiv.net.txt -> pixiv.txt) nếu file chuẩn chưa có
    pub fn migrate_legacy_cookie_files() {
        let dir = cookies_dir();
        if !dir.exists() {
            return;
        }
        let legacy_mappings = [
            ("pixiv.net.txt", "pixiv.txt"),
            ("pixiv_net.txt", "pixiv.txt"),
            ("threads.net.txt", "threads.txt"),
            ("x.com.txt", "twitter.txt"),
            ("twitter.com.txt", "twitter.txt"),
            ("facebook.com.txt", "facebook.txt"),
            ("youtube.com.txt", "youtube.txt"),
            ("tiktok.com.txt", "tiktok.txt"),
            ("instagram.com.txt", "instagram.txt"),
            ("reddit.com.txt", "reddit.txt"),
            ("bilibili.com.txt", "bilibili.txt"),
            ("douyin.com.txt", "douyin.txt"),
            ("soundcloud.com.txt", "soundcloud.txt"),
            ("tumblr.com.txt", "tumblr.txt"),
            ("linkedin.com.txt", "linkedin.txt"),
            ("pinterest.com.txt", "pinterest.txt"),
        ];

        for (legacy, standard) in legacy_mappings {
            let legacy_path = dir.join(legacy);
            let standard_path = dir.join(standard);
            if legacy_path.exists() {
                if !standard_path.exists() {
                    let _ = std::fs::rename(&legacy_path, &standard_path);
                } else {
                    let _ = std::fs::remove_file(&legacy_path);
                }
            }
        }

        // Tệp đã lưu bởi bản cũ (thiếu dòng đầu Netscape) được sửa luôn tại đây
        for platform in Self::supported_platforms() {
            let path = cookie_file_path(&platform);
            if path.is_file() {
                repair_cookie_file(&path);
            }
        }
    }

    /// Lưu cookie string vào file cho một platform
    pub fn save_cookies(platform: &str, cookie_string: &str) -> Result<SaveCookieResult, String> {
        let normalized = Self::validate_and_normalize_platform(platform)?;
        ensure_cookies_dir().map_err(|e| format!("Không tạo được thư mục cookie: {e}"))?;

        let file_path = cookie_file_path(&normalized);

        // Validate: phải có nội dung hợp lệ
        let trimmed = cookie_string.trim();
        if trimmed.is_empty() {
            return Err("Cookie string không được để trống".to_string());
        }

        let (netscape_content, count) = Self::convert_to_netscape(&normalized, trimmed);
        if count == 0 {
            return Err(
                "Không nhận diện được cookie nào. Hãy dán dạng \"tên=giá_trị; tên2=giá_trị2\", \
                 JSON xuất từ Cookie-Editor, hoặc nội dung tệp cookies.txt (Netscape)."
                    .to_string(),
            );
        }

        write_private_cookie_file(&file_path, &netscape_content)
            .map_err(|e| format!("Không thể ghi file cookie: {e}"))?;

        Self::cleanup_legacy_cookie_files(&normalized);

        let path_str = file_path.to_string_lossy().to_string();
        info!("Đã lưu {count} cookie(s) cho platform '{normalized}'");

        Ok(SaveCookieResult {
            success: true,
            platform: normalized.clone(),
            file_path: path_str,
            message: format!("Đã lưu thành công {count} cookie cho '{normalized}' theo chuẩn Netscape"),
        })
    }

    /// Lấy trạng thái cookie của tất cả các platform được hỗ trợ
    pub fn get_cookie_status() -> CookieStatusResult {
        Self::migrate_legacy_cookie_files();
        let supported = Self::supported_platforms();
        let mut statuses = Vec::new();

        for platform in &supported {
            let file_path = cookie_file_path(platform);
            let has_cookies = file_path.exists()
                && file_path
                    .metadata()
                    .map(|m| m.len() > 0)
                    .unwrap_or(false);

            let size_bytes = if has_cookies {
                file_path.metadata().map(|m| m.len()).ok()
            } else {
                None
            };

            statuses.push(PlatformCookieStatus {
                platform: platform.clone(),
                has_cookies,
                file_path: if has_cookies {
                    Some(file_path.to_string_lossy().to_string())
                } else {
                    None
                },
                size_bytes,
            });
        }

        CookieStatusResult { platforms: statuses }
    }

    /// Xóa cookie file của một platform (hoặc tất cả nếu domain = None)
    pub fn delete_cookies(platform: Option<&str>) -> Result<bool, String> {
        match platform {
            Some(p) => {
                // Chỉ nhận tên nền tảng đã chuẩn hoá. Trước đây tên lạ được ghép thẳng
                // vào đường dẫn ("../../x" → xoá tệp .txt nằm ngoài thư mục cookie).
                let norm = Self::validate_and_normalize_platform(p)?;
                let file_path = cookie_file_path(&norm);
                if file_path.exists() {
                    std::fs::remove_file(&file_path)
                        .map_err(|e| format!("Không thể xóa cookie file: {e}"))?;
                    info!("Đã xóa cookie cho platform: {}", norm);
                }
                Self::cleanup_legacy_cookie_files(&norm);
                Ok(true)
            }
            None => {
                // Xóa tất cả cookie files trong thư mục
                let dir = cookies_dir();
                if !dir.exists() {
                    return Ok(true);
                }

                let entries = std::fs::read_dir(&dir)
                    .map_err(|e| format!("Không thể đọc thư mục cookie: {e}"))?;

                for entry in entries.flatten() {
                    let path = entry.path();
                    if path.extension().and_then(|e| e.to_str()) == Some("txt") {
                        if let Err(e) = std::fs::remove_file(&path) {
                            warn!("Không xóa được {}: {e}", path.display());
                        }
                    }
                }
                info!("Đã xóa toàn bộ cookie files");
                Ok(true)
            }
        }
    }

    /// Lấy đường dẫn cookie file cho yt-dlp (dùng --cookies flag)
    pub fn get_cookie_file_path(platform: &str) -> Option<PathBuf> {
        let norm = Self::normalize_platform(platform).unwrap_or_else(|| platform.to_lowercase());
        let path = cookie_file_path(&norm);
        if path.exists() && path.metadata().map(|m| m.len() > 0).unwrap_or(false) {
            repair_cookie_file(&path);
            Some(path)
        } else {
            None
        }
    }

    /// Lấy map platform -> cookie_file_path cho tất cả platforms có cookie
    #[allow(dead_code)]
    pub fn get_all_cookie_paths() -> HashMap<String, PathBuf> {
        let mut map = HashMap::new();
        for platform in Self::supported_platforms() {
            if let Some(path) = Self::get_cookie_file_path(&platform) {
                map.insert(platform, path);
            }
        }
        map
    }

    /// Danh sách các platform hỗ trợ cookie
    pub fn supported_platforms() -> Vec<String> {
        vec![
            "instagram".to_string(),
            "tiktok".to_string(),
            "facebook".to_string(),
            "twitter".to_string(),
            "youtube".to_string(),
            "pinterest".to_string(),
            "threads".to_string(),
            "pixiv".to_string(),
            "reddit".to_string(),
            "bilibili".to_string(),
            "douyin".to_string(),
            "soundcloud".to_string(),
            "tumblr".to_string(),
            "linkedin".to_string(),
        ]
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_convert_to_netscape_with_custom_domain_dot() {
        let (output, count) = CookieService::convert_to_netscape("pixiv.net", "test=123");
        assert_eq!(count, 1);
        assert!(output.contains(".pixiv.net\tTRUE\t/\tFALSE\t"));
        assert!(output.contains("\ttest\t123"));

        let (output_leading_dot, count_dot) = CookieService::convert_to_netscape(".pixiv.net", "test=123");
        assert_eq!(count_dot, 1);
        assert!(output_leading_dot.contains(".pixiv.net\tTRUE\t/\tFALSE\t"));
    }

    #[test]
    fn test_convert_to_netscape_with_platform_without_dot() {
        let (output, count) = CookieService::convert_to_netscape("pixiv", "test=123");
        assert_eq!(count, 1);
        assert!(output.contains(".pixiv.net\tTRUE\t/\tFALSE\t"));

        let (output_insta, count_insta) = CookieService::convert_to_netscape("instagram", "sessionid=abc");
        assert_eq!(count_insta, 1);
        assert!(output_insta.contains(".instagram.com\tTRUE\t/\tFALSE\t"));
    }

    /// Ô nhập gợi ý dạng "c_user=...\nxs=..." — mỗi cookie một dòng.
    #[test]
    fn test_convert_to_netscape_multiline_key_values() {
        let raw = "c_user=1000123\nxs=2%3Aabc\r\nfr=zzz; datr=yyy";
        let (output, count) = CookieService::convert_to_netscape("facebook", raw);
        assert_eq!(count, 4);
        assert!(output.contains("\tc_user\t1000123\n"));
        assert!(output.contains("\txs\t2%3Aabc\n"));
        assert!(output.contains("\tfr\tzzz\n"));
        assert!(output.ends_with("\tdatr\tyyy"));
        // Không có giá trị nào chứa xuống dòng làm hỏng tệp Netscape
        for line in output.lines().filter(|l| !l.starts_with('#') && !l.is_empty()) {
            assert_eq!(line.split('\t').count(), 7, "Dòng cookie hỏng: {line:?}");
        }

        let (with_prefix, n) = CookieService::convert_to_netscape("instagram", "Cookie: sessionid=abc; csrftoken=def");
        assert_eq!(n, 2);
        assert!(with_prefix.contains("\tsessionid\tabc\n"));
    }

    #[test]
    fn test_convert_to_netscape_json_custom_domain() {
        let json = r#"[{"name": "session", "value": "xyz"}]"#;
        let (output, count) = CookieService::convert_to_netscape("pixiv.net", json);
        assert_eq!(count, 1);
        assert!(output.contains(".pixiv.net\tTRUE\t/\tFALSE\t"));
        assert!(output.contains("\tsession\txyz"));
    }

    #[test]
    fn test_convert_to_netscape_fallback_social_com() {
        let (output, count) = CookieService::convert_to_netscape("unknown_platform", "key=val");
        assert_eq!(count, 1);
        assert!(output.contains(".social.com\tTRUE\t/\tFALSE\t"));
    }

    #[test]
    fn test_normalize_platform() {
        assert_eq!(CookieService::normalize_platform("pixiv.net"), Some("pixiv".to_string()));
        assert_eq!(CookieService::normalize_platform(".pixiv.net"), Some("pixiv".to_string()));
        assert_eq!(CookieService::normalize_platform("https://www.pixiv.net/artworks/123"), Some("pixiv".to_string()));
        assert_eq!(CookieService::normalize_platform("pixiv.net.txt"), Some("pixiv".to_string()));
        assert_eq!(CookieService::normalize_platform("pixiv"), Some("pixiv".to_string()));
        assert_eq!(CookieService::normalize_platform("threads.net"), Some("threads".to_string()));
        assert_eq!(CookieService::normalize_platform("x.com"), Some("twitter".to_string()));
        assert_eq!(CookieService::normalize_platform("twitter.com"), Some("twitter".to_string()));
        assert_eq!(CookieService::normalize_platform("fb.com"), Some("facebook".to_string()));
        assert_eq!(CookieService::normalize_platform("facebook.com"), Some("facebook".to_string()));
        assert_eq!(CookieService::normalize_platform("youtu.be"), Some("youtube".to_string()));
        assert_eq!(CookieService::normalize_platform("b23.tv"), Some("bilibili".to_string()));
        assert_eq!(CookieService::normalize_platform("unknown-domain.xyz"), None);
        assert_eq!(CookieService::normalize_platform("   "), None);
    }

    #[test]
    fn test_validate_and_normalize_platform() {
        assert_eq!(CookieService::validate_and_normalize_platform("pixiv.net").unwrap(), "pixiv");
        assert_eq!(CookieService::validate_and_normalize_platform("threads").unwrap(), "threads");
        assert_eq!(CookieService::validate_and_normalize_platform("x.com").unwrap(), "twitter");

        let err_empty = CookieService::validate_and_normalize_platform("  ");
        assert!(err_empty.is_err());
        assert!(err_empty.unwrap_err().contains("không được để trống"));

        let err_unsupported = CookieService::validate_and_normalize_platform("invalidplatform.xyz");
        assert!(err_unsupported.is_err());
        assert!(err_unsupported.unwrap_err().contains("không được hỗ trợ"));
    }

    #[test]
    fn test_save_cookies_validation() {
        let res = CookieService::save_cookies("invalidplatform.xyz", "cookie=123");
        assert!(res.is_err());
        assert!(res.unwrap_err().contains("không được hỗ trợ"));

        let empty_res = CookieService::save_cookies("pixiv.net", "   ");
        assert!(empty_res.is_err());
        assert!(empty_res.unwrap_err().contains("không được để trống"));
    }

    /// Trước đây chuỗi không nhận diện được vẫn được lưu thành "1 cookie" — tệp hỏng
    /// đó được ưu tiên cho mọi lượt tải và yt-dlp từ chối nó.
    #[test]
    fn unrecognized_cookie_text_is_rejected_instead_of_saved() {
        assert_eq!(CookieService::convert_to_netscape("instagram", "chỉ là một câu văn").1, 0);
        assert_eq!(CookieService::convert_to_netscape("instagram", "a sentence with = sign").1, 0);
        assert_eq!(CookieService::convert_to_netscape("instagram", r#"[{"foo": 1}]"#).1, 0);
        assert_eq!(CookieService::convert_to_netscape("instagram", "{not json").1, 0);

        let res = CookieService::save_cookies("instagram", "chỉ là một câu văn");
        assert!(res.unwrap_err().contains("Không nhận diện"));
    }

    #[test]
    fn json_object_exports_are_supported() {
        let raw = r#"{"cookies": [{"name": "sessionid", "value": "abc", "domain": ".instagram.com"}]}"#;
        let (out, count) = CookieService::convert_to_netscape("instagram", raw);
        assert_eq!(count, 1);
        assert!(out.contains(".instagram.com\tTRUE\t/\tFALSE\t0\tsessionid\tabc"));
    }

    #[test]
    fn netscape_lines_with_spaces_instead_of_tabs_are_repaired() {
        let raw = ".instagram.com TRUE / TRUE 1790000000 sessionid abc123\n\
                   .instagram.com TRUE / TRUE 1790000000 csrftoken xyz";
        let (out, count) = CookieService::convert_to_netscape("instagram", raw);
        assert_eq!(count, 2);
        assert!(out.contains(".instagram.com\tTRUE\t/\tTRUE\t1790000000\tsessionid\tabc123"));
        for line in out.lines().filter(|l| !l.starts_with('#') && !l.is_empty()) {
            assert_eq!(line.split('\t').count(), 7, "Dòng cookie hỏng: {line:?}");
        }
    }

    /// Dán các dòng cookie Netscape (đúng tab) nhưng không có dòng đầu: yt-dlp từ
    /// chối tệp nếu dòng đầu không phải "# Netscape HTTP Cookie File".
    #[test]
    fn netscape_lines_without_header_get_one() {
        let raw = ".youtube.com\tTRUE\t/\tTRUE\t1890000000\tPREF\tf6=4\n\
                   .youtube.com\tTRUE\t/\tFALSE\t0\tVISITOR_INFO1_LIVE\tabc";
        let (out, count) = CookieService::convert_to_netscape("youtube", raw);
        assert_eq!(count, 2);
        assert!(out.starts_with("# Netscape HTTP Cookie File\n"), "thiếu dòng đầu: {out:?}");
        assert!(out.ends_with("VISITOR_INFO1_LIVE\tabc"));

        // Đã có dòng đầu (kể cả biến thể "# HTTP Cookie File") thì giữ nguyên
        let with_header = format!("# HTTP Cookie File\n{raw}");
        assert_eq!(CookieService::convert_to_netscape("youtube", &with_header).0, with_header);
    }

    #[test]
    fn saved_cookie_files_without_header_are_repaired() {
        let dir = std::env::temp_dir().join(format!("crwl_cookie_repair_{}", uuid::Uuid::new_v4()));
        std::fs::create_dir_all(&dir).unwrap();
        let lines = ".x.com\tTRUE\t/\tTRUE\t0\tauth_token\tabc\n";

        let bare = dir.join("bare.txt");
        std::fs::write(&bare, lines).unwrap();
        repair_cookie_file(&bare);
        assert_eq!(std::fs::read_to_string(&bare).unwrap(), format!("{NETSCAPE_HEADER}\n{lines}"));

        // BOM trước dòng đầu cũng làm http.cookiejar không nhận ra định dạng
        let bom = dir.join("bom.txt");
        std::fs::write(&bom, format!("\u{feff}{NETSCAPE_HEADER}\n{lines}")).unwrap();
        repair_cookie_file(&bom);
        assert_eq!(std::fs::read_to_string(&bom).unwrap(), format!("{NETSCAPE_HEADER}\n{lines}"));

        // Tệp đã đúng thì không bị đụng tới
        let good = dir.join("good.txt");
        let good_content = format!("{NETSCAPE_HEADER}\n{lines}");
        std::fs::write(&good, &good_content).unwrap();
        repair_cookie_file(&good);
        assert_eq!(std::fs::read_to_string(&good).unwrap(), good_content);

        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn deleting_cookies_rejects_path_like_platform_names() {
        assert!(CookieService::delete_cookies(Some("../../etc/passwd")).is_err());
        assert!(CookieService::delete_cookies(Some("unknown-site")).is_err());
    }

    #[test]
    fn test_cookie_expiry_semantics() {
        // Test 1: persistent cookie + expirationDate
        let json_persistent = r#"[{"name": "persist", "value": "1", "expirationDate": 1790000000}]"#;
        let (out_persist, _) = CookieService::convert_to_netscape("test.com", json_persistent);
        assert!(out_persist.contains("\t1790000000\tpersist\t1"));

        // Test 1: session cookie + no expirationDate
        let json_session = r#"[{"name": "sess", "value": "2"}]"#;
        let (out_sess, _) = CookieService::convert_to_netscape("test.com", json_session);
        assert!(out_sess.contains("\t0\tsess\t2"));
        
        // Test 1: expired cookie
        let json_expired = r#"[{"name": "exp", "value": "3", "expirationDate": 1200000000}]"#;
        let (out_exp, _) = CookieService::convert_to_netscape("test.com", json_expired);
        assert!(out_exp.contains("\t1200000000\texp\t3"));
        
        // Test 1: cookie header string -> session cookie
        let header_str = "cookie: sess=abc; another=123";
        let (out_hdr, _) = CookieService::convert_to_netscape("test.com", header_str);
        assert!(out_hdr.contains("\t0\tsess\tabc"));
        assert!(out_hdr.contains("\t0\tanother\t123"));
    }
}


