import { useState } from 'react'
import {
  IconClose,
  IconPaste,
  IconSparkles,
  IconVideo,
  IconImage,
} from './Icons'
import { detectPlatform } from '../constants'
import { crawlProfile } from '../services/api'

export default function AccountDownloader({ onProfileCrawled, isGlobalLoading, onShowToast }) {
  const [accountInput, setAccountInput] = useState('')
  const [selectedPlatform] = useState('auto')
  const [mediaTypeFilter, setMediaTypeFilter] = useState('all') // 'all' | 'video' | 'image'
  const [crawlLimit, setCrawlLimit] = useState('20')
  const [rangeFrom, setRangeFrom] = useState('1')
  const [rangeTo, setRangeTo] = useState('20')
  const [isCrawling, setIsCrawling] = useState(false)
  const [crawlProgress, setCrawlProgress] = useState(0)
  const [statusText, setStatusText] = useState('')

  // Tự động nhận diện platform từ input
  const detected = detectPlatform(accountInput)
  const activePlatform = selectedPlatform === 'auto' ? (detected || 'youtube') : selectedPlatform

  const handlePaste = async () => {
    try {
      const text = await navigator.clipboard.readText()
      if (text) {
        setAccountInput(text.trim())
        onShowToast?.('Đã dán tài khoản từ bộ nhớ tạm')
      }
    } catch {
      onShowToast?.('Vui lòng cấp quyền đọc clipboard hoặc dùng Ctrl+V')
    }
  }

  const handleStartCrawl = async (e) => {
    e?.preventDefault()
    const target = accountInput.trim()
    if (!target) {
      onShowToast?.('Vui lòng nhập tên người dùng (@username) hoặc liên kết tài khoản!')
      return
    }

    setIsCrawling(true)
    setCrawlProgress(15)
    setStatusText('Đang kết nối tài khoản...')

    const progressTimer = setInterval(() => {
      setCrawlProgress((prev) => {
        if (prev < 45) return prev + 8
        if (prev < 75) return prev + 4
        if (prev < 88) return prev + 2
        if (prev < 95) return prev + 1
        return prev
      })
    }, 400)

    try {
      const isRange = crawlLimit === 'range'
      const startNum = isRange ? (parseInt(rangeFrom, 10) || 1) : undefined
      const endNum = isRange ? (parseInt(rangeTo, 10) || 50) : undefined
      const limitNum = isRange
        ? Math.max(1, endNum - startNum + 1)
        : (crawlLimit === 'all' ? 100 : parseInt(crawlLimit, 10) || 50)

      setStatusText('Đang quét và phân tích danh sách phương tiện...')
      const resultData = await crawlProfile({
        url: target,
        limit: limitNum,
        mediaType: mediaTypeFilter,
        platform: activePlatform,
        rangeStart: startNum,
        rangeEnd: endNum,
      })

      clearInterval(progressTimer)
      setCrawlProgress(100)

      if (resultData && resultData.media && resultData.media.length > 0) {
        onProfileCrawled?.(resultData)
        onShowToast?.(`Đã quét thành công ${resultData.media.length} tệp từ @${resultData.name || 'tài khoản'}!`)
      } else {
        onShowToast?.('Không tìm thấy tệp phương tiện công khai nào từ tài khoản này.')
      }
    } catch (err) {
      clearInterval(progressTimer)
      onShowToast?.(err.message || 'Lỗi khi quét tài khoản!')
    } finally {
      setIsCrawling(false)
      setCrawlProgress(0)
      setStatusText('')
    }
  }

  return (
    <div className="panel-card panel-right-account">
      {/* Header của Panel */}
      <div className="panel-card-header">
        <div className="panel-title-area">
          <div className="panel-badge-num">02</div>
          <div>
            <h2 className="panel-title">Tải theo tài khoản</h2>
            <p className="panel-subtitle">Quét toàn bộ bài viết, Reels, video theo hồ sơ người dùng</p>
          </div>
        </div>

        {/* Bộ lọc loại media (Tất cả / Video / Ảnh) */}
        <div className="media-type-filter-group">
          <button
            type="button"
            className={`filter-btn ${mediaTypeFilter === 'all' ? 'active' : ''}`}
            onClick={() => setMediaTypeFilter('all')}
            title="Tất cả video và ảnh"
          >
            Tất cả
          </button>
          <button
            type="button"
            className={`filter-btn ${mediaTypeFilter === 'video' ? 'active' : ''}`}
            onClick={() => setMediaTypeFilter('video')}
            title="Chỉ lấy video"
          >
            <IconVideo className="w-3.5 h-3.5" /> Video
          </button>
          <button
            type="button"
            className={`filter-btn ${mediaTypeFilter === 'image' ? 'active' : ''}`}
            onClick={() => setMediaTypeFilter('image')}
            title="Chỉ lấy ảnh"
          >
            <IconImage className="w-3.5 h-3.5" /> Ảnh
          </button>
        </div>
      </div>

      {/* Thân nhập liệu */}
      <div className="panel-card-body">
        <form onSubmit={handleStartCrawl} className="form-stack">
          {/* Ô nhập @username hoặc URL tài khoản */}
          <div className="input-with-actions">
            <input
              type="text"
              className="clean-input"
              placeholder="Nhập @username hoặc URL tài khoản (TikTok, Instagram, YouTube, Pinterest, X)..."
              value={accountInput}
              onChange={(e) => setAccountInput(e.target.value)}
              disabled={isCrawling || isGlobalLoading}
            />
            {accountInput ? (
              <button
                type="button"
                className="icon-tool-btn"
                onClick={() => setAccountInput('')}
                title="Xóa"
              >
                <IconClose className="w-4 h-4" />
              </button>
            ) : (
              <button
                type="button"
                className="icon-tool-btn"
                onClick={handlePaste}
                title="Dán từ bộ nhớ tạm"
              >
                <IconPaste className="w-4 h-4" />
              </button>
            )}
          </div>

          {/* Cấu hình số lượng & Khoảng tải */}
          <div className="config-grid-row">
            <div className="limit-selector-container">
              <span className="control-label">Số lượng quét:</span>
              <div className="limit-buttons">
                {['10', '20', '50', 'range'].map((val) => (
                  <button
                    key={val}
                    type="button"
                    className={`limit-btn ${crawlLimit === val ? 'active' : ''}`}
                    onClick={() => setCrawlLimit(val)}
                  >
                    {val === 'range' ? 'Khoảng tuỳ chọn' : `${val} tệp`}
                  </button>
                ))}
              </div>
            </div>

            {/* Nếu chọn khoảng Range */}
            {crawlLimit === 'range' && (
              <div className="range-inputs-container">
                <span className="control-label">Từ tệp:</span>
                <input
                  type="number"
                  min={1}
                  className="clean-input-small"
                  value={rangeFrom}
                  onChange={(e) => setRangeFrom(e.target.value)}
                />
                <span className="control-label">Đến:</span>
                <input
                  type="number"
                  min={1}
                  className="clean-input-small"
                  value={rangeTo}
                  onChange={(e) => setRangeTo(e.target.value)}
                />
              </div>
            )}
          </div>

          {/* Thanh tiến trình khi đang quét */}
          {isCrawling && (
            <div className="progress-inline-card">
              <div className="progress-label-row">
                <span>{statusText || 'Đang quét dữ liệu...'}</span>
                <span>{crawlProgress}%</span>
              </div>
              <div className="progress-track">
                <div className="progress-fill" style={{ width: `${crawlProgress}%` }} />
              </div>
            </div>
          )}

          {/* Nút hành động quét */}
          <div className="action-row">
            <span className="action-hint">
              {detected ? `Đã nhận diện: ${detected.toUpperCase()}` : 'Hỗ trợ quét Profile, Kênh, Shorts, Reels'}
            </span>

            <button
              type="submit"
              className="primary-action-btn emerald-btn"
              disabled={!accountInput.trim() || isCrawling || isGlobalLoading}
            >
              {isCrawling ? (
                <>
                  <span className="clean-spinner" />
                  <span>Đang quét tài khoản...</span>
                </>
              ) : (
                <>
                  <IconSparkles className="w-4 h-4" />
                  <span>Bắt đầu quét tài khoản</span>
                </>
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
