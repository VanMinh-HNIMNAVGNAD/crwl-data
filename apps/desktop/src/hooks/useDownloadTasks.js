import { useCallback, useRef, useState } from 'react'

const FINISHED_STATUSES = new Set(['completed', 'error', 'cancelled'])

export const isTaskFinished = (task) => FINISHED_STATUSES.has(task?.progress?.status)

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
    setTasks((prev) =>
      prev.map((t) => {
        if (t.id !== id) return t
        const value = typeof next === 'function' ? next(t.progress) : next
        if (!value) return t
        // Sự kiện đến muộn từ backend không được "hồi sinh" tác vụ đã huỷ
        if (t.progress?.status === 'cancelled' && value.status !== 'cancelled') return t
        return { ...t, progress: { ...value, id } }
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
