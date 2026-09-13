import { useState } from 'react'
import {
  IconDownload,
  IconZip,
  IconExternalLink,
  IconClose,
  IconVideo,
  IconImage,
  IconCheck,
  IconYouTube,
  IconTikTok,
  IconPinterest,
} from './Icons'
import { detectPlatform } from '../constants'
import {
  crawlProfile,
  buildStreamDownloadUrl,
  buildProxyMediaUrl,
  buildProxyImageUrl,
  downloadZipArchive,
  triggerFileDownload,
} from '../services/api'

export default function BulkDownloader() {
  const [accountInput, setAccountInput] = useState('')
  const [selectedPlatform, setSelectedPlatform] = useState('auto')
  const [mediaTypeFilter, setMediaTypeFilter] = useState('all') // 'all' | 'video' | 'image'
  const [crawlLimit, setCrawlLimit] = useState('20')
  const [rangeFrom, setRangeFrom] = useState('1')
  const [rangeTo, setRangeTo] = useState('20')
  const [streamAudioMode, setStreamAudioMode] = useState('full') // 'full' | 'mute' | 'audio'
  const [isCrawling, setIsCrawling] = useState(false)
  const [crawlProgress, setCrawlProgress] = useState(0)
  const [crawlStatusText, setCrawlStatusText] = useState('')
  const [profileResult, setProfileResult] = useState(null)
  const [selectedMediaIds, setSelectedMediaIds] = useState({})
  const [isZipDownloading, setIsZipDownloading] = useState(false)
  const [zipProgressText, setZipProgressText] = useState('')
  const [toastMessage, setToastMessage] = useState('')
  const [crawlAlert, setCrawlAlert] = useState(null)

  const showToast = (msg) => {
    setToastMessage(msg)
    setTimeout(() => setToastMessage(''), 3500)
  }

  // Tự động nhận diện platform từ input (nếu nhập username @... mà không có domain thì mặc định youtube vì mở nhất)
  const detected = detectPlatform(accountInput)
  const activePlatform = selectedPlatform === 'auto' ? (detected || 'youtube') : selectedPlatform

  // Bắt đầu quét & thu thập toàn bộ media từ tài khoản
  const handleStartCrawl = async (overrideInput = null, overridePlatform = null) => {
    const target = (overrideInput !== null ? overrideInput : accountInput).trim()
    if (!target) {
      showToast('Vui lòng nhập tên người dùng (@username) hoặc liên kết tài khoản hợp lệ!')
      return
    }

    const platformToUse = overridePlatform || activePlatform

    setIsCrawling(true)
    setCrawlProgress(15)
    setCrawlStatusText('Đang kết nối API và chuẩn bị tài khoản...')
    setProfileResult(null)
    setCrawlAlert(null)

    let elapsed = 0
    const progressTimer = setInterval(() => {
      elapsed += 400
      setCrawlProgress((prev) => {
        if (prev < 45) return prev + 8
        if (prev < 75) return prev + 4
        if (prev < 88) return prev + 2
        if (prev < 95) return prev + 1
        return prev
      })

      if (elapsed >= 1600 && elapsed < 4000) {
        setCrawlStatusText('Đang phân tích bài viết / reels từ máy chủ...')
      } else if (elapsed >= 4000 && elapsed < 8000) {
        setCrawlStatusText('Đang trích xuất liên kết video/ảnh chất lượng gốc...')
      } else if (elapsed >= 8000) {
        setCrawlStatusText('Đang tổng hợp thông tin media, sắp hoàn tất...')
      }
    }, 400)

    try {
      const isRange = crawlLimit === 'range'
      const startNum = isRange ? (parseInt(rangeFrom) || 1) : undefined
      const endNum = isRange ? (parseInt(rangeTo) || 50) : undefined
      const limitNum = isRange ? Math.max(1, endNum - startNum + 1) : (crawlLimit === 'all' ? 100 : parseInt(crawlLimit) || 50)

      const resultData = await crawlProfile({
        url: target,
        limit: limitNum,
        mediaType: mediaTypeFilter,
        platform: platformToUse,
        rangeStart: startNum,
        rangeEnd: endNum,
      })

      clearInterval(progressTimer)
      setCrawlProgress(100)
      setCrawlStatusText('Đã trích xuất thành công!')

      if (resultData && resultData.media && resultData.media.length > 0) {
        setProfileResult(resultData)
        const initialSelected = {}
        resultData.media.forEach((m) => {
          initialSelected[m.id] = true
        })
        setSelectedMediaIds(initialSelected)
        showToast(`Đã quét thành công ${resultData.media.length} tệp nội dung từ ${resultData.name || resultData.handle}!`)
      } else {
        throw new Error('Không tìm thấy tệp phương tiện công khai nào từ tài khoản này')
      }
    } catch (err) {
      const errMsg = err.message || 'Không thể quét tài khoản. Vui lòng kiểm tra lại liên kết!'
      showToast(errMsg)
      setCrawlAlert({
        type: 'error',
        message: errMsg,
        isCookieNotice: errMsg.includes('cookies') || errMsg.includes('đăng nhập') || errMsg.includes('Tab 1') || errMsg.includes('bảo mật'),
      })
    } finally {
      clearInterval(progressTimer)
      setIsCrawling(false)
    }
  }

  const toggleSelect = (id) => {
    setSelectedMediaIds((prev) => ({ ...prev, [id]: !prev[id] }))
  }

  const toggleSelectAll = (selectAll = true) => {
    if (!profileResult || !profileResult.media) return
    const nextState = {}
    if (selectAll) {
      profileResult.media.forEach((m) => {
        nextState[m.id] = true
      })
    }
    setSelectedMediaIds(nextState)
  }

  // Tải danh sách tệp đã chọn thành 1 file ZIP
  const handleDownloadSelectedZip = async () => {
    if (!profileResult || !profileResult.media) return
    const selectedItems = profileResult.media.filter((m) => selectedMediaIds[m.id])
    const selectedCount = selectedItems.length

    if (selectedCount === 0) {
      showToast('Vui lòng chọn ít nhất 1 tệp phương tiện để tải!')
      return
    }

    setIsZipDownloading(true)
    setZipProgressText(`Đang chuẩn bị ${selectedCount} tệp...`)
    showToast(`Đang đóng gói ${selectedCount} tệp vào file ZIP tốc độ cao...`)

    try {
      const items = selectedItems.map((m, idx) => ({
        url: m.url,
        filename: `${m.title || `item_${idx + 1}`}.${m.type === 'video' ? 'mp4' : 'jpg'}`,
        referer: profileResult.platform ? `https://www.${profileResult.platform}.com` : undefined,
      }))

      const cleanHandle = (profileResult.handle || profileResult.name || 'media').replace(/[^a-zA-Z0-9_-]/g, '_')
      const zipName = `${cleanHandle}_bulk_${Date.now()}`
      await downloadZipArchive(items, zipName, (progress) => {
        if (progress.receivedBytes) {
          const mb = (progress.receivedBytes / (1024 * 1024)).toFixed(1)
          setZipProgressText(`Đang tải (${mb} MB)...`)
        }
      })

      showToast(`Đã tải xuống thành công: ${cleanHandle}_${selectedCount}_tep.zip!`)
    } catch (err) {
      console.error('ZIP download error:', err)
      showToast(err.message || 'Lỗi khi tải tệp nén ZIP')
    } finally {
      setIsZipDownloading(false)
      setZipProgressText('')
    }
  }

  // Tải trực tiếp 1 tệp media về máy (dùng stream hoặc proxy chống 403)
  const handleDownloadSingleMedia = (m) => {
    showToast(`Đang chuẩn bị tải "${m.title}"...`)

    if (m.type === 'video') {
      const isAudio = streamAudioMode === 'audio'
      const ext = isAudio ? 'mp3' : 'mp4'
      const downloadEndpoint = buildStreamDownloadUrl({
        url: m.url,
        title: m.title,
        isAudio,
        audioFormat: isAudio ? 'mp3' : undefined,
        audioBitrate: isAudio ? '320k' : undefined,
      })

      triggerFileDownload(downloadEndpoint, `${m.title.replace(/[^a-zA-Z0-9_-]/g, '_')}.${ext}`)
      showToast(`Đang truyền dữ liệu: "${m.title}.${ext}"`)
    } else {
      const ext = m.ext || (m.url.includes('.png') ? 'png' : m.url.includes('.webp') ? 'webp' : 'jpg')
      const cleanName = `${m.title.replace(/[/\\?%*:|"<>]/g, '_')}.${ext}`
      const downloadEndpoint = buildProxyMediaUrl(m.url, cleanName)
      triggerFileDownload(downloadEndpoint, cleanName)
    }
  }

  // Lọc media hiển thị theo filter loại (ảnh / video)
  const filteredMedia = profileResult && profileResult.media ? profileResult.media.filter((m) => {
    if (mediaTypeFilter === 'video' && m.type !== 'video') return false
    if (mediaTypeFilter === 'image' && m.type !== 'image') return false
    return true
  }) : []

  const selectedCount = Object.values(selectedMediaIds).filter(Boolean).length

  return (
    <div className="bulk-downloader-container">
      {/* Toast Notification */}
      {toastMessage && (
        <div className="toast-notification">
          <span>{toastMessage}</span>
        </div>
      )}

      {/* 1. Thanh tìm kiếm hồ sơ / tài khoản */}
      <div className="crawler-input-wrapper">
        <div className="crawler-input-container">
          <div className="input-group">
            <input
              type="text"
              className="crawler-text-input"
              value={accountInput}
              onChange={(e) => setAccountInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleStartCrawl()}
              placeholder="Nhập tên người dùng hoặc link trang cá nhân/kênh (VD: @username hoặc https://tiktok.com/@...)"
              autoComplete="off"
            />
            {accountInput && (
              <button
                type="button"
                className="btn-clear-x"
                onClick={() => {
                  setAccountInput('')
                  setProfileResult(null)
                }}
                title="Xóa"
                aria-label="Xóa"
              >
                <IconClose className="w-3.5 h-3.5" />
              </button>
            )}
          </div>

          <button
            type="button"
            className={`btn-primary-fetch ${isCrawling ? 'is-loading' : ''}`}
            onClick={() => handleStartCrawl()}
            disabled={isCrawling}
          >
            {isCrawling ? (
              <>
                <span className="spinner-dots" />
                <span>Đang quét...</span>
              </>
            ) : (
              <span>Bắt đầu quét</span>
            )}
          </button>
        </div>

        {/* Thanh gợi ý mẫu thử nhanh */}
        <div className="crawler-samples-bar">
          <span className="crawler-samples-label">Thử nhanh:</span>
          <div className="crawler-samples-list">
            <button
              type="button"
              className="crawler-sample-chip crawler-sample-chip-youtube"
              onClick={() => {
                setAccountInput('https://www.youtube.com/@nasa')
                setSelectedPlatform('youtube')
                handleStartCrawl('https://www.youtube.com/@nasa', 'youtube')
              }}
              title="Quét kênh YouTube @nasa"
            >
              <span className="sample-platform-icon youtube">
                <IconYouTube className="w-3.5 h-3.5" />
              </span>
              <span>@nasa (YouTube)</span>
            </button>
            <button
              type="button"
              className="crawler-sample-chip crawler-sample-chip-tiktok"
              onClick={() => {
                setAccountInput('https://www.tiktok.com/@tiktok')
                setSelectedPlatform('tiktok')
                handleStartCrawl('https://www.tiktok.com/@tiktok', 'tiktok')
              }}
              title="Quét kênh TikTok @tiktok"
            >
              <span className="sample-platform-icon tiktok">
                <IconTikTok className="w-3.5 h-3.5" />
              </span>
              <span>@tiktok (TikTok)</span>
            </button>
            <button
              type="button"
              className="crawler-sample-chip crawler-sample-chip-pinterest"
              onClick={() => {
                setAccountInput('https://www.pinterest.com/nasa/goddard-life/')
                setSelectedPlatform('pinterest')
                handleStartCrawl('https://www.pinterest.com/nasa/goddard-life/', 'pinterest')
              }}
              title="Quét bảng ảnh Pinterest Goddard Life của NASA"
            >
              <span className="sample-platform-icon pinterest">
                <IconPinterest className="w-3.5 h-3.5" />
              </span>
              <span>Pinterest Board (NASA)</span>
            </button>
          </div>
        </div>

        {/* Lưu ý chính sách nền tảng */}
        <div className="crawler-tip-notice">
          <span>💡</span>
          <span>
            <strong>Lưu ý:</strong> Trên <strong>Instagram</strong>, khi chọn <strong>"Chỉ Video"</strong> hệ thống sẽ tự động quét toàn bộ video tab <strong>Reels</strong> (yêu cầu cookies). Chọn <strong>"Chỉ Hình ảnh"</strong> hoặc <strong>"Tất cả"</strong> sẽ quét bài viết bảng tin (Posts).
          </span>
        </div>
      </div>

      {/* Alert thông báo lỗi hệ thống nếu có */}
      {crawlAlert && (
        <div className={`crawler-alert-card ${crawlAlert.type === 'warning' ? 'warning' : ''}`}>
          <div className="crawler-alert-content">
            <div className="crawler-alert-title">
              <span>Thông báo từ hệ thống:</span>
            </div>
            <div>{crawlAlert.message}</div>
          </div>
          <button
            type="button"
            className="btn-alert-close"
            onClick={() => setCrawlAlert(null)}
            title="Đóng thông báo"
          >
            <IconClose className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* 2. Tùy chọn lọc nội dung quét */}
      <div className="crawler-options-panel">
        <div className="settings-row">
          <div className="setting-col">
            <label className="setting-label">Nền tảng</label>
            <select
              value={selectedPlatform}
              onChange={(e) => setSelectedPlatform(e.target.value)}
              className="bulk-select"
            >
              <option value="auto">Tự động nhận diện</option>
              <option value="youtube">YouTube (Kênh / Playlist — Mở 100%)</option>
              <option value="pinterest">Pinterest (Bảng / Board — Mở 100%)</option>
              <option value="x">X / Twitter (Hỗ trợ Cookies)</option>
              <option value="tiktok">TikTok (Kênh / Hồ sơ)</option>
              <option value="instagram">Instagram (Yêu cầu Cookies)</option>
              <option value="reddit">Reddit (User / Subreddit)</option>
              <option value="facebook">Facebook</option>
            </select>
          </div>

          <div className="setting-col">
            <label className="setting-label">Nội dung quét</label>
            <select
              value={mediaTypeFilter}
              onChange={(e) => setMediaTypeFilter(e.target.value)}
              className="bulk-select"
            >
              <option value="all">Tất cả (Hình ảnh & Video)</option>
              <option value="video">Chỉ Video</option>
              <option value="image">Chỉ Hình ảnh</option>
            </select>
          </div>

          <div className="setting-col">
            <label className="setting-label">Giới hạn quét</label>
            <select
              value={crawlLimit}
              onChange={(e) => setCrawlLimit(e.target.value)}
              className="bulk-select"
            >
              <option value="10">10 mục gần nhất (Rất nhanh)</option>
              <option value="20">20 mục gần nhất (Khuyên dùng)</option>
              <option value="30">30 mục gần nhất</option>
              <option value="50">50 mục gần nhất</option>
              <option value="100">100 mục gần nhất</option>
              <option value="range">Tùy chọn khoảng mục (Từ - Đến)</option>
            </select>
          </div>

          <div className="setting-col">
            <label className="setting-label">Tùy chọn âm thanh</label>
            <select
              value={streamAudioMode}
              onChange={(e) => setStreamAudioMode(e.target.value)}
              className="bulk-select"
            >
              <option value="full">Video có tiếng (MP4)</option>
              <option value="audio">Chỉ tách âm thanh (MP3)</option>
            </select>
          </div>
        </div>

        {/* Khung nhập khoảng quét Range nếu người dùng chọn */}
        {crawlLimit === 'range' && (
          <div className="range-selector-box">
            <span className="range-box-title">Phạm vi các mục quét:</span>
            <div className="range-fields-row">
              <div className="range-field-group">
                <label>Từ mục số:</label>
                <input
                  type="number"
                  min="1"
                  value={rangeFrom}
                  onChange={(e) => setRangeFrom(e.target.value)}
                  className="range-number-input"
                />
              </div>
              <div className="range-field-group">
                <label>Đến mục số:</label>
                <input
                  type="number"
                  min="1"
                  value={rangeTo}
                  onChange={(e) => setRangeTo(e.target.value)}
                  className="range-number-input"
                />
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Thanh tiến trình quét */}
      {isCrawling && (
        <div className="crawl-progress-card">
          <div className="progress-info-row">
            <span className="progress-status-text">
              {crawlStatusText || 'Đang kết nối API và trích xuất danh sách media...'}
            </span>
            <span className="progress-percent-badge">{crawlProgress}%</span>
          </div>
          <div className="progress-track">
            <div className="progress-fill" style={{ width: `${crawlProgress}%` }} />
          </div>
        </div>
      )}

      {/* 3. Kết quả quét hồ sơ */}
      {profileResult && (
        <div className="profile-results-section">
          {/* Header thông tin tài khoản */}
          <div className="profile-header-card">
            <div className="profile-main-meta">
              <img
                src={buildProxyImageUrl(profileResult.avatar)}
                alt={profileResult.name}
                className="profile-avatar"
                width={40}
                height={40}
                loading="eager"
                decoding="async"
                referrerPolicy="no-referrer"
                onError={(e) => {
                  e.currentTarget.onerror = null
                  e.currentTarget.src = 'data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40" viewBox="0 0 100 100"><rect width="100%" height="100%" fill="%23232734"/><circle cx="50" cy="40" r="20" fill="%234B5563"/><path d="M20 90 A 30 30 0 0 1 80 90 Z" fill="%234B5563"/></svg>'
                }}
              />
              <div className="profile-titles">
                <div className="profile-name-row">
                  <h3 className="profile-display-name">{profileResult.name}</h3>
                  <span className="profile-platform-badge">
                    {profileResult.platform.toUpperCase()}
                  </span>
                </div>
                <div className="profile-handle-row">
                  <a
                    href={profileResult.url}
                    target="_blank"
                    rel="noreferrer"
                    className="profile-handle-link"
                  >
                    <span>{profileResult.handle}</span>
                    <IconExternalLink className="w-3 h-3" />
                  </a>
                  <span className="profile-stats-dot">•</span>
                  <span className="profile-stats-text">{profileResult.stats}</span>
                </div>
              </div>
            </div>

            {/* Thao tác chọn và tải tệp ZIP */}
            <div className="profile-actions-bar">
              <div className="selection-counter">
                <span>Đã chọn:</span>
                <strong>{selectedCount}/{profileResult.media.length}</strong>
              </div>
              <div className="action-buttons-group">
                <button
                  type="button"
                  className="btn-secondary-action"
                  onClick={() => toggleSelectAll(true)}
                >
                  Chọn tất cả
                </button>
                <button
                  type="button"
                  className="btn-secondary-action"
                  onClick={() => toggleSelectAll(false)}
                >
                  Bỏ chọn
                </button>
                <button
                  type="button"
                  className={`btn-primary-zip-download ${isZipDownloading ? 'is-loading' : ''}`}
                  onClick={handleDownloadSelectedZip}
                  disabled={isZipDownloading || selectedCount === 0}
                  title="Tải tất cả các mục đã chọn thành file ZIP"
                >
                  {isZipDownloading ? (
                    <>
                      <span className="spinner-dots-sm" />
                      <span>{zipProgressText || 'Đang nén & tải ZIP...'}</span>
                    </>
                  ) : (
                    <>
                      <IconZip className="w-4 h-4" />
                      <span>Tải tệp đã chọn ({selectedCount}) (.ZIP)</span>
                    </>
                  )}
                </button>
              </div>
            </div>
          </div>

          {/* Grid danh sách các phương tiện (Video & Hình ảnh) */}
          <div className="profile-media-grid">
            {filteredMedia.map((item) => {
              const isSelected = !!selectedMediaIds[item.id]
              return (
                <div
                  key={item.id}
                  className={`media-card-item ${isSelected ? 'is-selected' : ''}`}
                  onClick={() => toggleSelect(item.id)}
                >
                  <div className="media-preview-box">
                    <img
                      src={item.thumb}
                      alt={item.title}
                      loading="lazy"
                      decoding="async"
                      referrerPolicy="no-referrer"
                      onError={(e) => {
                        if (!e.currentTarget.dataset.fallback) {
                          e.currentTarget.dataset.fallback = '1'
                          e.currentTarget.src = buildProxyImageUrl(item.thumb)
                        }
                      }}
                    />
                    
                    <div className={`card-select-checkbox ${isSelected ? 'is-selected' : ''}`} title={isSelected ? 'Bỏ chọn mục này' : 'Chọn mục này'}>
                      {isSelected && <IconCheck className="select-check-icon" />}
                    </div>

                    {item.isReel ? (
                      <span className="media-type-badge-reel">
                        <IconVideo className="w-3 h-3" />
                        <span>REEL</span>
                      </span>
                    ) : item.isShort ? (
                      <span className="media-type-badge-short">
                        <IconVideo className="w-3 h-3" />
                        <span>SHORT</span>
                      </span>
                    ) : item.type === 'video' ? (
                      <span className="media-type-badge-video">
                        <IconVideo className="w-3 h-3" />
                        <span>VIDEO</span>
                      </span>
                    ) : (
                      <span className="media-type-badge-image">
                        <IconImage className="w-3 h-3" />
                        <span>ẢNH</span>
                      </span>
                    )}

                    {item.duration && (
                      <span className="media-duration-badge">{item.duration}</span>
                    )}
                  </div>

                  <div className="media-card-body">
                    <div className="media-card-title" title={item.title}>
                      {item.title}
                    </div>
                    <div className="media-card-footer">
                      <span className="media-spec-info">
                        {item.quality} • {item.size}
                      </span>
                      <button
                        type="button"
                        className="btn-single-download-action"
                        onClick={(e) => {
                          e.stopPropagation()
                          handleDownloadSingleMedia(item)
                        }}
                        title={item.type === 'video' ? 'Tải video này' : 'Tải hình ảnh này'}
                      >
                        <IconDownload className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
