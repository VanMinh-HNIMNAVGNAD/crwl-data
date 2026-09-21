import { useState, useEffect, useRef } from 'react'
import {
  getBrowsersList,
  getActiveBrowser,
  setActiveBrowser,
  getCookieStatus,
  getDefaultDownloadDirectory,
  selectDownloadDirectory,
  getAlwaysAskDownloadDir,
  setAlwaysAskDownloadDir,
} from '../services/api'
import { IconChevronDown, IconCheck, IconRefresh, IconHistory, IconSettings } from './Icons'
const mediaLogo = '/favicon.svg'

export default function SystemHeader({ onOpenHistory, onOpenCookies, onOpenTools, cookieRefreshKey }) {
  const [browsersData, setBrowsersData] = useState(null)
  const [selectedBrowser, setSelectedBrowser] = useState(getActiveBrowser() || '')
  const [downloadDir, setDownloadDir] = useState('')
  const [alwaysAsk, setAlwaysAsk] = useState(getAlwaysAskDownloadDir())
  const [isDropdownOpen, setIsDropdownOpen] = useState(false)
  const [toastMsg, setToastMsg] = useState('')
  const [cookieCount, setCookieCount] = useState(0)
  const browserDropdownRef = useRef(null)
  const toastTimer = useRef(null)
  const autoPickedRef = useRef(Boolean(getActiveBrowser()))

  const showToast = (msg) => {
    setToastMsg(msg)
    if (toastTimer.current) {
      clearTimeout(toastTimer.current)
    }
    toastTimer.current = setTimeout(() => {
      setToastMsg('')
    }, 3000)
  }


  const handleRefreshBrowsers = async (e) => {
    e?.stopPropagation()
    showToast('Đang quét lại trình duyệt trên máy...')
    try {
      const [b, cs] = await Promise.all([getBrowsersList(), getCookieStatus()])
      if (b) setBrowsersData(b)
      if (cs) setCookieCount(cs.platforms?.filter((p) => p.has_cookies).length || 0)
    } catch (err) {
      console.warn('Lỗi khi làm mới trình duyệt:', err)
      showToast(typeof err === 'string' ? err : err?.message || 'Lỗi khi làm mới trình duyệt')
    }
  }

  useEffect(() => {
    let active = true
    Promise.all([getBrowsersList(), getCookieStatus(), getDefaultDownloadDirectory()])
      .then(([b, cs, defDir]) => {
        if (!active) return
        if (b?.browsers) {
          setBrowsersData(b)
          if (!autoPickedRef.current) {
            autoPickedRef.current = true
            const detectedOne = b.browsers.find((item) => item.installed || item.detected)
            if (detectedOne) {
              setSelectedBrowser(detectedOne.id)
              setActiveBrowser(detectedOne.id)
            }
          }
        }
        if (cs) setCookieCount(cs.platforms?.filter((p) => p.has_cookies).length || 0)
        if (defDir) setDownloadDir(defDir)
      })
      .catch((err) => {
        console.warn('Lỗi khi tải thông tin hệ thống:', err)
        if (active) showToast(typeof err === 'string' ? err : err?.message || 'Lỗi khi tải thông tin hệ thống')
      })
    return () => {
      active = false
    }
    // Cố tình KHÔNG phụ thuộc vào `selectedBrowser`: đổi trình duyệt không cần
    // quét lại toàn bộ hệ thống, và việc tự động chọn lúc khởi động từng khiến
    // effect chạy hai lần liên tiếp.
  }, [cookieRefreshKey])

  useEffect(() => () => {
    if (toastTimer.current) clearTimeout(toastTimer.current)
  }, [])

  const handleSelectFolder = async () => {
    try {
      const dir = await selectDownloadDirectory()
      if (dir) {
        setDownloadDir(dir)
        showToast(`Đã đổi nơi lưu: ${dir}`)
      }
    } catch (err) {
      console.warn('Lỗi khi chọn thư mục:', err)
    }
  }

  const formatDirDisplay = (dir) => {
    if (!dir) return 'Thư mục lưu'
    const parts = dir.replace(/\\/g, '/').split('/').filter(Boolean)
    return parts.length > 0 ? `📁 ${parts[parts.length - 1]}` : '📁 Thư mục lưu'
  }

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
        {/* Brand / Tiêu đề & Logo mới */}
        <div className="system-brand-group">
          <div className="system-brand-logo-wrap" title="Media Downloader">
            <img src={mediaLogo} alt="Media Downloader Logo" className="system-brand-logo" />
          </div>
          <h1 className="system-brand-title">Media Downloader</h1>
        </div>

        {/* Trạng thái Binary & Bộ chọn Cookies Trình duyệt */}
        <div className="system-status-group">
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
                    onClick={handleRefreshBrowsers}
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
                          {(b.detected || b.installed) ? (
                            <span className="badge-detected-chip badge-primary">
                              ✓ Đã nhận diện
                            </span>
                          ) : (
                            <span className="badge-detected-chip">
                              Chưa cài
                            </span>
                          )}
                        </div>
                        {isSelected && <IconCheck className="browser-check-icon text-emerald-400" />}
                      </button>
                    )
                  })}
                  <button
                    type="button"
                    className={`browser-item-option ${selectedBrowser === 'none' ? 'is-selected' : ''}`}
                    onClick={() => handleSelectBrowser('none', 'Tắt Cookies')}
                  >
                    <div className="browser-name-row">
                      <strong>Tắt Cookies (Tải ẩn danh)</strong>
                    </div>
                    {selectedBrowser === 'none' && <IconCheck className="browser-check-icon text-emerald-400" />}
                  </button>
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

          {/* Chọn nơi lưu trữ tệp */}
          <div className="folder-selector-wrapper" style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <button
              type="button"
              className="btn-folder-trigger"
              onClick={handleSelectFolder}
              title={`Thư mục lưu hiện tại:\n${downloadDir || 'Mặc định (~/Downloads)'}\n\nNhấp để chọn thư mục lưu khác...`}
            >
              <span className="folder-name-text">{formatDirDisplay(downloadDir)}</span>
            </button>
            <label
              className="checkbox-opt-label"
              style={{ display: 'flex', alignItems: 'center', gap: '4px', cursor: 'pointer', color: '#cbd5e1', fontSize: '12px' }}
              title={alwaysAsk
                ? `Chế độ: Hỏi thư mục mỗi lần tải\nTắt để tự động lưu vào: ${downloadDir || '~/Downloads'}` 
                : `Chế độ: Tự động lưu vào ${downloadDir || '~/Downloads'}\nBật nếu muốn chọn thư mục trước mỗi lần tải`
              }
            >
              <input
                type="checkbox"
                checked={alwaysAsk}
                onChange={(e) => {
                  setAlwaysAsk(e.target.checked)
                  setAlwaysAskDownloadDir(e.target.checked)
                  showToast(
                    e.target.checked
                      ? 'Bật: Sẽ hỏi nơi lưu trước mỗi lần tải'
                      : `Tắt: Tự động lưu vào "${downloadDir || 'Downloads mặc định'}"`
                  )
                }}
                style={{ cursor: 'pointer', accentColor: '#38bdf8' }}
              />
              <span style={{ userSelect: 'none' }}>Hỏi trước khi tải</span>
            </label>
          </div>

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

          {/* Tools & Engine Status Trigger */}
          <button
            type="button"
            className="btn-tools-trigger"
            onClick={onOpenTools}
            title="Kiểm tra & Cập nhật các công cụ Engine (yt-dlp, gallery-dl, FFmpeg, aria2c...)"
          >
            <IconSettings className="w-4 h-4 text-slate-400" />
            <span>Công cụ</span>
          </button>
        </div>
      </div>
    </header>
  )
}
