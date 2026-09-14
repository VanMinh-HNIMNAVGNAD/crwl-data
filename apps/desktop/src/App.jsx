import { useState } from 'react'
import SystemHeader from './components/SystemHeader'
import LinkDownloader from './components/LinkDownloader'
import AccountDownloader from './components/AccountDownloader'
import MediaWorkspace from './components/MediaWorkspace'
import DownloadHistoryModal from './components/DownloadHistoryModal'
import CookieManager from './components/CookieManager'
import './App.css'

function App() {
  const [isHistoryOpen, setIsHistoryOpen] = useState(false)
  const [isCookieManagerOpen, setIsCookieManagerOpen] = useState(false)
  const [toastMessage, setToastMessage] = useState('')

  // State quản lý dữ liệu phương tiện
  const [activeWorkspaceTab, setActiveWorkspaceTab] = useState('link') // 'link' | 'account' | 'batch'
  const [singleMedia, setSingleMedia] = useState(null)
  const [batchMedias, setBatchMedias] = useState([])
  const [profileResult, setProfileResult] = useState(null)
  const isGlobalLoading = false

  const showToast = (msg) => {
    setToastMessage(msg)
    setTimeout(() => setToastMessage(''), 3500)
  }

  // Khi bóc tách 1 link thành công
  const handleMediaExtracted = (mediaData) => {
    setSingleMedia(mediaData)
    setActiveWorkspaceTab('link')
  }

  // Khi bóc tách nhiều link thành công
  const handleBatchExtracted = (items) => {
    setBatchMedias(items)
    setActiveWorkspaceTab('batch')
  }

  // Khi quét tài khoản thành công
  const handleProfileCrawled = (profileData) => {
    setProfileResult(profileData)
    setActiveWorkspaceTab('account')
  }

  return (
    <div className="desktop-app-container">
      {/* Thanh Header hệ thống */}
      <SystemHeader
        onOpenHistory={() => setIsHistoryOpen(true)}
        onOpenCookies={() => setIsCookieManagerOpen(true)}
      />

      {/* Vùng làm việc chính: Tối ưu màn hình Laptop 15" */}
      <main className="desktop-main-workspace">
        {/* Nửa trên: Chia đôi 2 cột (Trái: Link Downloader, Phải: Account Downloader) */}
        <div className="workspace-top-split">
          <LinkDownloader
            onMediaExtracted={handleMediaExtracted}
            onBatchExtracted={handleBatchExtracted}
            isGlobalLoading={isGlobalLoading}
            onShowToast={showToast}
          />
          <AccountDownloader
            onProfileCrawled={handleProfileCrawled}
            isGlobalLoading={isGlobalLoading}
            onShowToast={showToast}
          />
        </div>

        {/* Nửa dưới: Toàn màn hình xem trước thumbnail/video và tải về */}
        <div className="workspace-bottom-results">
          <MediaWorkspace
            activeTab={activeWorkspaceTab}
            onTabChange={setActiveWorkspaceTab}
            singleMedia={singleMedia}
            batchMedias={batchMedias}
            profileResult={profileResult}
            onShowToast={showToast}
          />
        </div>
      </main>

      {/* Floating Toast Notification */}
      {toastMessage && (
        <div className="desktop-floating-toast">
          <span>{toastMessage}</span>
        </div>
      )}

      {/* Modals */}
      <DownloadHistoryModal isOpen={isHistoryOpen} onClose={() => setIsHistoryOpen(false)} />
      <CookieManager isOpen={isCookieManagerOpen} onClose={() => setIsCookieManagerOpen(false)} />
    </div>
  )
}

export default App
