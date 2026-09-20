import { useState, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { getDownloadHistory, clearDownloadHistory } from '../services/api'
import { IconHistory, IconRefresh, IconTrash, IconClose, IconAlertCircle } from './Icons'

function formatBytes(bytes) {
  if (!bytes || isNaN(bytes) || Number(bytes) === 0) return '-'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(2))} ${sizes[i]}`
}

function formatDate(isoStr) {
  if (!isoStr) return ''
  try {
    const d = new Date(isoStr)
    return d.toLocaleString('vi-VN', {
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      day: '2-digit',
      month: '2-digit',
      year: 'numeric',
    })
  } catch {
    return isoStr
  }
}

export default function DownloadHistoryModal({ isOpen, onClose }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isClearing, setIsClearing] = useState(false)
  const [confirmClear, setConfirmClear] = useState(false)
  const [clearError, setClearError] = useState(null)

  const loadHistory = async () => {
    setIsLoading(true)
    setError(null)
    try {
      const res = await getDownloadHistory(50)
      setData(res)
    } catch (err) {
      console.error('Lỗi khi tải lịch sử:', err)
      setError(err.message || 'Lỗi khi tải lịch sử')
    } finally {
      setIsLoading(false)
    }
  }

  const handleClearHistory = async () => {
    setIsClearing(true)
    try {
      // Backend trả false khi không có kết nối DB hoặc câu lệnh xoá lỗi. Trước đây
      // giá trị này bị bỏ qua nên UI luôn hiện danh sách rỗng như thể đã xoá xong.
      const { success } = await clearDownloadHistory()
      if (!success) {
        setClearError('Không xóa được lịch sử — chưa kết nối được cơ sở dữ liệu. Danh sách vẫn giữ nguyên.')
        setConfirmClear(false)
        return
      }
      setData({ total: 0, history: [] })
      setClearError(null)
      setConfirmClear(false)
    } catch (err) {
      console.error('Lỗi khi xóa lịch sử:', err)
      setClearError(err?.message || 'Lỗi khi xóa lịch sử')
    } finally {
      setIsClearing(false)
    }
  }

  useEffect(() => {
    if (!isOpen) return undefined
    let active = true
    getDownloadHistory(50)
      .then((res) => {
        if (!active) return
        setConfirmClear(false)
        setData(res)
        setError(null)
        setIsLoading(false)
      })
      .catch((err) => {
        if (!active) return
        console.warn('Lỗi khi tải lịch sử:', err)
        setError(err.message || 'Lỗi khi tải lịch sử')
        setIsLoading(false)
      })
    // Dọn về trạng thái chờ khi đóng modal, để lần mở sau hiện spinner thay vì
    // danh sách cũ. Làm trong cleanup nên effect không setState đồng bộ.
    return () => {
      active = false
      setData(null)
      setError(null)
      setClearError(null)
      setIsLoading(true)
    }
  }, [isOpen])

  // Đóng modal khi bấm phím Escape
  useEffect(() => {
    if (!isOpen) return

    const handleKeyDown = (e) => {
      if (e.key === 'Escape') {
        if (confirmClear) {
          setConfirmClear(false)
        } else {
          onClose()
        }
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [isOpen, onClose, confirmClear])

  // Khóa cuộn trang nền khi mở modal
  useEffect(() => {
    if (isOpen) {
      const originalOverflow = document.body.style.overflow
      document.body.style.overflow = 'hidden'
      return () => {
        document.body.style.overflow = originalOverflow
      }
    }
  }, [isOpen])

  if (!isOpen) return null

  const items = data?.history || []

  return createPortal(
    <div
      className="history-modal-overlay"
      onPointerDown={(e) => {
        // Bấm ra ngoài phần nội dung sẽ đóng modal ngay lập tức
        if (e.target === e.currentTarget) {
          onClose()
        }
      }}
      role="dialog"
      aria-modal="true"
      aria-label="Lịch sử tải xuống"
    >
      <div className="history-modal-card">
        {/* Header modal */}
        <div className="history-modal-header">
          <div className="history-title-row">
            <div className="history-icon-badge">
              <IconHistory className="w-4 h-4 text-blue-400" />
            </div>
            <h3 className="history-modal-heading">Lịch sử tải xuống</h3>
            {items.length > 0 && (
              <span className="history-counter-pill">{items.length} tệp</span>
            )}
          </div>

          <div className="history-header-btn-group">
            {items.length > 0 && !confirmClear && (
              <button
                type="button"
                className="btn-history-clear"
                onClick={() => setConfirmClear(true)}
                disabled={isLoading || isClearing}
                title="Xóa toàn bộ lịch sử tải xuống"
              >
                <IconTrash className="w-3.5 h-3.5" />
                <span>Xóa lịch sử</span>
              </button>
            )}
            <button
              type="button"
              className="btn-history-refresh"
              onClick={loadHistory}
              disabled={isLoading || isClearing}
              title="Cập nhật lại danh sách"
            >
              <IconRefresh className={`w-3.5 h-3.5 ${isLoading ? 'animate-spin' : ''}`} />
              <span>{isLoading ? 'Đang đọc...' : 'Làm mới'}</span>
            </button>
            <button
              type="button"
              className="modal-close-icon-btn"
              onClick={onClose}
              title="Đóng hộp thoại (ESC)"
              aria-label="Đóng"
            >
              <IconClose className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Khung xác nhận xóa an toàn nội bộ */}
        {confirmClear && (
          <div className="history-confirm-banner">
            <div className="history-confirm-message">
              <IconAlertCircle className="w-4 h-4 text-rose-400 flex-shrink-0" />
              <span>Bạn có chắc chắn muốn xóa toàn bộ lịch sử tải xuống? Thao tác này không thể hoàn tác.</span>
            </div>
            <div className="history-confirm-actions">
              <button
                type="button"
                className="btn-secondary-action"
                onClick={() => setConfirmClear(false)}
                disabled={isClearing}
              >
                Hủy
              </button>
              <button
                type="button"
                className="btn-danger-action"
                onClick={handleClearHistory}
                disabled={isClearing}
              >
                {isClearing ? 'Đang xóa...' : 'Xác nhận xóa'}
              </button>
            </div>
          </div>
        )}

        {clearError && (
          <div className="history-confirm-banner history-clear-error">
            <div className="history-confirm-message">
              <IconAlertCircle className="w-4 h-4 text-rose-400 flex-shrink-0" />
              <span>{clearError}</span>
            </div>
            <div className="history-confirm-actions">
              <button type="button" className="btn-secondary-action" onClick={() => setClearError(null)}>
                Đóng
              </button>
            </div>
          </div>
        )}

        {/* Nội dung danh sách */}
        <div className="history-modal-body">
          {isLoading && !data && !error ? (
            <div className="history-empty-state">
              <span className="spinner-dots" />
              <p>Đang tải dữ liệu từ cơ sở dữ liệu...</p>
            </div>
          ) : error ? (
            <div className="history-empty-state">
              <div className="empty-icon-wrap">
                <IconAlertCircle className="w-8 h-8 text-rose-500" />
              </div>
              <p className="empty-state-text text-rose-400">Không thể tải lịch sử</p>
              <span className="empty-state-hint">{error}</span>
            </div>
          ) : items.length === 0 ? (
            <div className="history-empty-state">
              <div className="empty-icon-wrap">
                <IconHistory className="w-8 h-8 text-slate-500" />
              </div>
              <p className="empty-state-text">Chưa có tệp nào được tải về.</p>
              <span className="empty-state-hint">Các tệp bạn tải xuống sẽ xuất hiện tại đây.</span>
            </div>
          ) : (
            <div className="history-table-container">
              <table className="history-data-table">
                <thead>
                  <tr>
                    <th style={{ width: '40px' }}>#</th>
                    <th>Tên tệp</th>
                    <th style={{ width: '110px' }}>Nền tảng</th>
                    <th style={{ width: '100px' }}>Dung lượng</th>
                    <th style={{ width: '110px' }}>Trạng thái</th>
                    <th style={{ width: '160px' }}>Thời gian</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((item, idx) => {
                    const isSuccess = item.status === 'success'
                    return (
                      <tr key={item.id || idx}>
                        <td className="cell-muted cell-mono">{idx + 1}</td>
                        <td className="cell-filename" title={item.media_title || item.file_name}>
                          <div className="file-name-text">{item.file_name}</div>
                          {item.media_title && item.media_title !== item.file_name && (
                            <div className="file-title-sub">{item.media_title}</div>
                          )}
                        </td>
                        <td>
                          <span className="tag-platform">
                            {item.platform ? item.platform.toUpperCase() : 'OTHER'}
                          </span>
                        </td>
                        <td className="cell-mono">
                          {formatBytes(Number(item.file_size_bytes))}
                        </td>
                        <td>
                          <span className={`tag-status ${item.status || 'unknown'}`}>
                            {isSuccess ? 'Hoàn tất' : item.status === 'cancelled' ? 'Đã hủy' : 'Lỗi'}
                          </span>
                        </td>
                        <td className="cell-time cell-mono">
                          {formatDate(item.downloaded_at)}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Footer modal */}
        <div className="history-modal-footer">
          <div className="footer-hint-row">
            <span className="kbd-shortcut-chip">ESC</span>
            <span className="footer-hint">hoặc nhấp chuột ra ngoài để đóng</span>
          </div>
          <button type="button" className="btn-footer-close" onClick={onClose}>
            Đóng
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
