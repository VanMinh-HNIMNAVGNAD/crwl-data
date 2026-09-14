import { useState, useEffect } from 'react'
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
      onShowToast?.(err.message || 'Lỗi khi cập nhật yt-dlp')
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
      onShowToast?.(err.message || 'Lỗi khi cập nhật gallery-dl')
    } finally {
      setUpdatingTool(null)
    }
  }

  const toolsList = [
    {
      id: 'ytdlp',
      name: 'yt-dlp',
      role: 'Engine Video & Audio',
      desc: 'Bóc tách video 4K/1080p, audio, playlist YouTube, TikTok, Facebook...',
      data: toolsData?.ytdlp,
      canUpdate: true,
      onUpdate: handleUpdateYtdlp,
    },
    {
      id: 'gallery_dl',
      name: 'gallery-dl',
      role: 'Engine Album & Ảnh',
      desc: 'Bóc tách album ảnh chất lượng gốc Instagram, Pinterest, X, Reddit...',
      data: toolsData?.gallery_dl,
      canUpdate: true,
      onUpdate: handleUpdateGalleryDl,
    },
    {
      id: 'ffmpeg',
      name: 'FFmpeg',
      role: 'Bộ xử lý đa phương tiện',
      desc: 'Ghép nối luồng video + audio độ nét cao, cắt clip, chuyển đổi format',
      data: toolsData?.ffmpeg,
      canUpdate: false,
    },
    {
      id: 'ffprobe',
      name: 'FFprobe',
      role: 'Phân tích Media Stream',
      desc: 'Đọc thông số kỹ thuật bitrate, codec hình ảnh và âm thanh',
      data: toolsData?.ffprobe,
      canUpdate: false,
    },
    {
      id: 'aria2c',
      name: 'aria2c',
      role: 'Bộ tăng tốc tải đa luồng',
      desc: 'Tăng tốc độ tải file lên gấp 5-10 lần với đa kết nối song song',
      data: toolsData?.aria2c,
      canUpdate: false,
    },
    {
      id: 'node',
      name: 'Node.js',
      role: 'Runtime JavaScript',
      desc: 'Hỗ trợ yt-dlp giải mã chữ ký JavaScript n-sig challenges của YouTube',
      data: toolsData?.node,
      canUpdate: false,
    },
    {
      id: 'python3',
      name: 'Python 3',
      role: 'Lõi điều phối (Sidecar)',
      desc: 'Môi trường chạy IPC Sidecar Worker và bộ bóc tách dự phòng Web Scraper',
      data: toolsData?.python3,
      canUpdate: false,
    },
  ]

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal-card tools-modal-card" onClick={(e) => e.stopPropagation()}>
        {/* Modal Header */}
        <div className="modal-header">
          <div className="modal-title-group">
            <IconSettings className="w-4 h-4 text-emerald-400" />
            <h3 className="modal-title">Công cụ &amp; Engine Hệ Thống</h3>
          </div>
          <div className="modal-actions-right">
            <button
              type="button"
              className="btn-refresh-small"
              onClick={fetchStatus}
              disabled={loading}
              title="Quét lại công cụ"
            >
              <IconRefresh className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            </button>
            <button type="button" className="btn-modal-close" onClick={onClose} title="Đóng">
              <IconClose className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Modal Body */}
        <div className="modal-body tools-modal-body">
          <p className="tools-modal-hint">
            Hệ thống sử dụng các công cụ nhị phân local để bóc tách và tải dữ liệu trực tiếp với tốc độ tối đa.
          </p>

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
                    <p className="tool-desc">{tool.desc}</p>
                    {tool.data?.path && (
                      <span className="tool-path-code" title={tool.data.path}>
                        {tool.data.path}
                      </span>
                    )}
                  </div>

                  {tool.canUpdate && isInstalled && (
                    <div className="tool-actions-right">
                      <button
                        type="button"
                        className="btn-update-tool"
                        onClick={tool.onUpdate}
                        disabled={Boolean(updatingTool)}
                      >
                        {isUpdating ? (
                          <>
                            <span className="minimal-spinner" />
                            <span>Đang cập nhật...</span>
                          </>
                        ) : (
                          <>
                            <IconRefresh className="w-3 h-3" />
                            <span>Cập nhật</span>
                          </>
                        )}
                      </button>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      </div>
    </div>
  )
}
