import { useState, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { getBinaryStatus, updateYtdlp, updateGalleryDl } from '../services/api'
import { IconClose, IconRefresh, IconCheck, IconSettings } from './Icons'

export default function ToolsManagerModal({ isOpen, onClose, onShowToast }) {
  const [toolsData, setToolsData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [updatingTool, setUpdatingTool] = useState(null)

  const fetchStatus = async () => {
    setLoading(true)
    try {
      const data = await getBinaryStatus()
      if (data) setToolsData(data)
    } catch (err) {
      console.warn('Lỗi khi tải trạng thái công cụ:', err)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (isOpen) {
      fetchStatus()
    }
  }, [isOpen])

  // ESC to close
  useEffect(() => {
    if (!isOpen) return
    const handleKeyDown = (e) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [isOpen, onClose])

  if (!isOpen) return null

  const handleUpdateYtdlp = async () => {
    setUpdatingTool('ytdlp')
    onShowToast?.('Đang cập nhật yt-dlp lên phiên bản mới nhất...')
    try {
      const res = await updateYtdlp()
      onShowToast?.(res || 'Đã cập nhật yt-dlp thành công!')
      await fetchStatus()
    } catch (err) {
      onShowToast?.(typeof err === 'string' ? err : err?.message || 'Lỗi khi cập nhật yt-dlp')
    } finally {
      setUpdatingTool(null)
    }
  }

  const handleUpdateGalleryDl = async () => {
    setUpdatingTool('gallery_dl')
    onShowToast?.('Đang cập nhật gallery-dl qua pip...')
    try {
      const res = await updateGalleryDl()
      onShowToast?.(res || 'Đã cập nhật gallery-dl thành công!')
      await fetchStatus()
    } catch (err) {
      onShowToast?.(typeof err === 'string' ? err : err?.message || 'Lỗi khi cập nhật gallery-dl')
    } finally {
      setUpdatingTool(null)
    }
  }

  const handleCheckSystemTool = (tool) => {
    if (!tool.data?.is_installed) {
      onShowToast?.(`${tool.name} chưa được cài đặt trên hệ điều hành`)
      return
    }
    const ver = tool.data?.version ? `v${tool.data.version}` : 'hệ thống'
    onShowToast?.(`✓ ${tool.name} (${ver}) đã là phiên bản mới nhất (Gói hệ thống Linux)`)
  }

  const toolsList = [
    {
      id: 'ytdlp',
      name: 'yt-dlp',
      role: 'Engine Video & Audio',
      data: toolsData?.ytdlp,
      actionLabel: 'Cập nhật',
      onAction: handleUpdateYtdlp,
    },
    {
      id: 'gallery_dl',
      name: 'gallery-dl',
      role: 'Engine Album & Ảnh',
      data: toolsData?.gallery_dl,
      actionLabel: 'Cập nhật',
      onAction: handleUpdateGalleryDl,
    },
    {
      id: 'ffmpeg',
      name: 'FFmpeg',
      role: 'Bộ ghép luồng & Audio',
      data: toolsData?.ffmpeg,
      actionLabel: 'Kiểm tra',
      onAction: () => handleCheckSystemTool({ name: 'FFmpeg', data: toolsData?.ffmpeg }),
    },
    {
      id: 'ffprobe',
      name: 'FFprobe',
      role: 'Phân tích Media Stream',
      data: toolsData?.ffprobe,
      actionLabel: 'Kiểm tra',
      onAction: () => handleCheckSystemTool({ name: 'FFprobe', data: toolsData?.ffprobe }),
    },
    {
      id: 'aria2c',
      name: 'aria2c',
      role: 'Bộ tăng tốc tải đa luồng',
      data: toolsData?.aria2c,
      actionLabel: 'Kiểm tra',
      onAction: () => handleCheckSystemTool({ name: 'aria2c', data: toolsData?.aria2c }),
    },
    {
      id: 'node',
      name: 'Node.js',
      role: 'Runtime JavaScript n-sig',
      data: toolsData?.node,
      actionLabel: 'Kiểm tra',
      onAction: () => handleCheckSystemTool({ name: 'Node.js', data: toolsData?.node }),
    },
    {
      id: 'python3',
      name: 'Python 3',
      role: 'Lõi Sidecar Worker',
      data: toolsData?.python3,
      actionLabel: 'Kiểm tra',
      onAction: () => handleCheckSystemTool({ name: 'Python 3', data: toolsData?.python3 }),
    },
  ]

  return createPortal(
    <div
      className="tools-modal-overlay"
      onPointerDown={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
      role="dialog"
      aria-modal="true"
      aria-label="Công cụ & Engine Hệ Thống"
    >
      <div className="tools-modal-card">
        {/* Modal Header */}
        <div className="tools-modal-header">
          <div className="tools-title-group">
            <IconSettings className="w-4 h-4 text-emerald-400" />
            <h3 className="tools-modal-heading">Công cụ &amp; Engine Hệ Thống</h3>
          </div>
          <div className="tools-actions-right">
            <button
              type="button"
              className="btn-refresh-small"
              onClick={fetchStatus}
              disabled={loading}
              title="Quét lại phiên bản & đường dẫn công cụ (F5)"
            >
              <IconRefresh className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            </button>
            <button type="button" className="btn-modal-close" onClick={onClose} title="Đóng">
              <IconClose className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Modal Body (Đã bỏ toàn bộ mô tả nhỏ bên dưới theo yêu cầu) */}
        <div className="tools-modal-body">
          <div className="tools-list-stack">
            {toolsList.map((tool) => {
              const isInstalled = Boolean(tool.data?.is_installed)
              const version = tool.data?.version
              const isUpdating = updatingTool === tool.id

              return (
                <div key={tool.id} className={`tool-item-card ${isInstalled ? 'is-active' : 'is-missing'}`}>
                  <div className="tool-info-left">
                    <div className="tool-name-line">
                      <strong className="tool-name">{tool.name}</strong>
                      <span className="tool-role-tag">{tool.role}</span>
                      <span className={`tool-status-badge ${isInstalled ? 'badge-installed' : 'badge-not-installed'}`}>
                        {isInstalled ? (
                          <>
                            <IconCheck className="w-3 h-3 inline mr-0.5 text-emerald-400" />
                            <span>{version ? `v${version}` : 'Đã cài'}</span>
                          </>
                        ) : (
                          'Chưa cài đặt'
                        )}
                      </span>
                    </div>
                  </div>

                  <div className="tool-actions-right">
                    <button
                      type="button"
                      className="btn-update-tool"
                      onClick={tool.onAction}
                      disabled={Boolean(updatingTool) || !isInstalled}
                    >
                      {isUpdating ? (
                        <>
                          <span className="minimal-spinner" />
                          <span>Đang kiểm tra...</span>
                        </>
                      ) : (
                        <>
                          <IconRefresh className="w-3 h-3" />
                          <span>{tool.actionLabel}</span>
                        </>
                      )}
                    </button>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      </div>
    </div>,
    document.body
  )
}
