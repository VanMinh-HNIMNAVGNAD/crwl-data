import { useState, useEffect, useRef } from 'react'
import {
  IconDownload,
  IconAudio,
  IconVideo,
  IconMute,
  IconClose,
  IconCheck,
  IconZip,
  IconPlay,
  IconChevronDown,
  IconCopy,
  IconExternalLink,
  IconSubtitle,
  IconScissors,
  IconImage,
  IconInfo,
} from './Icons'
import { FORMAT_OPTIONS, detectPlatform, validatePlatformUrl, PLATFORMS, getPlatform } from '../constants'
import PlatformBadges from './PlatformBadges'
import {
  extractMedia,
  resolveShortUrl,
  buildStreamDownloadUrl,
  buildSubtitleDownloadUrl,
  buildProxyMediaUrl,
  buildProxyImageUrl,
  buildThumbnailDownloadUrl,
  downloadZipArchive,
  triggerFileDownload,
} from '../services/api'

export default function SingleDownloader() {
  const [url, setUrl] = useState('')
  const [selectedFormat, setSelectedFormat] = useState('all')
  const [streamFilter, setStreamFilter] = useState('all') // 'all' | 'full' | 'mute' | 'audio' | 'subtitles'
  const [qualityFilter, setQualityFilter] = useState('all')
  const [isLoading, setIsLoading] = useState(false)
  const [result, setResult] = useState(null)
  const [extractError, setExtractError] = useState(null)
  const [toastMessage, setToastMessage] = useState('')
  const [selectedImages, setSelectedImages] = useState({})
  const [isFormatDropdownOpen, setIsFormatDropdownOpen] = useState(false)
  const [downloadingId, setDownloadingId] = useState(null)
  const [isZipDownloading, setIsZipDownloading] = useState(false)
  const [zipProgressText, setZipProgressText] = useState('')
  const formatDropdownRef = useRef(null)

  // Quản lý nền tảng được chọn (chỉ được chọn 1 nền tảng)
  const [selectedPlatform, setSelectedPlatform] = useState(null)
  const [validationState, setValidationState] = useState({ valid: true, status: 'empty', message: '' })
  const [isResolvingUrl, setIsResolvingUrl] = useState(false)
  const [resolvedUrl, setResolvedUrl] = useState(null)

  // Kiểm tra tính hợp lệ của URL theo chuẩn hostname/domain (không so sánh chuỗi tuỳ tiện)
  // và hỗ trợ giải mã chuyển hướng link rút gọn an toàn
  useEffect(() => {
    const trimmed = url.trim()
    if (!trimmed) {
      setValidationState({ valid: true, status: 'empty', message: '' })
      setIsResolvingUrl(false)
      setResolvedUrl(null)
      return
    }

    const initial = validatePlatformUrl(trimmed, selectedPlatform)

    if (initial.status === 'needs_resolve') {
      setValidationState({
        valid: true,
        status: 'needs_resolve',
        message: initial.message,
      })
      setIsResolvingUrl(true)

      const timer = setTimeout(async () => {
        try {
          const res = await resolveShortUrl(trimmed, selectedPlatform)
          if (res.platform) {
            const pObj = getPlatform(res.platform)
            const isMatch = selectedPlatform
              ? res.platform === selectedPlatform || pObj?.id === selectedPlatform
              : true
            const expectedName = selectedPlatform
              ? getPlatform(selectedPlatform)?.name || selectedPlatform.toUpperCase()
              : ''
            const detectedName = pObj?.name || res.platform.toUpperCase()

            if (isMatch) {
              setValidationState({
                valid: true,
                status: 'matched',
                platform: res.platform,
                resolvedUrl: res.resolvedUrl,
                message: res.isShortened
                  ? `✓ Hợp lệ: Link rút gọn chuyển hướng đến ${detectedName}`
                  : `✓ Hợp lệ: Liên kết ${detectedName}`,
              })
              setResolvedUrl(res.resolvedUrl)
            } else {
              setValidationState({
                valid: false,
                status: 'mismatched',
                platform: res.platform,
                resolvedUrl: res.resolvedUrl,
                message: `⚠️ Link rút gọn này dẫn tới ${detectedName}, không khớp với ${expectedName} bạn đã chọn!`,
              })
            }
          } else {
            if (selectedPlatform) {
              const expectedName =
                PLATFORMS.find((p) => p.id === selectedPlatform)?.name || selectedPlatform.toUpperCase()
              setValidationState({
                valid: false,
                status: 'mismatched',
                message: `⚠️ Liên kết rút gọn không dẫn tới nền tảng ${expectedName}!`,
              })
            } else {
              setValidationState({
                valid: true,
                status: 'ok',
                resolvedUrl: res.resolvedUrl,
                message: 'Đã sẵn sàng tải liên kết',
              })
            }
          }
        } catch {
          setValidationState({
            valid: !selectedPlatform,
            status: selectedPlatform ? 'unresolved' : 'ok',
            message: selectedPlatform ? 'Không thể xác định đích đến của link rút gọn này' : '',
          })
        } finally {
          setIsResolvingUrl(false)
        }
      }, 450)

      return () => clearTimeout(timer)
    } else {
      setIsResolvingUrl(false)
      setResolvedUrl(null)
      setValidationState(initial)
    }
  }, [url, selectedPlatform])

  // Đóng dropdown chọn định dạng khi click ra ngoài hoặc nhấn ESC
  useEffect(() => {
    if (!isFormatDropdownOpen) return

    const handleClickOutside = (e) => {
      if (formatDropdownRef.current && !formatDropdownRef.current.contains(e.target)) {
        setIsFormatDropdownOpen(false)
      }
    }

    const handleKeyDown = (e) => {
      if (e.key === 'Escape') {
        setIsFormatDropdownOpen(false)
      }
    }

    document.addEventListener('pointerdown', handleClickOutside)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('pointerdown', handleClickOutside)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [isFormatDropdownOpen])

  // Tính năng Trimming (Cắt clip)
  const [isTrimmingEnabled, setIsTrimmingEnabled] = useState(false)
  const [trimStart, setTrimStart] = useState('')
  const [trimEnd, setTrimEnd] = useState('')

  // Tính năng nâng cao
  const [sponsorBlock, setSponsorBlock] = useState(false)
  const [embedThumbnail, setEmbedThumbnail] = useState(false)
  const [embedMetadata, setEmbedMetadata] = useState(false)

  // Modal / Accordion xem chi tiết
  const [showDetails, setShowDetails] = useState(false)

  const showToast = (msg) => {
    setToastMessage(msg)
    setTimeout(() => setToastMessage(''), 3500)
  }

  const handlePlatformSelect = (pId) => {
    if (selectedPlatform === pId) {
      setSelectedPlatform(null)
      showToast('Đã bỏ chọn nền tảng (chế độ tự do nhận diện mọi nền tảng)')
    } else {
      setSelectedPlatform(pId)
      const pObj = PLATFORMS.find((p) => p.id === pId)
      const pName = pObj ? pObj.name : pId.toUpperCase()
      showToast(`Đã chọn ${pName}: Vui lòng điền đúng link hoặc link rút gọn của ${pName}!`)
      const el = document.querySelector('.main-url-input')
      if (el) el.focus()
    }
  }

  const handleClear = () => {
    setUrl('')
    setResult(null)
    setExtractError(null)
    setIsTrimmingEnabled(false)
    setTrimStart('')
    setTrimEnd('')
    setShowDetails(false)
    setResolvedUrl(null)
    setIsResolvingUrl(false)
    setValidationState({ valid: true, status: 'empty', message: '' })
  }

  const handleFetch = async (customUrl = null) => {
    const rawTarget = (customUrl || url).trim()
    if (!rawTarget) {
      showToast('Vui lòng nhập hoặc dán liên kết cần tải xuống!')
      return
    }

    let targetUrl = resolvedUrl || rawTarget

    // Kiểm tra tính hợp lệ nếu đang ép buộc nền tảng
    if (selectedPlatform) {
      const pObj = PLATFORMS.find((p) => p.id === selectedPlatform)
      const pName = pObj ? pObj.name : selectedPlatform.toUpperCase()

      // Nếu đang sai nền tảng:
      if (!validationState.valid) {
        const errorMsg = validationState.message || `Vui lòng nhập đúng liên kết thuộc ${pName}!`
        showToast(errorMsg)
        setExtractError(errorMsg)
        return
      }

      // Nếu là link rút gọn đang cần giải mã:
      if (isResolvingUrl || validationState.status === 'needs_resolve') {
        setIsLoading(true)
        try {
          const res = await resolveShortUrl(rawTarget, selectedPlatform)
          const pObj = getPlatform(res.platform)
          const isMatch = pObj ? pObj.id === selectedPlatform : res.platform === selectedPlatform
          if (!isMatch) {
            const destName =
              pObj?.name ||
              (res.platform ? res.platform.toUpperCase() : 'trang ngoài')
            const msg = `⚠️ Link rút gọn này dẫn tới ${destName}, không khớp với ${pName} bạn đã chọn!`
            showToast(msg)
            setExtractError(msg)
            setIsLoading(false)
            return
          }
          if (res.resolvedUrl) {
            targetUrl = res.resolvedUrl
            setResolvedUrl(res.resolvedUrl)
          }
        } catch {
          // Bỏ qua lỗi mạng cục bộ nếu vẫn muốn thử
        }
      }
    } else if (isResolvingUrl || validationState.status === 'needs_resolve') {
      try {
        const res = await resolveShortUrl(rawTarget)
        if (res.resolvedUrl) {
          targetUrl = res.resolvedUrl
          setResolvedUrl(res.resolvedUrl)
        }
      } catch {}
    }

    setIsLoading(true)
    setResult(null)
    setExtractError(null)

    try {
      const data = await extractMedia(targetUrl)
      setResult(data)

      if (data.type === 'album' && data.images) {
        const initialMap = {}
        data.images.forEach((img) => {
          initialMap[img.id] = true
        })
        setSelectedImages(initialMap)
      }

      showToast(`Đã phân tích thành công nội dung từ ${data.platform ? data.platform.toUpperCase() : 'mạng xã hội'}!`)
    } catch (err) {
      const errMsg = err.message || 'Không thể trích xuất liên kết này. Vui lòng kiểm tra lại URL!'
      showToast(errMsg)
      setExtractError(errMsg)
    } finally {
      setIsLoading(false)
    }
  }

  const handleDownloadStream = (streamIndex, stream) => {
    setDownloadingId(streamIndex)
    showToast(`Đang kết nối luồng tải: ${stream.quality}...`)

    const isAudio = stream.streamType === 'audio'
    const isMute = stream.streamType === 'mute'
    const targetUrl = (stream.url && (stream.url.includes('.m3u8') || stream.url.includes('.mpd')))
      ? stream.url
      : (result.originalUrl || url)
    const referer = result.authorUrl?.startsWith('http') ? result.authorUrl : (result.originalUrl || url)
    const ext = isAudio ? stream.format.toLowerCase() : stream.format ? stream.format.toLowerCase() : 'mp4'

    const isYouTube = targetUrl.includes('youtube.com') || targetUrl.includes('youtu.be')

    const downloadEndpoint = buildStreamDownloadUrl({
      url: targetUrl,
      formatId: stream.formatId,
      isAudio,
      title: result.title,
      audioFormat: isAudio ? stream.format.toLowerCase() : undefined,
      audioBitrate: stream.bitrate ? stream.bitrate.replace('kbps', 'k') : undefined,
      startTime: isTrimmingEnabled && trimStart ? trimStart : undefined,
      endTime: isTrimmingEnabled && trimEnd ? trimEnd : undefined,
      format: stream.format ? stream.format.toLowerCase() : undefined,
      streamType: stream.streamType,
      isMute,
      sponsorBlock: sponsorBlock && isYouTube,
      embedThumbnail: embedThumbnail && isAudio,
      embedMetadata: embedMetadata && isAudio,
      referer: result.platform === 'movie' ? referer : undefined,
    })

    triggerFileDownload(downloadEndpoint, `${result.title || 'media'}.${ext}`)

    setTimeout(() => {
      setDownloadingId(null)
      showToast(`Đã bắt đầu truyền dữ liệu: "${stream.quality}"`)
    }, 1500)
  }

  const handleDownloadSubtitle = (lang, format = 'vtt') => {
    showToast(`Đang tải phụ đề [${lang.toUpperCase()}] (.${format})...`)
    const targetUrl = result.originalUrl || url
    const downloadEndpoint = buildSubtitleDownloadUrl({
      url: targetUrl,
      lang,
      format,
      title: result.title,
    })
    triggerFileDownload(downloadEndpoint, `${result.title || 'subtitle'}_${lang}.${format}`)
  }

  const handleDownloadThumbnail = () => {
    const targetUrl = result.originalUrl || url
    if (!targetUrl) {
      showToast('Không tìm thấy ảnh bìa chất lượng cao!')
      return
    }
    showToast('Đang tải ảnh bìa gốc độ phân giải cao...')
    // Thử dùng API endpoint download/thumbnail (yt-dlp trích xuất trực tiếp)
    // nếu có originalUrl, ngược lại fallback về proxy ảnh
    const directThumb = result.highResThumbnail || result.thumbnail
    if (result.type === 'video' || result.type === 'live') {
      const downloadEndpoint = buildThumbnailDownloadUrl({ url: targetUrl, title: result.title })
      triggerFileDownload(downloadEndpoint, `${result.title || 'thumbnail'}.jpg`)
    } else if (directThumb) {
      const downloadEndpoint = buildProxyMediaUrl(directThumb, `${result.title || 'thumbnail'}.jpg`)
      triggerFileDownload(downloadEndpoint, `${result.title || 'thumbnail'}.jpg`)
    } else {
      showToast('Không tìm thấy ảnh bìa!')
    }
  }

  const handleDownloadSingleMediaItem = (item) => {
    showToast(`Đang tải tệp "${item.title}"...`)
    const ext = item.ext || (item.type === 'video' ? 'mp4' : 'jpg')
    const fileName = `${item.title || 'media'}.${ext}`
    const downloadEndpoint = buildProxyMediaUrl(item.url, fileName)
    triggerFileDownload(downloadEndpoint, fileName)
  }

  const toggleImageSelect = (id) => {
    setSelectedImages((prev) => ({
      ...prev,
      [id]: !prev[id],
    }))
  }

  const toggleSelectAllImages = (all = true) => {
    if (!result || !result.images) return
    const nextMap = {}
    if (all) {
      result.images.forEach((img) => {
        nextMap[img.id] = true
      })
    }
    setSelectedImages(nextMap)
  }

  const handleDownloadSelectedImages = async () => {
    if (!result || !result.images) return
    const selectedList = result.images.filter((img) => selectedImages[img.id])
    const selectedCount = selectedList.length

    if (selectedCount === 0) {
      showToast('Vui lòng chọn ít nhất 1 tệp để tải xuống!')
      return
    }

    setIsZipDownloading(true)
    setZipProgressText(`Đang chuẩn bị ${selectedCount} tệp...`)
    showToast(`Đang đóng gói ${selectedCount} tệp vào ZIP tốc độ cao...`)

    try {
      const items = selectedList.map((img, idx) => ({
        url: img.url,
        filename: `${img.title || `media_${idx + 1}`}.${img.ext || (img.type === 'video' ? 'mp4' : 'jpg')}`,
        referer: result.platform ? `https://www.${result.platform}.com` : undefined,
      }))

      const cleanZipTitle = `${result.title || 'Album_Media'}`.replace(/[/\\?%*:|"<>]/g, '_')
      await downloadZipArchive(items, cleanZipTitle, (progress) => {
        if (progress.receivedBytes) {
          const mb = (progress.receivedBytes / (1024 * 1024)).toFixed(1)
          setZipProgressText(`Đang tải (${mb} MB)...`)
        }
      })
      showToast(`Đã tải xuống thành công tệp nén ${cleanZipTitle}.zip!`)
    } catch (err) {
      console.error('ZIP download error:', err)
      showToast(err.message || 'Lỗi khi tải tệp nén')
    } finally {
      setIsZipDownloading(false)
      setZipProgressText('')
    }
  }

  const handleSelectChapter = (ch, idx) => {
    setIsTrimmingEnabled(true)
    setTrimStart(ch.startFormatted || '')
    const nextChapter = result?.chapters?.[idx + 1]
    if (nextChapter && nextChapter.startFormatted) {
      setTrimEnd(nextChapter.startFormatted)
    } else {
      setTrimEnd('')
    }
    showToast(`Đã chọn phân đoạn "${ch.title}" (${ch.startFormatted}) để cắt`)
  }

  const filteredStreams = result && result.streams ? result.streams.filter((s) => {
    if (streamFilter === 'full' && s.streamType !== 'full') return false
    if (streamFilter === 'mute' && s.streamType !== 'mute') return false
    if (streamFilter === 'audio' && s.streamType !== 'audio') return false

    if (qualityFilter !== 'all') {
      if (!s.quality?.toLowerCase().includes(qualityFilter.toLowerCase())) {
        return false
      }
    }

    if (selectedFormat && selectedFormat !== 'all') {
      if (!s.format || s.format.toLowerCase() !== selectedFormat.toLowerCase()) return false
    }

    return true
  }) : []

  const selectedPlatformObj = selectedPlatform ? PLATFORMS.find((p) => p.id === selectedPlatform) : null
  const inputPlaceholder = selectedPlatformObj
    ? `Đang chọn ${selectedPlatformObj.name}: Điền đúng liên kết hoặc link rút gọn của ${selectedPlatformObj.name}...`
    : 'Dán liên kết video, bài viết hoặc link rút gọn (YouTube, TikTok, Facebook, Instagram...)'

  return (
    <div className="single-downloader-container">
      {/* Toast Notification */}
      {toastMessage && (
        <div className="toast-notification">
          <span>{toastMessage}</span>
        </div>
      )}

      {/* Main Single Bar */}
      <div className="main-search-wrapper">
        <div className={`search-bar-container ${!validationState.valid && url.trim() ? 'has-error-border' : ''}`}>
          <div className="input-group">
            <input
              type="text"
              className={`main-url-input ${!validationState.valid && url.trim() ? 'has-validation-error' : ''}`}
              placeholder={inputPlaceholder}
              value={url}
              onChange={(e) => setUrl(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleFetch()}
              autoComplete="off"
            />
            {url && (
              <button
                type="button"
                className="btn-clear-x"
                onClick={handleClear}
                title="Xóa liên kết"
                aria-label="Xóa"
              >
                <IconClose className="w-3.5 h-3.5" />
              </button>
            )}
          </div>

          <div className="format-selector-wrapper" ref={formatDropdownRef}>
            <button
              type="button"
              className={`format-dropdown-trigger ${isFormatDropdownOpen ? 'is-open' : ''}`}
              onClick={() => setIsFormatDropdownOpen(!isFormatDropdownOpen)}
              title="Chọn định dạng tải về"
            >
              <span>{selectedFormat.toUpperCase()}</span>
              <IconChevronDown className="w-3.5 h-3.5 chevron-icon" />
            </button>

            {isFormatDropdownOpen && (
              <div className="format-dropdown-menu">
                <div className="format-dropdown-options">
                  <button
                    type="button"
                    className={`format-option-item ${selectedFormat === 'all' ? 'is-selected' : ''}`}
                    onClick={() => {
                      setSelectedFormat('all')
                      setIsFormatDropdownOpen(false)
                    }}
                  >
                    <span>Tất cả định dạng</span>
                    {selectedFormat === 'all' && <IconCheck className="w-4 h-4 check-icon" />}
                  </button>
                  {FORMAT_OPTIONS.map((fmt) => (
                    <button
                      key={fmt.id}
                      type="button"
                      className={`format-option-item ${selectedFormat === fmt.id ? 'is-selected' : ''}`}
                      onClick={() => {
                        setSelectedFormat(fmt.id)
                        setIsFormatDropdownOpen(false)
                      }}
                    >
                      <div className="format-title-row">
                        <strong>{fmt.label}</strong>
                        <span className="format-desc-inline">({fmt.type})</span>
                      </div>
                      {selectedFormat === fmt.id && <IconCheck className="w-4 h-4 check-icon" />}
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>

          <button
            type="button"
            className={`btn-primary-fetch ${isLoading ? 'is-loading' : ''}`}
            onClick={() => handleFetch()}
            disabled={isLoading || (!validationState.valid && !!url.trim())}
            title={!validationState.valid && !!url.trim() ? validationState.message : 'Phân tích và tải'}
          >
            {isLoading ? (
              <>
                <span className="spinner-dots" />
                <span>Đang phân tích...</span>
              </>
            ) : (
              <span>Phân tích</span>
            )}
          </button>
        </div>

        {/* Thanh trạng thái kiểm tra URL & Link rút gọn theo thời gian thực */}
        {url.trim() && (validationState.message || isResolvingUrl) && (
          <div
            className={`url-validation-banner ${
              isResolvingUrl
                ? 'is-resolving'
                : validationState.valid
                ? 'is-valid'
                : 'is-invalid'
            }`}
          >
            {isResolvingUrl ? (
              <div className="validation-item is-resolving">
                <span className="spinner-dots-sm" />
                <span>{validationState.message || 'Đang giải mã liên kết rút gọn và kiểm tra nền tảng đích...'}</span>
              </div>
            ) : !validationState.valid ? (
              <div className="validation-item is-error">
                <IconInfo className="w-4 h-4 flex-shrink-0" />
                <span>{validationState.message}</span>
              </div>
            ) : validationState.status === 'matched' ? (
              <div className="validation-item is-success">
                <IconCheck className="w-4 h-4 flex-shrink-0" />
                <span>{validationState.message}</span>
              </div>
            ) : null}
          </div>
        )}

        {/* Danh sách các nền tảng hỗ trợ (Platform Badges - Chỉ được chọn 1 nền tảng) */}
        <PlatformBadges
          selectedPlatform={selectedPlatform}
          activePlatform={selectedPlatform || detectPlatform(url)}
          onSelectPlatform={handlePlatformSelect}
          onClearPlatform={() => setSelectedPlatform(null)}
        />

        {(selectedPlatform === 'movie' || detectPlatform(url) === 'movie') && (
          <div className="movie-tip-banner" style={{ marginTop: '0.75rem', padding: '0.625rem 0.875rem', borderRadius: '0.625rem', background: 'rgba(245, 158, 11, 0.1)', border: '1px solid rgba(245, 158, 11, 0.3)', color: '#fef3c7', fontSize: '0.8125rem', display: 'flex', alignItems: 'center', gap: '0.5rem', lineHeight: '1.4' }}>
            <span>🎬 <strong>Mẹo Phim &amp; HLS:</strong> Hỗ trợ link trang web phim hoặc link luồng <code style={{ background: 'rgba(0,0,0,0.3)', padding: '0.125rem 0.25rem', borderRadius: '0.25rem', color: '#fde68a' }}>(.m3u8</code> / <code style={{ background: 'rgba(0,0,0,0.3)', padding: '0.125rem 0.25rem', borderRadius: '0.25rem', color: '#fde68a' }}>.mpd hoặc các định dạng mp4, .ts, master )</code> trực tiếp. Nếu web phim dùng player nhúng sâu hoặc anti-bot, bạn có thể bấm <strong>F12 &gt; Network &gt; lọc &quot;m3u8&quot;</strong>, copy link dán vào đây để tải!</span>
          </div>
        )}
      </div>

      {/* Cảnh báo lỗi trích xuất */}
      {extractError && (
        <div className="crawler-alert-card extract-error-alert">
          <div className="crawler-alert-content">
            <div className="crawler-alert-title">
              <IconInfo className="w-4 h-4 flex-shrink-0" />
              <span>Không thể trích xuất nội dung:</span>
            </div>
            <div>{extractError}</div>
          </div>
          <button
            type="button"
            className="btn-alert-close"
            onClick={() => setExtractError(null)}
            title="Đóng thông báo"
          >
            <IconClose className="w-4 h-4" />
          </button>
        </div>
      )}

      {/* Kết quả phân tích */}
      {result && (
        <div className="media-result-card">
          <div className="result-card-header">
            <div className="result-media-badge">
              <span className="platform-tag">
                {result.platform.toUpperCase()}
              </span>
              <span className={`media-type-pill ${result.isLive ? 'media-type-pill-live' : result.isReel ? 'media-type-pill-reel' : result.isShort ? 'media-type-pill-short' : ''}`}>
                {result.isLive
                  ? 'ĐANG LIVE'
                  : result.isReel
                  ? 'REELS'
                  : result.isShort
                  ? 'SHORTS'
                  : result.type === 'album'
                  ? 'Album đa phương tiện'
                  : result.type === 'playlist'
                  ? 'Danh sách phát'
                  : result.type === 'live'
                  ? 'LIVE'
                  : 'Video'}
              </span>
            </div>

            <div className="result-header-actions">
              {/* Nút tải ảnh bìa gốc */}
              {(result.highResThumbnail || result.thumbnail || result.originalUrl) && (
                <button
                  type="button"
                  className="btn-action-icon"
                  onClick={handleDownloadThumbnail}
                  title="Tải ảnh bìa gốc chất lượng cao"
                >
                  <IconImage className="w-3.5 h-3.5" />
                  <span>Ảnh bìa</span>
                </button>
              )}

              {/* Nút bật/tắt cắt clip */}
              {(result.type === 'video' || result.type === 'live') && (
                <button
                  type="button"
                  className={`btn-action-icon ${isTrimmingEnabled ? 'btn-action-active' : ''}`}
                  onClick={() => setIsTrimmingEnabled(!isTrimmingEnabled)}
                  title="Cắt đoạn video theo thời gian"
                >
                  <IconScissors className="w-3.5 h-3.5" />
                  <span>{isTrimmingEnabled ? 'Đang cắt clip' : 'Cắt clip'}</span>
                </button>
              )}

              {/* Nút xem chi tiết / mô tả */}
              {(result.description || result.chapters) && (
                <button
                  type="button"
                  className={`btn-action-icon ${showDetails ? 'btn-action-active' : ''}`}
                  onClick={() => setShowDetails(!showDetails)}
                  title="Xem mô tả và các chương"
                >
                  <IconInfo className="w-3.5 h-3.5" />
                  <span>Chi tiết</span>
                </button>
              )}

              <button
                type="button"
                className="btn-action-icon"
                onClick={() => {
                  navigator.clipboard.writeText(url)
                  showToast('Đã sao chép liên kết!')
                }}
                title="Sao chép liên kết"
              >
                <IconCopy className="w-3.5 h-3.5" />
                <span>Sao chép</span>
              </button>

              <button
                type="button"
                className="btn-clear-x"
                onClick={handleClear}
                title="Đóng kết quả"
                aria-label="Đóng"
              >
                <IconClose className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>

          {/* Thanh công cụ Cắt clip (Video Trimming) nếu kích hoạt */}
          {isTrimmingEnabled && (
            <div className="trimming-bar-panel">
              <div className="trimming-header">
                <IconScissors className="w-4 h-4 text-emerald-400" />
                <span>Cắt đoạn video cần tải (Tùy chọn thời gian)</span>
              </div>
              <div className="trimming-inputs-row">
                <div className="trim-input-group">
                  <label>Bắt đầu:</label>
                  <input
                    type="text"
                    value={trimStart}
                    onChange={(e) => setTrimStart(e.target.value)}
                    className="trim-time-input"
                  />
                </div>
                <div className="trim-input-group">
                  <label>Kết thúc:</label>
                  <input
                    type="text"
                    value={trimEnd}
                    onChange={(e) => setTrimEnd(e.target.value)}
                    className="trim-time-input"
                  />
                </div>
              </div>
            </div>
          )}

          {/* Tính năng nâng cao cho Video YouTube */}
          {(result.type === 'video' || result.type === 'live') && result.streams && (
            <div className="advanced-options-bar">
              <span className="advanced-options-label">Tùy chọn nâng cao:</span>
              <div className="advanced-options-toggles">
                {/* SponsorBlock — chỉ cho YouTube */}
                {(result.originalUrl?.includes('youtube.com') || result.originalUrl?.includes('youtu.be')) && (
                  <button
                    type="button"
                    className={`toggle-option-btn ${sponsorBlock ? 'is-active' : ''}`}
                    onClick={() => setSponsorBlock(!sponsorBlock)}
                    title="Tự động bỏ qua đoạn quảng cáo, sponsor trong video"
                  >
                    <span className="toggle-dot" />
                    <span>Bỏ qua Sponsor</span>
                  </button>
                )}
                {/* Embed thumbnail — chỉ cho audio */}
                <button
                  type="button"
                  className={`toggle-option-btn ${embedThumbnail ? 'is-active' : ''}`}
                  onClick={() => setEmbedThumbnail(!embedThumbnail)}
                  title="Nhúng ảnh bìa vào file MP3/M4A (áp dụng khi tải âm thanh)"
                >
                  <span className="toggle-dot" />
                  <span>Nhúng Ảnh bìa (MP3)</span>
                </button>
                <button
                  type="button"
                  className={`toggle-option-btn ${embedMetadata ? 'is-active' : ''}`}
                  onClick={() => setEmbedMetadata(!embedMetadata)}
                  title="Nhúng metadata (tiêu đề, tác giả) vào file audio"
                >
                  <span className="toggle-dot" />
                  <span>Nhúng Metadata</span>
                </button>
              </div>
            </div>
          )}

          {/* Chi tiết Video / Mô tả / Chapters nếu mở */}
          {showDetails && (
            <div className="details-panel">
              {result.uploadDate && (
                <div className="details-row">
                  <strong>Ngày đăng:</strong> <span>{result.uploadDate}</span>
                </div>
              )}
              {result.likes && (
                <div className="details-row">
                  <strong>Lượt thích:</strong> <span>{result.likes}</span>
                </div>
              )}
              {result.description && (
                <div className="details-desc-box">
                  <strong>Mô tả nội dung:</strong>
                  <p>{result.description}</p>
                </div>
              )}
              {result.chapters && result.chapters.length > 0 && (
                <div className="details-chapters-box">
                  <strong>Mốc thời gian (Chapters):</strong>
                  <div className="chapters-list">
                    {result.chapters.map((ch, idx) => (
                      <button
                        key={idx}
                        type="button"
                        className="chapter-pill chapter-pill-interactive"
                        onClick={() => handleSelectChapter(ch, idx)}
                        title={`Cắt từ ${ch.startFormatted}`}
                      >
                        <span className="chapter-time">{ch.startFormatted}</span>
                        <span className="chapter-title">{ch.title}</span>
                        <span className="chapter-cut-tag">Cắt</span>
                      </button>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          <div className="result-main-grid">
            <div className="thumbnail-wrapper">
              <img
                src={result.thumbnail}
                alt={result.title}
                className="media-thumb-img"
                loading="eager"
                decoding="async"
                referrerPolicy="no-referrer"
                onError={(e) => {
                  if (!e.currentTarget.dataset.fallback) {
                    e.currentTarget.dataset.fallback = '1'
                    e.currentTarget.src = buildProxyImageUrl(result.thumbnail)
                  }
                }}
              />
              {result.type === 'video' && (
                <div className="thumb-play-overlay">
                  <span className="play-button-circle">
                    <IconPlay className="w-5 h-5 text-white" />
                  </span>
                  <span className="duration-pill">{result.duration}</span>
                </div>
              )}
            </div>

            <div className="result-info-col">
              <h3 className="media-title">{result.title}</h3>
              <div className="media-meta-row">
                <div className="meta-item">
                  <span className="meta-label">Tác giả:</span>
                  <a
                    href={result.authorUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="meta-value author-link"
                  >
                    {result.author}
                    <IconExternalLink className="w-3 h-3" />
                  </a>
                </div>
                <div className="meta-item">
                  <span className="meta-label">Lượt xem:</span>
                  <span className="meta-value">{result.views}</span>
                </div>
                {result.likes && (
                  <div className="meta-item">
                    <span className="meta-label">Lượt thích:</span>
                    <span className="meta-value">{result.likes}</span>
                  </div>
                )}
                {result.comments && (
                  <div className="meta-item">
                    <span className="meta-label">Bình luận:</span>
                    <span className="meta-value">{result.comments}</span>
                  </div>
                )}
                <div className="meta-item">
                  <span className="meta-label">Trạng thái:</span>
                  <span className={result.isLive ? 'meta-status-live' : 'meta-status-ready'}>
                    {result.isLive ? 'Đang phát trực tiếp' : 'Sẵn sàng tải'}
                  </span>
                </div>
              </div>
            </div>
          </div>

          {/* Album đa phương tiện (Ảnh + Video ngắn trong Carousel) */}
          {result.type === 'album' && result.images && (
            <div className="album-gallery-section">
              <div className="album-toolbar">
                <div className="album-title-group">
                  <h4>Danh sách tệp phương tiện ({result.images.length} tệp)</h4>
                  <span className="selected-count-badge">
                    Đã chọn: {Object.values(selectedImages).filter(Boolean).length}/{result.images.length}
                  </span>
                </div>
                <div className="album-actions action-buttons-group">
                  <button
                    type="button"
                    className="btn-secondary-action"
                    onClick={() => toggleSelectAllImages(true)}
                  >
                    Chọn tất cả
                  </button>
                  <button
                    type="button"
                    className="btn-secondary-action"
                    onClick={() => toggleSelectAllImages(false)}
                  >
                    Bỏ chọn
                  </button>
                  <button
                    type="button"
                    className={`btn-primary-zip-download ${isZipDownloading ? 'is-loading' : ''}`}
                    onClick={handleDownloadSelectedImages}
                    disabled={isZipDownloading || Object.values(selectedImages).filter(Boolean).length === 0}
                    title="Tải ảnh đã chọn thành file ZIP"
                  >
                    {isZipDownloading ? (
                      <>
                        <span className="spinner-dots-sm" />
                        <span>{zipProgressText || 'Đang nén & tải ZIP...'}</span>
                      </>
                    ) : (
                      <>
                        <IconZip className="w-4 h-4" />
                        <span>Tải tệp đã chọn ({Object.values(selectedImages).filter(Boolean).length}) (.ZIP)</span>
                      </>
                    )}
                  </button>
                </div>
              </div>

              <div className="album-grid">
                {result.images.map((img) => {
                  const isSelected = !!selectedImages[img.id]
                  return (
                    <div
                      key={img.id}
                      className={`gallery-card ${isSelected ? 'is-selected' : ''}`}
                      onClick={() => toggleImageSelect(img.id)}
                    >
                      <div className="gallery-thumb-container">
                        <img
                          src={img.thumb || img.url}
                          alt={img.title}
                          loading="lazy"
                          decoding="async"
                          referrerPolicy="no-referrer"
                          onError={(e) => {
                            if (!e.currentTarget.dataset.fallback) {
                              e.currentTarget.dataset.fallback = '1'
                              e.currentTarget.src = buildProxyImageUrl(img.thumb || img.url)
                            }
                          }}
                        />
                        <div className={`card-select-checkbox ${isSelected ? 'is-selected' : ''}`} title={isSelected ? 'Bỏ chọn mục này' : 'Chọn mục này'}>
                          {isSelected && <IconCheck className="select-check-icon" />}
                        </div>
                        {img.type === 'video' && (
                          <span className="media-type-badge-video">VIDEO</span>
                        )}
                        {img.type === 'gif' && (
                          <span className="media-type-badge-gif">GIF</span>
                        )}
                      </div>
                      <div className="gallery-card-footer">
                        <div className="gallery-meta">
                          <span className="gallery-img-title">{img.title}</span>
                          <span className="gallery-img-spec">
                            {img.resolution && `${img.resolution} • `}
                            {img.size}
                          </span>
                        </div>
                        <button
                          type="button"
                          className="btn-download-icon-sm"
                          onClick={(e) => {
                            e.stopPropagation()
                            handleDownloadSingleMediaItem(img)
                          }}
                          title="Tải tệp này"
                        >
                          <IconDownload className="w-4 h-4" />
                        </button>
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}

          {/* Bảng luồng Video & Âm thanh & Phụ đề */}
          {result.streams && (
            <div className="streams-table-section">
              <div className="result-filters-bar">
                <div className="segmented-control">
                  <button
                    type="button"
                    className={`segmented-btn ${streamFilter === 'all' ? 'active' : ''}`}
                    onClick={() => setStreamFilter('all')}
                  >
                    Tất cả
                  </button>
                  <button
                    type="button"
                    className={`segmented-btn ${streamFilter === 'full' ? 'active' : ''}`}
                    onClick={() => setStreamFilter('full')}
                  >
                    <IconVideo className="w-3.5 h-3.5" />
                    <span>Video có tiếng (HD/4K)</span>
                  </button>
                  <button
                    type="button"
                    className={`segmented-btn ${streamFilter === 'audio' ? 'active' : ''}`}
                    onClick={() => setStreamFilter('audio')}
                  >
                    <IconAudio className="w-3.5 h-3.5" />
                    <span>Chỉ Âm thanh (MP3/FLAC)</span>
                  </button>
                  <button
                    type="button"
                    className={`segmented-btn ${streamFilter === 'mute' ? 'active' : ''}`}
                    onClick={() => setStreamFilter('mute')}
                  >
                    <IconMute className="w-3.5 h-3.5" />
                    <span>Chỉ Video (Mute)</span>
                  </button>
                  {result.subtitles && result.subtitles.length > 0 && (
                    <button
                      type="button"
                      className={`segmented-btn ${streamFilter === 'subtitles' ? 'active' : ''}`}
                      onClick={() => setStreamFilter('subtitles')}
                    >
                      <IconSubtitle className="w-3.5 h-3.5" />
                      <span>Phụ đề ({result.subtitles.length})</span>
                    </button>
                  )}
                </div>

                {streamFilter !== 'subtitles' && (
                  <div className="quality-pills-group">
                    {['all', '2160p', '1440p', '1080p', '720p', '480p'].map((q) => (
                      <button
                        key={q}
                        type="button"
                        className={`quality-pill-btn ${qualityFilter === q ? 'active' : ''}`}
                        onClick={() => setQualityFilter(q)}
                      >
                        {q === 'all' ? 'Tất cả độ phân giải' : q}
                      </button>
                    ))}
                  </div>
                )}
              </div>

              {/* Danh sách Phụ đề nếu chọn tab phụ đề */}
              {streamFilter === 'subtitles' ? (
                <div className="subtitles-panel">
                  <div className="subtitles-header">
                    <h4>Danh sách phụ đề có sẵn cho video này</h4>
                    <span className="subtitles-subtitle">
                      Hỗ trợ định dạng chuẩn .SRT (cho mọi phần mềm xem video) và .VTT (cho trình duyệt)
                    </span>
                  </div>
                  <div className="subtitles-grid">
                    {result.subtitles.map((sub, idx) => (
                      <div key={idx} className="subtitle-card">
                        <div className="sub-info">
                          <span className="sub-lang-tag">{sub.lang.toUpperCase()}</span>
                          <span className="sub-name">{sub.name}</span>
                        </div>
                        <div className="sub-actions">
                          <button
                            type="button"
                            className="btn-sub-dl"
                            onClick={() => handleDownloadSubtitle(sub.lang, 'srt')}
                            title="Tải phụ đề SRT"
                          >
                            <IconDownload className="w-3.5 h-3.5" />
                            <span>Tải .SRT</span>
                          </button>
                          <button
                            type="button"
                            className="btn-sub-dl btn-sub-dl-secondary"
                            onClick={() => handleDownloadSubtitle(sub.lang, 'vtt')}
                            title="Tải phụ đề VTT"
                          >
                            <span>.VTT</span>
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              ) : filteredStreams.length === 0 ? (
                <div className="empty-streams-notice">
                  <p>Không tìm thấy định dạng tải xuống phù hợp với bộ lọc hiện tại.</p>
                  <button
                    type="button"
                    className="btn-reset-filters"
                    onClick={() => {
                      setStreamFilter('all')
                      setSelectedFormat('all')
                      setQualityFilter('all')
                    }}
                  >
                    Đặt lại bộ lọc
                  </button>
                </div>
              ) : (
                <div className="streams-list">
                  {filteredStreams.map((stream, idx) => {
                    const isDownloading = downloadingId === idx
                    return (
                      <div key={idx} className="stream-row-card">
                        <div className="stream-info-group">
                          <span className={`format-pill ${stream.streamType === 'full' ? 'format-pill-full' : ''}`}>
                            {stream.format}
                          </span>
                          <div className="stream-quality-col">
                            <span className="quality-name">{stream.quality}</span>
                            <span className="stream-extra-meta">
                              {stream.fps && `${stream.fps} • `}
                              {stream.bitrate && `${stream.bitrate} • `}
                              {stream.streamType === 'full' && 'Video + Âm thanh sắc nét'}
                              {stream.streamType === 'mute' && 'Chỉ Video (Không âm thanh)'}
                              {stream.streamType === 'audio' && 'Chỉ Âm thanh'}
                            </span>
                          </div>
                        </div>

                        <div className="stream-size-col">
                          <span className="stream-size-badge">{stream.size}</span>
                        </div>

                        <div className="stream-action-col">
                          <button
                            type="button"
                            className="btn-stream-download"
                            onClick={() => handleDownloadStream(idx, stream)}
                            disabled={isDownloading}
                          >
                            {isDownloading ? (
                              <span>Đang tải...</span>
                            ) : (
                              <>
                                <IconDownload className="w-4 h-4" />
                                <span>Tải xuống</span>
                              </>
                            )}
                          </button>
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
