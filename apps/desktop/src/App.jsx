import { useState, useRef, useCallback, useMemo, useEffect } from 'react'
import SystemHeader from './components/SystemHeader'
import LinkDownloader from './components/LinkDownloader'
import AccountDownloader from './components/AccountDownloader'
import DownloadHistoryModal from './components/DownloadHistoryModal'
import CookieManager from './components/CookieManager'
import ToolsManagerModal from './components/ToolsManagerModal'
import ToastStack from './components/ToastStack'
import ErrorBoundary from './components/ErrorBoundary'
import { normalizeToast } from './utils/toasts'
import { pendingToolUpdates, toolUpdateKey, toolUpdateToast } from './utils/toolUpdates'
import { checkToolUpdates } from './services/api'
import './App.css'

// Hiện tối đa chừng này thông báo cùng lúc; cái cũ nhất bị đẩy ra trước
const MAX_TOASTS = 3

// Đợi app và engine Python khởi động xong rồi mới kiểm tra bản mới: lệnh này chạy
// `--version` của yt-dlp / gallery-dl, không nên tranh tài nguyên lúc vừa mở.
const UPDATE_CHECK_DELAY_MS = 5000

// ─── Shared download options — persist qua localStorage ───────────────────────
function getLS(key, fallback) {
  try {
    const v = localStorage.getItem(key)
    return v !== null ? JSON.parse(v) : fallback
  } catch {
    return fallback
  }
}
function setLS(key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)) } catch { /* noop */ }
}

function App() {
  const [isHistoryOpen, setIsHistoryOpen] = useState(false)
  const [isCookieManagerOpen, setIsCookieManagerOpen] = useState(false)
  const [isToolsOpen, setIsToolsOpen] = useState(false)
  const [toasts, setToasts] = useState([])
  const toastSeq = useRef(0)
  const [cookieRefreshKey, setCookieRefreshKey] = useState(0)
  // Tăng mỗi khi lưu cấu hình ở modal Công cụ để thanh trên cùng đọc lại thư mục tải
  const [settingsVersion, setSettingsVersion] = useState(0)

  // ── Shared download options (dùng chung cho cả 3 chế độ tải) ──────────────
  const [videoContainer, setVideoContainer] = useState(() => getLS('dl_video_container', 'auto'))
  const [accelerate, setAccelerate] = useState(() => getLS('dl_accelerate', false))
  const [embedMetadata, setEmbedMetadata] = useState(() => getLS('dl_embed_metadata', true))
  const [embedThumbnail, setEmbedThumbnail] = useState(() => getLS('dl_embed_thumbnail', false))
  const [useAria2c, setUseAria2c] = useState(() => getLS('dl_use_aria2c', false))

  const handleSetVideoContainer = useCallback((v) => { setVideoContainer(v); setLS('dl_video_container', v) }, [])
  const handleSetAccelerate = useCallback((v) => { setAccelerate(v); setLS('dl_accelerate', v) }, [])
  const handleSetEmbedMetadata = useCallback((v) => { setEmbedMetadata(v); setLS('dl_embed_metadata', v) }, [])
  const handleSetEmbedThumbnail = useCallback((v) => { setEmbedThumbnail(v); setLS('dl_embed_thumbnail', v) }, [])
  const handleSetUseAria2c = useCallback((v) => { setUseAria2c(v); setLS('dl_use_aria2c', v) }, [])

  // Giữ nguyên object khi không có tuỳ chọn nào đổi: hai khung tải đã `memo`, nên mỗi
  // thông báo (toast) bật/tắt không còn kéo theo việc vẽ lại toàn bộ lưới kết quả.
  const sharedDlOptions = useMemo(
    () => ({
      videoContainer,
      accelerate,
      embedMetadata,
      embedThumbnail,
      useAria2c,
      onVideoContainerChange: handleSetVideoContainer,
      onAccelerateChange: handleSetAccelerate,
      onEmbedMetadataChange: handleSetEmbedMetadata,
      onEmbedThumbnailChange: handleSetEmbedThumbnail,
      onUseAria2cChange: handleSetUseAria2c,
    }),
    [
      videoContainer,
      accelerate,
      embedMetadata,
      embedThumbnail,
      useAria2c,
      handleSetVideoContainer,
      handleSetAccelerate,
      handleSetEmbedMetadata,
      handleSetEmbedThumbnail,
      handleSetUseAria2c,
    ]
  )
  // ──────────────────────────────────────────────────────────────────────────

  // Trước đây chỉ có MỘT toast dạng chuỗi: thông báo sau đè mất thông báo trước
  // (tải song song nhiều tệp thì chỉ thấy cái cuối) và lỗi hiện nguyên văn log.
  // Nay mỗi thông báo có loại (thành công / lỗi / cảnh báo / thông tin), tự ẩn
  // theo độ dài và xếp chồng tối đa MAX_TOASTS cái.
  const showToast = useCallback((input) => {
    const toast = normalizeToast(input)
    if (!toast) return
    setToasts((prev) => {
      // Cùng nội dung đang hiện: làm mới thời gian thay vì xếp thêm một bản sao
      if (prev.some((t) => t.key === toast.key)) {
        return prev.map((t) => (t.key === toast.key ? { ...toast, id: t.id, version: t.version + 1 } : t))
      }
      toastSeq.current += 1
      return [...prev, { ...toast, id: toastSeq.current, version: 0 }].slice(-MAX_TOASTS)
    })
  }, [])

  const dismissToast = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id))
  }, [])

  // Báo bản mới của yt-dlp / gallery-dl mỗi bản đúng một lần. Lỗi mạng thì im lặng:
  // đây chỉ là thông báo phụ, không được làm phiền lúc mở app.
  useEffect(() => {
    let active = true
    const timer = setTimeout(() => {
      checkToolUpdates()
        .then((report) => {
          if (!active) return
          const pending = pendingToolUpdates(report)
          const key = toolUpdateKey(pending)
          if (!pending.length || getLS('tool_update_notified', '') === key) return
          setLS('tool_update_notified', key)
          showToast(toolUpdateToast(pending, () => setIsToolsOpen(true)))
        })
        .catch((err) => console.warn('Không kiểm tra được bản mới của công cụ:', err))
    }, UPDATE_CHECK_DELAY_MS)
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [showToast])

  const handleCookieUpdated = () => setCookieRefreshKey((k) => k + 1)

  return (
    <div className="desktop-app-container">
      {/* Thanh Header hệ thống */}
      <SystemHeader
        onOpenHistory={() => setIsHistoryOpen(true)}
        onOpenCookies={() => setIsCookieManagerOpen(true)}
        onOpenTools={() => setIsToolsOpen(true)}
        cookieRefreshKey={cookieRefreshKey}
        settingsVersion={settingsVersion}
      />

      {/* Vùng làm việc chính: 2 cột chia đôi đối xứng bằng 1 thanh dọc | ở giữa */}
      <main className="desktop-workspace-split">
        {/* Nửa trái: Tải theo liên kết */}
        <section className="workspace-column column-left">
          <ErrorBoundary name="LinkDownloader" title="Khung “Tải theo liên kết” gặp lỗi hiển thị">
            <LinkDownloader onShowToast={showToast} dlOptions={sharedDlOptions} />
          </ErrorBoundary>
        </section>

        {/* Thanh dọc | phân chia chính giữa */}
        <div className="workspace-divider-vertical" />

        {/* Nửa phải: Tải theo tài khoản */}
        <section className="workspace-column column-right">
          <ErrorBoundary name="AccountDownloader" title="Khung “Tải theo tài khoản” gặp lỗi hiển thị">
            <AccountDownloader onShowToast={showToast} dlOptions={sharedDlOptions} />
          </ErrorBoundary>
        </section>
      </main>

      {/* Thông báo nổi */}
      <ToastStack toasts={toasts} onDismiss={dismissToast} />

      {/* Modals */}
      <DownloadHistoryModal
        isOpen={isHistoryOpen}
        onClose={() => setIsHistoryOpen(false)}
        onShowToast={showToast}
      />
      <CookieManager
        isOpen={isCookieManagerOpen}
        onClose={() => setIsCookieManagerOpen(false)}
        onCookieUpdated={handleCookieUpdated}
      />
      <ToolsManagerModal
        isOpen={isToolsOpen}
        onClose={() => setIsToolsOpen(false)}
        onShowToast={showToast}
        onSettingsSaved={() => setSettingsVersion((v) => v + 1)}
      />
    </div>
  )
}

export default App
