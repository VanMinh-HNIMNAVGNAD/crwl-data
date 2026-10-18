// Báo bản mới của yt-dlp / gallery-dl. Khi Instagram, X, TikTok... đổi API, bản vá
// gần như luôn đến qua hai công cụ này — người dùng cần biết mà không phải tự mở
// modal Công cụ.

/** Các công cụ có bản mới trong kết quả `checkToolUpdates`. */
export function pendingToolUpdates(report) {
  if (!report) return []
  return [report.ytdlp, report.galleryDl].filter((t) => t?.updateAvailable && t.latest)
}

/**
 * Khoá nhận diện đúng những bản mới đã báo. Cache phía backend giữ 24 giờ nên mỗi lần
 * mở app đều thấy `updateAvailable`; so khoá này để chỉ báo một lần cho mỗi bản mới.
 */
export function toolUpdateKey(pending) {
  return pending.map((t) => `${t.name}@${t.latest}`).join('|')
}

export function toolUpdateToast(pending, onOpenTools) {
  const lines = pending.map((t) => `${t.name} ${t.latest} (đang dùng ${t.installed})`)
  return {
    type: 'info',
    title: pending.length > 1 ? 'Có bản mới cho các công cụ tải' : `Có bản mới của ${pending[0].name}`,
    message: `${lines.join(', ')}. Bản mới thường sửa lỗi khi nền tảng vừa thay đổi.`,
    action: { label: 'Mở Công cụ', onClick: onOpenTools },
  }
}
