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
  askForDownloadDirectory,
} from '../services/api'
import { IconClose, IconRefresh, IconCheck, IconSettings, IconInfo, IconDownload } from './Icons'

const IS_WINDOWS = typeof navigator !== 'undefined' && /windows/i.test(navigator.userAgent || '')

// Lệnh cài gợi ý khi công cụ thiếu. Trước đây luôn là "sudo apt install ...",
// sai hoàn toàn trên bản Windows.
const INSTALL_HINTS = IS_WINDOWS
  ? {
      ytdlp: 'winget install yt-dlp.yt-dlp',
      gallery_dl: 'pip install gallery-dl',
      ffmpeg: 'winget install Gyan.FFmpeg',
      ffprobe: 'winget install Gyan.FFmpeg',
      aria2c: 'winget install aria2.aria2',
      node: 'winget install OpenJS.NodeJS.LTS',
      python3: 'winget install Python.Python.3.12',
    }
  : {
      ytdlp: 'pipx install yt-dlp',
      gallery_dl: 'pipx install gallery-dl',
      ffmpeg: 'sudo apt install ffmpeg',
      ffprobe: 'sudo apt install ffmpeg',
      aria2c: 'sudo apt install aria2',
      node: 'sudo apt install nodejs',
      python3: 'sudo apt install python3',
    }

const SIDECAR_STATE_LABELS = {
  uninitialized: 'chưa khởi tạo',
  starting: 'đang khởi động',
  running: 'đang chạy',
  failed: 'ĐÃ DỪNG VÌ LỖI',
  stopped: 'đã dừng',
}

const errorText = (err, fallback) => (typeof err === 'string' ? err : err?.message || fallback)

const loadStatus = () =>
  Promise.all([
    getBinaryStatus(),
    getSidecarStatus().catch(() => null),
    getAppSettings().catch(() => null),
  ])

export default function ToolsManagerModal({ isOpen, onClose, onShowToast, onSettingsSaved }) {
  const [toolsData, setToolsData] = useState(null)
  const [statusError, setStatusError] = useState(false)
  const [sidecar, setSidecar] = useState(null)
  const [settings, setSettings] = useState(null)
  const [savingSettings, setSavingSettings] = useState(false)
  // Lần mở đầu tiên đang quét ngay; mỗi lần đóng lại đặt về true để lần mở sau
  // hiện trạng thái "đang quét" thay vì lặng lẽ hiển thị dữ liệu cũ.
  const [loading, setLoading] = useState(true)
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
      const [data, sc, cfg] = await loadStatus()
      if (data) setToolsData(data)
      setStatusError(false)
      setSidecar(sc)
      if (cfg) setSettings(cfg)
    } catch (err) {
      console.warn('Lỗi khi tải trạng thái công cụ:', err)
      setStatusError(true)
      onShowToast?.(errorText(err, 'Lỗi khi tải trạng thái công cụ'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (!isOpen) return
    let active = true
    loadStatus()
      .then(([data, sc, cfg]) => {
        if (!active) return
        if (data) setToolsData(data)
        setStatusError(false)
        setSidecar(sc)
        if (cfg) setSettings(cfg)
      })
      .catch((err) => {
        if (!active) return
        console.warn('Lỗi khi tải trạng thái công cụ:', err)
        setStatusError(true)
        showToastRef.current?.(errorText(err, 'Lỗi khi tải trạng thái công cụ'))
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
      setLoading(true)
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

  // Chưa có kết quả quét lần nào: đừng vẽ mọi công cụ thành "Chưa cài đặt".
  const isChecking = !toolsData && !statusError

  // Cập nhật yt-dlp / gallery-dl. Luôn quét lại sau đó (kể cả khi lỗi) để phiên
  // bản hiển thị khớp với binary thật đang được dùng.
  const runUpdate = async (toolId, startMsg, action, fallbackError) => {
    setUpdatingTool(toolId)
    onShowToast?.(startMsg)
    try {
      const res = await action()
      onShowToast?.(res || 'Đã cập nhật xong.')
    } catch (err) {
      onShowToast?.(errorText(err, fallbackError))
    } finally {
      setUpdatingTool(null)
      await fetchStatus()
    }
  }

  const handleUpdateYtdlp = () =>
    runUpdate('ytdlp', 'Đang cập nhật yt-dlp lên phiên bản mới nhất...', updateYtdlp, 'Lỗi khi cập nhật yt-dlp')

  const handleUpdateGalleryDl = () =>
    runUpdate('gallery_dl', 'Đang cập nhật gallery-dl...', updateGalleryDl, 'Lỗi khi cập nhật gallery-dl')

  // Python worker chết 3 lần liên tiếp sẽ chuyển sang FAILED và KHÔNG bao giờ tự
  // hồi phục. Nút này là lối thoát duy nhất ngoài việc thoát hẳn ứng dụng.
  const handleRestartSidecar = async () => {
    setUpdatingTool('python3')
    onShowToast?.('Đang khởi động lại engine bóc tách (Python worker)...')
    try {
      const msg = await restartSidecar()
      onShowToast?.(msg || 'Đã khởi động lại Python worker.')
    } catch (err) {
      onShowToast?.(errorText(err, 'Lỗi khởi động lại Python worker'))
    } finally {
      setUpdatingTool(null)
      await fetchStatus()
    }
  }

  const patchSetting = (key, value) => setSettings((prev) => ({ ...(prev || {}), [key]: value }))

  const handlePickDownloadDir = async () => {
    try {
      // Chỉ điền vào ô; bấm "Lưu cấu hình" mới ghi thành thư mục mặc định.
      const dir = await askForDownloadDirectory()
      if (dir) patchSetting('downloadDir', dir)
    } catch (err) {
      onShowToast?.(errorText(err, 'Lỗi chọn thư mục'))
    }
  }

  const handleSaveSettings = async () => {
    setSavingSettings(true)
    try {
      // Trường rỗng phải gửi null, nếu không backend coi chuỗi rỗng là giá trị hợp lệ.
      const blank = (v) => (typeof v === 'string' && v.trim() === '' ? null : v ?? null)
      const engineRestarted = await saveAppSettings({
        downloadDir: blank(settings?.downloadDir),
        ytdlpPath: blank(settings?.ytdlpPath),
        galleryDlPath: blank(settings?.galleryDlPath),
        proxy: blank(settings?.proxy),
        schemaVersion: settings?.schemaVersion ?? 1,
      })
      onShowToast?.(
        engineRestarted
          ? 'Đã lưu cấu hình và khởi động lại engine bóc tách để áp dụng đường dẫn công cụ / proxy mới.'
          : 'Đã lưu cấu hình.'
      )
      // Thanh trên cùng đang hiển thị thư mục tải mặc định — báo để nó đọc lại.
      onSettingsSaved?.()
      await fetchStatus()
    } catch (err) {
      onShowToast?.(errorText(err, 'Lỗi khi lưu cấu hình'))
    } finally {
      setSavingSettings(false)
    }
  }

  const showInstallHint = (tool) => {
    const hint = INSTALL_HINTS[tool.id]
    onShowToast?.(hint ? `${tool.name} chưa được cài. Cài bằng: ${hint}` : `${tool.name} chưa được cài đặt`)
  }

  // Đây là các gói của hệ điều hành, app không tự cập nhật — chỉ nêu đúng những gì
  // thực sự đọc được, và chỉ cách cài khi thiếu.
  const handleCheckSystemTool = (tool) => {
    if (!tool.data?.is_installed) {
      showInstallHint(tool)
      return
    }
    const ver = tool.data?.version ? `phiên bản ${tool.data.version}` : 'không đọc được phiên bản'
    const path = tool.data?.path || 'không rõ đường dẫn'
    const how = IS_WINDOWS ? 'cập nhật bằng trình cài đặt / winget' : 'cập nhật qua trình quản lý gói của hệ điều hành'
    onShowToast?.(`${tool.name}: ${ver} — ${path} (${how})`)
  }

  const toolsList = [
    { id: 'ytdlp', name: 'yt-dlp', role: 'Engine Video & Audio', data: toolsData?.ytdlp, updatable: true, onUpdate: handleUpdateYtdlp },
    { id: 'gallery_dl', name: 'gallery-dl', role: 'Engine Album & Ảnh', data: toolsData?.gallery_dl, updatable: true, onUpdate: handleUpdateGalleryDl },
    { id: 'ffmpeg', name: 'FFmpeg', role: 'Bộ ghép luồng & Audio', data: toolsData?.ffmpeg },
    { id: 'ffprobe', name: 'FFprobe', role: 'Phân tích Media Stream', data: toolsData?.ffprobe },
    { id: 'aria2c', name: 'aria2c', role: 'Bộ tăng tốc tải đa luồng', data: toolsData?.aria2c },
    { id: 'node', name: 'Node.js', role: 'Runtime JavaScript n-sig', data: toolsData?.node },
    {
      id: 'python3',
      name: 'Python 3',
      role: sidecar
        ? `Lõi Sidecar Worker — ${SIDECAR_STATE_LABELS[sidecar.state] || sidecar.state}`
        : 'Lõi Sidecar Worker',
      data: toolsData?.python3,
      restartable: true,
    },
  ]

  // Nút hành động của từng dòng: cập nhật / khởi động lại / hướng dẫn cài / chi tiết
  const actionOf = (tool, isInstalled) => {
    if (tool.restartable) {
      return { label: 'Khởi động lại', icon: IconRefresh, onClick: handleRestartSidecar }
    }
    if (tool.updatable && isInstalled) {
      return { label: 'Cập nhật', icon: IconDownload, onClick: tool.onUpdate }
    }
    if (!isInstalled) {
      return { label: 'Cách cài', icon: IconInfo, onClick: () => showInstallHint(tool) }
    }
    return { label: 'Chi tiết', icon: IconInfo, onClick: () => handleCheckSystemTool(tool) }
  }

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
              title="Quét lại phiên bản & đường dẫn công cụ"
            >
              <IconRefresh className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
            </button>
            <button type="button" className="btn-modal-close" onClick={onClose} title="Đóng">
              <IconClose className="w-4 h-4" />
            </button>
          </div>
        </div>

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
              const isBusy = updatingTool === tool.id
              const action = actionOf(tool, isInstalled)
              const ActionIcon = action.icon
              const cardState = isChecking ? '' : isInstalled ? 'is-active' : 'is-missing'

              return (
                <div key={tool.id} className={`tool-item-card ${cardState}`}>
                  <div className="tool-info-left">
                    <div className="tool-name-line">
                      <strong className="tool-name">{tool.name}</strong>
                      <span className="tool-role-tag">{tool.role}</span>
                      {isChecking ? (
                        <span className="tool-status-badge badge-checking">Đang kiểm tra...</span>
                      ) : !toolsData ? (
                        <span className="tool-status-badge badge-checking">Không rõ</span>
                      ) : (
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
                      )}
                    </div>
                    {/* Đường dẫn THẬT đang được dùng — chính binary mà nút Cập nhật sẽ nâng cấp */}
                    {tool.data?.path && (
                      <span className="tool-path-code" title={tool.data.path}>
                        {tool.data.path}
                      </span>
                    )}
                  </div>

                  <div className="tool-actions-right">
                    <button
                      type="button"
                      className="btn-update-tool"
                      onClick={action.onClick}
                      disabled={Boolean(updatingTool) || (!toolsData && !tool.restartable)}
                    >
                      {isBusy ? (
                        <>
                          <span className="minimal-spinner" />
                          <span>Đang xử lý...</span>
                        </>
                      ) : (
                        <>
                          <ActionIcon className="w-3 h-3" />
                          <span>{action.label}</span>
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
              <span className="tools-setting-label">Proxy</span>
              <input
                type="text"
                className="tools-setting-input"
                placeholder="http://127.0.0.1:8080 hoặc socks5://... — để trống = không dùng proxy"
                value={settings?.proxy || ''}
                onChange={(e) => patchSetting('proxy', e.target.value)}
              />
            </label>

            <p className="tools-setting-hint">
              Lưu tại <code>~/.config/crwl/settings.json</code>. Proxy áp dụng cho cả bóc tách lẫn tải xuống.
              Đổi đường dẫn công cụ hoặc proxy sẽ khởi động lại engine bóc tách (lượt bóc tách đang chạy bị ngắt).
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
