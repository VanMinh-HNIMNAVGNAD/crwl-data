import { useState, useEffect, useRef } from 'react'
import { IconDownload, IconCheck, IconClose } from './Icons'

function formatSeconds(secs) {
  if (!Number.isFinite(secs) || secs < 0) return '00:00'
  const h = Math.floor(secs / 3600)
  const m = Math.floor((secs % 3600) / 60)
  const s = Math.floor(secs % 60)
  const mm = m.toString().padStart(2, '0')
  const ss = s.toString().padStart(2, '0')
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`
}

export default function DownloadProgressCard({
  progress,
  title,
  startTime,
  onDismiss,
  customStatusText,
}) {
  const status = progress?.status
  const isDone = status === 'completed'
  const isError = status === 'error'
  const isFinished = isDone || isError

  const [elapsed, setElapsed] = useState(0)
  // Giữ lại tổng thời gian của lần tải vừa xong để không bị reset về 00:00
  const frozenRef = useRef(null)

  useEffect(() => {
    if (!startTime) return undefined
    // Đếm giờ chỉ chạy khi còn đang tải. Trước đây bộ đếm tiếp tục chạy cả sau
    // khi đã báo hoàn tất, nên thời gian hiển thị cứ tăng vô nghĩa.
    if (isFinished) {
      if (frozenRef.current == null) {
        frozenRef.current = Math.max(0, Math.floor((Date.now() - startTime) / 1000))
        setElapsed(frozenRef.current)
      }
      return undefined
    }

    frozenRef.current = null
    setElapsed(Math.max(0, Math.floor((Date.now() - startTime) / 1000)))
    const interval = setInterval(() => {
      setElapsed(Math.max(0, Math.floor((Date.now() - startTime) / 1000)))
    }, 1000)
    return () => clearInterval(interval)
  }, [startTime, isFinished])

  if (!progress && !customStatusText) return null

  const percent = Math.min(100, Math.max(0, Number(progress?.percent) || 0))
  const savedPath = progress?.filePath || progress?.file_path
  const isPreparing = status === 'preparing'
  const isProcessing = status === 'processing'

  const statusText =
    customStatusText ||
    (isDone
      ? '✓ Tải hoàn tất thành công!'
      : isError
      ? `✕ ${progress?.message || 'Có lỗi xảy ra trong quá trình tải'}`
      : // Ưu tiên mô tả thật do tiến trình tải gửi lên thay vì đoán theo phần trăm
        progress?.phase ||
        (isPreparing ? 'Đang lấy thông tin tệp...' : 'Đang nhận dữ liệu từ máy chủ...'))

  return (
    <div className={`download-progress-card ${isDone ? 'is-completed' : ''} ${isError ? 'is-error' : ''}`}>
      <div className="progress-card-header">
        <div className="progress-card-title-group">
          <div className="progress-status-icon">
            {isDone ? (
              <IconCheck className="w-4 h-4 text-emerald-400" />
            ) : isError ? (
              <IconClose className="w-4 h-4 text-rose-400" />
            ) : (
              <IconDownload className="w-4 h-4 text-blue-400 animate-bounce" />
            )}
          </div>
          <div className="progress-card-text">
            <h4 className="progress-task-name" title={title || 'Tệp tải xuống'}>
              {title ? (title.length > 50 ? `${title.slice(0, 48)}...` : title) : 'Đang xử lý tải xuống...'}
            </h4>
            <span className="progress-sub-status">{statusText}</span>
            {isDone && savedPath && (
              <span className="progress-saved-path" title={savedPath}>
                Đã lưu tại: {savedPath}
              </span>
            )}
          </div>
        </div>

        {isFinished && onDismiss && (
          <button
            type="button"
            className="progress-card-close-btn"
            onClick={onDismiss}
            title="Đóng thông báo"
          >
            <IconClose className="w-3.5 h-3.5" />
          </button>
        )}
      </div>

      {/* Progress Track */}
      <div className="progress-bar-container">
        <div
          className={`progress-bar-fill ${isDone ? 'done-fill' : ''} ${
            isPreparing || isProcessing ? 'is-indeterminate' : ''
          }`}
          style={{ width: `${percent}%` }}
        />
      </div>

      {/* 4 Metrics Stats Grid */}
      <div className="progress-metrics-grid">
        <div className="progress-metric-item">
          <span className="metric-label">Tiến trình</span>
          <strong className="metric-value text-blue-400">{percent.toFixed(1)}%</strong>
        </div>

        <div className="progress-metric-item">
          <span className="metric-label">Tốc độ tải</span>
          <strong className="metric-value">{!isFinished && progress?.speed ? progress.speed : '--'}</strong>
        </div>

        <div className="progress-metric-item">
          <span className="metric-label">{isDone ? 'Tổng thời gian' : 'Thời gian tải'}</span>
          <strong className="metric-value">{formatSeconds(elapsed)}</strong>
        </div>

        <div className="progress-metric-item">
          <span className="metric-label">Ước tính còn lại</span>
          <strong className="metric-value text-emerald-400">
            {isFinished ? '--' : progress?.eta || '--'}
          </strong>
        </div>
      </div>
    </div>
  )
}
