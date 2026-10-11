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
