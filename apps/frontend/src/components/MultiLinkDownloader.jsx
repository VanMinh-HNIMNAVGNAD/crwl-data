import { useState, useEffect, useRef, useMemo } from 'react'
import {
  IconDownload,
  IconAudio,
  IconVideo,
  IconClose,
  IconCheck,
  IconZip,
  IconChevronDown,
  IconCopy,
  IconExternalLink,
  IconImage,
  IconInfo,
  IconPaste,
  IconSparkles,
  IconTrash,
} from './Icons'
import { FORMAT_OPTIONS, detectPlatform, isGenericShortenerUrl, parseUrlHostname, PLATFORMS } from '../constants'
import PlatformBadges from './PlatformBadges'
import {
  extractMedia,
  resolveShortUrl,
  buildStreamDownloadUrl,
  buildProxyMediaUrl,
  buildProxyImageUrl,
  buildThumbnailDownloadUrl,
  downloadZipArchive,
  triggerFileDownload,
} from '../services/api'

// Mẫu link cho người dùng thử nghiệm nhanh
const SAMPLE_LINKS = {
  youtube: [
    'https://www.youtube.com/watch?v=dQw4w9WgXcQ',
    'https://youtu.be/jNQXAC9IVRw',
  ],
  tiktok: [
    'https://www.tiktok.com/@tiktok/video/7106594312292453678',
  ],
  facebook: [
    'https://www.facebook.com/watch/?v=10153231379946729',
  ],
}

export default function MultiLinkDownloader() {
  const [inputText, setInputText] = useState('')
  const [selectedPlatform, setSelectedPlatform] = useState(null)
  const [selectedFormat, setSelectedFormat] = useState('all')
  const [isFormatDropdownOpen, setIsFormatDropdownOpen] = useState(false)
  const formatDropdownRef = useRef(null)

  // Tiến trình giải mã
  const [isDecoding, setIsDecoding] = useState(false)
  const [decodingProgress, setDecodingProgress] = useState({ current: 0, total: 0, currentUrl: '' })

  // Danh sách kết quả sau khi giải mã
  const [decodedItems, setDecodedItems] = useState([])
  const [selectedItemIds, setSelectedItemIds] = useState({})
  const [expandedStreamsMap, setExpandedStreamsMap] = useState({})
  const [downloadingId, setDownloadingId] = useState(null)
  const [isZipDownloading, setIsZipDownloading] = useState(false)
  const [zipProgressText, setZipProgressText] = useState('')
  const [toastMessage, setToastMessage] = useState('')

  const showToast = (msg) => {
    setToastMessage(msg)
    setTimeout(() => setToastMessage(''), 3500)
  }

  // Đóng dropdown chọn format khi nhấp ra ngoài hoặc bấm Escape
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

  // Phân tích danh sách link người dùng nhập theo thời gian thực
  const parsedAnalysis = useMemo(() => {
    const rawLines = inputText
      .split('\n')
      .map((l) => l.trim())
      .filter(Boolean)

    const isOverLimit = rawLines.length > 10
    const limitedLines = rawLines.slice(0, 10)

    const list = limitedLines.map((line, idx) => {
      const parsed = parseUrlHostname(line)
      if (!parsed) {
        return {
          id: `line_${idx}`,
          index: idx + 1,
          url: line,
          isValidUrl: false,
          platform: null,
          platformName: 'Không hợp lệ',
          isShortener: false,
          error: 'URL không hợp lệ (cần bắt đầu bằng http:// hoặc https://)',
        }
      }

      const detected = detectPlatform(line)
      const isShort = isGenericShortenerUrl(line) || line.includes('youtu.be') || line.includes('pin.it') || line.includes('fb.watch')
      const pObj = detected ? PLATFORMS.find((p) => p.id === detected) : null

      return {
        id: `line_${idx}`,
        index: idx + 1,
        url: line,
        isValidUrl: true,
        platform: detected,
        platformName: pObj ? pObj.name : detected ? detected.toUpperCase() : isShort ? 'Link rút gọn' : 'Chưa rõ',
        isShortener: isShort,
        error: null,
      }
    })

    // Xác định nền tảng chung bắt buộc
    let requiredPlatform = selectedPlatform
    if (!requiredPlatform) {
      const firstWithPlatform = list.find((item) => item.platform)
      if (firstWithPlatform) {
        requiredPlatform = firstWithPlatform.platform
      }
    }

    // Kiểm tra tính đồng nhất của nền tảng (cùng nền tảng)
    const detectedPlatformsSet = new Set()
    list.forEach((item) => {
      if (item.platform) {
        detectedPlatformsSet.add(item.platform)
      }
    })

    const hasMismatchedPlatforms = detectedPlatformsSet.size > 1
    const platformListArray = Array.from(detectedPlatformsSet)

    // Kiểm tra xem tất cả có khớp với requiredPlatform không
    let platformMismatchError = null
    if (selectedPlatform) {
      const pObj = PLATFORMS.find((p) => p.id === selectedPlatform)
      const expectedName = pObj ? pObj.name : selectedPlatform.toUpperCase()
      const wrongItem = list.find((item) => item.platform && item.platform !== selectedPlatform)
      if (wrongItem) {
        platformMismatchError = `Bạn đang chọn ${expectedName}, nhưng liên kết #${wrongItem.index} lại thuộc về ${wrongItem.platformName}!`
      }
    } else if (hasMismatchedPlatforms) {
      const names = platformListArray
        .map((pId) => PLATFORMS.find((p) => p.id === pId)?.name || pId.toUpperCase())
        .join(', ')
      platformMismatchError = `Tất cả liên kết phải cùng một nền tảng! Phát hiện có nhiều nền tảng khác nhau trong danh sách: ${names}.`
    }

    const allValidUrls = list.every((item) => item.isValidUrl)
    const canDecode = list.length > 0 && allValidUrls && !platformMismatchError

    const commonPlatformObj = requiredPlatform ? PLATFORMS.find((p) => p.id === requiredPlatform) : null

    return {
      rawCount: rawLines.length,
      isOverLimit,
      items: list,
      commonPlatform: requiredPlatform,
      commonPlatformName: commonPlatformObj ? commonPlatformObj.name : requiredPlatform ? requiredPlatform.toUpperCase() : null,
      hasMismatchedPlatforms,
      platformMismatchError,
      canDecode,
    }
  }, [inputText, selectedPlatform])

  // Chọn nền tảng trên PlatformBadges
  const handlePlatformSelect = (pId) => {
    if (selectedPlatform === pId) {
      setSelectedPlatform(null)
      showToast('Đã chuyển sang chế độ tự động nhận diện nền tảng chung')
    } else {
      setSelectedPlatform(pId)
      const pObj = PLATFORMS.find((p) => p.id === pId)
      showToast(`Đã chọn ${pObj?.name || pId.toUpperCase()}: Vui lòng chỉ dán các liên kết từ ${pObj?.name}!`)
    }
  }

  // Dán nội dung từ Clipboard
  const handlePasteFromClipboard = async () => {
    try {
      if (navigator.clipboard && navigator.clipboard.readText) {
        const text = await navigator.clipboard.readText()
        if (text) {
          setInputText((prev) => (prev ? `${prev.trim()}\n${text.trim()}` : text.trim()))
          showToast('Đã dán liên kết từ bộ nhớ tạm!')
          return
        }
      }
      showToast('Trình duyệt không cho phép đọc bộ nhớ tạm trực tiếp. Vui lòng bấm Ctrl + V.')
    } catch {
      showToast('Vui lòng dùng phím Ctrl + V để dán vào ô nhập liệu')
    }
  }

  // Nạp link mẫu
  const handleLoadSample = () => {
    const target = selectedPlatform || 'youtube'
    const samples = SAMPLE_LINKS[target] || SAMPLE_LINKS.youtube
    setInputText(samples.join('\n'))
    showToast(`Đã nạp ${samples.length} liên kết mẫu từ ${target.toUpperCase()}!`)
  }

  // Xóa trắng toàn bộ
  const handleClear = () => {
    setInputText('')
    setDecodedItems([])
    setSelectedItemIds({})
    setExpandedStreamsMap({})
    setDecodingProgress({ current: 0, total: 0, currentUrl: '' })
  }

  // Thực hiện giải mã danh sách liên kết
  const handleStartDecoding = async () => {
    if (!parsedAnalysis.canDecode) {
      if (parsedAnalysis.platformMismatchError) {
        showToast(parsedAnalysis.platformMismatchError)
      } else if (parsedAnalysis.items.length === 0) {
        showToast('Vui lòng nhập ít nhất 1 liên kết hợp lệ!')
      } else {
        showToast('Vui lòng kiểm tra lại tính hợp lệ của các liên kết!')
      }
      return
    }

    const itemsToDecode = parsedAnalysis.items
    const total = itemsToDecode.length
    setIsDecoding(true)
    setDecodingProgress({ current: 0, total, currentUrl: '' })
    setDecodedItems([])
    setSelectedItemIds({})

    const newDecodedList = []
    const newSelectedMap = {}

    for (let i = 0; i < itemsToDecode.length; i++) {
      const item = itemsToDecode[i]
      setDecodingProgress({
        current: i + 1,
        total,
        currentUrl: item.url,
      })

      try {
        let finalUrl = item.url
        // Giải mã link rút gọn nếu cần
        if (item.isShortener) {
          try {
            const resolveRes = await resolveShortUrl(item.url, parsedAnalysis.commonPlatform)
            if (resolveRes?.resolvedUrl) {
              finalUrl = resolveRes.resolvedUrl
            }
          } catch (e) {
            console.warn('Lỗi resolve link rút gọn:', e)
          }
        }

        const mediaData = await extractMedia(finalUrl)
        const record = {
          id: `decoded_${Date.now()}_${i}`,
          index: i + 1,
          originalUrl: item.url,
          status: 'success',
          data: mediaData,
          selectedImages: mediaData?.type === 'album' && mediaData.images ? mediaData.images.reduce((acc, img) => ({ ...acc, [img.id]: true }), {}) : {},
        }
        newDecodedList.push(record)
        newSelectedMap[record.id] = true
      } catch (err) {
        newDecodedList.push({
          id: `decoded_${Date.now()}_${i}`,
          index: i + 1,
          originalUrl: item.url,
          status: 'error',
          error: err.message || 'Không thể trích xuất dữ liệu từ liên kết này',
          data: null,
        })
      }
    }

    setDecodedItems(newDecodedList)
    setSelectedItemIds(newSelectedMap)
    setIsDecoding(false)

    const successCount = newDecodedList.filter((d) => d.status === 'success').length
    showToast(`Đã giải mã xong! (${successCount}/${total} liên kết thành công)`)
  }

  // Chọn / Bỏ chọn một mục trong danh sách kết quả
  const toggleItemSelect = (id) => {
    setSelectedItemIds((prev) => ({
      ...prev,
      [id]: !prev[id],
    }))
  }

  // Chọn / Bỏ chọn tất cả các mục thành công
  const toggleSelectAllDecoded = (all = true) => {
    const nextMap = {}
    if (all) {
      decodedItems.forEach((item) => {
        if (item.status === 'success') {
          nextMap[item.id] = true
        }
      })
    }
    setSelectedItemIds(nextMap)
  }

  // Mở / đóng xem bảng định dạng chi tiết của 1 item
  const toggleExpandStreams = (id) => {
    setExpandedStreamsMap((prev) => ({
      ...prev,
      [id]: !prev[id],
    }))
  }

  // Tải stream chất lượng tốt nhất hoặc stream cụ thể của 1 video
  const handleDownloadStream = (itemRecord, stream = null, isAudioOnly = false) => {
    const media = itemRecord.data
    if (!media) return

    setDownloadingId(itemRecord.id)
    showToast(`Đang kết nối luồng tải "${media.title || 'media'}"...`)

    const targetUrl = media.originalUrl || itemRecord.originalUrl
    const isAudio = isAudioOnly || stream?.streamType === 'audio' || selectedFormat === 'mp3' || selectedFormat === 'm4a' || selectedFormat === 'flac' || selectedFormat === 'wav'
    const chosenFormat = stream?.format || (isAudio ? (selectedFormat !== 'all' ? selectedFormat : 'mp3') : (selectedFormat !== 'all' ? selectedFormat : 'mp4'))
    const ext = chosenFormat.toLowerCase()

    const downloadEndpoint = buildStreamDownloadUrl({
      url: targetUrl,
      formatId: stream?.formatId,
      isAudio,
      title: media.title,
      audioFormat: isAudio ? ext : undefined,
      audioBitrate: stream?.bitrate ? stream.bitrate.replace('kbps', 'k') : '320k',
      format: ext,
      streamType: stream?.streamType || (isAudio ? 'audio' : 'full'),
      isMute: stream?.streamType === 'mute',
    })

    const filename = `${media.title || `media_${itemRecord.index}`}.${ext}`
    triggerFileDownload(downloadEndpoint, filename)

    setTimeout(() => {
      setDownloadingId(null)
      showToast(`Đã bắt đầu truyền dữ liệu: "${filename}"`)
    }, 1500)
  }

  // Tải ảnh bìa gốc của 1 video
  const handleDownloadThumbnail = (itemRecord) => {
    const media = itemRecord.data
    if (!media) return

    showToast('Đang tải ảnh bìa gốc độ phân giải cao...')
    const targetUrl = media.originalUrl || itemRecord.originalUrl
    const directThumb = media.highResThumbnail || media.thumbnail

    if (media.type === 'video' || media.type === 'live') {
      const downloadEndpoint = buildThumbnailDownloadUrl({ url: targetUrl, title: media.title })
      triggerFileDownload(downloadEndpoint, `${media.title || 'thumbnail'}.jpg`)
    } else if (directThumb) {
      const downloadEndpoint = buildProxyMediaUrl(directThumb, `${media.title || 'thumbnail'}.jpg`)
      triggerFileDownload(downloadEndpoint, `${media.title || 'thumbnail'}.jpg`)
    } else {
      showToast('Không tìm thấy ảnh bìa!')
    }
  }

  // Tải trực tiếp 1 file trong album ảnh
  const handleDownloadSingleImage = (img) => {
    const ext = img.ext || (img.type === 'video' ? 'mp4' : 'jpg')
    const fileName = `${img.title || 'media_item'}.${ext}`
    const downloadEndpoint = buildProxyMediaUrl(img.url, fileName)
    triggerFileDownload(downloadEndpoint, fileName)
  }

  // Tải tất cả các mục đã chọn dưới dạng tệp nén ZIP
  const handleDownloadSelectedZip = async () => {
    const selectedItems = decodedItems.filter((it) => it.status === 'success' && selectedItemIds[it.id] && it.data)
    if (selectedItems.length === 0) {
      showToast('Vui lòng chọn ít nhất 1 mục đã giải mã để tải về!')
      return
    }

    try {
      const zipItemList = []

      selectedItems.forEach((item, idx) => {
        const data = item.data
        if (data.type === 'album' && data.images && data.images.length > 0) {
          data.images.forEach((img, imgIdx) => {
            zipItemList.push({
              url: img.url,
              filename: `${data.title ? `${data.title}_` : ''}${img.title || `img_${imgIdx + 1}`}.${img.ext || (img.type === 'video' ? 'mp4' : 'jpg')}`,
              referer: data.platform ? `https://www.${data.platform}.com` : undefined,
            })
          })
        } else if (data.thumbnail || data.highResThumbnail) {
          // Lưu ảnh bìa hoặc thumbnail chất lượng cao vào ZIP
          const thumbUrl = data.highResThumbnail || data.thumbnail
          zipItemList.push({
            url: thumbUrl,
            filename: `${data.title || `media_${idx + 1}`}_cover.jpg`,
            referer: data.platform ? `https://www.${data.platform}.com` : undefined,
          })
        }
      })

      if (zipItemList.length === 0) {
        showToast('Không có tệp media trực tiếp để đóng gói ZIP. Hãy dùng nút tải trực tiếp từng video!')
        return
      }

      setIsZipDownloading(true)
      setZipProgressText(`Đang chuẩn bị ${zipItemList.length} tệp...`)
      showToast(`Đang đóng gói ${zipItemList.length} tệp vào file ZIP tốc độ cao...`)

      const platformLabel = parsedAnalysis.commonPlatform || 'media'
      const zipName = `${platformLabel}_batch_${Date.now()}`
      await downloadZipArchive(zipItemList, zipName, (progress) => {
        if (progress.receivedBytes) {
          const mb = (progress.receivedBytes / (1024 * 1024)).toFixed(1)
          setZipProgressText(`Đang tải (${mb} MB)...`)
        }
      })
      showToast(`Đã tải xuống thành công tệp nén ${zipName}.zip!`)
    } catch (err) {
      console.error('Lỗi khi tải ZIP hàng loạt:', err)
      showToast(err.message || 'Lỗi khi tạo file ZIP')
    } finally {
      setIsZipDownloading(false)
      setZipProgressText('')
    }
  }

  const successItems = decodedItems.filter((i) => i.status === 'success')
  const selectedCount = Object.values(selectedItemIds).filter(Boolean).length

  return (
    <div className="multi-downloader-container">
      {/* Toast Notification */}
      {toastMessage && (
        <div className="toast-notification">
          <span>{toastMessage}</span>
        </div>
      )}

      {/* Main Multi-Input Card */}
      <div className="batch-input-card">
        <div className="batch-input-header">
          <div className="batch-header-title">
            <span>Danh sách liên kết cùng nền tảng (Tối đa 10 link)</span>
          </div>

          <div className="batch-header-tools">
            <button
              type="button"
              className="btn-batch-tool"
              onClick={handlePasteFromClipboard}
              title="Dán nhanh nội dung từ bộ nhớ tạm"
            >
              <IconPaste className="w-3.5 h-3.5" />
              <span>Dán từ clipboard</span>
            </button>
            <button
              type="button"
              className="btn-batch-tool"
              onClick={handleLoadSample}
              title="Nạp các link mẫu để kiểm tra ngay"
            >
              <IconSparkles className="w-3.5 h-3.5 text-amber-400" />
              <span>Link mẫu</span>
            </button>
            {inputText && (
              <button
                type="button"
                className="btn-batch-tool btn-batch-tool-danger"
                onClick={handleClear}
                title="Xóa danh sách liên kết hiện tại"
              >
                <IconTrash className="w-3.5 h-3.5" />
                <span>Xóa tất cả</span>
              </button>
            )}
          </div>
        </div>

        {/* Textarea nhập nhiều dòng */}
        <div className={`batch-textarea-wrapper ${parsedAnalysis.platformMismatchError ? 'has-error' : ''}`}>
          <textarea
            className="batch-textarea"
            rows={5}
            value={inputText}
            onChange={(e) => setInputText(e.target.value)}
            placeholder={
              selectedPlatform
                ? `Đang chọn ${PLATFORMS.find((p) => p.id === selectedPlatform)?.name || selectedPlatform.toUpperCase()}: Dán tối đa 10 link của nền tảng này (mỗi link 1 dòng)...`
                : 'Dán tối đa 10 liên kết từ cùng một nền tảng (mỗi dòng 1 liên kết). Ví dụ: 5 link YouTube hoặc 5 link TikTok...'
            }
            autoComplete="off"
            spellCheck="false"
          />
        </div>

        {/* Cảnh báo nếu dán quá 10 link */}
        {parsedAnalysis.isOverLimit && (
          <div className="batch-limit-notice">
            <IconInfo className="w-4 h-4 text-amber-400" />
            <span>
              Hệ thống đã tự động giới hạn 10 liên kết đầu tiên (bạn đang nhập {parsedAnalysis.rawCount} liên kết).
            </span>
          </div>
        )}

        {/* Thanh công cụ và nút Giải mã */}
        <div className="batch-input-actions-bar">
          <div className="batch-status-indicators">
            {/* Bộ đếm link */}
            <span
              className={`batch-counter-chip ${
                parsedAnalysis.items.length === 0
                  ? 'is-empty'
                  : parsedAnalysis.platformMismatchError
                  ? 'is-error'
                  : 'is-ready'
              }`}
            >
              Số lượng: {parsedAnalysis.items.length} / 10 link
            </span>

            {/* Nền tảng nhận diện chung */}
            {parsedAnalysis.commonPlatformName && (
              <span className="batch-platform-chip">
                Nền tảng: <strong>{parsedAnalysis.commonPlatformName}</strong>
              </span>
            )}
          </div>

          <div className="batch-action-buttons-group">
            {/* Format Selector Dropdown tương tự SingleDownloader */}
            <div className="format-selector-wrapper" ref={formatDropdownRef}>
              <button
                type="button"
                className={`format-dropdown-trigger ${isFormatDropdownOpen ? 'is-open' : ''}`}
                onClick={() => setIsFormatDropdownOpen(!isFormatDropdownOpen)}
                title="Chọn định dạng tải về mặc định"
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

            {/* Primary Action Button: Giải mã liên kết */}
            <button
              type="button"
              className={`btn-primary-fetch ${isDecoding ? 'is-loading' : ''}`}
              onClick={handleStartDecoding}
              disabled={isDecoding || !parsedAnalysis.canDecode}
              title={
                parsedAnalysis.platformMismatchError
                  ? parsedAnalysis.platformMismatchError
                  : parsedAnalysis.items.length === 0
                  ? 'Vui lòng nhập danh sách link'
                  : 'Giải mã toàn bộ danh sách liên kết'
              }
            >
              {isDecoding ? (
                <>
                  <span className="spinner-dots" />
                  <span>
                    Đang giải mã ({decodingProgress.current}/{decodingProgress.total})...
                  </span>
                </>
              ) : (
                <span>Giải mã liên kết ({parsedAnalysis.items.length})</span>
              )}
            </button>
          </div>
        </div>

        {/* Thông báo lỗi nếu các link khác nền tảng */}
        {parsedAnalysis.platformMismatchError && (
          <div className="batch-error-banner">
            <IconInfo className="w-4 h-4 flex-shrink-0" />
            <span>{parsedAnalysis.platformMismatchError}</span>
          </div>
        )}

        {/* Huy hiệu chọn nền tảng (Platform Badges) */}
        <PlatformBadges
          selectedPlatform={selectedPlatform}
          activePlatform={selectedPlatform || parsedAnalysis.commonPlatform}
          onSelectPlatform={handlePlatformSelect}
          onClearPlatform={() => setSelectedPlatform(null)}
        />

        {/* Danh sách xem trước các liên kết hợp lệ đang chờ giải mã */}
        {parsedAnalysis.items.length > 0 && !isDecoding && decodedItems.length === 0 && (
          <div className="batch-links-preview-list">
            <div className="preview-list-header">
              <span>Xem trước {parsedAnalysis.items.length} liên kết sẵn sàng giải mã:</span>
            </div>
            <div className="preview-items-scroll">
              {parsedAnalysis.items.map((item) => (
                <div key={item.id} className="preview-item-row">
                  <span className="preview-item-index">#{item.index}</span>
                  <span className={`preview-item-platform ${item.platform ? `platform-${item.platform}` : 'unknown'}`}>
                    {item.platformName}
                  </span>
                  <span className="preview-item-url" title={item.url}>
                    {item.url}
                  </span>
                  {item.isValidUrl && !parsedAnalysis.platformMismatchError ? (
                    <span className="preview-item-status status-ok">
                      <IconCheck className="w-3.5 h-3.5 text-emerald-400" />
                    </span>
                  ) : (
                    <span className="preview-item-status status-err">
                      <IconClose className="w-3.5 h-3.5 text-red-400" />
                    </span>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Thanh tiến trình đang giải mã */}
        {isDecoding && (
          <div className="batch-progress-card">
            <div className="batch-progress-info">
              <span className="progress-text">
                Đang giải mã link {decodingProgress.current} / {decodingProgress.total}...
              </span>
              <span className="progress-percent">
                {Math.round((decodingProgress.current / (decodingProgress.total || 1)) * 100)}%
              </span>
            </div>
            <div className="batch-progress-track">
              <div
                className="batch-progress-fill"
                style={{
                  width: `${(decodingProgress.current / (decodingProgress.total || 1)) * 100}%`,
                }}
              />
            </div>
            <div className="progress-current-url" title={decodingProgress.currentUrl}>
              <span>{decodingProgress.currentUrl}</span>
            </div>
          </div>
        )}
      </div>

      {/* Danh sách Kết quả sau khi Giải mã */}
      {decodedItems.length > 0 && (
        <div className="batch-results-wrapper">
          {/* Thanh công cụ quản lý kết quả */}
          <div className="batch-results-toolbar">
            <div className="results-summary-info">
              <h3 className="results-heading">
                Kết quả giải mã ({successItems.length}/{decodedItems.length} thành công)
              </h3>
              <span className="results-platform-tag">
                Nền tảng: {parsedAnalysis.commonPlatformName || 'Đa phương tiện'}
              </span>
            </div>

            <div className="results-action-buttons action-buttons-group">
              <button
                type="button"
                className="btn-secondary-action"
                onClick={() => toggleSelectAllDecoded(true)}
              >
                <span>Chọn tất cả</span>
              </button>
              <button
                type="button"
                className="btn-secondary-action"
                onClick={() => toggleSelectAllDecoded(false)}
              >
                <span>Bỏ chọn</span>
              </button>
              <button
                type="button"
                className={`btn-primary-zip-download ${isZipDownloading ? 'is-loading' : ''}`}
                onClick={handleDownloadSelectedZip}
                disabled={isZipDownloading || selectedCount === 0}
                title="Tải tất cả tệp từ các mục đã chọn thành file ZIP"
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

          {/* Danh sách thẻ kết quả từng liên kết */}
          <div className="batch-cards-list">
            {decodedItems.map((item) => {
              const isSuccess = item.status === 'success'
              const media = item.data
              const isSelected = !!selectedItemIds[item.id]
              const isExpanded = !!expandedStreamsMap[item.id]
              const isDownloading = downloadingId === item.id

              if (!isSuccess) {
                return (
                  <div key={item.id} className="batch-item-card is-error-card">
                    <div className="batch-item-header">
                      <div className="item-meta-left">
                        <span className="item-index-badge">#{item.index}</span>
                        <span className="item-status-pill pill-error">Lỗi giải mã</span>
                      </div>
                      <span className="item-url-truncated" title={item.originalUrl}>
                        {item.originalUrl}
                      </span>
                    </div>
                    <div className="batch-error-message">
                      <IconInfo className="w-4 h-4 flex-shrink-0" />
                      <span>{item.error || 'Không thể trích xuất nội dung từ liên kết này.'}</span>
                    </div>
                  </div>
                )
              }

              return (
                <div key={item.id} className={`batch-item-card ${isSelected ? 'is-selected' : ''}`}>
                  {/* Hàng trên: Checkbox, Badge nền tảng, Tác vụ */}
                  <div className="batch-item-header">
                    <div className="item-meta-left">
                      <label className="item-checkbox-container" title="Chọn mục này để tải ZIP hàng loạt">
                        <input
                          type="checkbox"
                          checked={isSelected}
                          onChange={() => toggleItemSelect(item.id)}
                        />
                        <span className="item-checkbox-checkmark">
                          {isSelected && <IconCheck className="w-3 h-3 text-white" />}
                        </span>
                      </label>
                      <span className="item-index-badge">#{item.index}</span>
                      <span className="platform-tag">
                        {media.platform ? media.platform.toUpperCase() : 'MEDIA'}
                      </span>
                      <span className={`media-type-pill ${media.isLive ? 'media-type-pill-live' : media.isReel ? 'media-type-pill-reel' : media.isShort ? 'media-type-pill-short' : ''}`}>
                        {media.isLive
                          ? 'LIVE'
                          : media.isReel
                          ? 'REELS'
                          : media.isShort
                          ? 'SHORTS'
                          : media.type === 'album'
                          ? 'Album ảnh'
                          : 'Video'}
                      </span>
                    </div>

                    <div className="item-meta-actions">
                      <button
                        type="button"
                        className="btn-action-icon"
                        onClick={() => {
                          navigator.clipboard.writeText(item.originalUrl)
                          showToast('Đã sao chép liên kết!')
                        }}
                        title="Sao chép liên kết"
                      >
                        <IconCopy className="w-3.5 h-3.5" />
                        <span>Sao chép</span>
                      </button>
                      <a
                        href={item.originalUrl}
                        target="_blank"
                        rel="noreferrer"
                        className="btn-action-icon"
                        title="Mở liên kết gốc"
                      >
                        <IconExternalLink className="w-3.5 h-3.5" />
                        <span>Mở link</span>
                      </a>
                    </div>
                  </div>

                  {/* Phần thân: Thumbnail, Chi tiết & Nút tải nhanh */}
                  <div className="batch-item-body">
                    {/* Thumbnail */}
                    <div className="batch-thumb-wrapper">
                      <img
                        src={media.thumbnail || media.highResThumbnail}
                        alt={media.title}
                        className="batch-thumb-img"
                        loading="lazy"
                        decoding="async"
                        referrerPolicy="no-referrer"
                        onError={(e) => {
                          if (!e.currentTarget.dataset.fallback) {
                            e.currentTarget.dataset.fallback = '1'
                            e.currentTarget.src = buildProxyImageUrl(media.thumbnail || media.highResThumbnail)
                          }
                        }}
                      />
                      {media.duration && (
                        <span className="batch-duration-badge">{media.duration}</span>
                      )}
                    </div>

                    {/* Nội dung thông tin */}
                    <div className="batch-info-col">
                      <h4 className="batch-media-title" title={media.title}>
                        {media.title || 'Nội dung phương tiện'}
                      </h4>

                      <div className="batch-media-meta">
                        {media.author && (
                          <span className="meta-author">Tác giả: <strong>{media.author}</strong></span>
                        )}
                        {media.views && (
                          <span className="meta-views">• {media.views} lượt xem</span>
                        )}
                        {media.likes && (
                          <span className="meta-likes">• {media.likes} thích</span>
                        )}
                      </div>

                      {/* Các nút Tải Nhanh (Video MP4 / Âm thanh MP3 / Ảnh bìa) */}
                      <div className="batch-quick-downloads">
                        {/* Nút tải Video MP4 tốt nhất */}
                        {(media.type === 'video' || media.type === 'live' || !media.type) && (
                          <button
                            type="button"
                            className="btn-quick-dl btn-quick-dl-primary"
                            onClick={() => handleDownloadStream(item, null, false)}
                            disabled={isDownloading}
                            title="Tải video đầy đủ có tiếng (chất lượng tốt nhất)"
                          >
                            <IconVideo className="w-4 h-4" />
                            <span>Tải Video (MP4)</span>
                          </button>
                        )}

                        {/* Nút tải Âm thanh MP3 */}
                        <button
                          type="button"
                          className="btn-quick-dl btn-quick-dl-audio"
                          onClick={() => handleDownloadStream(item, null, true)}
                          disabled={isDownloading}
                          title="Tải file âm thanh MP3 chất lượng cao"
                        >
                          <IconAudio className="w-4 h-4" />
                          <span>Tải Âm thanh (MP3)</span>
                        </button>

                        {/* Nút tải ảnh bìa gốc */}
                        {(media.thumbnail || media.highResThumbnail) && (
                          <button
                            type="button"
                            className="btn-quick-dl btn-quick-dl-secondary"
                            onClick={() => handleDownloadThumbnail(item)}
                            title="Tải ảnh bìa gốc độ nét cao"
                          >
                            <IconImage className="w-4 h-4" />
                            <span>Ảnh bìa</span>
                          </button>
                        )}

                        {/* Nút mở rộng xem tất cả định dạng */}
                        {media.streams && media.streams.length > 0 && (
                          <button
                            type="button"
                            className={`btn-quick-dl btn-quick-dl-toggle ${isExpanded ? 'is-active' : ''}`}
                            onClick={() => toggleExpandStreams(item.id)}
                            title="Xem tất cả định dạng và độ phân giải chi tiết"
                          >
                            <span>{isExpanded ? 'Thu gọn' : 'Tùy chọn độ phân giải'}</span>
                            <IconChevronDown className={`w-3.5 h-3.5 ${isExpanded ? 'rotate-180' : ''}`} />
                          </button>
                        )}
                      </div>
                    </div>
                  </div>

                  {/* Album ảnh nếu là bài viết nhiều ảnh */}
                  {media.type === 'album' && media.images && media.images.length > 0 && (
                    <div className="batch-album-preview">
                      <div className="album-preview-heading">
                        <span>Danh sách hình ảnh ({media.images.length} tệp):</span>
                      </div>
                      <div className="album-preview-grid">
                        {media.images.map((img, imgIdx) => (
                          <div key={img.id || imgIdx} className="album-mini-card">
                            <img
                              src={img.thumb || img.url}
                              alt={img.title || `img_${imgIdx}`}
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
                            <button
                              type="button"
                              className="btn-mini-dl"
                              onClick={() => handleDownloadSingleImage(img)}
                              title="Tải tệp này"
                            >
                              <IconDownload className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Bảng chi tiết định dạng khi mở rộng */}
                  {isExpanded && media.streams && (
                    <div className="batch-streams-expanded-panel">
                      <div className="streams-panel-title">
                        <span>Các luồng tải khả dụng ({media.streams.length} luồng):</span>
                      </div>
                      <div className="streams-expanded-list">
                        {media.streams.slice(0, 10).map((stream, sIdx) => {
                          const isAudio = stream.streamType === 'audio'
                          const isMute = stream.streamType === 'mute'
                          return (
                            <div key={sIdx} className="stream-expanded-row">
                              <div className="stream-spec-left">
                                <span className={`stream-type-tag ${isAudio ? 'tag-audio' : isMute ? 'tag-mute' : 'tag-full'}`}>
                                  {isAudio ? 'AUDIO' : isMute ? 'VIDEO (KHÔNG TIẾNG)' : 'FULL HD/4K'}
                                </span>
                                <strong className="stream-res-text">{stream.quality || 'Chuẩn'}</strong>
                                <span className="stream-fmt-text">{stream.format?.toUpperCase()}</span>
                                {stream.size && <span className="stream-size-text">• {stream.size}</span>}
                              </div>
                              <button
                                type="button"
                                className="btn-stream-dl-mini"
                                onClick={() => handleDownloadStream(item, stream, isAudio)}
                                title={`Tải luồng ${stream.quality}`}
                              >
                                <IconDownload className="w-3.5 h-3.5" />
                                <span>Tải luồng này</span>
                              </button>
                            </div>
                          )
                        })}
                      </div>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
