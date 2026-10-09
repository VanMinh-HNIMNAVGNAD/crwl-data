import { useEffect, useState } from 'react'
import { IconCheck, IconClose, IconAlertCircle, IconInfo } from './Icons'

const ICONS = { success: IconCheck, error: IconAlertCircle, warning: IconAlertCircle, info: IconInfo }

function ToastItem({ toast, onDismiss }) {
  const [expanded, setExpanded] = useState(false)
  const [hovered, setHovered] = useState(false)
  const [copied, setCopied] = useState(false)

  // Rê chuột vào hoặc đang mở "Chi tiết" thì giữ thông báo lại để đọc cho hết.
  // `version` tăng khi cùng thông báo được gửi lại → bắt đầu đếm lại từ đầu.
  useEffect(() => {
    if (expanded || hovered) return undefined
    const timer = setTimeout(() => onDismiss(toast.id), toast.duration)
    return () => clearTimeout(timer)
  }, [expanded, hovered, toast.id, toast.duration, toast.version, onDismiss])

  const Icon = ICONS[toast.type] || IconInfo

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(toast.detail)
      setCopied(true)
    } catch {
      setCopied(false)
    }
  }

  return (
    <div
      className={`toast-item toast-${toast.type}`}
      role={toast.type === 'error' ? 'alert' : 'status'}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <span className="toast-icon">
        <Icon className="w-4 h-4" />
      </span>
      <div className="toast-body">
        <strong className="toast-title">{toast.title}</strong>
        {toast.message && <p className="toast-message">{toast.message}</p>}
        {(toast.action || toast.detail) && (
          <div className="toast-actions">
            {toast.action && (
              <button
                type="button"
                className="toast-action-btn"
                onClick={() => {
                  toast.action.onClick()
                  onDismiss(toast.id)
                }}
              >
                {toast.action.label}
              </button>
            )}
            {toast.detail && (
              <button type="button" className="toast-link-btn" onClick={() => setExpanded((v) => !v)}>
                {expanded ? 'Ẩn chi tiết' : 'Chi tiết'}
              </button>
            )}
            {expanded && toast.detail && (
              <button type="button" className="toast-link-btn" onClick={handleCopy}>
                {copied ? 'Đã sao chép' : 'Sao chép'}
              </button>
            )}
          </div>
        )}
        {expanded && toast.detail && <pre className="toast-detail">{toast.detail}</pre>}
      </div>
      <button type="button" className="toast-close-btn" onClick={() => onDismiss(toast.id)} aria-label="Đóng thông báo">
        <IconClose className="w-3.5 h-3.5" />
      </button>
    </div>
  )
}

/** Các thông báo đang hiện, mới nhất ở dưới cùng. */
export default function ToastStack({ toasts, onDismiss }) {
  if (!toasts.length) return null
  return (
    <div className="toast-stack" aria-live="polite">
      {toasts.map((toast) => (
        <ToastItem key={toast.id} toast={toast} onDismiss={onDismiss} />
      ))}
    </div>
  )
}
