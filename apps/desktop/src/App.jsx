import { useState } from 'react'
import SystemHeader from './components/SystemHeader'
import LinkDownloader from './components/LinkDownloader'
import AccountDownloader from './components/AccountDownloader'
import DownloadHistoryModal from './components/DownloadHistoryModal'
import CookieManager from './components/CookieManager'
import ToolsManagerModal from './components/ToolsManagerModal'
import './App.css'

function App() {
  const [isHistoryOpen, setIsHistoryOpen] = useState(false)
  const [isCookieManagerOpen, setIsCookieManagerOpen] = useState(false)
  const [isToolsOpen, setIsToolsOpen] = useState(false)
  const [toastMessage, setToastMessage] = useState('')
  const [cookieRefreshKey, setCookieRefreshKey] = useState(0)

  const showToast = (msg) => {
    setToastMessage(msg)
    setTimeout(() => setToastMessage(''), 3200)
  }

  const handleCookieUpdated = () => {
    setCookieRefreshKey((k) => k + 1)
  }

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
          <LinkDownloader onShowToast={showToast} />
        </section>

        {/* Thanh dọc | phân chia chính giữa */}
        <div className="workspace-divider-vertical" />

        {/* Nửa phải: Tải theo tài khoản */}
        <section className="workspace-column column-right">
          <AccountDownloader onShowToast={showToast} />
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
