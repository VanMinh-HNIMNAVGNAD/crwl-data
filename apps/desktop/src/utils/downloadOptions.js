/**
 * Quy tắc của các công cụ nhỏ trên kết quả bóc tách: "Cắt đoạn" và "Tùy chọn tải".
 * Không import React / Tauri để chạy test bằng `node --test`.
 */

/**
 * Mốc "Cắt đoạn" → số giây: "90", "1:30", "01:02:03", "1:30.5". Trống → null, sai → NaN.
 * Giữ cùng quy tắc với `parse_timecode` trong downloader.rs.
 */
export function parseTimecode(text) {
  const s = typeof text === 'string' ? text.trim() : ''
  if (!s) return null
  const parts = s.split(':').map((part) => part.trim())
  if (parts.length > 3) return NaN
  let total = 0
  for (let i = 0; i < parts.length; i++) {
    const isLast = i === parts.length - 1
    if (!(isLast ? /^\d+(?:\.\d*)?$/ : /^\d+$/).test(parts[i])) return NaN
    const value = Number(parts[i])
    // Phút / giây sau dấu ":" đầu tiên phải dưới 60 ("1:75" là gõ nhầm)
    if (i > 0 && value >= 60) return NaN
    total = total * 60 + value
  }
  return total
}

/** Số giây → "1:05" / "1:02:03" (giữ một chữ số lẻ nếu có). */
export function formatTimecode(seconds) {
  const total = Math.max(0, Number(seconds) || 0)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const rest = Math.round((total - h * 3600 - m * 60) * 10) / 10
  const sec = Number.isInteger(rest) ? String(rest).padStart(2, '0') : rest.toFixed(1).padStart(4, '0')
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`
}

/**
 * Kiểm tra mốc "Cắt đoạn" trước khi tải.
 *
 * Trước đây mốc được gửi nguyên văn: gõ "1p30" nhận lỗi cú pháp của yt-dlp, mốc ngược
 * (0:30 → 0:10) ra tệp không có hình lẫn tiếng mà app vẫn báo "Hoàn tất".
 *
 * @param durationText thời lượng video do engine trả về ("3:45"), để chặn mốc vượt quá.
 * @returns `{ ok: false, error }` | `{ ok: true, active: false }` (tải nguyên video) |
 *   `{ ok: true, active: true, startTime, endTime, summary }` (mốc tính bằng giây).
 */
export function checkTrimRange(start, end, durationText = null) {
  const startSec = parseTimecode(start)
  const endSec = parseTimecode(end)
  const invalid = (which, raw) =>
    `Mốc ${which} "${String(raw).trim()}" không hợp lệ — hãy nhập dạng 90 (giây), 1:30 hoặc 01:02:03.`
  if (Number.isNaN(startSec)) return { ok: false, error: invalid('bắt đầu', start) }
  if (Number.isNaN(endSec)) return { ok: false, error: invalid('kết thúc', end) }

  const from = startSec ?? 0
  const duration = parseTimecode(typeof durationText === 'string' ? durationText : '')
  const knownDuration = Number.isFinite(duration) && duration > 0
  if (endSec !== null && endSec <= from) {
    return { ok: false, error: 'Mốc kết thúc phải sau mốc bắt đầu.' }
  }
  if (knownDuration && from >= duration) {
    return { ok: false, error: `Mốc bắt đầu vượt quá độ dài video (${formatTimecode(duration)}).` }
  }
  if (endSec === null && from === 0) return { ok: true, active: false }

  const to = endSec === null ? (knownDuration ? duration : null) : knownDuration ? Math.min(endSec, duration) : endSec
  const span = to === null ? 'hết video' : `${formatTimecode(to)} (dài ${formatTimecode(to - from)})`
  return {
    ok: true,
    active: true,
    startTime: String(from),
    endTime: endSec === null ? '' : String(endSec),
    summary: `Sẽ tải ${formatTimecode(from)} → ${span}`,
  }
}

/**
 * Ngôn ngữ phụ đề nhúng vào video: có tối đa 2 phụ đề (hiện dạng nút) thì nhúng cả hai,
 * nhiều hơn thì nhúng ngôn ngữ đang chọn ở ô "Phụ đề" — YouTube liệt kê cả trăm bản dịch
 * tự động, nhúng hết là hàng trăm lượt tải.
 */
export function embedSubtitleLangs(subtitles, selectedLang) {
  const list = Array.isArray(subtitles) ? subtitles.filter((sub) => sub?.lang) : []
  if (list.length <= 2) return list.map((sub) => sub.lang)
  return [(list.find((sub) => sub.lang === selectedLang) || list[0]).lang]
}

// Container yt-dlp không nhúng được ảnh bìa (MOV: báo đã nhúng nhưng tệp ra không có)
const NO_THUMBNAIL_CONTAINERS = { mov: 'MOV', webm: 'WebM', gif: 'GIF' }

const TRIM_NOTE = 'Không dùng được khi đang cắt đoạn: mốc thời gian của video gốc sẽ lệch với đoạn cắt.'

const isYoutubeMedia = (media) =>
  media?.platform === 'youtube' || /(?:^|[/.])(?:youtube\.com|youtu\.be)\//i.test(media?.originalUrl || '')

/**
 * Lý do một tuỳ chọn tải KHÔNG có tác dụng với kết quả đang mở ('' = dùng được).
 *
 * Trước đây các ô này luôn tick được nhưng có khi không làm gì: "Tách theo chương" với
 * video không có chương, "Bỏ đoạn tài trợ" với video không phải YouTube, "Nhúng Phụ đề"
 * với video không có phụ đề, "Nhúng Thumbnail" vào MOV/WebM/GIF.
 */
export function optionLimits(media, { trimming = false, videoContainer = 'auto' } = {}) {
  const subtitleCount = Array.isArray(media?.subtitles) ? media.subtitles.length : 0
  const chapterCount = Array.isArray(media?.chapters) ? media.chapters.length : 0
  const noThumb = NO_THUMBNAIL_CONTAINERS[videoContainer]
  return {
    embedSubs: subtitleCount === 0 ? 'Video này không có phụ đề.' : trimming ? TRIM_NOTE : '',
    sponsorBlock: !isYoutubeMedia(media) ? 'Chỉ có với video YouTube.' : trimming ? TRIM_NOTE : '',
    splitChapters: chapterCount === 0 ? 'Video này không chia chương.' : trimming ? TRIM_NOTE : '',
    embedThumbnail: noThumb ? `Tệp ${noThumb} không chứa được ảnh bìa — chọn MP4, MKV hoặc Tự động.` : '',
  }
}

/** Lưu ý riêng của từng định dạng file đầu ra (hiện dưới ô chọn định dạng). */
export function containerNote(videoContainer) {
  switch (videoContainer) {
    case 'webm':
      return 'WebM chỉ chứa VP9/AV1 + Opus: video H.264 (TikTok, Facebook, Instagram...) sẽ được chuyển mã, lâu hơn.'
    case 'mov':
      return 'MOV không chứa VP9/AV1/Opus (YouTube từ 1080p): khi đó app tự chuyển mã sang H.264, lâu hơn.'
    case 'gif':
      return 'GIF không có tiếng, rộng tối đa 480px, 12 khung/giây — nên dùng kèm Cắt đoạn.'
    default:
      return ''
  }
}
