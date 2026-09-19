import { useState, useEffect, useMemo, useRef } from 'react'
import {
  IconDownload,
  IconClose,
  IconPaste,
  IconScissors,
  IconSubtitle,
  IconZip,
  IconVideo,
  IconAudio,
  IconCopy,
  IconSettings,
} from './Icons'
import { FORMAT_OPTIONS, detectPlatform, validatePlatformUrl, getPlatform, isGenericShortenerUrl } from '../constants'
import {
  extractMedia,
  resolveShortUrl,
  isTauri,
  startNativeDownload,
  createTaskId,
  downloadThumbnail,
  downloadSubtitle,
  downloadAlbumBatch,
  downloadDirectFile,
  onDownloadProgress,
  buildProxyMediaUrl,
  buildProxyImageUrl,
  selectDownloadDirectory,
  getAlwaysAskDownloadDir,
  setAlwaysAskDownloadDir,
} from '../services/api'
import DownloadProgressCard from './DownloadProgressCard'

export default function LinkDownloader({ onShowToast }) {
  const [mode, setMode] = useState('single') // 'single' | 'batch'
  const [url, setUrl] = useState('')
  const [batchText, setBatchText] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [batchProgress, setBatchProgress] = useState({ current: 0, total: 0, statusText: '' })

  // Kết quả sau khi phân tích
  const [singleMedia, setSingleMedia] = useState(null)
  const [batchMedias, setBatchMedias] = useState([])
  const [streamFilter, setStreamFilter] = useState('all') // 'all' | 'full' | 'mute' | 'audio'
  const [selectedImages, setSelectedImages] = useState({})
  const [downloadingId, setDownloadingId] = useState(null)
  const [nativeProgress, setNativeProgress] = useState(null)
  const [downloadTaskTitle, setDownloadTaskTitle] = useState('')
  const [userSelectedSubLang, setUserSelectedSubLang] = useState('')
  const selectedSubLang = userSelectedSubLang || singleMedia?.subtitles?.[0]?.lang || ''
  const [alwaysAskDir, setAlwaysAskDir] = useState(getAlwaysAskDownloadDir())
  const [isZipDownloading, setIsZipDownloading] = useState(false)
  const cancelRef = useRef(false)         // dùng để hủy batch extract
  const [isCancelling, setIsCancelling] = useState(false)

  // Trimmer tool state
  const [isTrimmerOpen, setIsTrimmerOpen] = useState(false)
  const [trimStart, setTrimStart] = useState('')
  const [trimEnd, setTrimEnd] = useState('')

  // Advanced download options state
  const [isOptionsOpen, setIsOptionsOpen] = useState(false)
  const [embedSubs, setEmbedSubs] = useState(false)
  const [embedMetadata, setEmbedMetadata] = useState(true)
  const [accelerate, setAccelerate] = useState(true)
  const [videoContainer, setVideoContainer] = useState('auto')

  // Synchronous validation state computed from URL
  const trimmedUrl = url.trim()
  const syncValidation = useMemo(() => {
    if (!trimmedUrl) return { valid: true, status: 'empty', message: '' }
    const initial = validatePlatformUrl(trimmedUrl, null)
    if (!initial.valid) {
      return initial
    }
    if (initial.status === 'needs_resolve' || isGenericShortenerUrl(trimmedUrl)) {
      return { valid: true, status: 'needs_resolve', message: 'Đang kiểm tra chuyển hướng link...' }
    }
    const detected = initial.platform || detectPlatform(trimmedUrl)
    if (detected) {
      const pName = getPlatform(detected)?.name
      if (detected === 'movie') {
        return {
          valid: true,
          status: 'matched',
          platform: detected,
          message: `✓ Nhận diện: Phim / Web Media`,
        }
      }
      return {
        valid: true,
        status: 'matched',
        platform: detected,
        message: `✓ Hợp lệ: ${pName || detected}`,
      }
    } else {
      // URL hợp lệ nhưng không rõ nền tảng → vẫn cho phép thử
      return { valid: true, status: 'unknown', message: '🌐 Sẽ thử phân tích web media...' }
    }
  }, [trimmedUrl])

  const [asyncResolved, setAsyncResolved] = useState(null)

  useEffect(() => {
    if (syncValidation.status === 'needs_resolve') {
      let active = true
      const timer = setTimeout(async () => {
        try {
          const res = await resolveShortUrl(trimmedUrl)
          if (active && res.platform) {
            const pObj = getPlatform(res.platform)
            setAsyncResolved({
              url: trimmedUrl,
              valid: true,
              status: 'matched',
              platform: res.platform,
              resolvedUrl: res.resolvedUrl,
              message: res.isShortened
                ? `✓ Đã giải mã: ${pObj?.name || res.platform}`
                : `✓ Hợp lệ: ${pObj?.name || res.platform}`,
            })
          }
        } catch {
          if (active) {
            setAsyncResolved({ url: trimmedUrl, valid: true, status: 'manual', message: 'Liên kết cần phân tích trực tiếp' })
          }
        }
      }, 350)
      return () => {
        active = false
        clearTimeout(timer)
      }
    }
  }, [syncValidation.status, trimmedUrl])

  const isResolvedForCurrentUrl =
    syncValidation.status === 'needs_resolve' &&
    asyncResolved?.url === trimmedUrl

  const validationState = isResolvedForCurrentUrl ? asyncResolved : syncValidation
  const resolvedUrl =
    isResolvedForCurrentUrl && asyncResolved?.resolvedUrl
      ? asyncResolved.resolvedUrl
      : trimmedUrl


  const parsedBatchLinks = useMemo(() => {
    return batchText
      .split('\n')
      .map((l) => l.trim())
      .filter((l) => l.startsWith('http://') || l.startsWith('https://') || (l.includes('.') && !l.includes(' ')))
      .slice(0, 10)
  }, [batchText])

  const handlePaste = async (targetMode) => {
    try {
      const text = await navigator.clipboard.readText()
      if (text) {
        if (targetMode === 'single') {
          setUrl(text.trim())
          setAsyncResolved(null)
        } else {
          setBatchText((prev) => (prev ? `${prev}\n${text.trim()}` : text.trim()))
        }
        onShowToast?.('Đã dán liên kết từ bộ nhớ tạm')
      }
    } catch {
      onShowToast?.('Vui lòng dùng phím tắt Ctrl+V để dán')
    }
  }

  const handleSingleExtract = async (e) => {
    e?.preventDefault()
    const targetUrl = (resolvedUrl || url).trim()
    if (!targetUrl) {
      onShowToast?.('Vui lòng nhập đường dẫn liên kết!')
      return
    }

    cancelRef.current = false
    setIsCancelling(false)
    setIsLoading(true)
    try {
      const data = await extractMedia(targetUrl)
      if (!cancelRef.current && data) {
        setSingleMedia(data)
        setBatchMedias([])
        onShowToast?.(`Đã trích xuất: ${data.title?.slice(0, 30) || 'Thành công'}...`)
      }
    } catch (err) {
      if (!cancelRef.current) {
        const errorMsg = typeof err === 'string' ? err : err?.message || 'Lỗi khi trích xuất liên kết'
        onShowToast?.(errorMsg)
      }
    } finally {
      setIsLoading(false)
      setIsCancelling(false)
    }
  }

  const handleBatchExtract = async (e) => {
    e?.preventDefault()
    if (parsedBatchLinks.length === 0) {
      onShowToast?.('Vui lòng nhập ít nhất 1 liên kết!')
      return
    }

    cancelRef.current = false
    setIsCancelling(false)
    setIsLoading(true)
    setBatchProgress({ current: 0, total: parsedBatchLinks.length, statusText: 'Đang bắt đầu...' })

    const results = []
    const failedLinks = []

    for (let i = 0; i < parsedBatchLinks.length; i++) {
      // Kiểm tra nếu đã hủy
      if (cancelRef.current) {
        setBatchProgress((prev) => ({ ...prev, statusText: `Đã hủy (${results.length} thành công)` }))
        break
      }

      const link = parsedBatchLinks[i]
      const shortLink = link.length > 50 ? link.slice(0, 47) + '...' : link
      setBatchProgress({
        current: i + 1,
        total: parsedBatchLinks.length,
        statusText: `[${i + 1}/${parsedBatchLinks.length}] Đang giải mã: ${shortLink}`,
      })

      try {
        const item = await extractMedia(link)
        if (item && !cancelRef.current) results.push(item)
      } catch (err) {
        const errMsg = typeof err === 'string' ? err : err?.message || ''
        const isTimeout = errMsg.toLowerCase().includes('timeout') || errMsg.toLowerCase().includes('55 giây')
        console.warn(`[Batch] Lỗi ${isTimeout ? 'timeout' : 'bóc tách'} ${link}:`, errMsg)
        failedLinks.push({ link, reason: isTimeout ? 'Timeout' : errMsg.slice(0, 60) })
      }
    }

    setIsLoading(false)
    setIsCancelling(false)
    setBatchProgress({ current: 0, total: 0, statusText: '' })

    if (results.length > 0) {
      setBatchMedias(results)
      setSingleMedia(null)
      const failMsg = failedLinks.length > 0 ? ` (${failedLinks.length} lỗi/timeout)` : ''
      onShowToast?.(`Giải mã thành công ${results.length}/${parsedBatchLinks.length} liên kết!${failMsg}`)
    } else {
      onShowToast?.('Không thể giải mã các liên kết đã nhập. Vui lòng kiểm tra lại liên kết.')
    }
  }

  // Hủy giải mã đang chạy
  const handleCancelExtract = () => {
    cancelRef.current = true
    setIsCancelling(true)
    onShowToast?.('Đang hủy giải mã...')
  }

  // Tải stream video/audio đơn
  const handleDownloadStream = async (stream, media = singleMedia) => {
    if (!media) return
    const streamId = stream.formatId || stream.quality || 'stream'
    setDownloadingId(streamId)

    let targetDir = undefined
    if (alwaysAskDir) {
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

    const taskId = createTaskId()
    setDownloadTaskTitle(media.title || stream.quality || 'Video')
    setNativeProgress({
      id: taskId,
      percent: 0,
      speed: '',
      eta: '',
      status: 'preparing',
      phase: 'Đang chuẩn bị tải...',
    })

    let unlisten = null
    try {
      const isAudioOnly = stream.streamType === 'audio'
      const isMute = stream.streamType === 'mute'
      onShowToast?.(`Đang tải: ${stream.quality || 'tệp'}`)

      try {
        // Chỉ nhận sự kiện của đúng tác vụ này
        unlisten = await onDownloadProgress((payload) => setNativeProgress(payload), taskId)
      } catch (e) {
        console.warn('Cannot attach progress listener:', e)
      }

      // Với các nền tảng mạng xã hội có extractor chuẩn, luôn dùng URL bài viết gốc + formatId
      // để yt-dlp sử dụng đầy đủ cookies, referer và session đăng nhập, tránh lỗi 403 Forbidden.
      const isDirectStreamOnly =
        !media.originalUrl ||
        media.platform === 'movie' ||
        media.platform === 'generic' ||
        stream.formatId?.startsWith('web_video_') ||
        Boolean(stream.url && (stream.url.includes('.m3u8') || stream.url.includes('.mpd')))

      const downloadUrl = isDirectStreamOnly && stream.url ? stream.url : (media.originalUrl || stream.url)
      const downloadFormatId = isDirectStreamOnly && stream.url ? null : (stream.formatId || null)

      const res = await startNativeDownload({
        url: downloadUrl,
        formatId: downloadFormatId,
        isAudio: isAudioOnly,
        isMute: isMute,
        referer: media.originalUrl || undefined,
        startTime: trimStart || undefined,
        endTime: trimEnd || undefined,
        title: media.title,
        destDir: targetDir,
        embedSubs: embedSubs,
        embedMetadata: embedMetadata,
        embedThumbnail: embedMetadata,
        concurrentFragments: accelerate ? 8 : 1,
        videoFormat: videoContainer !== 'auto' ? videoContainer : undefined,
        taskId,
      })

      if (res?.success) {
        setNativeProgress({
          id: taskId,
          percent: 100,
          speed: '',
          eta: '',
          status: 'completed',
          phase: 'Hoàn tất',
          filePath: res.file_path,
          fileName: res.file_name,
        })
        onShowToast?.(`Đã tải xong: ${res.file_path || res.file_name || 'tệp'}`)
      }
    } catch (err) {
      const errMsg = typeof err === 'string' ? err : err?.message || 'Lỗi khi tải stream'
      setNativeProgress({
        id: taskId,
        percent: 0,
        speed: '',
        eta: '',
        status: 'error',
        phase: 'Tải thất bại',
        message: errMsg,
      })
      onShowToast?.(errMsg)
    } finally {
      // Gỡ listener trong mọi trường hợp — trước đây khi tải lỗi listener bị rò rỉ,
      // tích lũy dần và làm thanh tiến trình nhảy loạn ở các lần tải sau.
      if (typeof unlisten === 'function') unlisten()
      setDownloadingId(null)
    }
  }

  // Tải thumbnail
  const handleDownloadThumbnail = async () => {
    if (!singleMedia) return
    try {
      onShowToast?.('Đang tải ảnh thumbnail...')
      const res = await downloadThumbnail({
        url: singleMedia.originalUrl,
        title: singleMedia.title,
      })
      if (res?.file_name) {
        onShowToast?.(`Đã lưu thumbnail: ${res.file_name}`)
      }
    } catch (err) {
      onShowToast?.(typeof err === 'string' ? err : err?.message || 'Lỗi khi tải thumbnail')
    }
  }

  // Tải phụ đề
  const handleDownloadSubtitle = async (sub) => {
    if (!singleMedia) return
    try {
      onShowToast?.(`Đang tải phụ đề: ${sub.name || sub.lang}...`)
      const res = await downloadSubtitle({
        url: singleMedia.originalUrl,
        lang: sub.lang,
        format: sub.ext,
        title: singleMedia.title,
      })
      if (res?.file_name) {
        onShowToast?.(`Đã lưu phụ đề: ${res.file_name}`)
      }
    } catch (err) {
      onShowToast?.(typeof err === 'string' ? err : err?.message || 'Lỗi khi tải phụ đề')
    }
  }

  // Tải ảnh album đơn lẻ
  const handleDownloadImage = async (img) => {
    try {
      if (isTauri()) {
        onShowToast?.(`Đang tải ảnh: ${img.title || 'photo'}...`)
        const res = await downloadDirectFile({
          url: img.url,
          filename: `${img.title || 'photo'}.${img.ext || 'jpg'}`,
          referer: singleMedia?.originalUrl,
        })
        if (res?.file_name) {
          onShowToast?.(`Đã lưu: ${res.file_name}`)
        }
        return
      }
      const directUrl = buildProxyMediaUrl(img.url)
      const a = document.createElement('a')
      a.href = directUrl
      a.download = `${img.title || 'photo'}.${img.ext || 'jpg'}`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      onShowToast?.('Bắt đầu tải ảnh')
    } catch (err) {
      onShowToast?.(typeof err === 'string' ? err : err?.message || 'Lỗi khi tải ảnh')
    }
  }

  const albumImages = useMemo(() => singleMedia?.images || [], [singleMedia])
  const selectedImageCount = useMemo(
    () => Object.values(selectedImages).filter(Boolean).length,
    [selectedImages]
  )

  const handleToggleSelectAllImages = () => {
    if (selectedImageCount === albumImages.length) {
      setSelectedImages({})
    } else {
      const all = {}
      albumImages.forEach((img) => {
        all[img.id] = true
      })
      setSelectedImages(all)
    }
  }

  const handleDownloadAlbum = async (asZip = false) => {
    const itemsToDownload = albumImages.filter((img) => selectedImages[img.id])
    if (itemsToDownload.length === 0) {
      onShowToast?.('Vui lòng chọn ít nhất 1 ảnh để tải')
      return
    }

    let targetDir = undefined
    if (alwaysAskDir) {
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

    const taskId = createTaskId()
    setIsZipDownloading(true)
    setDownloadTaskTitle(`${asZip ? 'Nén ZIP' : 'Tải'}: ${itemsToDownload.length} ảnh`)
    setNativeProgress({
      id: taskId,
      percent: 0,
      speed: '',
      eta: '',
      status: 'preparing',
      phase: `Chuẩn bị tải ${itemsToDownload.length} ảnh...`,
    })

    let unlisten = null
    try {
      try {
        unlisten = await onDownloadProgress((payload) => setNativeProgress(payload), taskId)
      } catch (e) {
        console.warn('Cannot attach progress listener:', e)
      }

      const itemsPayload = itemsToDownload.map((img) => ({
        url: img.url,
        // Rust tự bổ sung đúng đuôi tệp theo URL thật, không ép cứng .jpg nữa
        filename: img.title || 'image',
        referer: singleMedia?.originalUrl,
      }))
      const res = await downloadAlbumBatch({
        items: itemsPayload,
        albumName: `${singleMedia?.title || 'Album'}_Media`,
        destDir: targetDir,
        asZip: asZip,
        taskId,
      })
      setNativeProgress({
        id: taskId,
        percent: 100,
        speed: '',
        eta: '',
        status: 'completed',
        phase: 'Hoàn tất',
        filePath: res?.file_path,
        fileName: res?.file_name,
      })
      onShowToast?.(res?.message || (res?.file_path ? `Đã lưu tại: ${res.file_path}` : `Đã tải xong ${itemsToDownload.length} ảnh!`))
    } catch (err) {
      const errMsg = typeof err === 'string' ? err : err?.message || 'Lỗi khi tải album'
      setNativeProgress({
        id: taskId,
        percent: 0,
        speed: '',
        eta: '',
        status: 'error',
        phase: 'Tải thất bại',
        message: errMsg,
      })
      onShowToast?.(errMsg)
    } finally {
      if (typeof unlisten === 'function') unlisten()
      setIsZipDownloading(false)
    }
  }

  const filteredStreams = useMemo(() => {
    if (!singleMedia?.streams) return []
    if (streamFilter === 'all') return singleMedia.streams
    if (streamFilter === 'full') return singleMedia.streams.filter((s) => s.streamType === 'full')
    if (streamFilter === 'mute') return singleMedia.streams.filter((s) => s.streamType === 'mute')
    if (streamFilter === 'audio') return singleMedia.streams.filter((s) => s.streamType === 'audio')
    return singleMedia.streams
  }, [singleMedia, streamFilter])

  const handleClearUrl = () => {
    setUrl('')
    setSingleMedia(null)
    setBatchMedias([])
    setAsyncResolved(null)
    setSelectedImages({})
    setNativeProgress(null)
    setDownloadTaskTitle('')
    setDownloadingId(null)
    setIsZipDownloading(false)
    setIsTrimmerOpen(false)
  }

  const handleClearBatch = () => {
    setBatchText('')
    setBatchMedias([])
    setSelectedImages({})
    setNativeProgress(null)
    setDownloadingId(null)
    setIsZipDownloading(false)
  }

  const handleCopy = (text) => {
    if (!text) return
    navigator.clipboard.writeText(text)
    onShowToast?.('Đã sao chép liên kết vào bộ nhớ tạm')
  }

  return (
    <div className="downloader-pane">
      {/* Header khu vực Tải theo liên kết */}
      <div className="pane-header">
        <h2 className="pane-title">Tải theo liên kết</h2>
        <div className="pane-toggle-group">
          <button
            type="button"
            className={`pane-toggle-btn ${mode === 'single' ? 'active' : ''}`}
            onClick={() => setMode('single')}
          >
            1 Liên kết
          </button>
          <button
            type="button"
            className={`pane-toggle-btn ${mode === 'batch' ? 'active' : ''}`}
            onClick={() => setMode('batch')}
          >
            Nhiều link ({parsedBatchLinks.length}/10)
          </button>
        </div>
      </div>

      {/* Form nhập liệu */}
      <div className="pane-input-section">
        {mode === 'single' ? (
          <form onSubmit={handleSingleExtract} className="pane-form">
            <div className="input-group">
              <input
                type="text"
                className="pane-input"
                placeholder="Dán link YouTube, TikTok, Facebook, Instagram, phimhay.com, nhac.vn..."
                value={url}
                onChange={(e) => {
                  const val = e.target.value
                  setUrl(val)
                  setAsyncResolved(null)
                  if (!val.trim()) {
                    setSingleMedia(null)
                    setBatchMedias([])
                    setSelectedImages({})
                    setNativeProgress(null)
                    setDownloadTaskTitle('')
                  }
                }}
                disabled={isLoading}
              />
              {url ? (
                <button
                  type="button"
                  className="input-inline-btn"
                  onClick={handleClearUrl}
                  title="Xóa URL"
                >
                  <IconClose className="w-3.5 h-3.5" />
                </button>
              ) : (
                <button
                  type="button"
                  className="input-inline-btn"
                  onClick={() => handlePaste('single')}
                  title="Dán từ bộ nhớ tạm"
                >
                  <IconPaste className="w-3.5 h-3.5" />
                </button>
              )}
            </div>

            <div className="pane-control-row">
              <div className="pills-group">
                {FORMAT_OPTIONS.slice(0, 3).map((f) => (
                  <button
                    key={f.id}
                    type="button"
                    className={`minimal-pill ${videoContainer === f.id ? 'active' : ''}`}
                    onClick={() => setVideoContainer(f.id)}
                  >
                    {f.label}
                  </button>
                ))}
              </div>

              {validationState.message && (
                <span className={`validation-hint ${validationState.status === 'matched' ? 'status-ok' : ''}`}>
                  {validationState.message}
                </span>
              )}

              <button
                type="submit"
                className="pane-submit-btn"
                disabled={isLoading || !url.trim()}
              >
                {isLoading ? (
                  <>
                    <span className="minimal-spinner" />
                    <span>{isCancelling ? 'Đang hủy...' : 'Đang bóc tách...'}</span>
                  </>
                ) : (
                  <>
                    <IconDownload className="w-3.5 h-3.5" />
                    <span>Phân tích &amp; Tải</span>
                  </>
                )}
              </button>
              {isLoading && !isCancelling && (
                <button
                  type="button"
                  className="pane-cancel-btn"
                  onClick={handleCancelExtract}
                  title="Hủy giải mã đang chạy"
                >
                  ✕ Hủy
                </button>
              )}
            </div>
          </form>
        ) : (
          <form onSubmit={handleBatchExtract} className="pane-form">
            <div className="batch-textarea-wrap">
              <textarea
                className="pane-textarea"
                placeholder="Dán danh sách liên kết, mỗi link một dòng (tối đa 10 link)..."
                value={batchText}
                onChange={(e) => {
                  const val = e.target.value
                  setBatchText(val)
                  if (!val.trim()) {
                    setBatchMedias([])
                    setSelectedImages({})
                    setNativeProgress(null)
                  }
                }}
                disabled={isLoading}
              />
              <div className="textarea-footer-bar">
                <span>{parsedBatchLinks.length}/10 link hợp lệ</span>
                <div className="textarea-footer-actions">
                  <button
                    type="button"
                    className="minimal-small-btn"
                    onClick={() => handlePaste('batch')}
                  >
                    Dán thêm
                  </button>
                  {batchText && (
                    <button
                      type="button"
                      className="minimal-small-btn"
                      onClick={handleClearBatch}
                    >
                      Xóa
                    </button>
                  )}
                </div>
              </div>
            </div>

            <div className="pane-control-row">
              {batchProgress.statusText && (
                <span className="validation-hint">{batchProgress.statusText}</span>
              )}

              <button
                type="submit"
                className="pane-submit-btn"
                disabled={isLoading || parsedBatchLinks.length === 0}
              >
                {isLoading ? (
                  <>
                    <span className="minimal-spinner" />
                    <span>{isCancelling ? 'Đang hủy...' : `Đang tải hàng loạt...`}</span>
                  </>
                ) : (
                  <>
                    <IconDownload className="w-3.5 h-3.5" />
                    <span>Bóc tách hàng loạt</span>
                  </>
                )}
              </button>
              {isLoading && !isCancelling && (
                <button
                  type="button"
                  className="pane-cancel-btn"
                  onClick={handleCancelExtract}
                  title="Hủy giải mã đang chạy"
                >
                  ✕ Hủy
                </button>
              )}
            </div>
          </form>
        )}
      </div>

      {/* Khu vực hiển thị kết quả (Scrollable) */}
      <div className="pane-results-container">
        {/* Kết quả tải đơn */}
        {singleMedia && (
          <div className="result-content-wrap">
            {/* Header thông tin media */}
            <div className="media-summary-row">
              <div className="media-thumb-box">
                <img
                  src={buildProxyImageUrl(singleMedia.thumbnail || singleMedia.highResThumbnail)}
                  alt={singleMedia.title}
                  className="preview-img"
                  referrerPolicy="no-referrer"
                  onError={(e) => {
                    e.currentTarget.onerror = null
                    e.currentTarget.src = singleMedia.thumbnail || singleMedia.highResThumbnail
                  }}
                />
                {singleMedia.duration && (
                  <span className="duration-tag">{singleMedia.duration}</span>
                )}
              </div>

              <div className="media-info-box">
                <div className="media-title-line">
                  <h3 className="media-title" title={singleMedia.title}>
                    {singleMedia.title || 'Phương tiện đã bóc tách'}
                  </h3>
                  <button
                    type="button"
                    className="icon-close-small"
                    onClick={() => setSingleMedia(null)}
                    title="Đóng kết quả"
                  >
                    <IconClose className="w-3.5 h-3.5" />
                  </button>
                </div>

                <p className="media-meta-line">
                  {singleMedia.platform?.toUpperCase()}
                  {singleMedia.author && ` • @${singleMedia.author}`}
                  {singleMedia.viewCount && ` • ${singleMedia.viewCount}`}
                </p>

                {/* Các nút công cụ nhanh */}
                <div className="media-actions-toolbar">
                  <button
                    type="button"
                    className="minimal-small-btn"
                    onClick={() => handleCopy(singleMedia.originalUrl)}
                  >
                    <IconCopy className="w-3 h-3" />
                    <span>Copy URL</span>
                  </button>
                  <button
                    type="button"
                    className="minimal-small-btn"
                    onClick={handleDownloadThumbnail}
                  >
                    <span>Lưu Thumbnail</span>
                  </button>
                  <button
                    type="button"
                    className={`minimal-small-btn ${isTrimmerOpen ? 'active' : ''}`}
                    onClick={() => setIsTrimmerOpen(!isTrimmerOpen)}
                  >
                    <IconScissors className="w-3 h-3" />
                    <span>Cắt đoạn</span>
                  </button>
                  <button
                    type="button"
                    className={`minimal-small-btn ${isOptionsOpen ? 'active' : ''}`}
                    onClick={() => setIsOptionsOpen(!isOptionsOpen)}
                  >
                    <IconSettings className="w-3 h-3" />
                    <span>Tùy chọn tải</span>
                  </button>
                </div>
              </div>
            </div>

            {/* Trimmer box (nếu mở) */}
            {isTrimmerOpen && (
              <div className="trimmer-inline-box">
                <span className="trimmer-label">Cắt clip:</span>
                <input
                  type="text"
                  className="trimmer-input"
                  placeholder="00:00"
                  value={trimStart}
                  onChange={(e) => setTrimStart(e.target.value)}
                  title="Thời điểm bắt đầu (vd: 00:10)"
                />
                <span>đến</span>
                <input
                  type="text"
                  className="trimmer-input"
                  placeholder="00:30"
                  value={trimEnd}
                  onChange={(e) => setTrimEnd(e.target.value)}
                  title="Thời điểm kết thúc (vd: 01:00)"
                />
                <div className="trimmer-presets">
                  <button
                    type="button"
                    className="btn-trim-preset"
                    onClick={() => { setTrimStart('00:00'); setTrimEnd('00:30') }}
                  >
                    30s đầu
                  </button>
                  <button
                    type="button"
                    className="btn-trim-preset"
                    onClick={() => { setTrimStart('00:00'); setTrimEnd('01:00') }}
                  >
                    1p đầu
                  </button>
                  {(trimStart || trimEnd) && (
                    <button
                      type="button"
                      className="btn-trim-preset btn-trim-reset"
                      onClick={() => { setTrimStart(''); setTrimEnd('') }}
                    >
                      Đặt lại
                    </button>
                  )}
                </div>
              </div>
            )}

            {/* Advanced download options box (nếu mở) */}
            {isOptionsOpen && (
              <div className="download-options-inline-box">
                <label className="checkbox-opt-label">
                  <input
                    type="checkbox"
                    checked={accelerate}
                    onChange={(e) => setAccelerate(e.target.checked)}
                  />
                  <span>Tăng tốc 8x (Đa luồng)</span>
                </label>

                <label className="checkbox-opt-label">
                  <input
                    type="checkbox"
                    checked={embedMetadata}
                    onChange={(e) => setEmbedMetadata(e.target.checked)}
                  />
                  <span>Nhúng Metadata</span>
                </label>

                <label className="checkbox-opt-label">
                  <input
                    type="checkbox"
                    checked={embedSubs}
                    onChange={(e) => setEmbedSubs(e.target.checked)}
                  />
                  <span>Nhúng Phụ đề</span>
                </label>

                <label className="checkbox-opt-label">
                  <input
                    type="checkbox"
                    checked={alwaysAskDir}
                    onChange={(e) => {
                      setAlwaysAskDir(e.target.checked)
                      setAlwaysAskDownloadDir(e.target.checked)
                    }}
                  />
                  <span>Hỏi nơi lưu trữ trước khi tải</span>
                </label>

                <div className="format-container-picker">
                  <span className="picker-label">Định dạng file:</span>
                  <select
                    className="minimal-select"
                    value={videoContainer}
                    onChange={(e) => setVideoContainer(e.target.value)}
                  >
                    <option value="auto">Mặc định (Khuyên dùng)</option>
                    <option value="mp4">MP4 (Tương thích cao)</option>
                    <option value="mkv">MKV (Chất lượng gốc)</option>
                    <option value="webm">WebM (Nhẹ / Web)</option>
                    <option value="gif">GIF (Ảnh động)</option>
                  </select>
                </div>
              </div>
            )}

            {/* Thẻ hiển thị Tiến trình tải xuống */}
            {nativeProgress && (
              <DownloadProgressCard
                progress={nativeProgress}
                title={downloadTaskTitle}
                onDismiss={() => setNativeProgress(null)}
              />
            )}

            {/* Phụ đề có sẵn (Nếu nhiều hơn 2 thì dùng Dropdown chọn gọn gàng) */}
            {singleMedia.subtitles && singleMedia.subtitles.length > 0 && (
              <div className="subtitles-section">
                <div className="sub-title-label">
                  <IconSubtitle className="w-3.5 h-3.5 text-blue-400" />
                  <span>Phụ đề ({singleMedia.subtitles.length}):</span>
                </div>
                {singleMedia.subtitles.length <= 2 ? (
                  <div className="sub-pills-row">
                    {singleMedia.subtitles.map((sub) => (
                      <button
                        key={sub.lang}
                        type="button"
                        className="minimal-pill-small"
                        onClick={() => handleDownloadSubtitle(sub)}
                      >
                        <IconSubtitle className="w-3 h-3" />
                        <span>{sub.name || sub.lang}</span>
                      </button>
                    ))}
                  </div>
                ) : (
                  <div className="sub-dropdown-control">
                    <select
                      className="sub-select-dropdown"
                      value={selectedSubLang}
                      onChange={(e) => setUserSelectedSubLang(e.target.value)}
                    >
                      {singleMedia.subtitles.map((sub) => (
                        <option key={sub.lang} value={sub.lang}>
                          {sub.name ? `${sub.name} (${sub.lang})` : sub.lang}
                        </option>
                      ))}
                    </select>
                    <button
                      type="button"
                      className="btn-download-sub-action"
                      onClick={() => {
                        const chosen =
                          singleMedia.subtitles.find((s) => s.lang === selectedSubLang) ||
                          singleMedia.subtitles[0]
                        if (chosen) handleDownloadSubtitle(chosen)
                      }}
                    >
                      <IconDownload className="w-3.5 h-3.5" />
                      <span>Tải phụ đề</span>
                    </button>
                  </div>
                )}
              </div>
            )}

            {/* Nếu là bài viết Album Ảnh */}
            {albumImages.length > 0 && (
              <div className="album-section">
                <div className="album-header-bar">
                  <span className="album-count-text">Album ảnh ({albumImages.length} tệp)</span>
                  <div className="album-actions-group">
                    <button
                      type="button"
                      className="minimal-small-btn"
                      onClick={handleToggleSelectAllImages}
                    >
                      {selectedImageCount === albumImages.length ? 'Bỏ chọn' : 'Chọn tất cả'}
                    </button>
                    {selectedImageCount > 0 && (
                      <>
                        <button
                          type="button"
                          className="minimal-small-btn"
                          onClick={() => handleDownloadAlbum(false)}
                          disabled={isZipDownloading}
                          title="Tải ảnh trực tiếp vào một thư mục riêng biệt"
                        >
                          <span>📁 Tải thư mục ({selectedImageCount})</span>
                        </button>
                        <button
                          type="button"
                          className="minimal-small-btn"
                          onClick={() => handleDownloadAlbum(true)}
                          disabled={isZipDownloading}
                          title="Đóng gói toàn bộ ảnh đã chọn thành file nén ZIP"
                        >
                          <IconZip className="w-3 h-3" />
                          <span>Tải ZIP ({selectedImageCount})</span>
                        </button>
                      </>
                    )}
                  </div>
                </div>

                <div className="album-grid">
                  {albumImages.map((img) => (
                    <div
                      key={img.id}
                      className={`album-item ${selectedImages[img.id] ? 'is-selected' : ''}`}
                      onClick={() =>
                        setSelectedImages((prev) => ({
                          ...prev,
                          [img.id]: !prev[img.id],
                        }))
                      }
                    >
                      <img
                        src={buildProxyImageUrl(img.thumb || img.url)}
                        alt=""
                        className="album-img"
                        onError={(e) => {
                          e.target.style.display = 'none'
                        }}
                      />
                      <input
                        type="checkbox"
                        className="album-checkbox"
                        checked={Boolean(selectedImages[img.id])}
                        readOnly
                      />
                      <button
                        type="button"
                        className="album-download-btn"
                        onClick={(e) => {
                          e.stopPropagation()
                          handleDownloadImage(img)
                        }}
                        title="Tải ảnh này"
                      >
                        <IconDownload className="w-3 h-3" />
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Cảnh báo khi stream bị ẩn / DRM */}
            {singleMedia.streams !== null && singleMedia.streams !== undefined &&
             singleMedia.streams.length === 0 && singleMedia.description?.startsWith('⚠️') && (
              <div className="stream-not-found-notice">
                <div className="stream-not-found-icon">🔒</div>
                <div className="stream-not-found-text">
                  <strong>Không tìm được nguồn video</strong>
                  <p>Player của trang này có thể dùng DRM (Widevine/PlayReady), mã hóa phức tạp, hoặc token đã hết hạn ngay khi load. Không thể tải về.</p>
                  <p className="stream-not-found-hint">💡 Thử mở trang bằng trình duyệt, chọn chất lượng và sao chép URL stream trực tiếp.</p>
                </div>
              </div>
            )}

            {/* Badge khi stream được phát hiện bởi Playwright sniffer */}
            {singleMedia.description?.startsWith('🔍') && singleMedia.streams && singleMedia.streams.length > 0 && (
              <div className="sniff-success-badge">
                🔍 Đã phát hiện {singleMedia.streams.length} nguồn stream ẩn qua Network Interceptor
              </div>
            )}

            {/* Danh sách định dạng Video / Âm thanh */}
            {filteredStreams.length > 0 && (
              <div className="streams-section">
                {/* Bộ lọc format stream */}
                <div className="streams-filter-bar">
                  <button
                    type="button"
                    className={`stream-tab-btn ${streamFilter === 'all' ? 'active' : ''}`}
                    onClick={() => setStreamFilter('all')}
                  >
                    Tất cả định dạng
                  </button>
                  <button
                    type="button"
                    className={`stream-tab-btn ${streamFilter === 'full' ? 'active' : ''}`}
                    onClick={() => setStreamFilter('full')}
                  >
                    Có tiếng
                  </button>
                  <button
                    type="button"
                    className={`stream-tab-btn ${streamFilter === 'mute' ? 'active' : ''}`}
                    onClick={() => setStreamFilter('mute')}
                  >
                    Chỉ video
                  </button>
                  <button
                    type="button"
                    className={`stream-tab-btn ${streamFilter === 'audio' ? 'active' : ''}`}
                    onClick={() => setStreamFilter('audio')}
                  >
                    Âm thanh (MP3)
                  </button>
                </div>

                {/* Danh sách các stream */}
                <div className="streams-list">
                  {filteredStreams.map((stream, sIdx) => {
                    const isAudio = stream.streamType === 'audio'
                    const streamId = stream.formatId || stream.quality || sIdx
                    const isCurrentDownloading = downloadingId === streamId

                    return (
                      <div key={streamId} className="stream-row-item">
                        <div className="stream-left-info">
                          {isAudio ? (
                            <IconAudio className="w-4 h-4 text-emerald-400" />
                          ) : (
                            <IconVideo className="w-4 h-4 text-blue-400" />
                          )}
                          <div className="stream-title-group">
                            <span className="stream-quality-title">
                              {stream.quality || (isAudio ? 'MP3 Audio' : 'Video Stream')}
                            </span>
                            <span className="stream-details-sub">
                              {stream.ext?.toUpperCase() || (isAudio ? 'MP3' : 'MP4')}
                              {stream.filesize ? ` • ${stream.filesize}` : ''}
                              {stream.resolution ? ` • ${stream.resolution}` : ''}
                            </span>
                          </div>
                        </div>

                        <button
                          type="button"
                          className="stream-download-btn"
                          onClick={() => handleDownloadStream(stream)}
                          disabled={isCurrentDownloading}
                        >
                          {isCurrentDownloading ? (
                            <>
                              <span className="minimal-spinner" />
                              <span>Đang tải...</span>
                            </>
                          ) : (
                            <>
                              <IconDownload className="w-3.5 h-3.5" />
                              <span>Tải về</span>
                            </>
                          )}
                        </button>
                      </div>
                    )
                  })}
                </div>
              </div>
            )}
          </div>
        )}

        {/* Kết quả nhiều link (Batch) */}
        {batchMedias && batchMedias.length > 0 && (
          <div className="batch-results-list">
            <div className="batch-results-header">
              <span>Đã bóc tách {batchMedias.length} liên kết</span>
              <button
                type="button"
                className="minimal-small-btn"
                onClick={() => setBatchMedias([])}
              >
                Xóa kết quả
              </button>
            </div>
            <div className="batch-items-stack">
              {batchMedias.map((m, idx) => (
                <div key={m.id || idx} className="batch-row-item">
                  <img
                    src={buildProxyImageUrl(m.thumbnail || m.highResThumbnail)}
                    alt=""
                    className="batch-item-thumb"
                    onError={(e) => {
                      e.target.style.display = 'none'
                    }}
                  />
                  <div className="batch-item-info">
                    <span className="batch-item-title" title={m.title}>
                      {m.title || 'Liên kết'}
                    </span>
                    <span className="batch-item-sub">
                      {m.platform?.toUpperCase()}
                      {m.duration ? ` • ${m.duration}` : ''}
                    </span>
                  </div>
                  <button
                    type="button"
                    className="minimal-small-btn"
                    onClick={() => {
                      const topStream = m.streams?.[0] || { quality: 'Tự động' }
                      handleDownloadStream(topStream, m)
                    }}
                  >
                    <IconDownload className="w-3.5 h-3.5" />
                    <span>Tải về</span>
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Trạng thái trống tối giản */}
        {!singleMedia && (!batchMedias || batchMedias.length === 0) && (
          <div className="pane-empty-state">
            <p className="empty-subtle-hint">
              Dán liên kết video, Reels hoặc album bài viết để phân tích và tải về
            </p>
          </div>
        )}
      </div>
    </div>
  )
}
