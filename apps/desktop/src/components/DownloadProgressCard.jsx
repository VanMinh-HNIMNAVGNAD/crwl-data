import { useState, useEffect } from 'react'
import { IconDownload, IconCheck, IconClose } from './Icons'

function formatSeconds(secs) {
  if (isNaN(secs) || secs < 0) return '00:00'
  const m = Math.floor(secs / 60)
  const s = Math.floor(secs % 60)
  return `${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
}

export default function DownloadProgressCard({
  progress,
  title,
  startTime,
  onDismiss,
  customStatusText,
}) {
  const [elapsed, setElapsed] = useState(0)

  useEffect(() => {
    if (!startTime) return
    const interval = setInterval(() => {
      setElapsed(Math.floor((Date.now() - startTime) / 1000))
    }, 1000)
    return () => clearInterval(interval)
  }, [startTime])

  if (!progress && !customStatusText) return null

  const percent = Math.min(100, Math.max(0, Number(progress?.percent) || 0))
  const isDone = progress?.status === 'completed' || percent >= 100
  const isError = progress?.status === 'error'

  return (
    <div className={`download-progress-card ${isDone ? 'is-completed' : ''} ${isError ? 'is-error' : ''}`}>
      <div className="progress-card-header">
        <div className="progress-card-title-group">
          <div className="progress-status-icon">
            {isDone ? (
              <IconCheck className="w-4 h-4 text-emerald-400" />
            ) : (
              <IconDownload className="w-4 h-4 text-blue-400 animate-bounce" />
            )}
          </div>
          <div className="progress-card-text">
            <h4 className="progress-task-name" title={title || 'Tệp tải xuống'}>
              {title ? (title.length > 50 ? `${title.slice(0, 48)}...` : title) : 'Đang xử lý tải xuống...'}
            </h4>
            <span className="progress-sub-status">
              {customStatusText ||
                (isDone
                  ? '✓ Tải hoàn tất thành công!'
                  : isError
                  ? '✕ Có lỗi xảy ra trong quá trình tải'
                  : percent > 95
                  ? 'Đang ghép luồng tệp hoàn chỉnh qua FFmpeg...'
                  : 'Đang nhận gói tin từ máy chủ...')}
            </span>
          </div>
        </div>

        {(isDone || isError) && onDismiss && (
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
          className={`progress-bar-fill ${isDone ? 'done-fill' : ''}`}
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
          <strong className="metric-value">{progress?.speed ? progress.speed : '--'}</strong>
        </div>

        <div className="progress-metric-item">
          <span className="metric-label">Thời gian tải</span>
          <strong className="metric-value">{formatSeconds(elapsed)}</strong>
        </div>

        <div className="progress-metric-item">
          <span className="metric-label">Ước tính còn lại</span>
          <strong className="metric-value text-emerald-400">
            {isDone ? '00:00' : progress?.eta ? progress.eta : '--'}
          </strong>
        </div>
      </div>
    </div>
  )
}
