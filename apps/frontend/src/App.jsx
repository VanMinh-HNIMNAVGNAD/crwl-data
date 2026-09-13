import { useState, useCallback } from 'react'
import SystemHeader from './components/SystemHeader'
import SingleDownloader from './components/SingleDownloader'
import MultiLinkDownloader from './components/MultiLinkDownloader'
import BulkDownloader from './components/BulkDownloader'
import DownloadHistoryModal from './components/DownloadHistoryModal'
import CookieManager from './components/CookieManager'
import TurnstileGate from './components/TurnstileGate'
import { setTurnstileToken } from './services/api'
import './App.css'

function App() {
  const [isHistoryOpen, setIsHistoryOpen] = useState(false)
  const [isCookieManagerOpen, setIsCookieManagerOpen] = useState(false)

  // Trạng thái xác thực Turnstile (chỉ lưu in-memory, mỗi lần F5 / reload đều bắt buộc xác thực lại)
  const [isTurnstileVerified, setIsTurnstileVerified] = useState(false)

  const handleTurnstileVerify = useCallback((token) => {
    setTurnstileToken(token)
    setIsTurnstileVerified(true)
  }, [])

  return (
    <div className="media-app-layout">
      {/* Cổng xác thực Turnstile: Tự động tải lại mỗi lần vào web hoặc F5 */}
      {!isTurnstileVerified && (
        <TurnstileGate onVerify={handleTurnstileVerify} />
      )}

      <SystemHeader
        onOpenHistory={() => setIsHistoryOpen(true)}
        onOpenCookies={() => setIsCookieManagerOpen(true)}
        isTurnstileVerified={isTurnstileVerified}
      />
      <main className="app-main-content">
        <section className="section-block">
          <div className="section-header">
            <div className="section-header-left">
              <span className="section-index-badge badge-1">01</span>
              <div>
                <h2 className="section-title">Tải theo liên kết đơn lẻ</h2>
                <p className="section-subtitle">
                  Tải video, âm thanh hoặc hình ảnh từ liên kết bài viết đơn lẻ
                </p>
              </div>
            </div>
          </div>
          <SingleDownloader />
        </section>

        <div className="section-divider" />

        <section className="section-block">
          <div className="section-header">
            <div className="section-header-left">
              <span className="section-index-badge badge-2">02</span>
              <div>
                <h2 className="section-title">
                  Tải theo danh sách liên kết
                  <span className="section-title-tag">Tối đa 10 link cùng nền tảng</span>
                </h2>
                <p className="section-subtitle">
                  Điền danh sách tối đa 10 liên kết cùng nền tảng để giải mã và tải phương tiện hàng loạt
                </p>
              </div>
            </div>
          </div>
          <MultiLinkDownloader />
        </section>

        <div className="section-divider" />

        <section className="section-block">
          <div className="section-header">
            <div className="section-header-left">
              <span className="section-index-badge badge-3">03</span>
              <div>
                <h2 className="section-title">Quét &amp; Tải toàn bộ nội dung theo tài khoản</h2>
                <p className="section-subtitle">
                  Quét tự động và tải hàng loạt nội dung theo hồ sơ người dùng hoặc kênh
                </p>
              </div>
            </div>
          </div>
          <BulkDownloader />
        </section>
      </main>

      <DownloadHistoryModal isOpen={isHistoryOpen} onClose={() => setIsHistoryOpen(false)} />
      <CookieManager isOpen={isCookieManagerOpen} onClose={() => setIsCookieManagerOpen(false)} />
    </div>
  )
}

export default App
