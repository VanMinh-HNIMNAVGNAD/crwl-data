import { useState, useMemo } from 'react'
import {
  IconClose,
  IconPaste,
  IconVideo,
  IconImage,
  IconDownload,
  IconZip,
} from './Icons'
import { detectPlatform } from '../constants'
import {
  crawlProfile,
  startNativeDownload,
  downloadZipArchive,
  downloadAlbumBatch,
  onDownloadProgress,
  buildProxyImageUrl,
  selectDownloadDirectory,
  getAlwaysAskDownloadDir,
} from '../services/api'
import DownloadProgressCard from './DownloadProgressCard'

export default function AccountDownloader({ onShowToast }) {
  const [accountInput, setAccountInput] = useState('')
  const [selectedPlatform, setSelectedPlatform] = useState('auto')
  const [mediaTypeFilter, setMediaTypeFilter] = useState('all') // 'all' | 'video' | 'image'
  const [crawlLimit, setCrawlLimit] = useState('20')
  const [rangeFrom, setRangeFrom] = useState('1')
  const [rangeTo, setRangeTo] = useState('20')
  const [isCrawling, setIsCrawling] = useState(false)
  const [crawlProgress, setCrawlProgress] = useState(0)
  const [statusText, setStatusText] = useState('')

  // Kết quả quét tài khoản
  const [profileResult, setProfileResult] = useState(null)
  const [selectedBatchIds, setSelectedBatchIds] = useState({})
  const [downloadingId, setDownloadingId] = useState(null)
  const [nativeProgress, setNativeProgress] = useState(null)
  const [downloadStartTime, setDownloadStartTime] = useState(null)
  const [downloadTaskTitle, setDownloadTaskTitle] = useState('')
  const [isZipDownloading, setIsZipDownloading] = useState(false)

  // Tự động nhận diện platform từ input hoặc dùng platform đã chọn
  const detected = detectPlatform(accountInput)
  const activePlatform = selectedPlatform !== 'auto' ? selectedPlatform : (detected || undefined)

  const profileMediaList = useMemo(() => profileResult?.media || [], [profileResult])
  const selectedProfileCount = useMemo(
    () => Object.values(selectedBatchIds).filter(Boolean).length,
    [selectedBatchIds]
  )

  const handleClearAccount = () => {
    setAccountInput('')
    setProfileResult(null)
    setSelectedBatchIds({})
    setNativeProgress(null)
    setDownloadStartTime(null)
    setDownloadTaskTitle('')
    setCrawlProgress(0)
    setStatusText('')
    setDownloadingId(null)
    setIsZipDownloading(false)
  }

  const handlePaste = async () => {
    try {
      const text = await navigator.clipboard.readText()
      if (text) {
        setAccountInput(text.trim())
        onShowToast?.('Đã dán tài khoản từ bộ nhớ tạm')
      }
    } catch {
      onShowToast?.('Vui lòng dùng phím tắt Ctrl+V để dán')
    }
  }

  const handleStartCrawl = async (e) => {
    e?.preventDefault()
    const target = accountInput.trim()
    if (!target) {
      onShowToast?.('Vui lòng nhập tên người dùng (@username) hoặc liên kết!')
      return
    }

    if ((target.startsWith('@') || (!target.includes('.') && !target.includes('/'))) && (!activePlatform || activePlatform === 'auto')) {
      onShowToast?.('Vui lòng bấm chọn một nền tảng (Facebook, Instagram, X...) phía dưới để quét username!')
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
      let startNum = undefined
      let endNum = undefined
      let limitNum = 20

      if (isRange) {
        startNum = Math.max(1, parseInt(rangeFrom, 10) || 1)
        endNum = Math.max(startNum, parseInt(rangeTo, 10) || startNum)
        limitNum = Math.max(1, endNum - startNum + 1)
      } else if (crawlLimit === 'all') {
        limitNum = 0 // 0 = Không giới hạn số lượng, quét toàn bộ
      } else {
        limitNum = Math.max(1, parseInt(crawlLimit, 10) || 20)
      }

      setStatusText('Đang quét và lấy danh sách phương tiện...')
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
        setProfileResult(resultData)
        setSelectedBatchIds({})
        onShowToast?.(`Đã quét được ${resultData.media.length} tệp từ @${resultData.name || 'tài khoản'}!`)
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

  // Tải 1 tệp trong profile
  const handleDownloadProfileItem = async (item) => {
    setDownloadingId(item.id)

    let targetDir = undefined
    if (getAlwaysAskDownloadDir()) {
      try {
        targetDir = await selectDownloadDirectory()
        if (!targetDir) {
          onShowToast?.('Đã hủy tải do chưa chọn thư mục lưu')
          setDownloadingId(null)
          return
        }
      } catch (err) {
        console.warn('Lỗi chọn thư mục:', err)
      }
    }

    setDownloadStartTime(Date.now())
    setDownloadTaskTitle(item.title || 'Tệp tải xuống')
    setNativeProgress({ percent: 0, speed: '', eta: 'Khởi tạo luồng tải...', status: 'downloading' })

    try {
      onShowToast?.(`Bắt đầu tải: ${item.title?.slice(0, 30) || 'video'}...`)
      let unlisten = null
      try {
        unlisten = await onDownloadProgress((payload) => {
          setNativeProgress(payload)
        })
      } catch (e) {
        console.warn('Cannot attach progress listener:', e)
      }

      const res = await startNativeDownload({
        url: item.url,
        title: item.title,
        destDir: targetDir,
      })

      if (typeof unlisten === 'function') unlisten()

      if (res?.file_name) {
        setNativeProgress({ percent: 100, speed: '', eta: '00:00', status: 'completed' })
        onShowToast?.(`Đã lưu: ${res.file_name}`)
      }
    } catch (err) {
      setNativeProgress({ percent: 0, speed: '', eta: '', status: 'error' })
      onShowToast?.(err.message || 'Lỗi khi tải video')
    } finally {
      setTimeout(() => {
        setDownloadingId(null)
      }, 2000)
    }
  }

  // Chọn tất cả
  const handleToggleSelectAllProfile = () => {
    if (selectedProfileCount === profileMediaList.length) {
      setSelectedBatchIds({})
    } else {
      const all = {}
      profileMediaList.forEach((it) => {
        all[it.id] = true
      })
      setSelectedBatchIds(all)
    }
  }

  // Tải hàng loạt (thư mục riêng hoặc nén ZIP)
  const handleDownloadProfileBatch = async (asZip = false) => {
    const itemsToDownload = profileMediaList.filter((it) => selectedBatchIds[it.id])
    if (itemsToDownload.length === 0) {
      onShowToast?.('Vui lòng chọn ít nhất 1 tệp để tải')
      return
    }

    let targetDir = undefined
    if (getAlwaysAskDownloadDir()) {
      try {
        targetDir = await selectDownloadDirectory()
        if (!targetDir) {
          onShowToast?.('Đã hủy do chưa chọn thư mục lưu')
          return
        }
      } catch (err) {
        console.warn('Lỗi chọn thư mục:', err)
      }
    }

    setIsZipDownloading(true)
    setDownloadStartTime(Date.now())
    setDownloadTaskTitle(`${asZip ? 'Nén ZIP' : 'Tải'}: ${itemsToDownload.length} tệp`)
    setNativeProgress({ percent: 15, speed: '', eta: 'Đang chuẩn bị...', status: 'downloading' })

    try {
      const zipPayload = itemsToDownload.map((it) => ({
        url: it.url,
        filename: `${it.title || 'media'}_${it.id}.mp4`,
        referer: profileResult?.url,
      }))
      const res = await downloadAlbumBatch({
        items: zipPayload,
        albumName: `Profile_${profileResult?.name || 'Media'}`,
        destDir: targetDir,
        asZip: asZip,
      })
      setNativeProgress({ percent: 100, speed: '', eta: '00:00', status: 'completed' })
      onShowToast?.(res?.message || `Đã tải thành công (${itemsToDownload.length} tệp)!`)
    } catch (err) {
      setNativeProgress({ percent: 0, speed: '', eta: '', status: 'error' })
      onShowToast?.(err.message || 'Lỗi khi tải danh sách')
    } finally {
      setIsZipDownloading(false)
    }
  }

  return (
    <div className="downloader-pane">
      {/* Header khu vực Tải theo tài khoản */}
      <div className="pane-header">
        <h2 className="pane-title">Tải theo tài khoản</h2>
        <div className="pane-toggle-group">
          <button
            type="button"
            className={`pane-toggle-btn ${mediaTypeFilter === 'all' ? 'active' : ''}`}
            onClick={() => setMediaTypeFilter('all')}
          >
            Tất cả
          </button>
          <button
            type="button"
            className={`pane-toggle-btn ${mediaTypeFilter === 'video' ? 'active' : ''}`}
            onClick={() => setMediaTypeFilter('video')}
          >
            <IconVideo className="w-3.5 h-3.5" />
            <span>Video</span>
          </button>
          <button
            type="button"
            className={`pane-toggle-btn ${mediaTypeFilter === 'image' ? 'active' : ''}`}
            onClick={() => setMediaTypeFilter('image')}
          >
            <IconImage className="w-3.5 h-3.5" />
            <span>Ảnh</span>
          </button>
        </div>
      </div>

      {/* Form nhập liệu */}
      <div className="pane-input-section">
        <form onSubmit={handleStartCrawl} className="pane-form">
          <div className="input-group">
            <input
              type="text"
              className="pane-input"
              placeholder="Nhập @username hoặc link profile TikTok, Instagram, YouTube..."
              value={accountInput}
              onChange={(e) => {
                const val = e.target.value
                setAccountInput(val)
                if (!val.trim()) {
                  setProfileResult(null)
                  setSelectedBatchIds({})
                  setNativeProgress(null)
                  setDownloadStartTime(null)
                  setDownloadTaskTitle('')
                  setCrawlProgress(0)
                  setStatusText('')
                }
              }}
              disabled={isCrawling}
            />
            {accountInput ? (
              <button
                type="button"
                className="input-inline-btn"
                onClick={handleClearAccount}
                title="Xóa tài khoản"
              >
                <IconClose className="w-3.5 h-3.5" />
              </button>
            ) : (
              <button
                type="button"
                className="input-inline-btn"
                onClick={handlePaste}
                title="Dán từ bộ nhớ tạm"
              >
                <IconPaste className="w-3.5 h-3.5" />
              </button>
            )}
          </div>

          <div className="pane-control-row" style={{ marginBottom: '8px' }}>
            <div className="pills-group">
              <span className="control-label-text">Nền tảng:</span>
              {[
                { id: 'auto', label: 'Tự động' },
                { id: 'facebook', label: 'Facebook' },
                { id: 'instagram', label: 'Instagram' },
                { id: 'x', label: 'X (Twitter)' },
                { id: 'tiktok', label: 'TikTok' },
                { id: 'youtube', label: 'YouTube' },
                { id: 'pinterest', label: 'Pinterest' },
                { id: 'reddit', label: 'Reddit' },
              ].map((p) => (
                <button
                  key={p.id}
                  type="button"
                  className={`minimal-pill ${selectedPlatform === p.id ? 'active' : ''}`}
                  onClick={() => setSelectedPlatform(p.id)}
                >
                  {p.label}
                </button>
              ))}
            </div>
          </div>

          <div className="pane-control-row">
            <div className="pills-group">
              <span className="control-label-text">Số lượng:</span>
              {[
                { id: '20', label: '20' },
                { id: '50', label: '50' },
                { id: '100', label: '100' },
                { id: 'all', label: 'Tất cả' },
                { id: 'range', label: 'Khoảng' },
              ].map((item) => (
                <button
                  key={item.id}
                  type="button"
                  className={`minimal-pill ${crawlLimit === item.id ? 'active' : ''}`}
                  onClick={() => setCrawlLimit(item.id)}
                >
                  {item.label}
                </button>
              ))}

              {crawlLimit === 'range' && (
                <div className="range-box-inline">
                  <span>Từ</span>
                  <input
                    type="number"
                    className="range-input-clean"
                    value={rangeFrom}
                    onChange={(e) => setRangeFrom(e.target.value)}
                    min="1"
                    title="Số thứ tự bắt đầu"
                  />
                  <span>-</span>
                  <span>Đến</span>
                  <input
                    type="number"
                    className="range-input-clean"
                    value={rangeTo}
                    onChange={(e) => setRangeTo(e.target.value)}
                    min="1"
                    title="Số thứ tự kết thúc"
                  />
                </div>
              )}
            </div>

            <button
              type="submit"
              className="pane-submit-btn"
              disabled={isCrawling || !accountInput.trim()}
            >
              {isCrawling ? (
                <>
                  <span className="minimal-spinner" />
                  <span>Đang quét... {crawlProgress}%</span>
                </>
              ) : (
                <>
                  <IconVideo className="w-3.5 h-3.5" />
                  <span>Quét tài khoản</span>
                </>
              )}
            </button>
          </div>

          {statusText && (
            <div className="status-progress-line">
              <span className="validation-hint">{statusText}</span>
              {crawlProgress > 0 && (
                <div className="progress-track-small">
                  <div className="progress-fill" style={{ width: `${crawlProgress}%` }} />
                </div>
              )}
            </div>
          )}
        </form>
      </div>

      {/* Khu vực hiển thị kết quả Profile (Scrollable) */}
      <div className="pane-results-container">
        {profileResult && (
          <div className="result-content-wrap">
            {/* Header hồ sơ tài khoản */}
            <div className="profile-summary-row">
              <div className="profile-info-block">
                {profileResult.avatar && (
                  <img
                    src={buildProxyImageUrl(profileResult.avatar)}
                    alt=""
                    className="profile-avatar-img"
                    onError={(e) => {
                      e.target.style.display = 'none'
                    }}
                  />
                )}
                <div>
                  <h3 className="profile-name-title">
                    {profileResult.name || accountInput}
                  </h3>
                  <p className="profile-sub-meta">
                    {profileResult.platform?.toUpperCase()} • {profileMediaList.length} tệp phương tiện
                  </p>
                </div>
              </div>

              <div className="profile-top-actions">
                <button
                  type="button"
                  className="minimal-small-btn"
                  onClick={handleToggleSelectAllProfile}
                >
                  {selectedProfileCount === profileMediaList.length ? 'Bỏ chọn' : 'Chọn tất cả'}
                </button>
                {profileMediaList.length > 10 && (
                  <button
                    type="button"
                    className="minimal-small-btn"
                    onClick={() => {
                      const batch = {}
                      profileMediaList.slice(0, 10).forEach((it) => {
                        batch[it.id] = true
                      })
                      setSelectedBatchIds(batch)
                    }}
                    title="Chọn nhanh 10 tệp đầu tiên"
                  >
                    10 đầu
                  </button>
                )}
                {profileMediaList.length > 20 && (
                  <button
                    type="button"
                    className="minimal-small-btn"
                    onClick={() => {
                      const batch = {}
                      profileMediaList.slice(0, 20).forEach((it) => {
                        batch[it.id] = true
                      })
                      setSelectedBatchIds(batch)
                    }}
                    title="Chọn nhanh 20 tệp đầu tiên"
                  >
                    20 đầu
                  </button>
                )}
                {selectedProfileCount > 0 && (
                  <>
                    <button
                      type="button"
                      className="minimal-small-btn"
                      onClick={() => handleDownloadProfileBatch(false)}
                      disabled={isZipDownloading}
                      title="Tải toàn bộ tệp đã chọn trực tiếp vào thư mục riêng"
                    >
                      <span>📁 Tải thư mục ({selectedProfileCount})</span>
                    </button>
                    <button
                      type="button"
                      className="minimal-small-btn"
                      onClick={() => handleDownloadProfileBatch(true)}
                      disabled={isZipDownloading}
                      title="Nén toàn bộ tệp đã chọn thành một file ZIP duy nhất"
                    >
                      <IconZip className="w-3 h-3" />
                      <span>Tải ZIP ({selectedProfileCount})</span>
                    </button>
                  </>
                )}
                <button
                  type="button"
                  className="icon-close-small"
                  onClick={() => setProfileResult(null)}
                  title="Đóng kết quả"
                >
                  <IconClose className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>

            {/* Mục hiển thị Tiến trình chi tiết (Tốc độ, Thời gian đã tải, Ước tính ETA) */}
            {nativeProgress && (
              <DownloadProgressCard
                progress={nativeProgress}
                title={downloadTaskTitle}
                startTime={downloadStartTime}
                onDismiss={() => setNativeProgress(null)}
              />
            )}

            {/* Lưới tệp video/ảnh của tài khoản */}
            <div className="profile-grid">
              {profileMediaList.map((item) => {
                const isSelected = Boolean(selectedBatchIds[item.id])
                const isItemDownloading = downloadingId === item.id

                return (
                  <div
                    key={item.id}
                    className={`profile-grid-item ${isSelected ? 'is-selected' : ''}`}
                    onClick={() =>
                      setSelectedBatchIds((prev) => ({
                        ...prev,
                        [item.id]: !prev[item.id],
                      }))
                    }
                  >
                    <div className="profile-item-thumb-box">
                      <img
                        src={buildProxyImageUrl(item.thumb || item.url)}
                        alt=""
                        className="profile-item-thumb"
                        onError={(e) => {
                          e.target.style.opacity = '0.5'
                        }}
                      />
                      {item.duration && (
                        <span className="duration-tag">{item.duration}</span>
                      )}
                      <input
                        type="checkbox"
                        className="profile-item-checkbox"
                        checked={isSelected}
                        onChange={(e) => {
                          e.stopPropagation()
                          setSelectedBatchIds((prev) => ({
                            ...prev,
                            [item.id]: !prev[item.id],
                          }))
                        }}
                        onClick={(e) => e.stopPropagation()}
                      />
                    </div>

                    <div className="profile-item-details">
                      <span className="profile-item-title" title={item.title}>
                        {item.title || 'Phương tiện'}
                      </span>
                      <div className="profile-item-footer">
                        <span className="item-quality-pill">{item.quality || 'HD'}</span>
                        <button
                          type="button"
                          className="profile-download-btn"
                          onClick={(e) => {
                            e.stopPropagation()
                            handleDownloadProfileItem(item)
                          }}
                          disabled={isItemDownloading}
                          title="Tải video này"
                        >
                          {isItemDownloading ? (
                            <span className="minimal-spinner" />
                          ) : (
                            <IconDownload className="w-3 h-3" />
                          )}
                        </button>
                      </div>
                    </div>
                  </div>
                )
              })}
            </div>
          </div>
        )}

        {/* Trạng thái trống tối giản */}
        {!profileResult && (
          <div className="pane-empty-state">
            <p className="empty-subtle-hint">
              Nhập tài khoản hoặc kênh mạng xã hội để quét hàng loạt video và hình ảnh
            </p>
          </div>
        )}
      </div>
    </div>
  )
}
