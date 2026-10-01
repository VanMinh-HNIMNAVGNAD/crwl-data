import { useCallback, useRef, useState } from 'react'

const FINISHED_STATUSES = new Set(['completed', 'error', 'cancelled'])

export const isTaskFinished = (task) => FINISHED_STATUSES.has(task?.progress?.status)

export function formatSeconds(secs) {
  if (!Number.isFinite(secs) || secs < 0) return '00:00'
  const h = Math.floor(secs / 3600)
  const m = Math.floor((secs % 3600) / 60)
  const s = Math.floor(secs % 60)
  const mm = m.toString().padStart(2, '0')
  const ss = s.toString().padStart(2, '0')
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`
}

const SPEED_UNITS = { '': 1, K: 1024, M: 1024 ** 2, G: 1024 ** 3, T: 1024 ** 4 }

/** "2.40MiB/s" (yt-dlp) hay "2.4MiB/s" (aria2c) → byte/giây; không đọc được → 0. */
export function parseSpeed(text) {
  const m = /([\d.]+)\s*([KMGT]?)i?B/i.exec(text || '')
  if (!m) return 0
  return Number(m[1]) * SPEED_UNITS[m[2].toUpperCase()] || 0
}

/** Byte/giây → chuỗi cùng kiểu yt-dlp ("2.40MiB/s"); 0 → chuỗi rỗng. */
export function formatSpeed(bytesPerSec) {
  if (!(bytesPerSec > 0)) return ''
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB']
  let value = bytesPerSec
  let i = 0
  while (value >= 1024 && i < units.length - 1) {
    value /= 1024
    i++
  }
  return `${value.toFixed(2)}${units[i]}/s`
}

// Đo dưới 2 giây thì tốc độ còn dao động mạnh, ước tính sẽ nhảy loạn
const ETA_MIN_SAMPLE_MS = 2000

/**
 * Bổ sung "thời gian còn lại" khi backend không gửi.
 *
 * yt-dlp báo ETA "Unknown" ở vài giây đầu (chưa đo được tốc độ) và với luồng
 * HLS/DASH chia mảnh; tải album thì không bao giờ có ETA. Mỗi sự kiện lại ghi
 * đè cả object tiến trình, nên chỉ một sự kiện thiếu ETA cũng làm ô "Ước tính
 * còn lại" về "--". Ở đây: giữ ETA cũ khi sự kiện mới để trống, và tự ước tính
 * theo tốc độ tăng phần trăm kể từ lúc bắt đầu nhận dữ liệu.
 *
 * Bỏ qua bước hậu xử lý (ghép FFmpeg...): phần trăm gần như đứng yên nên
 * không ước tính được, hiển thị "--" là trung thực hơn một con số sai.
 */
function withEstimatedEta(task, progress, now) {
  const percent = Number(progress.percent) || 0
  const measurable =
    !FINISHED_STATUSES.has(progress.status) &&
    progress.status !== 'processing' &&
    !progress.isIndeterminate &&
    !progress.is_indeterminate &&
    percent > 0
  if (!measurable) return { progress, etaBase: task.etaBase }

  const etaBase = task.etaBase || { at: now, percent }
  if (progress.eta) return { progress, etaBase }

  const elapsedMs = now - etaBase.at
  const gained = percent - etaBase.percent
  const eta =
    elapsedMs >= ETA_MIN_SAMPLE_MS && gained > 0
      ? formatSeconds(((elapsedMs / 1000) * (100 - percent)) / gained)
      : task.progress?.eta || ''
  return { progress: { ...progress, eta }, etaBase }
}

/**
 * Danh sách tác vụ tải của một khung (tải theo liên kết / theo tài khoản).
 *
 * Trước đây mỗi khung chỉ có MỘT trạng thái tiến trình và MỘT task-id "hiện tại":
 * tải hai tệp song song thì hai luồng sự kiện ghi đè nhau trên cùng một thẻ, và
 * nút Hủy chỉ huỷ được tác vụ bắt đầu sau cùng. Nay mỗi tác vụ có thẻ riêng.
 */
export function useDownloadTasks() {
  const [tasks, setTasks] = useState([])
  const cancelledRef = useRef(new Set())

  /** Thêm tác vụ mới lên đầu danh sách. `sourceKey` dùng để biết nút nào đang bận. */
  const startTask = useCallback((id, { title, sourceKey = null, progress }) => {
    cancelledRef.current.delete(id)
    setTasks((prev) => [
      { id, title, sourceKey, startedAt: Date.now(), progress: { ...progress, id } },
      ...prev.filter((t) => t.id !== id),
    ])
  }, [])

  /** Cập nhật tiến trình (giá trị mới hoặc hàm `prev => next`). */
  const updateTask = useCallback((id, next) => {
    const now = Date.now()
    setTasks((prev) =>
      prev.map((t) => {
        if (t.id !== id) return t
        const value = typeof next === 'function' ? next(t.progress) : next
        if (!value) return t
        // Sự kiện đến muộn từ backend không được "hồi sinh" tác vụ đã huỷ
        if (t.progress?.status === 'cancelled' && value.status !== 'cancelled') return t
        const { progress, etaBase } = withEstimatedEta(t, value, now)
        return { ...t, etaBase, progress: { ...progress, id } }
      })
    )
  }, [])

  const dismissTask = useCallback((id) => {
    setTasks((prev) => prev.filter((t) => t.id !== id))
  }, [])

  /** Bỏ các thẻ đã xong, giữ lại tác vụ đang chạy để vẫn huỷ được. */
  const clearFinishedTasks = useCallback(() => {
    setTasks((prev) => prev.filter((t) => !isTaskFinished(t)))
  }, [])

  const markCancelled = useCallback((id) => {
    cancelledRef.current.add(id)
  }, [])

  const isCancelled = useCallback((id) => cancelledRef.current.has(id), [])

  const isSourceBusy = (sourceKey) =>
    tasks.some((t) => t.sourceKey === sourceKey && !isTaskFinished(t))

  return {
    tasks,
    startTask,
    updateTask,
    dismissTask,
    clearFinishedTasks,
    markCancelled,
    isCancelled,
    isSourceBusy,
  }
}
