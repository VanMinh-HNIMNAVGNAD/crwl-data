import { useState, useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import {
  getBinaryStatus,
  updateYtdlp,
  updateGalleryDl,
  getSidecarStatus,
  restartSidecar,
  getAppSettings,
  saveAppSettings,
  selectDownloadDirectory,
} from '../services/api'
import { IconClose, IconRefresh, IconCheck, IconSettings } from './Icons'

// Tên gói để gợi ý lệnh cài khi công cụ thiếu
const SYSTEM_PACKAGE_HINTS = {
  ffmpeg: 'ffmpeg',
  ffprobe: 'ffmpeg',
  aria2c: 'aria2',
  node: 'nodejs',
  python3: 'python3',
}

const SIDECAR_STATE_LABELS = {
  uninitialized: 'chưa khởi tạo',
  starting: 'đang khởi động',
  running: 'đang chạy',
  failed: 'ĐÃ DỪNG VÌ LỖI',
  stopped: 'đã dừng',
}

export default function ToolsManagerModal({ isOpen, onClose, onShowToast }) {
  const [toolsData, setToolsData] = useState(null)
  const [sidecar, setSidecar] = useState(null)
  const [settings, setSettings] = useState(null)
  const [savingSettings, setSavingSettings] = useState(false)
  const [loading, setLoading] = useState(false)
  const [updatingTool, setUpdatingTool] = useState(null)
  // Giữ callback mới nhất trong ref: đưa thẳng vào deps sẽ khiến effect chạy lại
  // mỗi lần App render (onShowToast được tạo mới mỗi lần).
  const showToastRef = useRef(onShowToast)
  useEffect(() => {
    showToastRef.current = onShowToast
  }, [onShowToast])

  const fetchStatus = async () => {
    setLoading(true)
    try {
      const [data, sc, cfg] = await Promise.all([
        getBinaryStatus(),
        getSidecarStatus().catch(() => null),
        getAppSettings().catch(() => null),
      ])
      if (data) setToolsData(data)
      setSidecar(sc)
      if (cfg) setSettings(cfg)
    } catch (err) {
      console.warn('Lỗi khi tải trạng thái công cụ:', err)
      onShowToast?.(typeof err === 'string' ? err : err?.message || 'Lỗi khi tải trạng thái công cụ')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (!isOpen) return
    let active = true
    Promise.all([
      getBinaryStatus(),
      getSidecarStatus().catch(() => null),
      getAppSettings().catch(() => null),
    ])
      .then(([data, sc, cfg]) => {
        if (!active) return
        if (data) setToolsData(data)
        setSidecar(sc)
        if (cfg) setSettings(cfg)
      })
      .catch((err) => {
        if (active) {
          console.warn('Lỗi khi tải trạng thái công cụ:', err)
          showToastRef.current?.(typeof err === 'string' ? err : err?.message || 'Lỗi khi tải trạng thái công cụ')
        }
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
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

  // Python worker chết 3 lần liên tiếp sẽ chuyển sang FAILED và KHÔNG bao giờ tự
  // hồi phục. Nút này là lối thoát duy nhất ngoài việc thoát hẳn ứng dụng.
  const handleRestartSidecar = async () => {
    setUpdatingTool('python3')
    onShowToast?.('Đang khởi động lại engine bóc tách (Python worker)...')
    try {
      const msg = await restartSidecar()
      onShowToast?.(msg || 'Đã khởi động lại Python worker.')
    } catch (err) {
      onShowToast?.(typeof err === 'string' ? err : err?.message || 'Lỗi khởi động lại Python worker')
    } finally {
      setUpdatingTool(null)
      await fetchStatus()
    }
  }

  // Đây là các gói của hệ điều hành, app KHÔNG tự cập nhật được. Trước đây nút
  // này báo "đã là phiên bản mới nhất" dù chẳng kiểm tra gì — nay chỉ nêu đúng
  // những gì thực sự đọc được, và chỉ cách cài khi thiếu.
  const patchSetting = (key, value) => setSettings((prev) => ({ ...(prev || {}), [key]: value }))

  const handlePickDownloadDir = async () => {
    try {
      const dir = await selectDownloadDirectory()
      if (dir) patchSetting('downloadDir', dir)
    } catch (err) {
      onShowToast?.(typeof err === 'string' ? err : err?.message || 'Lỗi chọn thư mục')
    }
  }

  const handleSaveSettings = async () => {
    setSavingSettings(true)
    try {
      // Trường rỗng phải gửi null, nếu không backend coi chuỗi rỗng là giá trị hợp lệ.
      const blank = (v) => (typeof v === 'string' && v.trim() === '' ? null : v ?? null)
      await saveAppSettings({
        downloadDir: blank(settings?.downloadDir),
        ytdlpPath: blank(settings?.ytdlpPath),
        galleryDlPath: blank(settings?.galleryDlPath),
        databaseUrl: blank(settings?.databaseUrl),
        schemaVersion: settings?.schemaVersion ?? 1,
      })
      onShowToast?.('Đã lưu cấu hình. Đường dẫn công cụ và DATABASE_URL áp dụng ngay.')
      await fetchStatus()
    } catch (err) {
      onShowToast?.(typeof err === 'string' ? err : err?.message || 'Lỗi khi lưu cấu hình')
    } finally {
      setSavingSettings(false)
    }
  }

  const handleCheckSystemTool = (tool) => {
    if (!tool.data?.is_installed) {
      const pkg = SYSTEM_PACKAGE_HINTS[tool.id]
      onShowToast?.(
        pkg
          ? `${tool.name} chưa được cài. Cài bằng: sudo apt install ${pkg}`
          : `${tool.name} chưa được cài đặt trên hệ điều hành`
      )
      return
    }
    const ver = tool.data?.version ? `v${tool.data.version}` : 'không đọc được phiên bản'
    const path = tool.data?.path || 'không rõ đường dẫn'
    onShowToast?.(`${tool.name}: ${ver} — ${path} (gói hệ thống, cập nhật qua trình quản lý gói của Linux)`)
  }

  const toolsList = [
    {
      id: 'ytdlp',
      name: 'yt-dlp',
      role: 'Engine Video & Audio',
      data: toolsData?.ytdlp,
      actionLabel: 'Cập nhật',
      onAction: handleUpdateYtdlp,
      needsInstallToAct: true,
    },
    {
      id: 'gallery_dl',
      name: 'gallery-dl',
      role: 'Engine Album & Ảnh',
      data: toolsData?.gallery_dl,
      actionLabel: 'Cập nhật',
      onAction: handleUpdateGalleryDl,
      needsInstallToAct: true,
    },
    {
      id: 'ffmpeg',
      name: 'FFmpeg',
      role: 'Bộ ghép luồng & Audio',
      data: toolsData?.ffmpeg,
      actionLabel: 'Chi tiết',
      onAction: () => handleCheckSystemTool({ id: 'ffmpeg', name: 'FFmpeg', data: toolsData?.ffmpeg }),
    },
    {
      id: 'ffprobe',
      name: 'FFprobe',
      role: 'Phân tích Media Stream',
      data: toolsData?.ffprobe,
      actionLabel: 'Chi tiết',
      onAction: () => handleCheckSystemTool({ id: 'ffprobe', name: 'FFprobe', data: toolsData?.ffprobe }),
    },
    {
      id: 'aria2c',
      name: 'aria2c',
      role: 'Bộ tăng tốc tải đa luồng',
      data: toolsData?.aria2c,
      actionLabel: 'Chi tiết',
      onAction: () => handleCheckSystemTool({ id: 'aria2c', name: 'aria2c', data: toolsData?.aria2c }),
    },
    {
      id: 'node',
      name: 'Node.js',
      role: 'Runtime JavaScript n-sig',
      data: toolsData?.node,
      actionLabel: 'Chi tiết',
      onAction: () => handleCheckSystemTool({ id: 'node', name: 'Node.js', data: toolsData?.node }),
    },
    {
      id: 'python3',
      name: 'Python 3',
      role: sidecar
        ? `Lõi Sidecar Worker — ${SIDECAR_STATE_LABELS[sidecar.state] || sidecar.state}`
        : 'Lõi Sidecar Worker',
      data: toolsData?.python3,
      actionLabel: 'Khởi động lại',
      onAction: handleRestartSidecar,
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
          {sidecar && !sidecar.healthy && sidecar.state !== 'uninitialized' && (
            <div className="tools-sidecar-alert">
              <strong>⚠ Engine bóc tách (Python worker) đang không hoạt động.</strong>
              <p>
                Mọi thao tác phân tích liên kết và quét tài khoản sẽ thất bại cho tới khi engine chạy lại.
                Hãy cài đặt/khắc phục Python rồi bấm <em>Khởi động lại</em> ở mục Python 3 bên dưới.
              </p>
              {sidecar.lastError && <code className="tools-sidecar-error">{sidecar.lastError}</code>}
            </div>
          )}
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
                      disabled={Boolean(updatingTool) || (!isInstalled && tool.needsInstallToAct)}
                    >
                      {isUpdating ? (
                        <>
                          <span className="minimal-spinner" />
                          <span>Đang xử lý...</span>
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

          {/* Cấu hình ứng dụng — ~/.config/crwl/settings.json */}
          <div className="tools-settings-block">
            <h4 className="tools-settings-heading">Cấu hình ứng dụng</h4>

            <label className="tools-setting-row">
              <span className="tools-setting-label">Thư mục tải mặc định</span>
              <span className="tools-setting-input-group">
                <input
                  type="text"
                  className="tools-setting-input"
                  placeholder="Để trống = thư mục Tải xuống của hệ thống"
                  value={settings?.downloadDir || ''}
                  onChange={(e) => patchSetting('downloadDir', e.target.value)}
                />
                <button type="button" className="btn-refresh-small" onClick={handlePickDownloadDir} title="Chọn thư mục">
                  📁
                </button>
              </span>
            </label>

            <label className="tools-setting-row">
              <span className="tools-setting-label">Đường dẫn yt-dlp</span>
              <input
                type="text"
                className="tools-setting-input"
                placeholder={toolsData?.ytdlp?.path || 'Để trống = tự dò trong PATH'}
                value={settings?.ytdlpPath || ''}
                onChange={(e) => patchSetting('ytdlpPath', e.target.value)}
              />
            </label>

            <label className="tools-setting-row">
              <span className="tools-setting-label">Đường dẫn gallery-dl</span>
              <input
                type="text"
                className="tools-setting-input"
                placeholder={toolsData?.gallery_dl?.path || 'Để trống = tự dò trong PATH'}
                value={settings?.galleryDlPath || ''}
                onChange={(e) => patchSetting('galleryDlPath', e.target.value)}
              />
            </label>

            <label className="tools-setting-row">
              <span className="tools-setting-label">DATABASE_URL</span>
              <input
                type="password"
                className="tools-setting-input"
                placeholder="postgresql://... — để trống thì chạy chế độ Offline"
                value={settings?.databaseUrl || ''}
                onChange={(e) => patchSetting('databaseUrl', e.target.value)}
              />
            </label>

            <p className="tools-setting-hint">
              Lưu tại <code>~/.config/crwl/settings.json</code>. Đổi DATABASE_URL sẽ kết nối lại cơ sở dữ liệu ngay.
            </p>

            <button
              type="button"
              className="btn-update-tool tools-setting-save"
              onClick={handleSaveSettings}
              disabled={savingSettings || !settings}
            >
              {savingSettings ? (
                <>
                  <span className="minimal-spinner" />
                  <span>Đang lưu...</span>
                </>
              ) : (
                <span>Lưu cấu hình</span>
              )}
            </button>
          </div>
        </div>
      </div>
    </div>,
    document.body
  )
}
