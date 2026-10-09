/**
 * Thông báo cho người dùng: biến lỗi thô (stderr của yt-dlp/gallery-dl, traceback
 * Python, chuỗi lỗi Rust) thành MỘT câu ngắn kèm gợi ý cách xử lý.
 *
 * Trước đây toast và thẻ tải in nguyên văn chuỗi lỗi, kiểu
 * "yt-dlp extract thất bại: ERROR: [youtube] abc: Sign in to confirm you’re not a
 * bot. Use --cookies-from-browser or --cookies for the authentication. See ..." —
 * dài, tiếng Anh và không cho biết cần làm gì. Chi tiết gốc vẫn được giữ trong
 * `detail` để xem (và sao chép) khi cần báo lỗi.
 *
 * Module thuần JS, không phụ thuộc trình duyệt — chạy test bằng `node --test`.
 */

/** Chuỗi lỗi từ string / Error / object có `message`. */
export function errorText(err, fallback = '') {
  if (typeof err === 'string') return err
  return err?.message || fallback
}

const VIET_RE = /[àáạảãâầấậẩẫăằắặẳẵèéẹẻẽêềếệểễìíịỉĩòóọỏõôồốộổỗơờớợởỡùúụủũưừứựửữỳýỵỷỹđ]/i

// Dấu hiệu của log kỹ thuật: có chúng thì không đưa nguyên văn cho người dùng
const RAW_MARKERS =
  /ERROR:|WARNING:|Traceback|Stderr|Exception\b|exit (?:code|status)|kết thúc với mã|errno|os error|ConnectionPool|--[a-z][a-z-]+|\b[a-z]+_[a-z_]+\b|\bat [\w.]+\.(?:py|rs):\d+|^\s*File "/im

// Phần "(Chi tiết: ...)" mà backend gắn vào cuối câu lỗi thân thiện
const DETAIL_RE = /\s*\((?:Chi tiết|Details?):\s*([\s\S]*)\)\s*$/i

const PLATFORM_NAMES = [
  ['youtube', 'YouTube'],
  ['facebook', 'Facebook'],
  ['instagram', 'Instagram'],
  ['tiktok', 'TikTok'],
  ['x (twitter)', 'X (Twitter)'],
  ['twitter', 'X (Twitter)'],
  ['reddit', 'Reddit'],
  ['threads', 'Threads'],
  ['pinterest', 'Pinterest'],
]

/** Nền tảng được nhắc tới trong câu lỗi ("Nội dung Instagram ...", "[youtube] abc: ..."). */
function platformOf(text) {
  const lower = text.toLowerCase()
  const tagged = /\[(youtube|facebook|instagram|tiktok|twitter|reddit|threads|pinterest)[^\]]*\]/.exec(lower)
  if (tagged) return PLATFORM_NAMES.find(([key]) => key === tagged[1])?.[1] || ''
  const named = PLATFORM_NAMES.find(([key]) =>
    new RegExp(`(?:^|[^a-z])${key.replace(/[()]/g, '\\$&')}(?:[^a-z]|$)`).test(lower)
  )
  return named?.[1] || ''
}

const TOOL_LABELS = {
  'yt-dlp': 'yt-dlp',
  'gallery-dl': 'gallery-dl',
  ffmpeg: 'FFmpeg',
  ffprobe: 'FFmpeg',
  aria2c: 'aria2c',
  python: 'Python 3',
  python3: 'Python 3',
}

const TOOL_RE = '(yt-dlp|gallery-dl|ffmpeg|ffprobe|aria2c|python3?)'
const MISSING_TOOL_RE = new RegExp(
  `(?:không (?:được )?tìm thấy|chưa (?:được )?cài(?: đặt)?|không thể khởi chạy)\\s+${TOOL_RE}|` +
    `${TOOL_RE}\\b[^.]*?(?:không được tìm thấy|không tìm thấy|not found|no such file|chưa (?:được )?cài|not installed)`,
  'i'
)

/**
 * Các nhóm lỗi thường gặp, xét theo thứ tự — nhóm cụ thể đứng trước nhóm chung.
 * `title` / `message` có thể là hàm nhận chuỗi lỗi gốc.
 */
const RULES = [
  {
    test: /không phải một video đơn/i,
    strong: true,
    title: (raw) => {
      const n = /gồm (\d+) mục/i.exec(raw)?.[1]
      return n ? `Liên kết là danh sách ${n} video` : 'Liên kết là danh sách nhiều video'
    },
    message: 'Dán vào khung "Tải theo tài khoản" để quét và chọn video cần tải, hoặc dán link của từng video.',
  },
  {
    test: /time-?out|timed out|quá thời gian|quá \d+ giây|hết thời gian/i,
    strong: true,
    title: 'Quá thời gian chờ',
    message: (raw) =>
      /giảm số lượng|khoảng/i.test(raw)
        ? 'Hãy giảm số bài cần quét (hoặc chọn "Khoảng" nhỏ hơn) rồi thử lại.'
        : 'Máy chủ phản hồi quá chậm hoặc mạng không ổn định — hãy thử lại sau.',
  },
  {
    test: MISSING_TOOL_RE,
    strong: true,
    title: (raw) => {
      const m = MISSING_TOOL_RE.exec(raw)
      const tool = (m?.[1] || m?.[2] || '').toLowerCase()
      return `Chưa cài ${TOOL_LABELS[tool] || tool}`
    },
    message: (raw) => {
      const m = MISSING_TOOL_RE.exec(raw)
      const tool = (m?.[1] || m?.[2] || '').toLowerCase()
      return tool.startsWith('python')
        ? 'Cài Python 3, rồi mở "Công cụ" (⚙) và bấm "Khởi động lại" ở mục Python 3.'
        : 'Mở "Công cụ" (⚙) để xem cách cài, hoặc nhập đường dẫn tới công cụ trong phần cấu hình.'
    },
  },
  {
    test: /python worker|sidecar|engine bóc tách|lỗi nội bộ engine/i,
    strong: true,
    title: 'Bộ phân tích liên kết đang gặp sự cố',
    message: 'Mở "Công cụ" (⚙) và bấm "Khởi động lại" ở mục Python 3, rồi thử lại.',
  },
  {
    test: /could not (?:copy|find|open|read)[^.]*cookie|failed to decrypt|cookies? database|không đọc được cookie/i,
    strong: true,
    title: 'Không đọc được cookie của trình duyệt',
    message: 'Chọn đúng trình duyệt đang đăng nhập ở mục "Cookies", đóng trình duyệt đó rồi thử lại — hoặc chọn "Tắt Cookies".',
  },
  {
    test: /hết hạn|expired|http error 410|\b410 gone/i,
    title: 'Liên kết tải đã hết hạn',
    message: 'Hãy phân tích (hoặc quét) lại liên kết rồi tải lại.',
  },
  {
    test: /not available in your country|geo[- ]?restrict|from your location|khu vực của bạn/i,
    title: 'Nội dung bị chặn theo khu vực',
    message: 'Thử đặt proxy ở khu vực được phép trong "Công cụ" (⚙).',
  },
  {
    test: /yêu cầu đăng nhập|cần đăng nhập|\blog ?in\b|logged[- ]in|sign in|authenticat|not a bot|confirm your age|age[- ]restrict|giới hạn (?:độ )?tuổi|members?[- ]only|\bprivate\b|riêng tư|empty media response|cookies? (?:are |is )?(?:needed|required)|http error 401|unauthori[sz]ed/i,
    title: (raw) => {
      const platform = platformOf(raw)
      return platform ? `Nội dung ${platform} cần đăng nhập` : 'Nội dung cần đăng nhập'
    },
    message: (raw) => {
      const platform = platformOf(raw)
      return platform
        ? `Mở Cookie (🍪) → tab ${platform} để dán cookie, hoặc chọn trình duyệt đang đăng nhập ${platform} ở mục "Cookies".`
        : 'Mở Cookie (🍪) để thêm cookie cho nền tảng này, hoặc chọn trình duyệt đang đăng nhập ở mục "Cookies".'
    },
  },
  {
    test: /permission denied|access is denied|os error 13\b|os error 5\b|read-only file system|không có quyền ghi/i,
    strong: true,
    title: 'Không có quyền ghi vào thư mục lưu',
    message: 'Chọn thư mục lưu khác (nút 📁 trên thanh trên cùng) rồi thử lại.',
  },
  {
    test: /no space left|disk (?:is )?full|not enough space|ổ đĩa (?:đã )?đầy|os error 28\b/i,
    strong: true,
    title: 'Ổ đĩa đã đầy',
    message: 'Giải phóng dung lượng hoặc chọn thư mục lưu khác.',
  },
  {
    test: /http error 429|too many requests|rate[- ]?limit/i,
    strong: true,
    title: 'Máy chủ đang giới hạn lượt truy cập',
    message: 'Đợi vài phút rồi thử lại.',
  },
  {
    test: /video unavailable|is (?:no longer )?unavailable|has been removed|been deleted|no longer available|does not exist|không còn tồn tại|đã bị xo[áa]|http error 404|404 not found|\(404\)/i,
    title: 'Nội dung không còn tồn tại',
    message: 'Bài đăng / video có thể đã bị xoá, bị ẩn hoặc chuyển sang riêng tư.',
  },
  {
    test: /http error 403|\b403\b|forbidden|bị chặn/i,
    title: 'Máy chủ từ chối cho tải',
    message: 'Liên kết có thể đã hết hạn hoặc cần đăng nhập — hãy phân tích lại; nếu vẫn lỗi, thêm cookie (🍪).',
  },
  {
    test: /requested format (?:is )?not available|định dạng đã chọn/i,
    title: 'Định dạng đã chọn không còn khả dụng',
    message: 'Phân tích lại liên kết rồi chọn chất lượng khác.',
  },
  {
    test: /no video formats|no formats? found|unsupported url|không có luồng/i,
    title: 'Không tìm thấy video/âm thanh tải được',
    message: 'Liên kết không chứa luồng tải được, hoặc trang dùng cơ chế chống tải (DRM).',
  },
  {
    test: /ffmpeg|ffprobe|postprocess|ghép tệp|merger/i,
    title: 'Lỗi khi ghép hoặc chuyển đổi tệp',
    message: 'Kiểm tra FFmpeg trong "Công cụ" (⚙), hoặc chọn định dạng "Tự động" rồi tải lại.',
  },
  {
    test: /không (?:có tệp nào|tải được (?:tệp|mục) nào)/i,
    strong: true,
    title: 'Không tải được tệp nào',
    message: 'Liên kết có thể đã hết hạn hoặc bị chặn — hãy phân tích / quét lại rồi thử.',
  },
  {
    test: /\bzip\b/i,
    strong: true,
    title: 'Không nén được tệp ZIP',
    message: (raw) =>
      /vẫn nằm trong thư mục/i.test(raw)
        ? 'Các tệp đã tải vẫn còn nguyên trong thư mục album.'
        : 'Hãy thử tải dạng thư mục thay vì ZIP.',
  },
  {
    test: /unable to download (?:webpage|api|json|video data)|failed to resolve|name or service not known|name resolution|getaddrinfo|network is unreachable|connection (?:reset|refused|aborted|timed out|error)|could not resolve host|\bssl\b|certificate|không thể truy cập trang|lỗi kết nối|kết nối mạng|curl: \(\d+\)/i,
    title: 'Lỗi kết nối mạng',
    message: 'Kiểm tra kết nối Internet hoặc proxy rồi thử lại.',
  },
  {
    test: /không tìm thấy (?:dữ liệu|tệp|hình ảnh|phương tiện|media|nội dung)|không (?:thể )?bóc tách được|no media found|nothing to download/i,
    title: 'Không tìm thấy nội dung tải được',
    message: 'Liên kết không có ảnh / video công khai, hoặc cần đăng nhập để xem.',
  },
  {
    test: /not a valid url|invalid url|url không hợp lệ|định dạng url|missing 'url'/i,
    title: 'Liên kết không hợp lệ',
    message: 'Hãy dán liên kết đầy đủ, ví dụ https://...',
  },
]

const resolve = (value, raw) => (typeof value === 'function' ? value(raw) : value)

/** Nhận nhóm lỗi theo `probe`; tiêu đề/gợi ý lấy thông tin (nền tảng, số mục...) từ cả `context`. */
function matchRule(probe, context, strongOnly = false) {
  if (!probe) return null
  const rule = RULES.find((r) => (!strongOnly || r.strong) && r.test.test(probe))
  if (!rule) return null
  return { title: resolve(rule.title, context), message: resolve(rule.message, context) }
}

// Không viết hoa "yt-dlp ..." thành "Yt-dlp ..."
const capitalize = (s) => (!s || /^(?:yt-dlp|gallery-dl|ffmpeg|ffprobe|aria2c)/i.test(s) ? s : s.charAt(0).toUpperCase() + s.slice(1))
const stripEndPunct = (s) => s.replace(/[\s.:;,—–-]+$/u, '')

/** Câu tiếng Việt do chính app viết (không phải log) → tiêu đề + phần gợi ý. */
function friendlyParts(text) {
  let main = text.trim()
  // "Lỗi chờ yt-dlp: No such process" → bỏ phần đuôi kỹ thuật tiếng Anh
  const colon = main.indexOf(': ')
  if (colon > 0 && !VIET_RE.test(main.slice(colon + 2))) main = main.slice(0, colon)
  main = main
    .replace(/\bhttps?:\/\/\S+/g, 'liên kết này')
    // "(exit status: 1)", "(404)"... — giữ ngoặc tiếng Việt như "(quá 24 giờ)"
    .replace(/\s*\(([^()]*)\)/g, (whole, inner) => (VIET_RE.test(inner) ? whole : ''))
    .replace(/\s+/g, ' ')

  const dash = /^(.{6,90}?)\s+[—–]\s+(.+)$/u.exec(main)
  if (dash) return { title: stripEndPunct(dash[1]), message: capitalize(dash[2].trim()) }
  const sentence = /^(.{6,140}?[.!?])\s+(.+)$/u.exec(main)
  if (sentence) return { title: stripEndPunct(sentence[1]), message: sentence[2].trim() }
  return { title: stripEndPunct(main), message: '' }
}

const isFriendly = (text) => text.length > 0 && text.length <= 260 && VIET_RE.test(text) && !RAW_MARKERS.test(text)

/**
 * @param err      Error / chuỗi lỗi từ backend
 * @param fallback `{ title, message }` dùng khi không nhận ra lỗi (mặc định chung chung)
 * @returns `{ title, message, detail }` — `detail` là văn bản gốc để xem khi cần,
 *          rỗng nếu câu lỗi vốn đã dễ hiểu.
 */
export function describeError(err, fallback = {}) {
  const raw = errorText(err, '').trim()
  const fallbackTitle = fallback.title || 'Đã xảy ra lỗi'
  const fallbackMessage =
    fallback.message ?? 'Hãy thử lại sau ít phút. Nếu vẫn lỗi, cập nhật yt-dlp trong "Công cụ" (⚙).'
  if (!raw) return { title: fallbackTitle, message: fallbackMessage, detail: '' }

  const detailMatch = DETAIL_RE.exec(raw)
  const explicitDetail = detailMatch ? detailMatch[1].trim() : ''
  const main = detailMatch ? raw.slice(0, detailMatch.index).trim() : raw

  // 1. Nguyên nhân gốc (phần "Chi tiết") chính xác hơn câu tóm tắt: backend Rust gộp
  //    mọi lỗi 401/403/đăng nhập vào cùng một câu "cần đăng nhập hoặc bị chặn".
  // 2. Nhóm "mạnh" (timeout, thiếu công cụ, playlist...) có gợi ý rõ hơn câu gốc.
  // 3. Câu tiếng Việt do app tự viết (vd. lỗi Story) cụ thể hơn mọi gợi ý chung.
  const matched = matchRule(explicitDetail, raw) || matchRule(main, raw, true)
  if (matched) return { ...matched, detail: raw }
  if (isFriendly(main)) {
    const parts = friendlyParts(main)
    if (parts.title.length <= 90) return { ...parts, detail: explicitDetail ? raw : '' }
  }
  const general = matchRule(main, raw)
  if (general) return { ...general, detail: raw }
  return { title: fallbackTitle, message: fallbackMessage, detail: raw }
}

/** Tên tệp cuối cùng của một đường dẫn (Linux lẫn Windows). */
export function fileNameOf(path) {
  if (!path || typeof path !== 'string') return ''
  const parts = path.replace(/[\\/]+$/, '').split(/[\\/]/)
  return parts[parts.length - 1] || path
}

/** Rút gọn ở giữa để luôn thấy được đuôi tệp: "Mot_video_rat_dai…_1080p.mp4". */
export function shortenMiddle(text, max = 48) {
  const chars = Array.from(String(text || ''))
  if (chars.length <= max) return chars.join('')
  const tail = Math.min(16, Math.floor(max / 3))
  return `${chars.slice(0, max - tail - 1).join('')}…${chars.slice(-tail).join('')}`
}
