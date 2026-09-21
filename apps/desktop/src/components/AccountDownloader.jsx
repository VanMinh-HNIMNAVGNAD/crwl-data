import { useState, useMemo, useRef } from 'react'
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
  cancelExtraction,
  cancelDownload,
  startNativeDownload,
  createTaskId,
  downloadDirectFile,
  downloadAlbumBatch,
  onDownloadProgress,
  selectDownloadDirectory,
  getAlwaysAskDownloadDir,
} from '../services/api'
import DownloadProgressCard from './DownloadProgressCard'

function safeMediaTitle(item, fallback = 'media') {
  const raw = `${item?.title || fallback}_${item?.id || ''}`
  const cleaned = raw
    .replace(/[/\0\\:*?"<>|;&$!`\n\r\t]/g, '_')
    .replace(/\.{2,}/g, '_')
    .trim()
    .replace(/^\.+|\.+$/g, '')
  return Array.from(cleaned || fallback).slice(0, 80).join('') || fallback
}

export default function AccountDownloader({ onShowToast }) {
  const [accountInput, setAccountInput] = useState('')
  const [selectedPlatform, setSelectedPlatform] = useState('auto')
  const [mediaTypeFilter, setMediaTypeFilter] = useState('all') // 'all' | 'video' | 'image'
  const [crawlLimit, setCrawlLimit] = useState('20')
  const [rangeFrom, setRangeFrom] = useState('1')
  const [rangeTo, setRangeTo] = useState('20')
  const [isCrawling, setIsCrawling] = useState(false)
  const [statusText, setStatusText] = useState('')
  const [elapsedCrawl, setElapsedCrawl] = useState(0)

  // Kết quả quét tài khoản
  const [profileResult, setProfileResult] = useState(null)
  const [selectedBatchIds, setSelectedBatchIds] = useState({})
  const [downloadingId, setDownloadingId] = useState(null)
  const [nativeProgress, setNativeProgress] = useState(null)
  const [downloadTaskTitle, setDownloadTaskTitle] = useState('')
  const [isZipDownloading, setIsZipDownloading] = useState(false)
  const [isCancellingCrawl, setIsCancellingCrawl] = useState(false)
  const crawlTaskRef = useRef(null)
  const currentDownloadTaskIdRef = useRef(null)
  const isCancelledRef = useRef(false)

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
    setDownloadTaskTitle('')
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
    setIsCancellingCrawl(false)
    setElapsedCrawl(0)
    setStatusText('Đang kết nối tài khoản...')
    const crawlTaskId = createTaskId()
    crawlTaskRef.current = crawlTaskId

    // Không có cách nào biết trước tổng số bài viết, nên hiển thị thời gian đã
    // trôi qua (số liệu thật) thay vì một thanh phần trăm tự bịa.
    const startedAt = Date.now()
    const elapsedTimer = setInterval(() => {
      setElapsedCrawl(Math.floor((Date.now() - startedAt) / 1000))
    }, 1000)

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
        taskId: crawlTaskId,
      })

      if (resultData && resultData.media && resultData.media.length > 0) {
        setProfileResult(resultData)
        setSelectedBatchIds({})
        onShowToast?.(`Đã quét được ${resultData.media.length} tệp từ @${resultData.name || 'tài khoản'}!`)
      } else {
        onShowToast?.('Không tìm thấy tệp phương tiện công khai nào từ tài khoản này.')
      }
    } catch (err) {
      onShowToast?.(typeof err === 'string' ? err : err?.message || 'Lỗi khi quét tài khoản!')
    } finally {
      clearInterval(elapsedTimer)
      crawlTaskRef.current = null
      setIsCrawling(false)
      setIsCancellingCrawl(false)
      setStatusText('')
    }
  }

  // Dừng thật tiến trình quét đang chạy phía Python
  const handleCancelCrawl = async () => {
    const taskId = crawlTaskRef.current
    if (!taskId) return
    setIsCancellingCrawl(true)
    onShowToast?.('Đang dừng tiến trình quét...')
    await cancelExtraction(taskId)
  }

  // Hủy tải xuống hiện tại và xoá sạch tệp dở dang
  const handleCancelDownload = async () => {
    const taskId = currentDownloadTaskIdRef.current
    isCancelledRef.current = true
    if (!taskId) return
    try {
      await cancelDownload(taskId)
      setNativeProgress({
        id: taskId,
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
    } finally {
      currentDownloadTaskIdRef.current = null
      setDownloadingId(null)
      setIsZipDownloading(false)
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

    isCancelledRef.current = false
    const taskId = createTaskId()
    currentDownloadTaskIdRef.current = taskId
    const isImage = item.type === 'image'
    setDownloadTaskTitle(item.title || 'Tệp tải xuống')
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
      onShowToast?.(`Bắt đầu tải: ${item.title?.slice(0, 30) || (isImage ? 'ảnh' : 'video')}...`)

      try {
        unlisten = await onDownloadProgress((payload) => setNativeProgress(payload), taskId)
      } catch (e) {
        console.warn('Cannot attach progress listener:', e)
      }

      let res
      if (isImage) {
        // Ảnh là liên kết CDN trực tiếp — tải thẳng, không cần cho qua yt-dlp
        res = await downloadDirectFile({
          url: item.url,
          filename: safeMediaTitle(item, 'image'),
          referer: profileResult?.url,
          destDir: targetDir,
          platform: profileResult?.platform || item.platform,
          taskId,
        })
      } else {
        res = await startNativeDownload({
          url: item.url,
          title: safeMediaTitle(item),
          destDir: targetDir,
          taskId,
          platform: profileResult?.platform || item.platform,
        })
      }

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
        onShowToast?.(`Đã lưu tại: ${res.file_path || res.file_name || 'tệp'}`)
      }
    } catch (err) {
      const errMsg = typeof err === 'string' ? err : err?.message || 'Lỗi khi tải tệp'
      if (isCancelledRef.current || errMsg.includes('cancelled') || errMsg.includes('hủy') || errMsg.includes('abort')) {
        setNativeProgress({
          id: taskId,
          percent: 0,
          speed: '',
          eta: '',
          status: 'cancelled',
          phase: 'Đã hủy tải xuống',
          message: 'Đã hủy tải xuống và xoá sạch tệp dở dang',
        })
      } else {
        setNativeProgress((prev) => {
          if (prev?.status === 'cancelled') return prev
          return {
            id: taskId,
            percent: 0,
            speed: '',
            eta: '',
            status: 'error',
            phase: 'Tải thất bại',
            message: errMsg,
          }
        })
        onShowToast?.(errMsg)
      }
    } finally {
      if (typeof unlisten === 'function') unlisten()
      if (currentDownloadTaskIdRef.current === taskId) {
        currentDownloadTaskIdRef.current = null
      }
      setDownloadingId(null)
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

    let customAlbumName = `${profileResult?.username || 'Profile'}_Media`
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

    isCancelledRef.current = false
    const videoItems = itemsToDownload.filter((it) => it.type === 'video')
    const imageItems = itemsToDownload.filter((it) => it.type === 'image' || it.type === 'gif')

    const albumName = customAlbumName
    const taskId = createTaskId()
    currentDownloadTaskIdRef.current = taskId
    setIsZipDownloading(true)
    setDownloadTaskTitle(`${asZip ? 'Nén ZIP' : 'Tải'}: ${itemsToDownload.length} tệp`)
    setNativeProgress({
      id: taskId,
      percent: 0,
      speed: '',
      eta: '',
      status: 'preparing',
      phase: `Chuẩn bị tải ${itemsToDownload.length} tệp...`,
    })

    let unlisten = null
    try {
      let albumRes = null
      let albumFolder = null

      // CASE A: Chỉ có ảnh và người dùng chọn ZIP -> Tải ảnh và nén ZIP trực tiếp trong 1 lượt
      if (asZip && videoItems.length === 0) {
        try {
          unlisten = await onDownloadProgress((payload) => setNativeProgress(payload), taskId)
        } catch (e) {
          console.warn('Cannot attach progress listener:', e)
        }

        const zipPayload = imageItems.map((it) => ({
          url: it.url,
          filename: safeMediaTitle(it),
          referer: profileResult?.url,
        }))

        albumRes = await downloadAlbumBatch({
          items: zipPayload,
          albumName,
          destDir: targetDir,
          asZip: true,
          taskId,
          platform: profileResult?.platform || itemsToDownload[0]?.platform,
        })

        if (typeof unlisten === 'function') {
          unlisten()
          unlisten = null
        }

        if (isCancelledRef.current) return

        if (albumRes && albumRes.success === false) {
          setNativeProgress({
            id: taskId,
            percent: 100,
            speed: '',
            eta: '',
            status: 'error',
            phase: 'Nén ZIP thất bại',
            filePath: albumRes.file_path,
            message: albumRes.message,
          })
          onShowToast?.(albumRes.message || 'Nén ZIP thất bại')
          return
        }

        setNativeProgress({
          id: taskId,
          percent: 100,
          speed: '',
          eta: '',
          status: 'completed',
          phase: 'Hoàn tất',
          filePath: albumRes?.file_path,
          fileName: albumRes?.file_name,
        })
        onShowToast?.(
          albumRes?.message ||
            (albumRes?.file_path
              ? `Đã lưu ZIP tại: ${albumRes.file_path}`
              : `Đã tải thành công (${itemsToDownload.length} tệp)!`)
        )
        return
      }

      // CASE B: Có video (chỉ video, hoặc cả ảnh + video) hoặc không chọn ZIP
      // Bước 1: Tải toàn bộ ảnh vào thư mục album (chưa nén ZIP)
      if (imageItems.length > 0) {
        try {
          unlisten = await onDownloadProgress((payload) => setNativeProgress(payload), taskId)
        } catch (e) {
          console.warn('Cannot attach progress listener:', e)
        }

        const imgPayload = imageItems.map((it) => ({
          url: it.url,
          filename: `${it.title || 'media'}_${it.id}`,
          referer: profileResult?.url,
        }))

        albumRes = await downloadAlbumBatch({
          items: imgPayload,
          albumName,
          destDir: targetDir,
          asZip: false,
          taskId,
          platform: profileResult?.platform || imageItems[0]?.platform,
        })

        if (typeof unlisten === 'function') {
          unlisten()
          unlisten = null
        }

        if (isCancelledRef.current) return

        if (albumRes?.file_path) {
          albumFolder = albumRes.file_path
        }
      } else if (asZip) {
        // Nếu không có ảnh nhưng cần nén ZIP: khởi tạo thư mục album để tải video vào đó
        const prepRes = await downloadAlbumBatch({
          items: [],
          albumName,
          destDir: targetDir,
          asZip: false,
          taskId,
          platform: profileResult?.platform,
        })
        if (prepRes?.file_path) {
          albumFolder = prepRes.file_path
        }
      }

      if (isCancelledRef.current) return

      if (asZip && !albumFolder) {
        throw new Error('Không tạo được thư mục album để nén ZIP.')
      }

      // Bước 2: Tải các video (lưu thẳng vào albumFolder nếu có, để gom chung với ảnh)
      let lastVideoRes = null
      if (videoItems.length > 0) {
        const videoDestDir = albumFolder || targetDir
        const maxParallelVideos = 3
        let nextVideoIndex = 0
        let completedVideos = 0
        const videoResults = new Array(videoItems.length)
        const videoErrors = []

        const downloadNextVideo = async () => {
          while (!isCancelledRef.current) {
            const index = nextVideoIndex++
            if (index >= videoItems.length) return

            const item = videoItems[index]
            const vidTaskId = `${taskId}_vid_${index}`
            let videoUnlisten = null

            try {
              videoUnlisten = await onDownloadProgress((payload) => {
                setNativeProgress({
                  ...payload,
                  id: vidTaskId,
                  phase: `Đang tải video [${index + 1}/${videoItems.length}]...`,
                })
              }, vidTaskId)

              videoResults[index] = await startNativeDownload({
                url: item.url,
                title: safeMediaTitle(item),
                destDir: videoDestDir,
                taskId: vidTaskId,
                platform: profileResult?.platform || item.platform,
              })
            } catch (error) {
              videoErrors.push(error)
            } finally {
              if (typeof videoUnlisten === 'function') videoUnlisten()
              completedVideos++
              setNativeProgress((prev) => ({
                ...(prev || {}),
                id: taskId,
                percent: Math.round((completedVideos / videoItems.length) * 90),
                status: 'downloading',
                phase: `Đã tải ${completedVideos}/${videoItems.length} video`,
              }))
            }
          }
        }

        await Promise.all(
          Array.from(
            { length: Math.min(maxParallelVideos, videoItems.length) },
            () => downloadNextVideo()
          )
        )

        if (isCancelledRef.current) return
        const failedVideo = videoResults.find((result) => result && !result.success)
        if (failedVideo || videoErrors.length > 0) {
          throw new Error(
            failedVideo?.message ||
              videoErrors[0]?.message ||
              'Một hoặc nhiều video tải thất bại'
          )
        }
        lastVideoRes = videoResults[videoResults.length - 1]
      }

      if (isCancelledRef.current) return

      // Bước 3: Nếu là chế độ ZIP -> nén toàn bộ thư mục (đã chứa cả ảnh và video) thành file ZIP
      if (asZip) {
        setNativeProgress({
          id: taskId,
          percent: 92,
          speed: '',
          eta: '',
          status: 'processing',
          phase: 'Đang nén toàn bộ tệp vào file ZIP...',
        })

        const zipRes = await downloadAlbumBatch({
          items: [],
          albumName,
          destDir: targetDir,
          albumDir: albumFolder,
          asZip: true,
          taskId,
          platform: profileResult?.platform,
        })

        if (isCancelledRef.current) return

        if (zipRes && zipRes.success === false) {
          setNativeProgress({
            id: taskId,
            percent: 100,
            speed: '',
            eta: '',
            status: 'error',
            phase: 'Nén ZIP thất bại',
            filePath: zipRes.file_path,
            message: zipRes.message,
          })
          onShowToast?.(zipRes.message || 'Nén ZIP thất bại')
          return
        }

        setNativeProgress({
          id: taskId,
          percent: 100,
          speed: '',
          eta: '',
          status: 'completed',
          phase: 'Hoàn tất',
          filePath: zipRes?.file_path,
          fileName: zipRes?.file_name,
        })
        onShowToast?.(
          zipRes?.message ||
            (zipRes?.file_path
              ? `Đã lưu ZIP tại: ${zipRes.file_path}`
              : `Đã nén thành công (${itemsToDownload.length} tệp)!`)
        )
      } else {
        // Chế độ download bình thường không ZIP
        setNativeProgress({
          id: taskId,
          percent: 100,
          speed: '',
          eta: '',
          status: 'completed',
          phase: 'Hoàn tất',
          filePath: albumFolder || lastVideoRes?.file_path || targetDir,
          fileName: albumRes?.file_name || lastVideoRes?.file_name,
        })
        onShowToast?.(`Đã tải thành công (${itemsToDownload.length} tệp)!`)
      }
    } catch (err) {
      const errMsg = typeof err === 'string' ? err : err?.message || 'Lỗi khi tải danh sách'
      if (isCancelledRef.current || errMsg.includes('cancelled') || errMsg.includes('hủy') || errMsg.includes('abort')) {
        setNativeProgress({
          id: taskId,
          percent: 0,
          speed: '',
          eta: '',
          status: 'cancelled',
          phase: 'Đã hủy tải xuống',
          message: 'Đã hủy tải xuống và xoá sạch tệp dở dang',
        })
      } else {
        setNativeProgress((prev) => {
          if (prev?.status === 'cancelled') return prev
          return {
            id: taskId,
            percent: 0,
            speed: '',
            eta: '',
            status: 'error',
            phase: 'Tải thất bại',
            message: errMsg,
          }
        })
        onShowToast?.(errMsg)
      }
    } finally {
      if (typeof unlisten === 'function') unlisten()
      if (currentDownloadTaskIdRef.current === taskId) {
        currentDownloadTaskIdRef.current = null
      }
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
                  setDownloadTaskTitle('')
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
                  <span>{isCancellingCrawl ? 'Đang dừng...' : `Đang quét... ${elapsedCrawl}s`}</span>
                </>
              ) : (
                <>
                  <IconVideo className="w-3.5 h-3.5" />
                  <span>Quét tài khoản</span>
                </>
              )}
            </button>
            {isCrawling && !isCancellingCrawl && (
              <button
                type="button"
                className="pane-cancel-btn"
                onClick={handleCancelCrawl}
                title="Dừng tiến trình quét đang chạy"
              >
                ✕ Hủy
              </button>
            )}
          </div>

          {statusText && (
            <div className="status-progress-line">
              <span className="validation-hint">{statusText}</span>
              {isCrawling && (
                <div className="progress-track-small">
                  <div className="progress-fill is-indeterminate" />
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
                    src={profileResult.avatar}
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
                onCancel={handleCancelDownload}
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
                        src={item.thumb || item.url}
                        alt=""
                        className="profile-item-thumb"
                        loading="lazy"
                        decoding="async"
                        onError={(e) => {
                          e.target.style.display = 'none'
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
