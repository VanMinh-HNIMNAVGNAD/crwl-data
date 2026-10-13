/**
 * Quy tắc thuần về kết quả bóc tách. Không import React / Tauri để chạy test bằng
 * `node --test`.
 */

/**
 * Kết quả bóc tách có thứ gì tải được không (luồng video/âm thanh hoặc tệp ảnh/video).
 *
 * Trang dùng DRM / player mã hoá vẫn trả về "thành công" nhưng không có luồng nào
 * (kèm mô tả "⚠️ Không tìm được nguồn video..."). Ở khung "Nhiều link" kết quả đó
 * từng được đánh ✓ và hiện nút "Tải về" — bấm vào chỉ để nhận lỗi.
 */
export function hasDownloadableContent(media) {
  if (!media || typeof media !== 'object') return false
  if (Array.isArray(media.streams) && media.streams.length > 0) return true
  return Array.isArray(media.images) && media.images.some((img) => Boolean(img?.url))
}

/** Lý do hiển thị khi kết quả không có gì để tải. */
export function noDownloadableContentReason(media) {
  const description = typeof media?.description === 'string' ? media.description.trim() : ''
  const reason = description.replace(/^⚠️\s*/u, '').trim()
  return reason || 'Không tìm được nguồn video hay tệp ảnh nào để tải từ liên kết này.'
}

/**
 * Ghi chú của engine về kết quả: `⚠️ ...` (không tải được) hoặc `ℹ️ ...` (tải được
 * nhưng cần biết, vd. link chỉ có hình không tiếng, chỉ là một mảnh của luồng).
 * Trả về `{ kind: 'warning' | 'info', text }` hoặc null.
 */
export function mediaNote(media) {
  const description = typeof media?.description === 'string' ? media.description.trim() : ''
  const m = /^(⚠️|ℹ️)\s*/u.exec(description)
  if (!m) return null
  const text = description.slice(m[0].length).trim()
  return text ? { kind: m[1] === '⚠️' ? 'warning' : 'info', text } : null
}

/**
 * Số mảnh HLS/DASH yt-dlp tải song song (`-N`).
 *
 * Trước đây mặc định là 1 "cho máy yếu": luồng HLS của trang phim (hàng nghìn mảnh
 * vài giây) bị tải tuần tự từng mảnh, chậm theo độ trễ mạng. Đo trên luồng HLS 720p:
 * -N 4 nhanh gấp ~2,9 lần mà CPU trên mỗi MB còn THẤP hơn (0,045 s/MB so với
 * 0,062 s/MB), RAM chỉ thêm ~20 MB. Không ảnh hưởng tệp tải một lần (YouTube, MP4).
 */
export const concurrentFragmentsFor = (accelerate) => (accelerate ? 8 : 4)

const DIRECT_MEDIA_RE =
  /\.(?:m3u8|mpd|ts|m4s|cmfv|cmfa|mp4|m4v|mov|webm|mkv|flv|avi|3gp|ogv|wmv|mpg|mpeg|m2ts|mts|mp3|m4a|aac|ogg|oga|opus|flac|wav|weba|wma)$/i

/** URL trỏ thẳng tới tệp / luồng media (theo đuôi tệp trong đường dẫn). */
export function isDirectMediaUrl(url) {
  if (typeof url !== 'string' || !url) return false
  try {
    return DIRECT_MEDIA_RE.test(new URL(url).pathname)
  } catch {
    return false
  }
}

/**
 * Referer gửi kèm khi tải: trang chứa luồng nếu engine biết. Không lấy chính link tệp
 * / luồng làm Referer — trước đây link .m3u8 dán thẳng được tải kèm Referer là chính
 * nó, trong khi lúc phân tích không có Referer nào. CDN chống hotlink kiểu danh sách
 * "Allowed Referrers" (BunnyCDN...) cho qua yêu cầu không Referer nhưng chặn Referer
 * ngoài danh sách: phân tích được mà tải thì 403.
 */
export function downloadRefererFor(media) {
  if (media?.referer) return media.referer
  const original = media?.originalUrl
  return original && !isDirectMediaUrl(original) ? original : undefined
}

// Manifest HLS/DASH. Giữ đồng bộ với `isDirectStreamUrl()` trong constants.js (file đó
// import icon JSX nên không dùng được ở module thuần này).
const STREAM_MANIFEST_RE = /\.(?:m3u8|mpd)(?:[?#]|$)/i

/**
 * Tệp của album phải tải qua yt-dlp thay vì tải thẳng bằng curl.
 *
 * Bài viết trên trang bất kỳ có thể nhúng video YouTube/Vimeo (URL là TRANG video)
 * hoặc phát luồng HLS/DASH (.m3u8/.mpd là danh sách mảnh, không phải tệp video).
 * curl chỉ tải được tệp: trước đây bấm tải những mục này luôn thất bại với lỗi
 * "nội dung là trang web HTML" hoặc lưu ra một tệp playlist vô dụng.
 */
export function needsYtdlpDownload(item) {
  if (!item || typeof item !== 'object' || typeof item.url !== 'string' || !item.url) return false
  if (item.via === 'ytdlp') return true
  return item.type === 'video' && STREAM_MANIFEST_RE.test(item.url)
}

/** Chia các tệp được chọn theo cách tải: `direct` (curl) và `viaYtdlp` (yt-dlp), giữ thứ tự. */
export function splitByDownloader(items) {
  const direct = []
  const viaYtdlp = []
  for (const item of items || []) {
    if (!item?.url) continue
    ;(needsYtdlpDownload(item) ? viaYtdlp : direct).push(item)
  }
  return { direct, viaYtdlp }
}
