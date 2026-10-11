/**
 * Quy tắc thuần cho danh sách thẻ tiến trình tải (xem hooks/useDownloadTasks).
 * Không import React / Tauri để chạy test bằng `node --test`.
 */

const FINISHED_STATUSES = new Set(['completed', 'error', 'cancelled'])

/**
 * Số thẻ ĐÃ XONG (hoàn tất / lỗi / đã huỷ) được giữ lại trong mỗi khung tải.
 *
 * Trước đây mọi thẻ nằm lại tới khi người dùng tự đóng: tải vài trăm tệp trong một
 * phiên là vài trăm thẻ, và MỖI sự kiện tiến trình lại vẽ lại toàn bộ chúng — app
 * càng dùng lâu càng chậm. Thẻ đang chạy luôn được giữ để vẫn bấm Huỷ được.
 */
export const MAX_FINISHED_TASKS = 20

export const isTaskFinished = (task) => FINISHED_STATUSES.has(task?.progress?.status)

/**
 * Bỏ các thẻ đã xong cũ nhất vượt quá `maxFinished` (danh sách xếp mới nhất trước).
 * Không có gì để bỏ thì trả về CHÍNH mảng cũ để React không vẽ lại vô ích.
 */
export function trimFinishedTasks(tasks, maxFinished = MAX_FINISHED_TASKS) {
  let finished = 0
  let dropped = false
  const kept = tasks.filter((task) => {
    if (!isTaskFinished(task)) return true
    finished += 1
    if (finished <= maxFinished) return true
    dropped = true
    return false
  })
  return dropped ? kept : tasks
}

/** Tập `sourceKey` của các tác vụ đang chạy — để tra "nút này có đang bận" trong O(1). */
export function busySourceKeys(tasks) {
  const keys = new Set()
  for (const task of tasks) {
    if (task.sourceKey != null && !isTaskFinished(task)) keys.add(task.sourceKey)
  }
  return keys
}
