import { useState, useEffect, useRef } from 'react'
import { getBrowsersList, getActiveBrowser, setActiveBrowser, getCookieStatus } from '../services/api'
import { IconGlobe, IconChevronDown, IconCheck, IconRefresh, IconHistory, IconShieldCheck } from './Icons'

export default function SystemHeader({ onOpenHistory, onOpenCookies, isTurnstileVerified = false }) {
  const [browsersData, setBrowsersData] = useState(null)
  const [selectedBrowser, setSelectedBrowser] = useState(getActiveBrowser() || '')
  const [isDropdownOpen, setIsDropdownOpen] = useState(false)
  const [toastMsg, setToastMsg] = useState('')
  const [cookieCount, setCookieCount] = useState(0)
  const browserDropdownRef = useRef(null)

  const showToast = (msg) => {
    setToastMsg(msg)
    setTimeout(() => setToastMsg(''), 3000)
  }

  const loadData = async () => {
    try {
      const [b, cs] = await Promise.all([getBrowsersList(), getCookieStatus()])
      if (b) {
        setBrowsersData(b)
        if (!getActiveBrowser() && b.current) {
          setSelectedBrowser(b.current)
          setActiveBrowser(b.current)
        }
      }
      if (cs) setCookieCount(cs.platforms?.filter((p) => p.isValid).length || 0)
    } catch (err) {
      console.error('Lỗi khi tải thông tin hệ thống:', err)
    }
  }

  useEffect(() => {
    loadData()
  }, [])

  // Đóng dropdown khi click ra ngoài hoặc nhấn phím ESC
  useEffect(() => {
    if (!isDropdownOpen) return

    const handleClickOutside = (e) => {
      if (browserDropdownRef.current && !browserDropdownRef.current.contains(e.target)) {
        setIsDropdownOpen(false)
      }
    }

    const handleKeyDown = (e) => {
      if (e.key === 'Escape') {
        setIsDropdownOpen(false)
      }
    }

    document.addEventListener('pointerdown', handleClickOutside)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('pointerdown', handleClickOutside)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [isDropdownOpen])

  const handleSelectBrowser = (browserId, browserName) => {
    setSelectedBrowser(browserId)
    setActiveBrowser(browserId)
    setIsDropdownOpen(false)
    showToast(`Đã chuyển sang cookies trình duyệt: ${browserName}`)
  }

  // Tên hiển thị của trình duyệt hiện tại
  const currentBrowserObj = browsersData?.browsers?.find((b) => b.id === selectedBrowser)
  const currentBrowserName = currentBrowserObj ? currentBrowserObj.name : selectedBrowser ? selectedBrowser.toUpperCase() : 'Mặc định'

  return (
    <header className="system-header-bar">
      {/* Toast thông báo chuyển trình duyệt */}
      {toastMsg && (
        <div className="system-toast-alert">
          <span>{toastMsg}</span>
        </div>
      )}

      <div className="system-header-inner">
        {/* Brand / Tiêu đề */}
        <div className="system-brand-group">
          <div className="system-brand-icon">
            <IconGlobe className="w-4 h-4 text-emerald-400" />
            <span className="brand-dot-pulse" />
          </div>
          <h1 className="system-brand-title">Media Downloader</h1>
        </div>

        {/* Trạng thái Binary & Bộ chọn Cookies Trình duyệt */}
        <div className="system-status-group">
          {/* Turnstile Verified Badge */}
          {isTurnstileVerified && (
            <div className="engine-badges-container">
              <span className="engine-status-pill" title="Phiên làm việc đã xác thực an toàn qua Cloudflare Turnstile">
                <IconShieldCheck className="w-3.5 h-3.5 text-emerald-400" />
                <span className="engine-name" style={{ color: '#10b981' }}>Turnstile</span>
                <span className="engine-ver">Verified</span>
              </span>
            </div>
          )}

          {/* Browser Cookies Selector Dropdown */}
          <div className="browser-selector-wrapper" ref={browserDropdownRef}>
            <button
              type="button"
              className={`browser-selector-trigger ${isDropdownOpen ? 'is-open' : ''}`}
              onClick={() => setIsDropdownOpen(!isDropdownOpen)}
              title="Chọn trình duyệt dùng để đọc cookies tài khoản (cho Instagram, Twitter/X, YouTube...)"
            >
              <div className="browser-trigger-label">
                <span className="trigger-label-small">Cookies:</span>
                <strong className="trigger-browser-name">{currentBrowserName}</strong>
              </div>
              <IconChevronDown className="w-3.5 h-3.5 text-slate-400 chevron-icon" />
            </button>

            {isDropdownOpen && (
              <div className="browser-dropdown-menu">
                <div className="browser-dropdown-header">
                  <span>Nguồn Cookies trình duyệt</span>
                  <button
                    type="button"
                    className="btn-refresh-browsers"
                    onClick={(e) => {
                      e.stopPropagation()
                      loadData()
                      showToast('Đang quét lại trình duyệt trên máy chủ...')
                    }}
                    title="Quét lại trình duyệt"
                  >
                    <IconRefresh className="w-3.5 h-3.5" />
                  </button>
                </div>
                <div className="browser-dropdown-list">
                  {browsersData?.browsers?.map((b) => {
                    const isSelected = selectedBrowser === b.id
                    return (
                      <button
                        key={b.id}
                        type="button"
                        className={`browser-item-option ${isSelected ? 'is-selected' : ''}`}
                        onClick={() => handleSelectBrowser(b.id, b.name)}
                      >
                        <div className="browser-name-row">
                          <strong>{b.name}</strong>
                          {b.detected && (
                            <span className={`badge-detected-chip ${b.id === browsersData?.defaultConfigured ? 'badge-primary' : ''}`}>
                              {b.id === browsersData?.defaultConfigured
                                ? 'Mặc định'
                                : b.id === 'none'
                                ? 'Tắt cookies'
                                : 'Có sẵn'}
                            </span>
                          )}
                        </div>
                        {isSelected && <IconCheck className="browser-check-icon text-emerald-400" />}
                      </button>
                    )
                  })}
                </div>
              </div>
            )}
          </div>

          {/* Cookie Manager Button */}
          <button
            type="button"
            className="btn-cookie-trigger"
            onClick={onOpenCookies}
            title="Quản lý Cookie — Xác thực Instagram, X, TikTok..."
          >
            <span className="cookie-icon">🍪</span>
            <span>Cookie</span>
            {cookieCount > 0 && (
              <span className="cookie-count-badge">{cookieCount}</span>
            )}
          </button>

          {/* History Button Trigger */}
          <button
            type="button"
            className="btn-history-trigger"
            onClick={onOpenHistory}
            title="Xem lịch sử tải xuống"
          >
            <IconHistory className="w-4 h-4 text-slate-400" />
            <span>Lịch sử tải</span>
          </button>
        </div>
      </div>
    </header>
  )
}
