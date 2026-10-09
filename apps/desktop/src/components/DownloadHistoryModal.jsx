import { useState, useEffect } from 'react'
import { createPortal } from 'react-dom'
import {
  getDownloadHistory,
  clearDownloadHistory,
  getAppSettings,
  previewHistoryPurge,
  setHistoryRetention,
} from '../services/api'
import { IconHistory, IconRefresh, IconTrash, IconClose, IconAlertCircle } from './Icons'
import { errorToast } from '../utils/toasts'

// Backend ghi tên tệp giữ chỗ cho lượt tải lỗi / bị huỷ (chưa có tệp thật)
const PLACEHOLDER_FILE_NAMES = new Set(['failed_download', 'cancelled_download'])

const DEFAULT_RETENTION_DAYS = 30
const MAX_RETENTION_DAYS = 3650

function formatBytes(bytes) {
  if (!bytes || isNaN(bytes) || Number(bytes) === 0) return '-'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  // Kẹp chỉ số: tệp >= 1 TB từng hiện "1.2 undefined"
  const i = Math.min(sizes.length - 1, Math.max(0, Math.floor(Math.log(bytes) / Math.log(k))))
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

export default function DownloadHistoryModal({ isOpen, onClose, onShowToast }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isClearing, setIsClearing] = useState(false)
  const [confirmClear, setConfirmClear] = useState(false)
  const [clearError, setClearError] = useState(null)
  // Tự động xoá lịch sử: undefined = chưa đọc cấu hình, null = tắt, số = số ngày giữ
  const [savedRetention, setSavedRetention] = useState(undefined)
  const [daysDraft, setDaysDraft] = useState(String(DEFAULT_RETENTION_DAYS))
  // Đang chờ xác nhận vì bật/đổi số ngày sẽ xoá ngay `count` mục cũ
  const [pendingRetention, setPendingRetention] = useState(null)
  const [isSavingRetention, setIsSavingRetention] = useState(false)
  const [retentionError, setRetentionError] = useState('')

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

  // Lưu cấu hình (đã được xác nhận nếu có xoá) rồi tải lại danh sách nếu vừa xoá mục cũ
  const applyRetention = async (days) => {
    setIsSavingRetention(true)
    setRetentionError('')
    try {
      const res = await setHistoryRetention(days)
      setSavedRetention(res?.days ?? null)
      setPendingRetention(null)
      const removed = Number(res?.removed) || 0
      onShowToast?.(
        days
          ? {
              type: 'success',
              title: `Sẽ tự động xoá lịch sử cũ hơn ${days} ngày`,
              message: removed > 0 ? `Đã xoá ${removed} mục quá hạn.` : 'Ứng dụng kiểm tra khi khởi động và mỗi 6 giờ.',
            }
          : { type: 'info', title: 'Đã tắt tự động xoá lịch sử' }
      )
      if (removed > 0) await loadHistory()
    } catch (err) {
      setPendingRetention(null)
      setDaysDraft(String(savedRetention || DEFAULT_RETENTION_DAYS))
      onShowToast?.(errorToast(err, { title: 'Chưa lưu được cài đặt tự xoá' }))
    } finally {
      setIsSavingRetention(false)
    }
  }

  // Bật / đổi số ngày: nếu có mục sẽ bị xoá ngay thì hỏi lại trước (không hoàn tác được)
  const requestRetention = async (rawDays) => {
    const days = Number(rawDays)
    if (!Number.isInteger(days) || days < 1 || days > MAX_RETENTION_DAYS) {
      setRetentionError(`Nhập số ngày từ 1 đến ${MAX_RETENTION_DAYS}`)
      return
    }
    setRetentionError('')
    if (days === savedRetention) return
    setIsSavingRetention(true)
    let count = 0
    try {
      count = Number(await previewHistoryPurge(days)) || 0
    } catch (err) {
      console.warn('Không đếm được lịch sử cũ:', err)
    } finally {
      setIsSavingRetention(false)
    }
    if (count > 0) {
      setConfirmClear(false)
      setPendingRetention({ days, count })
    } else {
      await applyRetention(days)
    }
  }

  const handleToggleRetention = (checked) => {
    if (checked) {
      requestRetention(daysDraft || DEFAULT_RETENTION_DAYS)
    } else {
      applyRetention(null)
    }
  }

  // Ô số ngày chỉ áp dụng khi rời ô / nhấn Enter: lưu theo từng phím gõ thì gõ
  // "100" sẽ đi qua "1" và xoá sạch lịch sử cũ hơn 1 ngày.
  const commitDaysDraft = () => {
    if (savedRetention == null || pendingRetention) return
    if (Number(daysDraft) === savedRetention) return
    requestRetention(daysDraft)
  }

  const cancelPendingRetention = () => {
    setPendingRetention(null)
    setDaysDraft(String(savedRetention || DEFAULT_RETENTION_DAYS))
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
    getAppSettings()
      .then((cfg) => {
        if (!active) return
        const days = cfg?.historyRetentionDays ?? null
        setSavedRetention(days)
        if (days) setDaysDraft(String(days))
      })
      .catch((err) => {
        console.warn('Không đọc được cấu hình tự xoá lịch sử:', err)
        if (active) setSavedRetention(null)
      })
    // Dọn về trạng thái chờ khi đóng modal, để lần mở sau hiện spinner thay vì
    // danh sách cũ. Làm trong cleanup nên effect không setState đồng bộ.
    return () => {
      active = false
      setData(null)
      setError(null)
      setClearError(null)
      setIsLoading(true)
      setSavedRetention(undefined)
      setPendingRetention(null)
      setRetentionError('')
    }
  }, [isOpen])

  // Đóng modal khi bấm phím Escape
  useEffect(() => {
    if (!isOpen) return

    const handleKeyDown = (e) => {
      if (e.key === 'Escape') {
        if (pendingRetention) {
          setPendingRetention(null)
          setDaysDraft(String(savedRetention || DEFAULT_RETENTION_DAYS))
        } else if (confirmClear) {
          setConfirmClear(false)
        } else {
          onClose()
        }
      }
    }

    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [isOpen, onClose, confirmClear, pendingRetention, savedRetention])

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

        {pendingRetention && (
          <div className="history-confirm-banner">
            <div className="history-confirm-message">
              <IconAlertCircle className="w-4 h-4 text-rose-400 flex-shrink-0" />
              <span>
                Có {pendingRetention.count} mục lịch sử cũ hơn {pendingRetention.days} ngày sẽ bị xoá ngay. Tiếp tục?
              </span>
            </div>
            <div className="history-confirm-actions">
              <button
                type="button"
                className="btn-secondary-action"
                onClick={cancelPendingRetention}
                disabled={isSavingRetention}
              >
                Hủy
              </button>
              <button
                type="button"
                className="btn-danger-action"
                onClick={() => applyRetention(pendingRetention.days)}
                disabled={isSavingRetention}
              >
                {isSavingRetention ? 'Đang xóa...' : 'Xóa & bật tự động'}
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
                    // Lượt lỗi / huỷ không có tệp: hiện tiêu đề nội dung thay vì "failed_download"
                    const hasRealFile = item.file_name && !PLACEHOLDER_FILE_NAMES.has(item.file_name)
                    const displayName = hasRealFile ? item.file_name : item.media_title || 'Không rõ tên'
                    return (
                      <tr key={item.id || idx}>
                        <td className="cell-muted cell-mono">{idx + 1}</td>
                        <td className="cell-filename" title={item.media_title || displayName}>
                          <div className="file-name-text">{displayName}</div>
                          {item.media_title && item.media_title !== displayName && (
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
                          <span className={`tag-status ${['success', 'failed', 'cancelled'].includes(item.status) ? item.status : 'unknown'}`}>
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
          <div
            className="history-retention-control"
            title="Ứng dụng tự xoá các mục cũ hơn số ngày này khi khởi động và mỗi 6 giờ (gồm cả danh sách liên kết đã phân tích)."
          >
            <label className="history-retention-toggle">
              <input
                type="checkbox"
                checked={Boolean(pendingRetention) || savedRetention != null}
                onChange={(e) => handleToggleRetention(e.target.checked)}
                disabled={savedRetention === undefined || isSavingRetention || Boolean(pendingRetention)}
              />
              <span>Tự động xoá lịch sử cũ hơn</span>
            </label>
            <input
              type="number"
              className="history-retention-days"
              min={1}
              max={MAX_RETENTION_DAYS}
              value={daysDraft}
              onChange={(e) => {
                setDaysDraft(e.target.value)
                setRetentionError('')
              }}
              onBlur={commitDaysDraft}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault()
                  commitDaysDraft()
                }
              }}
              disabled={savedRetention === undefined || isSavingRetention || Boolean(pendingRetention)}
              aria-label="Số ngày giữ lịch sử"
            />
            <span>ngày</span>
            {isSavingRetention && <span className="minimal-spinner" aria-label="Đang lưu" />}
            {retentionError && <span className="history-retention-error">{retentionError}</span>}
          </div>
          <button type="button" className="btn-footer-close" onClick={onClose} title="Đóng (ESC)">
            Đóng
          </button>
        </div>
      </div>
    </div>,
    document.body,
  )
}
