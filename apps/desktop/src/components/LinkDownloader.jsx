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
import { VIDEO_CONTAINER_OPTIONS, detectPlatform, validatePlatformUrl, getPlatform, isGenericShortenerUrl, safeThumbSrc } from '../constants'
import {
  extractMedia,
  cancelExtraction,
  cancelDownload,
  resolveShortUrl,
  startNativeDownload,
  createTaskId,
  downloadThumbnail,
  downloadSubtitle,
  downloadAlbumBatch,
  downloadDirectFile,
  onDownloadProgress,
  askForDownloadDirectory,
  getAlwaysAskDownloadDir,
} from '../services/api'
import { DownloadTaskList } from './DownloadProgressCard'
import { useDownloadTasks } from '../hooks/useDownloadTasks'

/**
 * Loại bỏ ký tự không hợp lệ và nguy hiểm trên Linux, ngăn chặn path traversal.
 */
function sanitizeFilenamePart(str, fallback = 'image') {
  if (!str || typeof str !== 'string') return fallback
  const cleaned = str
    .replace(/[/\0\\:*?"<>|;&$!`\n\r\t]/g, '_')
    .replace(/\.{2,}/g, '_')
    .trim()
    .replace(/^\.+|\.+$/g, '')
    .slice(0, 100)
    .trim()
  return cleaned || fallback
}

/**
 * Sinh danh sách item tải batch với filename unique cho mỗi media:
 * - Ưu tiên: title + media ID (ví dụ: image_12345.jpg)
 * - Nếu media ID không tồn tại: dùng cơ chế collision-safe (ví dụ: image_001.jpg)
 * - Đảm bảo mỗi selected media trong một batch phải có filename unique (không trùng lặp)
 * - Filename hợp lệ trên Linux, loại bỏ ký tự nguy hiểm, chống path traversal, extension đúng
 */
function generateBatchMediaFilenames(items, defaultReferer = null) {
  const usedNames = new Set()
  return items.map((img, idx) => {
    // 1. Xác định extension chuẩn
    let ext = (img.ext || '').toLowerCase().replace(/[^a-z0-9]/g, '')
    if (!ext && img.url) {
      const match = img.url.split('?')[0].match(/\.([a-zA-Z0-9]{3,4})$/)
      if (match) ext = match[1].toLowerCase()
    }
    if (!ext) ext = 'jpg'

    // 2. Làm sạch title và tách extension nếu title đã chứa
    let rawTitle = (img.title || 'image').trim()
    if (ext && rawTitle.toLowerCase().endsWith(`.${ext}`)) {
      rawTitle = rawTitle.slice(0, -(ext.length + 1))
    }
    const cleanTitle = sanitizeFilenamePart(rawTitle, 'image')

    // 3. Ưu tiên: title + media ID
    const rawId = img.id != null ? String(img.id).trim() : ''
    const cleanId = rawId ? sanitizeFilenamePart(rawId, '') : ''

    let baseName
    if (cleanId) {
      if (cleanTitle.endsWith(`_${cleanId}`) || cleanTitle === cleanId) {
        baseName = cleanTitle
      } else {
        baseName = `${cleanTitle}_${cleanId}`
      }
    } else {
      // Cơ chế collision-safe khi không có media ID
      const indexStr = String(idx + 1).padStart(3, '0')
      baseName = `${cleanTitle}_${indexStr}`
    }

    // 4. Đảm bảo 100% unique trong batch
    let candidate = `${baseName}.${ext}`
    let counter = 1
    while (usedNames.has(candidate)) {
      candidate = `${baseName}_${counter}.${ext}`
      counter++
    }
    usedNames.add(candidate)

    return {
      url: img.url,
      filename: candidate,
      referer: img.referer || defaultReferer || null,
    }
  })
}

export default function LinkDownloader({ onShowToast, dlOptions = {} }) {
  // Shared download options từ App level (persist qua localStorage)
  const {
    videoContainer = 'auto',
    accelerate = false,
    embedMetadata = true,
    embedThumbnail = false,
    useAria2c = false,
    onVideoContainerChange,
    onAccelerateChange,
    onEmbedMetadataChange,
    onEmbedThumbnailChange,
    onUseAria2cChange,
  } = dlOptions

  const [mode, setMode] = useState('single') // 'single' | 'batch'
  const [url, setUrl] = useState('')
  const [batchText, setBatchText] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const [batchProgress, setBatchProgress] = useState({ current: 0, total: 0, statusText: '' })

  // Kết quả sau khi phân tích
  const [singleMedia, setSingleMedia] = useState(null)
  const [batchMedias, setBatchMedias] = useState([])
  const [selectedBatchIds, setSelectedBatchIds] = useState({})
  const [streamFilter, setStreamFilter] = useState('all') // 'all' | 'full' | 'mute' | 'audio'
  const [selectedImages, setSelectedImages] = useState({})
  // Mỗi lượt tải một thẻ tiến trình riêng — tải song song không còn ghi đè nhau
  const {
    tasks: downloadTasks,
    startTask,
    updateTask,
    dismissTask,
    clearFinishedTasks,
    markCancelled,
    isCancelled,
    isSourceBusy,
  } = useDownloadTasks()
  const [userSelectedSubLang, setUserSelectedSubLang] = useState('')
  const selectedSubLang = userSelectedSubLang || singleMedia?.subtitles?.[0]?.lang || ''
  const cancelRef = useRef(false)         // chặn xử lý kết quả sau khi đã hủy
  const activeExtractTaskRef = useRef(null) // task đang chạy, để huỷ thật ở backend
  const [isCancelling, setIsCancelling] = useState(false)

  // Trimmer tool state — chỉ có nghĩa với tải đơn link
  const [isTrimmerOpen, setIsTrimmerOpen] = useState(false)
  const [trimStart, setTrimStart] = useState('')
  const [trimEnd, setTrimEnd] = useState('')
  const resetTrimmer = () => {
    setIsTrimmerOpen(false)
    setTrimStart('')
    setTrimEnd('')
  }

  // Advanced options UI state
  const [isOptionsOpen, setIsOptionsOpen] = useState(false)
  // embedSubs / SponsorBlock / tách chương chỉ dùng cho tải đơn (gắn với 1 video cụ thể)
  const [embedSubs, setEmbedSubs] = useState(false)
  const [sponsorBlock, setSponsorBlock] = useState(false)
  const [splitChapters, setSplitChapters] = useState(false)

  // concurrentFragments: tối ưu CPU — accelerate=false→1 (máy yếu), true→4 (mạng tốt)
  const concurrentFragments = accelerate ? 4 : 1

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
    const taskId = createTaskId()
    activeExtractTaskRef.current = taskId
    try {
      const data = await extractMedia(targetUrl, null, taskId)
      if (!cancelRef.current && data) {
        setSingleMedia(data)
        setBatchMedias([])
        // Lựa chọn của kết quả TRƯỚC không được áp sang kết quả mới. Trước đây ảnh
        // được đánh dấu theo id (1, 2, 3...) nên album mới hiện sẵn các ô đã chọn và
        // "Tải ZIP" tải luôn những ảnh người dùng chưa hề chọn; mốc "Cắt đoạn" của
        // video trước cũng âm thầm cắt cụt video sau.
        setSelectedImages({})
        setUserSelectedSubLang('')
        resetTrimmer()
        onShowToast?.(`Đã trích xuất: ${data.title?.slice(0, 30) || 'Thành công'}...`)
      }
    } catch (err) {
      if (!cancelRef.current) {
        const errorMsg = typeof err === 'string' ? err : err?.message || 'Lỗi khi trích xuất liên kết'
        onShowToast?.(errorMsg)
      }
    } finally {
      activeExtractTaskRef.current = null
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

      const linkTaskId = createTaskId()
      activeExtractTaskRef.current = linkTaskId
      try {
        const item = await extractMedia(link, null, linkTaskId)
        if (item && !cancelRef.current) results.push(item)
      } catch (err) {
        const errMsg = typeof err === 'string' ? err : err?.message || ''
        const isTimeout = errMsg.toLowerCase().includes('timeout') || errMsg.toLowerCase().includes('55 giây')
        console.warn(`[Batch] Lỗi ${isTimeout ? 'timeout' : 'bóc tách'} ${link}:`, errMsg)
        failedLinks.push({ link, reason: isTimeout ? 'Timeout' : errMsg.slice(0, 60) })
      } finally {
        activeExtractTaskRef.current = null
      }
    }

    setIsLoading(false)
    setIsCancelling(false)
    setBatchProgress({ current: 0, total: 0, statusText: '' })

    if (results.length > 0) {
      setBatchMedias(results)
      setSelectedBatchIds({})
      setSingleMedia(null)
      const failMsg = failedLinks.length > 0 ? ` (${failedLinks.length} lỗi/timeout)` : ''
      onShowToast?.(`Giải mã thành công ${results.length}/${parsedBatchLinks.length} liên kết!${failMsg}`)
    } else {
      onShowToast?.('Không thể giải mã các liên kết đã nhập. Vui lòng kiểm tra lại liên kết.')
    }
  }

  // Hủy giải mã đang chạy — kill luôn tiến trình yt-dlp/gallery-dl phía Python,
  // không chỉ bỏ qua kết quả như trước.
  const handleCancelExtract = async () => {
    cancelRef.current = true
    setIsCancelling(true)
    const taskId = activeExtractTaskRef.current
    if (taskId) {
      onShowToast?.('Đang dừng tiến trình bóc tách...')
      await cancelExtraction(taskId)
    }
    onShowToast?.('Đã hủy giải mã.')
  }

  // Chế độ "Hỏi trước khi tải": undefined = dùng thư mục mặc định, null = người dùng bỏ chọn
  const pickTargetDir = async (cancelMsg) => {
    if (!getAlwaysAskDownloadDir()) return undefined
    try {
      const dir = await askForDownloadDirectory()
      if (!dir) {
        onShowToast?.(cancelMsg)
        return null
      }
      return dir
    } catch (err) {
      onShowToast?.(typeof err === 'string' ? err : err?.message || 'Lỗi chọn thư mục lưu')
      return null
    }
  }

  // Hủy một tác vụ tải và xoá sạch tệp dở dang của nó
  const handleCancelDownload = async (taskId) => {
    if (!taskId) return
    markCancelled(taskId)
    try {
      await cancelDownload(taskId)
      updateTask(taskId, {
        percent: 0,
        speed: '',
        eta: '',
        status: 'cancelled',
        phase: 'Đã hủy tải xuống',
        message: 'Đã hủy tải xuống và xoá sạch tệp dở dang',
      })
      onShowToast?.('Đã hủy tải và dọn dẹp tệp dở dang')
    } catch (err) {
      console.error('Cancel download error:', err)
    }
  }

  const errorText = (err, fallback) => (typeof err === 'string' ? err : err?.message || fallback)
  const isCancelError = (taskId, msg) =>
    isCancelled(taskId) || msg.includes('cancelled') || msg.includes('hủy') || msg.includes('huỷ') || msg.includes('abort')

  // Kết thúc một tác vụ bằng lỗi — phân biệt "đã huỷ" với lỗi thật
  const reportTaskError = (taskId, err, fallback) => {
    const errMsg = errorText(err, fallback)
    if (isCancelError(taskId, errMsg)) {
      updateTask(taskId, {
        percent: 0,
        speed: '',
        eta: '',
        status: 'cancelled',
        phase: 'Đã hủy tải xuống',
        message: 'Đã hủy tải xuống và xoá sạch tệp dở dang',
      })
      return
    }
    updateTask(taskId, (prev) => ({
      ...(prev || {}),
      speed: '',
      eta: '',
      status: 'error',
      phase: 'Tải thất bại',
      message: errMsg,
    }))
    onShowToast?.(errMsg)
  }

  const streamSourceKey = (media, stream) =>
    `stream:${media?.id || media?.originalUrl || ''}:${stream?.formatId || stream?.quality || 'stream'}`

  // Tải stream video/audio đơn
  const handleDownloadStream = async (stream, media = singleMedia) => {
    if (!media) return
    if (!media.originalUrl && !stream?.url) {
      onShowToast?.('Liên kết này không có nguồn tải hợp lệ để tải về.')
      return
    }

    const targetDir = await pickTargetDir('Đã hủy tải do chưa chọn thư mục lưu')
    if (targetDir === null) return

    const taskId = createTaskId()
    startTask(taskId, {
      title: media.title || stream.quality || 'Video',
      sourceKey: streamSourceKey(media, stream),
      progress: { percent: 0, speed: '', eta: '', status: 'preparing', phase: 'Đang chuẩn bị tải...' },
    })

    let unlisten = null
    try {
      const isAudioOnly = stream.streamType === 'audio'
      const isMute = stream.streamType === 'mute'
      // Cắt đoạn / SponsorBlock / tách chương chỉ áp dụng cho kết quả tải đơn đang mở
      const isSingle = media === singleMedia
      onShowToast?.(`Đang tải: ${stream.quality || 'tệp'}`)

      try {
        // Chỉ nhận sự kiện của đúng tác vụ này
        unlisten = await onDownloadProgress((payload) => updateTask(taskId, payload), taskId)
      } catch (e) {
        console.warn('Cannot attach progress listener:', e)
      }

      // Với các nền tảng mạng xã hội có extractor chuẩn, luôn dùng URL bài viết gốc + formatId
      // để yt-dlp sử dụng đầy đủ cookies, referer và session đăng nhập, tránh lỗi 403 Forbidden.
      // Ngoại lệ: stream do engine KHÁC yt-dlp tìm ra (Story, gallery-dl, sniffer, web scraper)
      // — yt-dlp đã không đọc được trang gốc (vd. không hỗ trợ Facebook Story) nên phải tải
      // thẳng URL tệp của stream.
      const fid = stream.formatId || ''
      const isNonYtdlpStream =
        media.isStory ||
        fid === 'original_video' ||
        fid.startsWith('story_video_') ||
        fid.startsWith('sniff_') ||
        fid.startsWith('web_video_')
      const isDirectStreamOnly =
        !media.originalUrl ||
        media.platform === 'movie' ||
        media.platform === 'generic' ||
        isNonYtdlpStream ||
        Boolean(stream.url && (stream.url.includes('.m3u8') || stream.url.includes('.mpd')))

      const downloadUrl = isDirectStreamOnly && stream.url ? stream.url : (media.originalUrl || stream.url)
      const downloadFormatId = isDirectStreamOnly && stream.url ? null : (stream.formatId || null)

      // "Cắt đoạn" chỉ áp dụng khi hộp cắt đang mở: đóng hộp lại là tải nguyên video
      // (trước đây mốc đã nhập vẫn âm thầm được dùng dù hộp đã đóng).
      const useTrim = isSingle && isTrimmerOpen
      const res = await startNativeDownload({
        url: downloadUrl,
        formatId: downloadFormatId,
        isAudio: isAudioOnly,
        isMute: isMute,
        // Luồng tìm thấy bên trong một trang (trang phim) cần Referer là trang đó
        referer: media.referer || media.originalUrl || undefined,
        startTime: (useTrim && trimStart) || undefined,
        endTime: (useTrim && trimEnd) || undefined,
        title: media.title,
        destDir: targetDir,
        embedSubs: isSingle && embedSubs,
        embedMetadata: embedMetadata,
        embedThumbnail: embedThumbnail,
        sponsorBlock: isSingle && sponsorBlock,
        splitChapters: isSingle && splitChapters && !isAudioOnly,
        useAria2c,
        concurrentFragments,
        // Container video không thể áp dụng cho stream audio/MP3.
        videoFormat: !isAudioOnly && videoContainer !== 'auto' ? videoContainer : undefined,
        taskId,
        platform: media.platform,
      })

      if (res?.success) {
        updateTask(taskId, {
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
      reportTaskError(taskId, err, 'Lỗi khi tải stream')
    } finally {
      // Gỡ listener trong mọi trường hợp — trước đây khi tải lỗi listener bị rò rỉ,
      // tích lũy dần và làm thanh tiến trình nhảy loạn ở các lần tải sau.
      if (typeof unlisten === 'function') unlisten()
    }
  }

  // Tải thumbnail
  const handleDownloadThumbnail = async () => {
    if (!singleMedia) return
    const targetDir = await pickTargetDir('Đã hủy lưu thumbnail do chưa chọn thư mục')
    if (targetDir === null) return
    onShowToast?.('Đang tải ảnh thumbnail...')

    let firstError = null
    if (singleMedia.originalUrl) {
      try {
        const res = await downloadThumbnail({
          url: singleMedia.originalUrl,
          title: singleMedia.title,
          destDir: targetDir,
        })
        onShowToast?.(`Đã lưu thumbnail: ${res?.file_name || 'ảnh bìa'}`)
        return
      } catch (err) {
        firstError = err
      }
    }

    // yt-dlp không đọc được trang gốc (bài ảnh Instagram, story, kết quả của
    // gallery-dl...) trong khi app đang hiển thị sẵn ảnh bìa: tải thẳng ảnh đó.
    const shownThumb = safeThumbSrc(singleMedia.highResThumbnail, singleMedia.thumbnail)
    if (shownThumb) {
      try {
        const res = await downloadDirectFile({
          url: shownThumb,
          filename: `${sanitizeFilenamePart(singleMedia.title || 'thumbnail', 'thumbnail')}_thumbnail`,
          referer: singleMedia.originalUrl,
          destDir: targetDir,
          platform: singleMedia.platform,
        })
        onShowToast?.(`Đã lưu thumbnail: ${res?.file_name || 'ảnh bìa'}`)
        return
      } catch (err) {
        firstError = firstError || err
      }
    }
    onShowToast?.(errorText(firstError, 'Không tìm thấy ảnh bìa để tải'))
  }

  // Tải phụ đề
  const handleDownloadSubtitle = async (sub) => {
    if (!singleMedia) return
    const targetDir = await pickTargetDir('Đã hủy lưu phụ đề do chưa chọn thư mục')
    if (targetDir === null) return
    try {
      onShowToast?.(`Đang tải phụ đề: ${sub.name || sub.lang}...`)
      const res = await downloadSubtitle({
        url: singleMedia.originalUrl,
        lang: sub.lang,
        format: sub.ext,
        title: singleMedia.title,
        destDir: targetDir,
      })
      if (res?.file_name) {
        onShowToast?.(`Đã lưu phụ đề: ${res.file_name}`)
      }
    } catch (err) {
      onShowToast?.(errorText(err, 'Lỗi khi tải phụ đề'))
    }
  }

  // Tải ảnh album đơn lẻ
  const handleDownloadImage = async (img) => {
    const targetDir = await pickTargetDir('Đã hủy tải do chưa chọn thư mục lưu')
    if (targetDir === null) return

    const kind = img.type === 'video' ? 'video' : 'ảnh'
    const taskId = createTaskId()
    startTask(taskId, {
      title: img.title || (img.type === 'video' ? 'Video' : 'Ảnh'),
      sourceKey: `image:${img.id}`,
      progress: { percent: 0, speed: '', eta: '', status: 'downloading', phase: `Đang tải ${kind}...`, isIndeterminate: true },
    })

    let unlisten = null
    try {
      try {
        unlisten = await onDownloadProgress((payload) => updateTask(taskId, payload), taskId)
      } catch (e) {
        console.warn('Cannot attach progress listener:', e)
      }

      const generated = generateBatchMediaFilenames([img], singleMedia?.originalUrl)
      const filename = generated[0]?.filename || `${sanitizeFilenamePart(img.title || 'photo')}.${img.ext || 'jpg'}`
      onShowToast?.(`Đang tải ${kind}: ${filename}...`)
      const res = await downloadDirectFile({
        url: img.url,
        filename,
        destDir: targetDir,
        referer: singleMedia?.originalUrl,
        platform: singleMedia?.platform,
        taskId,
      })
      if (res?.file_name) {
        updateTask(taskId, {
          percent: 100,
          speed: '',
          eta: '',
          status: 'completed',
          phase: 'Hoàn tất',
          filePath: res.file_path,
          fileName: res.file_name,
        })
        onShowToast?.(`Đã lưu: ${res.file_name}`)
      }
    } catch (err) {
      reportTaskError(taskId, err, 'Lỗi khi tải ảnh')
    } finally {
      if (typeof unlisten === 'function') unlisten()
    }
  }

  // Bài đăng nhiều video (vd. 1 tweet 3 video) dùng chung link bài viết, nhưng mỗi
  // video có URL file riêng → đưa vào lưới để tải được TỪNG video.
  const albumImages = useMemo(
    () =>
      (singleMedia?.images || []).filter(
        (img) =>
          img.type === 'image' ||
          img.type === 'gif' ||
          (img.type === 'video' && singleMedia?.type === 'album')
      ),
    [singleMedia]
  )
  const albumVideoOrder = useMemo(() => {
    const videos = albumImages.filter((img) => img.type === 'video')
    return { total: videos.length, indexOf: new Map(videos.map((img, i) => [img.id, i + 1])) }
  }, [albumImages])
  const selectedImageCount = useMemo(
    () => Object.values(selectedImages).filter(Boolean).length,
    [selectedImages]
  )
  const isAlbumBusy = isSourceBusy('album')
  const isBatchZipBusy = isSourceBusy('batch-zip')

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

  // Tải trực tiếp các tệp ảnh/video của MỘT bài đăng vào thư mục riêng (hoặc nén ZIP)
  const downloadMediaFiles = async ({ media, items: itemsToDownload, asZip, albumName: customAlbumName, sourceKey }) => {
    const targetDir = await pickTargetDir('Đã hủy do chưa chọn thư mục lưu')
    if (targetDir === null) return

    const taskId = createTaskId()
    startTask(taskId, {
      title: `${asZip ? 'Nén ZIP' : 'Tải'}: ${itemsToDownload.length} tệp`,
      sourceKey,
      progress: {
        percent: 0,
        speed: '',
        eta: '',
        status: 'preparing',
        phase: `Chuẩn bị tải ${itemsToDownload.length} tệp...`,
      },
    })

    let unlisten = null
    try {
      try {
        unlisten = await onDownloadProgress((payload) => updateTask(taskId, payload), taskId)
      } catch (e) {
        console.warn('Cannot attach progress listener:', e)
      }

      const itemsPayload = generateBatchMediaFilenames(
        itemsToDownload.map((img) => ({
          ...img,
          referer: media?.originalUrl,
        })),
        media?.originalUrl
      )
      const res = await downloadAlbumBatch({
        items: itemsPayload,
        albumName: customAlbumName,
        destDir: targetDir,
        asZip: asZip,
        taskId,
        platform: media?.platform,
      })
      if (res && res.success === false) {
        // Tải xong nhưng nén ZIP hỏng — báo đúng thay vì "Hoàn tất".
        updateTask(taskId, {
          percent: 100,
          speed: '',
          eta: '',
          status: 'error',
          phase: 'Nén ZIP thất bại',
          filePath: res.file_path,
          message: res.message,
        })
        onShowToast?.(res.message || 'Nén ZIP thất bại')
        return
      }

      updateTask(taskId, {
        percent: 100,
        speed: '',
        eta: '',
        status: 'completed',
        phase: 'Hoàn tất',
        filePath: res?.file_path,
        fileName: res?.file_name,
        message: res?.message,
      })
      onShowToast?.(res?.message || (res?.file_path ? `Đã lưu tại: ${res.file_path}` : `Đã tải xong ${itemsToDownload.length} tệp!`))
    } catch (err) {
      reportTaskError(taskId, err, 'Lỗi khi tải album')
    } finally {
      if (typeof unlisten === 'function') unlisten()
    }
  }

  const handleDownloadAlbum = async (asZip = false) => {
    const itemsToDownload = albumImages.filter((img) => selectedImages[img.id])
    if (itemsToDownload.length === 0) {
      onShowToast?.('Vui lòng chọn ít nhất 1 ảnh để tải')
      return
    }

    let customAlbumName = `${singleMedia?.title || 'Album'}_Media`
    if (asZip) {
      const promptResult = window.prompt(
        'Tên file quá dài có thể gây lỗi nén ZIP. Nhập tên file ZIP bạn muốn (để trống sẽ dùng tên mặc định):',
        customAlbumName
      )
      if (promptResult === null) {
        // Người dùng ấn Cancel
        return
      }
      if (promptResult.trim() !== '') {
        customAlbumName = promptResult.trim()
      }
    }

    await downloadMediaFiles({
      media: singleMedia,
      items: itemsToDownload,
      asZip,
      albumName: customAlbumName,
      sourceKey: 'album',
    })
  }

  // Khoá "đang bận" của nút "Tải về" ở từng mục trong danh sách nhiều link —
  // phải khớp đúng khoá mà nhánh tải tương ứng bên dưới dùng.
  const batchItemSourceKey = (media) => {
    if (media.streams?.length) return streamSourceKey(media, media.streams[0])
    if (media.images?.some((img) => img?.url)) return `post:${media.id || media.originalUrl || ''}`
    return streamSourceKey(media, { quality: 'Tự động' })
  }

  // "Tải về" của một mục trong danh sách nhiều link. Bài chỉ có ảnh (không có
  // stream video) trước đây vẫn bị đưa qua yt-dlp — luôn thất bại với "No video
  // formats found". Nay tải thẳng các tệp ảnh/video của bài vào một thư mục.
  const handleDownloadBatchItem = (media) => {
    if (media.streams?.length) {
      handleDownloadStream(media.streams[0], media)
      return
    }
    const files = (media.images || []).filter((img) => img?.url)
    if (files.length > 0) {
      downloadMediaFiles({
        media,
        items: files,
        asZip: false,
        albumName: `${media.title || 'Album'}_Media`,
        sourceKey: batchItemSourceKey(media),
      })
      return
    }
    handleDownloadStream({ quality: 'Tự động' }, media)
  }

  const selectedBatchCount = useMemo(
    () => Object.values(selectedBatchIds).filter(Boolean).length,
    [selectedBatchIds]
  )

  const handleToggleSelectAllBatch = () => {
    if (selectedBatchCount === batchMedias.length) {
      setSelectedBatchIds({})
      return
    }

    const all = {}
    batchMedias.forEach((media, index) => {
      all[media.id || index] = true
    })
    setSelectedBatchIds(all)
  }

  const handleDownloadBatchZip = async () => {
    const itemsToDownload = batchMedias.filter((media, index) => selectedBatchIds[media.id || index])
    if (itemsToDownload.length === 0) {
      onShowToast?.('Vui lòng chọn ít nhất 1 mục để tải ZIP')
      return
    }

    const promptResult = window.prompt(
      'Nhập tên file ZIP (để trống sẽ dùng tên mặc định):',
      'Batch_Media'
    )
    if (promptResult === null) return
    const albumName = promptResult.trim() || 'Batch_Media'

    const targetDir = await pickTargetDir('Đã hủy do chưa chọn thư mục lưu')
    if (targetDir === null) return

    const taskId = createTaskId()
    startTask(taskId, {
      title: `Nén ZIP: ${itemsToDownload.length} mục`,
      sourceKey: 'batch-zip',
      progress: {
        percent: 0,
        speed: '',
        eta: '',
        status: 'preparing',
        phase: `Chuẩn bị nén ${itemsToDownload.length} mục...`,
      },
    })

    // Một mục lỗi (link hết hạn, video bị chặn...) không được làm hỏng cả gói ZIP:
    // ghi lại rồi tải tiếp, cuối cùng nén những gì đã tải được.
    const failures = []
    let failedCount = 0
    let unlisten = null
    try {
      // Tạo thư mục trước. Video phải đi qua yt-dlp với URL gốc vì stream URL
      // của YouTube là URL ký tạm thời và thường là HLS playlist.
      const prepRes = await downloadAlbumBatch({
        items: [],
        albumName,
        destDir: targetDir,
        asZip: false,
        taskId,
        platform: itemsToDownload[0]?.platform,
      })
      const albumFolder = prepRes?.file_path
      if (!albumFolder) {
        throw new Error('Không tạo được thư mục tạm để tải ZIP')
      }

      const imageItems = itemsToDownload.flatMap((media) =>
        (media.images || [])
          .filter((image) => image.type === 'image' || image.type === 'gif')
          .map((image) => ({
            ...image,
            referer: media.originalUrl,
            title: image.title || media.title || 'image',
          }))
      )
      // Mỗi video của bài đăng là một mục tải riêng. Trước đây chỉ lấy video ĐẦU
      // TIÊN (find) nên bài có 3 video chỉ tải về 1.
      const videoItems = itemsToDownload.flatMap((media) => {
        const videos = (media.images || []).filter((image) => image.type === 'video')
        if (videos.length > 0) {
          return videos.map((image, i) => ({
            media,
            url: image.url,
            title: videos.length > 1 ? `${media.title || 'media'} (video ${i + 1})` : media.title,
          }))
        }
        if (media.images?.length > 0) return []
        return [{ media, url: media.originalUrl || media.url, title: media.title }]
      })
      const totalUnits = imageItems.length + videoItems.length
      let completed = 0

      if (imageItems.length > 0) {
        // Bước ảnh "completed"/"error" chưa phải kết cục của cả lượt ZIP
        unlisten = await onDownloadProgress((payload) => {
          const stepDone = payload?.status === 'completed' || payload?.status === 'error'
          updateTask(taskId, stepDone ? { ...payload, status: 'processing', filePath: undefined } : payload)
        }, taskId)
        try {
          const imageRes = await downloadAlbumBatch({
            items: generateBatchMediaFilenames(imageItems, null),
            albumName,
            destDir: targetDir,
            albumDir: albumFolder,
            asZip: false,
            taskId,
            platform: itemsToDownload[0]?.platform,
          })
          const failedImages = Number(imageRes?.message?.match(/(\d+) tệp thất bại/)?.[1] || 0)
          if (failedImages > 0) {
            failedCount += failedImages
            failures.push(imageRes.message)
          }
        } catch (err) {
          const msg = errorText(err, 'Không tải được ảnh')
          if (isCancelError(taskId, msg)) throw err
          failedCount += imageItems.length
          failures.push(msg)
        } finally {
          unlisten()
          unlisten = null
        }
        completed = imageItems.length
      }

      for (let index = 0; index < videoItems.length; index++) {
        if (isCancelled(taskId)) return
        const { media, url: videoUrl, title: videoTitle } = videoItems[index]
        const videoTaskId = `${taskId}_video_${index}`
        let videoUnlisten = null
        try {
          videoUnlisten = await onDownloadProgress((payload) => {
            // Quy về phần trăm của cả lượt ZIP và không để "completed"/"error" của
            // một video làm thẻ báo xong (ẩn nút Huỷ) khi các video sau vẫn đang tải.
            const videoPercent = Math.min(100, Math.max(0, Number(payload?.percent) || 0))
            const finishedOne = payload?.status === 'completed' || payload?.status === 'error'
            updateTask(taskId, {
              ...payload,
              percent: ((completed + videoPercent / 100) / totalUnits) * 90,
              // ETA của riêng một video không phải ETA của cả gói ZIP: để trống cho
              // useDownloadTasks ước tính theo phần trăm tổng. Video đang ghép tệp
              // cũng vẫn là "đang tải" xét trên cả gói.
              eta: '',
              status: finishedOne || payload?.status === 'processing' ? 'downloading' : payload?.status,
              filePath: undefined,
              isIndeterminate: false,
              phase: `Đang tải video [${index + 1}/${videoItems.length}]...`,
            })
          }, videoTaskId)
          if (!videoUrl) throw new Error(`Mục ${index + 1} không có URL gốc để tải`)
          const videoRes = await startNativeDownload({
            url: videoUrl,
            title: videoTitle || `media_${index + 1}`,
            destDir: albumFolder,
            embedMetadata,
            embedThumbnail,
            useAria2c,
            concurrentFragments,
            videoFormat: videoContainer !== 'auto' ? videoContainer : undefined,
            taskId: videoTaskId,
            platform: media.platform,
          })
          if (!videoRes?.success) {
            throw new Error(videoRes?.message || `Không tải được video thứ ${index + 1}`)
          }
        } catch (err) {
          const msg = errorText(err, `Không tải được video thứ ${index + 1}`)
          if (isCancelError(taskId, msg)) throw err
          failedCount++
          failures.push(`${videoTitle || `Video ${index + 1}`}: ${msg}`)
        } finally {
          if (typeof videoUnlisten === 'function') videoUnlisten()
          completed++
          updateTask(taskId, (prev) => ({
            ...(prev || {}),
            percent: Math.round((completed / totalUnits) * 90),
            status: 'downloading',
            phase: `Đã xử lý ${completed}/${totalUnits} tệp`,
          }))
        }
      }

      if (isCancelled(taskId)) return
      updateTask(taskId, {
        percent: 92,
        speed: '',
        eta: '',
        status: 'processing',
        phase: 'Đang nén các tệp đã tải...',
      })
      let res
      try {
        res = await downloadAlbumBatch({
          items: [],
          albumName,
          destDir: targetDir,
          albumDir: albumFolder,
          asZip: true,
          taskId,
          platform: itemsToDownload[0]?.platform,
        })
      } catch (err) {
        // Không mục nào tải được: báo nguyên nhân thật của mục đầu tiên
        if (failures.length > 0 && !isCancelError(taskId, errorText(err, ''))) {
          throw new Error(`Không tải được mục nào để nén ZIP. ${failures[0]}`, { cause: err })
        }
        throw err
      }
      if (res?.success === false) throw new Error(res.message || 'Nén ZIP thất bại')

      const summary = failedCount > 0
        ? `Đã nén ZIP, ${failedCount} tệp lỗi — ${failures[0]}`
        : res?.message
      updateTask(taskId, {
        percent: 100,
        speed: '',
        eta: '',
        status: 'completed',
        phase: 'Hoàn tất',
        filePath: res?.file_path,
        fileName: res?.file_name,
        message: summary,
      })
      onShowToast?.(summary || `Đã lưu ZIP: ${res?.file_path || res?.file_name || 'Batch_Media.zip'}`)
    } catch (err) {
      reportTaskError(taskId, err, 'Lỗi khi tải ZIP')
    } finally {
      if (typeof unlisten === 'function') unlisten()
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
    setSelectedBatchIds({})
    setAsyncResolved(null)
    setSelectedImages({})
    clearFinishedTasks()
    resetTrimmer()
  }

  const handleClearBatch = () => {
    setBatchText('')
    setBatchMedias([])
    setSelectedBatchIds({})
    setSelectedImages({})
    clearFinishedTasks()
  }

  const handleCopy = async (text) => {
    if (!text) return
    try {
      await navigator.clipboard.writeText(text)
      onShowToast?.('Đã sao chép liên kết vào bộ nhớ tạm')
    } catch {
      // Trước đây luôn báo "Đã sao chép" kể cả khi webview từ chối ghi clipboard
      onShowToast?.('Không sao chép được — hãy bôi đen liên kết và nhấn Ctrl+C')
    }
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
                    clearFinishedTasks()
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
                {VIDEO_CONTAINER_OPTIONS.map((f) => (
                  <button
                    key={f.id}
                    type="button"
                    className={`minimal-pill ${videoContainer === f.id ? 'active' : ''}`}
                    title={f.desc}
                    onClick={() => onVideoContainerChange?.(f.id)}
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
                    clearFinishedTasks()
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

            {/* Container + options cho batch mode */}
            <div className="pane-control-row">
              <div className="pills-group">
                {VIDEO_CONTAINER_OPTIONS.map((f) => (
                  <button
                    key={f.id}
                    type="button"
                    className={`minimal-pill ${videoContainer === f.id ? 'active' : ''}`}
                    title={f.desc}
                    onClick={() => onVideoContainerChange?.(f.id)}
                  >
                    {f.label}
                  </button>
                ))}
              </div>
              <button
                type="button"
                className={`minimal-small-btn ${isOptionsOpen ? 'active' : ''}`}
                onClick={() => setIsOptionsOpen(!isOptionsOpen)}
                title="Tùy chọn tải nâng cao"
              >
                <IconSettings className="w-3 h-3" />
                <span>Tùy chọn</span>
              </button>
            </div>

            {/* Options box cho batch mode */}
            {isOptionsOpen && (
              <div className="download-options-inline-box">
                <label className="checkbox-opt-label" title="Tăng tốc tải bằng đa luồng — tiêu thụ nhiều CPU/RAM hơn">
                  <input
                    type="checkbox"
                    checked={accelerate}
                    onChange={(e) => onAccelerateChange?.(e.target.checked)}
                  />
                  <span>Tăng tốc đa luồng</span>
                </label>
                <label className="checkbox-opt-label">
                  <input
                    type="checkbox"
                    checked={embedMetadata}
                    onChange={(e) => onEmbedMetadataChange?.(e.target.checked)}
                  />
                  <span>Nhúng Metadata</span>
                </label>
                <label className="checkbox-opt-label">
                  <input
                    type="checkbox"
                    checked={embedThumbnail}
                    onChange={(e) => onEmbedThumbnailChange?.(e.target.checked)}
                  />
                  <span>Nhúng Thumbnail</span>
                </label>
                <label className="checkbox-opt-label" title="Tải bằng aria2c (16 kết nối) — cần cài aria2c">
                  <input
                    type="checkbox"
                    checked={useAria2c}
                    onChange={(e) => onUseAria2cChange?.(e.target.checked)}
                  />
                  <span>Tải bằng aria2c</span>
                </label>
              </div>
            )}

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
        {/* Tiến trình của mọi lượt tải trong khung này — nằm ngoài khối kết quả để
            đóng kết quả không làm mất nút Huỷ của lượt tải đang chạy */}
        <DownloadTaskList tasks={downloadTasks} onCancel={handleCancelDownload} onDismiss={dismissTask} />

        {/* Kết quả tải đơn */}
        {singleMedia && (
          <div className="result-content-wrap">
            {/* Header thông tin media */}
            <div className="media-summary-row">
              <div className="media-thumb-box">
                {safeThumbSrc(singleMedia.thumbnail, singleMedia.highResThumbnail) ? (
                  <img
                    src={safeThumbSrc(singleMedia.thumbnail, singleMedia.highResThumbnail)}
                    alt={singleMedia.title}
                    className="preview-img"
                    referrerPolicy="no-referrer"
                    onError={(e) => {
                      e.currentTarget.style.display = 'none'
                    }}
                  />
                ) : (
                  <div className="preview-img thumb-placeholder">
                    <IconVideo className="w-5 h-5" />
                  </div>
                )}
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
                  {singleMedia.views && ` • ${singleMedia.views}`}
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

            {/* Advanced download options box (nếu mở) — Tải đơn link */}
            {isOptionsOpen && (
              <div className="download-options-inline-box">
                <label className="checkbox-opt-label" title="Tăng tốc tải bằng đa luồng — tiêu thụ nhiều CPU/RAM hơn">
                  <input
                    type="checkbox"
                    checked={accelerate}
                    onChange={(e) => onAccelerateChange?.(e.target.checked)}
                  />
                  <span>Tăng tốc đa luồng</span>
                </label>

                <label className="checkbox-opt-label">
                  <input
                    type="checkbox"
                    checked={embedMetadata}
                    onChange={(e) => onEmbedMetadataChange?.(e.target.checked)}
                  />
                  <span>Nhúng Metadata</span>
                </label>

                <label className="checkbox-opt-label">
                  <input
                    type="checkbox"
                    checked={embedThumbnail}
                    onChange={(e) => onEmbedThumbnailChange?.(e.target.checked)}
                  />
                  <span>Nhúng Thumbnail</span>
                </label>

                <label className="checkbox-opt-label" title="Nhúng phụ đề vào file video (chỉ có nghĩa với tải đơn link)">
                  <input
                    type="checkbox"
                    checked={embedSubs}
                    onChange={(e) => setEmbedSubs(e.target.checked)}
                  />
                  <span>Nhúng Phụ đề</span>
                </label>

                <label className="checkbox-opt-label" title="Tự động cắt bỏ đoạn quảng cáo tài trợ, intro... (chỉ video YouTube, dữ liệu từ SponsorBlock)">
                  <input
                    type="checkbox"
                    checked={sponsorBlock}
                    onChange={(e) => setSponsorBlock(e.target.checked)}
                  />
                  <span>Bỏ đoạn tài trợ (YouTube)</span>
                </label>

                <label className="checkbox-opt-label" title="Tách video thành nhiều tệp theo từng chương (nếu video có chương)">
                  <input
                    type="checkbox"
                    checked={splitChapters}
                    onChange={(e) => setSplitChapters(e.target.checked)}
                  />
                  <span>Tách theo chương</span>
                </label>

                <label className="checkbox-opt-label" title="Tải bằng aria2c (16 kết nối) — cần cài aria2c; nếu chưa cài sẽ tự dùng bộ tải mặc định">
                  <input
                    type="checkbox"
                    checked={useAria2c}
                    onChange={(e) => onUseAria2cChange?.(e.target.checked)}
                  />
                  <span>Tải bằng aria2c</span>
                </label>

                <div className="format-container-picker">
                  <span className="picker-label">Định dạng file:</span>
                  <select
                    className="minimal-select"
                    value={videoContainer}
                    onChange={(e) => onVideoContainerChange?.(e.target.value)}
                    aria-label="Định dạng container video đầu ra"
                  >
                    {VIDEO_CONTAINER_OPTIONS.map((f) => (
                      <option key={f.id} value={f.id}>
                        {f.label} — {f.desc}
                      </option>
                    ))}
                  </select>
                  <span className="tools-setting-hint">Chỉ áp dụng khi tải video; stream âm thanh vẫn là audio.</span>
                </div>
              </div>
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
                  <span className="album-count-text">
                    Album {albumVideoOrder.total > 0 ? 'media' : 'ảnh'} ({albumImages.length} tệp)
                  </span>
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
                          className="minimal-small-btn btn-success"
                          onClick={() => handleDownloadAlbum(false)}
                          disabled={isAlbumBusy}
                          title="Tải ảnh trực tiếp vào một thư mục riêng biệt"
                        >
                          <span>📁 Tải thư mục ({selectedImageCount})</span>
                        </button>
                        <button
                          type="button"
                          className="minimal-small-btn btn-success"
                          onClick={() => handleDownloadAlbum(true)}
                          disabled={isAlbumBusy}
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
                      {safeThumbSrc(img.thumb, img.url) ? (
                        <img
                          src={safeThumbSrc(img.thumb, img.url)}
                          alt=""
                          className="album-img"
                          loading="lazy"
                          decoding="async"
                          onError={(e) => {
                            e.target.style.display = 'none'
                          }}
                        />
                      ) : (
                        <div className="album-img thumb-placeholder">
                          <IconVideo className="w-5 h-5" />
                        </div>
                      )}
                      {img.type === 'video' && (
                        <span className="album-video-badge">
                          <IconVideo className="w-3 h-3" />
                          {albumVideoOrder.total > 1
                            ? `${albumVideoOrder.indexOf.get(img.id)}/${albumVideoOrder.total}`
                            : 'Video'}
                          {img.duration ? ` · ${img.duration}` : ''}
                        </span>
                      )}
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
                        title={img.type === 'video' ? 'Tải video này' : 'Tải ảnh này'}
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

            {/* Danh sách định dạng Video / Âm thanh. Hiện theo danh sách GỐC chứ không
                theo danh sách đã lọc: trước đây chọn tab không có định dạng nào (vd.
                "Chỉ video" với video Facebook) làm biến mất luôn thanh tab, không quay lại được. */}
            {singleMedia.streams?.length > 0 && (
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
                    Âm thanh
                  </button>
                </div>

                {/* Danh sách các stream */}
                <div className="streams-list">
                  {filteredStreams.length === 0 && (
                    <p className="empty-subtle-hint">Không có định dạng nào thuộc nhóm này.</p>
                  )}
                  {filteredStreams.map((stream, sIdx) => {
                    const isAudio = stream.streamType === 'audio'
                    const streamId = stream.formatId || stream.quality || sIdx
                    const isCurrentDownloading = isSourceBusy(streamSourceKey(singleMedia, stream))

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
                              {stream.format || (isAudio ? 'Âm thanh' : 'Video')}
                              {stream.size ? ` • ${stream.size}` : ''}
                              {stream.fps ? ` • ${stream.fps}` : ''}
                              {stream.bitrate ? ` • ${stream.bitrate}` : ''}
                            </span>
                          </div>
                        </div>

                        <button
                          type="button"
                          className="stream-download-btn btn-success"
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
              <div className="profile-top-actions">
                <button
                  type="button"
                  className="minimal-small-btn"
                  onClick={handleToggleSelectAllBatch}
                >
                  {selectedBatchCount === batchMedias.length ? 'Bỏ chọn' : 'Chọn tất cả'}
                </button>
                {selectedBatchCount > 0 && (
                  <button
                    type="button"
                    className="minimal-small-btn btn-success"
                    onClick={handleDownloadBatchZip}
                    disabled={isBatchZipBusy}
                    title="Đóng gói các mục đã chọn thành file ZIP"
                  >
                    <IconZip className="w-3 h-3" />
                    <span>Tải ZIP ({selectedBatchCount})</span>
                  </button>
                )}
                <button
                  type="button"
                  className="minimal-small-btn"
                  onClick={() => {
                    setBatchMedias([])
                    setSelectedBatchIds({})
                  }}
                >
                  Xóa kết quả
                </button>
              </div>
            </div>

            <div className="batch-items-stack">
              {batchMedias.map((m, idx) => (
                <div
                  key={m.id || idx}
                  className={`batch-row-item ${selectedBatchIds[m.id || idx] ? 'is-selected' : ''}`}
                  onClick={() => {
                    const itemId = m.id || idx
                    setSelectedBatchIds((prev) => ({ ...prev, [itemId]: !prev[itemId] }))
                  }}
                >
                  <input
                    type="checkbox"
                    checked={Boolean(selectedBatchIds[m.id || idx])}
                    onChange={() => {
                      const itemId = m.id || idx
                      setSelectedBatchIds((prev) => ({ ...prev, [itemId]: !prev[itemId] }))
                    }}
                    onClick={(e) => e.stopPropagation()}
                    aria-label={`Chọn ${m.title || 'liên kết'}`}
                  />
                  {safeThumbSrc(m.thumbnail, m.highResThumbnail) ? (
                    <img
                      src={safeThumbSrc(m.thumbnail, m.highResThumbnail)}
                      alt=""
                      className="batch-item-thumb"
                      loading="lazy"
                      decoding="async"
                      onError={(e) => {
                        e.target.style.display = 'none'
                      }}
                    />
                  ) : (
                    <div className="batch-item-thumb thumb-placeholder">
                      <IconVideo className="w-3.5 h-3.5" />
                    </div>
                  )}
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
                    className="minimal-small-btn btn-success"
                    onClick={(e) => {
                      e.stopPropagation()
                      handleDownloadBatchItem(m)
                    }}
                    disabled={isSourceBusy(batchItemSourceKey(m))}
                    title={m.streams?.length ? 'Tải định dạng tốt nhất' : 'Tải toàn bộ ảnh/video của bài vào một thư mục'}
                  >
                    <IconDownload className="w-3.5 h-3.5" />
                    <span>{isSourceBusy(batchItemSourceKey(m)) ? 'Đang tải...' : 'Tải về'}</span>
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
