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
