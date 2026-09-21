import { useState, useRef, useEffect } from 'react'
import SystemHeader from './components/SystemHeader'
import LinkDownloader from './components/LinkDownloader'
import AccountDownloader from './components/AccountDownloader'
import DownloadHistoryModal from './components/DownloadHistoryModal'
import CookieManager from './components/CookieManager'
import ToolsManagerModal from './components/ToolsManagerModal'
import './App.css'

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
  const [toastMessage, setToastMessage] = useState('')
  const [cookieRefreshKey, setCookieRefreshKey] = useState(0)
  const toastTimer = useRef(null)

  // ── Shared download options (dùng chung cho cả 3 chế độ tải) ──────────────
  const [videoContainer, setVideoContainer] = useState(() => getLS('dl_video_container', 'auto'))
  const [accelerate, setAccelerate] = useState(() => getLS('dl_accelerate', false))
  const [embedMetadata, setEmbedMetadata] = useState(() => getLS('dl_embed_metadata', true))
  const [embedThumbnail, setEmbedThumbnail] = useState(() => getLS('dl_embed_thumbnail', false))

  const handleSetVideoContainer = (v) => { setVideoContainer(v); setLS('dl_video_container', v) }
  const handleSetAccelerate = (v) => { setAccelerate(v); setLS('dl_accelerate', v) }
  const handleSetEmbedMetadata = (v) => { setEmbedMetadata(v); setLS('dl_embed_metadata', v) }
  const handleSetEmbedThumbnail = (v) => { setEmbedThumbnail(v); setLS('dl_embed_thumbnail', v) }

  const sharedDlOptions = {
    videoContainer,
    accelerate,
    embedMetadata,
    embedThumbnail,
    onVideoContainerChange: handleSetVideoContainer,
    onAccelerateChange: handleSetAccelerate,
    onEmbedMetadataChange: handleSetEmbedMetadata,
    onEmbedThumbnailChange: handleSetEmbedThumbnail,
  }
  // ──────────────────────────────────────────────────────────────────────────

  const showToast = (msg) => {
    setToastMessage(msg)
    if (toastTimer.current) clearTimeout(toastTimer.current)
    toastTimer.current = setTimeout(() => setToastMessage(''), 3000)
  }

  useEffect(() => () => {
    if (toastTimer.current) clearTimeout(toastTimer.current)
  }, [])

  const handleCookieUpdated = () => setCookieRefreshKey((k) => k + 1)

  return (
    <div className="desktop-app-container">
      {/* Thanh Header hệ thống */}
      <SystemHeader
        onOpenHistory={() => setIsHistoryOpen(true)}
        onOpenCookies={() => setIsCookieManagerOpen(true)}
        onOpenTools={() => setIsToolsOpen(true)}
        onShowToast={showToast}
        cookieRefreshKey={cookieRefreshKey}
      />

      {/* Vùng làm việc chính: 2 cột chia đôi đối xứng bằng 1 thanh dọc | ở giữa */}
      <main className="desktop-workspace-split">
        {/* Nửa trái: Tải theo liên kết */}
        <section className="workspace-column column-left">
          <LinkDownloader onShowToast={showToast} dlOptions={sharedDlOptions} />
        </section>

        {/* Thanh dọc | phân chia chính giữa */}
        <div className="workspace-divider-vertical" />

        {/* Nửa phải: Tải theo tài khoản */}
        <section className="workspace-column column-right">
          <AccountDownloader onShowToast={showToast} dlOptions={sharedDlOptions} />
        </section>
      </main>

      {/* Floating Toast Notification */}
      {toastMessage && (
        <div className="desktop-floating-toast">
          <span>{toastMessage}</span>
        </div>
      )}

      {/* Modals */}
      <DownloadHistoryModal isOpen={isHistoryOpen} onClose={() => setIsHistoryOpen(false)} />
      <CookieManager
        isOpen={isCookieManagerOpen}
        onClose={() => setIsCookieManagerOpen(false)}
        onCookieUpdated={handleCookieUpdated}
      />
      <ToolsManagerModal isOpen={isToolsOpen} onClose={() => setIsToolsOpen(false)} onShowToast={showToast} />
    </div>
  )
}

export default App
