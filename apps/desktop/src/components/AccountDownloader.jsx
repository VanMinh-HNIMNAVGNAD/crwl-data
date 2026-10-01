import { useState, useMemo, useRef } from 'react'
import {
  IconClose,
  IconPaste,
  IconVideo,
  IconImage,
  IconDownload,
  IconZip,
  IconSettings,
} from './Icons'
import { detectPlatform, VIDEO_CONTAINER_OPTIONS, safeThumbSrc } from '../constants'
import {
  crawlProfile,
  cancelExtraction,
  cancelDownload,
  startNativeDownload,
  createTaskId,
  downloadDirectFile,
  downloadAlbumBatch,
  onDownloadProgress,
  askForDownloadDirectory,
  getAlwaysAskDownloadDir,
} from '../services/api'
import { DownloadTaskList } from './DownloadProgressCard'
import { useDownloadTasks, parseSpeed, formatSpeed } from '../hooks/useDownloadTasks'

function safeMediaTitle(item, fallback = 'media') {
  // Một bài đăng có thể chứa nhiều ảnh. Ghép mã bài đăng + số thứ tự trong bài
  // để các ảnh cùng một bài không đè lên nhau và vẫn nhìn ra được chúng đi cùng
  // nhau khi mở thư mục tải về.
  const parts = [item?.title || fallback]
  if (item?.postId) {
    parts.push(item.postId)
  }
  if (item?.totalInPost > 1 && item?.indexInPost) {
    parts.push(`${String(item.indexInPost).padStart(2, '0')}of${item.totalInPost}`)
  }
  parts.push(item?.id ?? '')

  const cleaned = parts
    .filter((part) => part !== '' && part != null)
    .join('_')
    .replace(/[/\0\\:*?"<>|;&$!`\n\r\t]/g, '_')
    .replace(/\.{2,}/g, '_')
    .trim()
    .replace(/^\.+|\.+$/g, '')
  return Array.from(cleaned || fallback).slice(0, 80).join('') || fallback
}

export default function AccountDownloader({ onShowToast, dlOptions = {} }) {
  // Shared download options từ App level
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

  // concurrentFragments: accelerate=false→1 (tiết kiệm CPU), true→4 (đa luồng)
  const concurrentFragments = accelerate ? 4 : 1
  // maxParallelVideos: số video tải song song trong batch
  const maxParallelVideos = accelerate ? 2 : 1

  const [accountInput, setAccountInput] = useState('')
  const [selectedPlatform, setSelectedPlatform] = useState('auto')
  const [mediaTypeFilter, setMediaTypeFilter] = useState('all') // 'all' | 'video' | 'image'
  const [crawlLimit, setCrawlLimit] = useState('20')
  const [rangeFrom, setRangeFrom] = useState('1')
  const [rangeTo, setRangeTo] = useState('20')
  const [isCrawling, setIsCrawling] = useState(false)
  const [statusText, setStatusText] = useState('')
  const [elapsedCrawl, setElapsedCrawl] = useState(0)
  const [isOptionsOpen, setIsOptionsOpen] = useState(false)

  // Kết quả quét tài khoản
  const [profileResult, setProfileResult] = useState(null)
  const [selectedBatchIds, setSelectedBatchIds] = useState({})
  const [isCancellingCrawl, setIsCancellingCrawl] = useState(false)
  const crawlTaskRef = useRef(null)
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
    clearFinishedTasks()
    setStatusText('')
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

  const errorText = (err, fallback) => (typeof err === 'string' ? err : err?.message || fallback)
  const isCancelError = (taskId, msg) =>
    isCancelled(taskId) || msg.includes('cancelled') || msg.includes('hủy') || msg.includes('huỷ') || msg.includes('abort')

  const markTaskCancelled = (taskId) =>
    updateTask(taskId, {
      percent: 0,
      speed: '',
      eta: '',
      status: 'cancelled',
      phase: 'Đã hủy tải xuống',
      message: 'Đã hủy tải xuống và xoá sạch tệp dở dang',
    })

  // Kết thúc một tác vụ bằng lỗi — phân biệt "đã huỷ" với lỗi thật
  const reportTaskError = (taskId, err, fallback) => {
    const errMsg = errorText(err, fallback)
    if (isCancelError(taskId, errMsg)) {
      markTaskCancelled(taskId)
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

  // Hủy một tác vụ tải và xoá sạch tệp dở dang của nó
  const handleCancelDownload = async (taskId) => {
    if (!taskId) return
    markCancelled(taskId)
    try {
      await cancelDownload(taskId)
      markTaskCancelled(taskId)
      onShowToast?.('Đã hủy tải và dọn dẹp tệp dở dang')
    } catch (err) {
      console.error('Cancel download error:', err)
    }
  }

  // Tải 1 tệp trong profile
  const handleDownloadProfileItem = async (item) => {
    const targetDir = await pickTargetDir('Đã hủy tải do chưa chọn thư mục lưu')
    if (targetDir === null) return

    const taskId = createTaskId()
    const isImage = item.type === 'image' || item.type === 'gif'
    startTask(taskId, {
      title: item.title || 'Tệp tải xuống',
      sourceKey: `item:${item.id}`,
      progress: {
        percent: 0,
        speed: '',
        eta: '',
        status: isImage ? 'downloading' : 'preparing',
        phase: isImage ? 'Đang tải ảnh...' : 'Đang chuẩn bị tải...',
        isIndeterminate: isImage,
      },
    })

    let unlisten = null
    try {
      onShowToast?.(`Bắt đầu tải: ${item.title?.slice(0, 30) || (isImage ? 'ảnh' : 'video')}...`)

      try {
        unlisten = await onDownloadProgress((payload) => updateTask(taskId, payload), taskId)
      } catch (e) {
        console.warn('Cannot attach progress listener:', e)
      }

      if (!item.url) throw new Error('Mục này không có liên kết tải hợp lệ')

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
          embedMetadata,
          embedThumbnail,
          useAria2c,
          concurrentFragments,
          videoFormat: videoContainer !== 'auto' ? videoContainer : undefined,
          taskId,
          platform: profileResult?.platform || item.platform,
        })
      }

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
        onShowToast?.(`Đã lưu tại: ${res.file_path || res.file_name || 'tệp'}`)
      }
    } catch (err) {
      reportTaskError(taskId, err, 'Lỗi khi tải tệp')
    } finally {
      if (typeof unlisten === 'function') unlisten()
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

  const isProfileBatchBusy = isSourceBusy('profile-batch')

  // Tải hàng loạt (thư mục riêng hoặc nén ZIP)
  const handleDownloadProfileBatch = async (asZip = false) => {
    const itemsToDownload = profileMediaList.filter((it) => selectedBatchIds[it.id])
    if (itemsToDownload.length === 0) {
      onShowToast?.('Vui lòng chọn ít nhất 1 tệp để tải')
      return
    }

    const accountLabel = (profileResult?.handle || profileResult?.name || 'Profile').replace(/^@/, '')
    let customAlbumName = `${accountLabel}_Media`
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

    const targetDir = await pickTargetDir('Đã hủy do chưa chọn thư mục lưu')
    if (targetDir === null) return

    const videoItems = itemsToDownload.filter((it) => it.type === 'video')
    const imageItems = itemsToDownload.filter((it) => it.type === 'image' || it.type === 'gif')

    const albumName = customAlbumName
    const taskId = createTaskId()
    startTask(taskId, {
      title: `${asZip ? 'Nén ZIP' : 'Tải'}: ${itemsToDownload.length} tệp`,
      sourceKey: 'profile-batch',
      progress: {
        percent: 0,
        speed: '',
        eta: '',
        status: 'preparing',
        phase: `Chuẩn bị tải ${itemsToDownload.length} tệp...`,
      },
    })

    // Một tệp lỗi (link CDN hết hạn, video bị chặn...) không được làm hỏng cả lượt:
    // ghi lại rồi tải tiếp các tệp còn lại, cuối cùng nén/báo những gì đã tải được.
    const failures = []
    let failedCount = 0
    let unlisten = null
    try {
      let albumRes = null
      let albumFolder = null

      // CASE A: Chỉ có ảnh và người dùng chọn ZIP -> Tải ảnh và nén ZIP trực tiếp trong 1 lượt
      if (asZip && videoItems.length === 0) {
        try {
          unlisten = await onDownloadProgress((payload) => updateTask(taskId, payload), taskId)
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

        if (isCancelled(taskId)) return

        if (albumRes && albumRes.success === false) {
          updateTask(taskId, {
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

        updateTask(taskId, {
          percent: 100,
          speed: '',
          eta: '',
          status: 'completed',
          phase: 'Hoàn tất',
          filePath: albumRes?.file_path,
          fileName: albumRes?.file_name,
          message: albumRes?.message,
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
        const moreStepsFollow = asZip || videoItems.length > 0
        try {
          unlisten = await onDownloadProgress((payload) => {
            // Bước ảnh xong/lỗi chưa phải kết cục của cả lượt khi còn video / nén ZIP phía sau
            const stepDone = payload?.status === 'completed' || payload?.status === 'error'
            if (moreStepsFollow && stepDone) {
              updateTask(taskId, { ...payload, status: 'processing', filePath: undefined })
            } else {
              updateTask(taskId, payload)
            }
          }, taskId)
        } catch (e) {
          console.warn('Cannot attach progress listener:', e)
        }

        const imgPayload = imageItems.map((it) => ({
          url: it.url,
          filename: safeMediaTitle(it, 'image'),
          referer: profileResult?.url,
        }))

        try {
          albumRes = await downloadAlbumBatch({
            items: imgPayload,
            albumName,
            destDir: targetDir,
            asZip: false,
            taskId,
            platform: profileResult?.platform || imageItems[0]?.platform,
          })
          if (albumRes?.file_path) albumFolder = albumRes.file_path
          const failedImages = Number(albumRes?.message?.match(/(\d+) tệp thất bại/)?.[1] || 0)
          if (failedImages > 0) {
            failedCount += failedImages
            failures.push(albumRes.message)
          }
        } catch (err) {
          const msg = errorText(err, 'Không tải được ảnh')
          if (isCancelError(taskId, msg) || !moreStepsFollow) throw err
          failedCount += imageItems.length
          failures.push(msg)
        } finally {
          if (typeof unlisten === 'function') {
            unlisten()
            unlisten = null
          }
        }

        if (isCancelled(taskId)) return
      }

      if (asZip && !albumFolder) {
        // Chưa có thư mục album (không có ảnh, hoặc cả lượt ảnh đều lỗi): khởi tạo
        // thư mục để tải video vào đó
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

      if (isCancelled(taskId)) return

      if (asZip && !albumFolder) {
        throw new Error('Không tạo được thư mục album để nén ZIP.')
      }

      // Bước 2: Tải các video (lưu thẳng vào albumFolder nếu có, để gom chung với ảnh)
      let lastVideoRes = null
      if (videoItems.length > 0) {
        const videoDestDir = albumFolder || targetDir
        // Mỗi video thường khởi chạy một process yt-dlp + ffmpeg.
        // maxParallelVideos điều chỉnh theo accelerate để tối ưu CPU.
        let nextVideoIndex = 0
        let completedVideos = 0
        const videoResults = new Array(videoItems.length)
        // Phần trăm của TỪNG video: thanh tiến trình hiển thị trung bình thay vì
        // nhảy qua lại giữa các video đang tải song song.
        const videoPercents = new Array(videoItems.length).fill(0)
        const averageVideoPercent = () =>
          (videoPercents.reduce((sum, p) => sum + p, 0) / videoItems.length) * 0.9
        // Tốc độ (byte/giây) của TỪNG video: ô "Tốc độ tải" là tổng các video đang
        // chạy, không phải tốc độ của riêng video vừa gửi sự kiện.
        const videoSpeeds = new Array(videoItems.length).fill(0)
        const totalVideoSpeed = () => formatSpeed(videoSpeeds.reduce((sum, v) => sum + v, 0))

        const downloadNextVideo = async () => {
          while (!isCancelled(taskId)) {
            const index = nextVideoIndex++
            if (index >= videoItems.length) return

            const item = videoItems[index]
            const vidTaskId = `${taskId}_vid_${index}`
            let videoUnlisten = null

            try {
              videoUnlisten = await onDownloadProgress((payload) => {
                videoPercents[index] = Math.max(videoPercents[index], Number(payload?.percent) || 0)
                // yt-dlp báo tốc độ "Unknown" thoáng qua khi đang tải: giữ số cũ thay
                // vì để tổng tụt xuống. Ghép tệp / xong / lỗi thì video đó không còn tải.
                if (payload?.speed || payload?.status !== 'downloading') {
                  videoSpeeds[index] = parseSpeed(payload?.speed)
                }
                // "completed"/"error" của MỘT video không phải trạng thái của cả lượt:
                // để nguyên sẽ làm thẻ báo "Hoàn tất" và ẩn nút Huỷ giữa chừng.
                const finishedOne = payload?.status === 'completed' || payload?.status === 'error'
                updateTask(taskId, {
                  ...payload,
                  percent: averageVideoPercent(),
                  speed: totalVideoSpeed(),
                  // Các video tải song song cùng ghi vào một thẻ: ETA của từng video
                  // làm ô "còn lại" nhảy qua lại. Để trống cho useDownloadTasks ước
                  // tính theo phần trăm tổng; video đang ghép tệp vẫn là "đang tải".
                  eta: '',
                  status: finishedOne || payload?.status === 'processing' ? 'downloading' : payload?.status,
                  filePath: undefined,
                  isIndeterminate: false,
                  phase: `Đang tải video [${index + 1}/${videoItems.length}]...`,
                })
              }, vidTaskId)

              if (!item.url) throw new Error('Mục này không có liên kết tải hợp lệ')
              const res = await startNativeDownload({
                url: item.url,
                title: safeMediaTitle(item),
                destDir: videoDestDir,
                embedMetadata,
                embedThumbnail,
                useAria2c,
                concurrentFragments,
                videoFormat: videoContainer !== 'auto' ? videoContainer : undefined,
                taskId: vidTaskId,
                platform: profileResult?.platform || item.platform,
              })
              if (!res?.success) throw new Error(res?.message || `Không tải được video thứ ${index + 1}`)
              videoResults[index] = res
            } catch (error) {
              const msg = errorText(error, `Không tải được video thứ ${index + 1}`)
              if (!isCancelError(taskId, msg)) {
                failedCount++
                failures.push(`${item.title || `Video ${index + 1}`}: ${msg}`)
              }
            } finally {
              if (typeof videoUnlisten === 'function') videoUnlisten()
              completedVideos++
              videoPercents[index] = 100
              videoSpeeds[index] = 0
              updateTask(taskId, (prev) => ({
                ...(prev || {}),
                percent: averageVideoPercent(),
                speed: totalVideoSpeed(),
                status: 'downloading',
                phase: `Đã xử lý ${completedVideos}/${videoItems.length} video`,
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

        if (isCancelled(taskId)) return
        lastVideoRes = videoResults.filter(Boolean).pop() || null
      }

      if (isCancelled(taskId)) return

      const okCount = itemsToDownload.length - failedCount
      const failSummary = failedCount > 0 ? `${failedCount} tệp lỗi — ${failures[0]}` : ''

      // Bước 3: Nếu là chế độ ZIP -> nén toàn bộ thư mục (đã chứa cả ảnh và video) thành file ZIP
      if (asZip) {
        updateTask(taskId, {
          percent: 92,
          speed: '',
          eta: '',
          status: 'processing',
          phase: 'Đang nén toàn bộ tệp vào file ZIP...',
        })

        let zipRes
        try {
          zipRes = await downloadAlbumBatch({
            items: [],
            albumName,
            destDir: targetDir,
            albumDir: albumFolder,
            asZip: true,
            taskId,
            platform: profileResult?.platform,
          })
        } catch (err) {
          if (failures.length > 0 && !isCancelError(taskId, errorText(err, ''))) {
            throw new Error(`Không tải được tệp nào để nén ZIP. ${failures[0]}`, { cause: err })
          }
          throw err
        }

        if (isCancelled(taskId)) return

        if (zipRes && zipRes.success === false) {
          updateTask(taskId, {
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

        const summary = failSummary ? `Đã nén ZIP, ${failSummary}` : zipRes?.message
        updateTask(taskId, {
          percent: 100,
          speed: '',
          eta: '',
          status: 'completed',
          phase: 'Hoàn tất',
          filePath: zipRes?.file_path,
          fileName: zipRes?.file_name,
          message: summary,
        })
        onShowToast?.(
          summary ||
            (zipRes?.file_path
              ? `Đã lưu ZIP tại: ${zipRes.file_path}`
              : `Đã nén thành công (${itemsToDownload.length} tệp)!`)
        )
        return
      }

      // Chế độ download bình thường không ZIP
      if (okCount <= 0) {
        throw new Error(failures[0] || 'Không tải được tệp nào')
      }
      const summary = failSummary
        ? `Đã tải ${okCount}/${itemsToDownload.length} tệp, ${failSummary}`
        : `Đã tải thành công (${itemsToDownload.length} tệp)!`
      updateTask(taskId, {
        percent: 100,
        speed: '',
        eta: '',
        status: 'completed',
        phase: 'Hoàn tất',
        filePath: albumFolder || lastVideoRes?.file_path || targetDir,
        fileName: albumRes?.file_name || lastVideoRes?.file_name,
        message: summary,
      })
      onShowToast?.(summary)
    } catch (err) {
      reportTaskError(taskId, err, 'Lỗi khi tải danh sách')
    } finally {
      if (typeof unlisten === 'function') unlisten()
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
          {/* Nút tùy chọn tải */}
          <button
            type="button"
            className={`pane-toggle-btn ${isOptionsOpen ? 'active' : ''}`}
            onClick={() => setIsOptionsOpen(!isOptionsOpen)}
            title="Tùy chọn tải: container, tăng tốc, metadata..."
          >
            <IconSettings className="w-3.5 h-3.5" />
            <span>Tùy chọn</span>
          </button>
        </div>
      </div>

      {/* Options bar (nếu mở) */}
      {isOptionsOpen && (
        <div className="download-options-inline-box" style={{ margin: '0 0 8px 0' }}>
          {/* Container pills — chỉ hiện với video */}
          {mediaTypeFilter !== 'image' && (
            <div style={{ display: 'flex', alignItems: 'center', gap: '4px', flexWrap: 'wrap' }}>
              <span style={{ fontSize: '11px', color: 'var(--text-muted)', minWidth: 'max-content' }}>Định dạng video:</span>
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
          )}
          <label className="checkbox-opt-label" title="Tăng tốc tải bằng đa luồng — tiêu thụ nhiều CPU/RAM hơn">
            <input
              type="checkbox"
              checked={accelerate}
              onChange={(e) => onAccelerateChange?.(e.target.checked)}
            />
            <span>Tăng tốc đa luồng {accelerate ? `(2 video song song, ${concurrentFragments} luồng/video)` : '(1 video, 1 luồng)'}</span>
          </label>
          {mediaTypeFilter !== 'image' && (
            <>
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
              <label className="checkbox-opt-label" title="Tải video bằng aria2c (16 kết nối) — cần cài aria2c">
                <input
                  type="checkbox"
                  checked={useAria2c}
                  onChange={(e) => onUseAria2cChange?.(e.target.checked)}
                />
                <span>Tải bằng aria2c</span>
              </label>
            </>
          )}
        </div>
      )}

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
                  clearFinishedTasks()
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
              <span className="control-label-text" title="Số BÀI ĐĂNG cần quét — mọi ảnh/video bên trong mỗi bài đều được lấy đủ">Số bài đăng:</span>
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
        {/* Tiến trình của mọi lượt tải trong khung này — nằm ngoài khối kết quả để
            đóng kết quả không làm mất nút Huỷ của lượt tải đang chạy */}
        <DownloadTaskList tasks={downloadTasks} onCancel={handleCancelDownload} onDismiss={dismissTask} />

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
                      disabled={isProfileBatchBusy}
                      title="Tải toàn bộ tệp đã chọn trực tiếp vào thư mục riêng"
                    >
                      <span>📁 Tải thư mục ({selectedProfileCount})</span>
                    </button>
                    <button
                      type="button"
                      className="minimal-small-btn"
                      onClick={() => handleDownloadProfileBatch(true)}
                      disabled={isProfileBatchBusy}
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

            {/* Lưới tệp video/ảnh của tài khoản */}
            <div className="profile-grid">
              {profileMediaList.map((item) => {
                const isSelected = Boolean(selectedBatchIds[item.id])
                const isItemDownloading = isSourceBusy(`item:${item.id}`)

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
                      {safeThumbSrc(item.thumb, item.url) ? (
                        <img
                          src={safeThumbSrc(item.thumb, item.url)}
                          alt=""
                          className="profile-item-thumb"
                          loading="lazy"
                          decoding="async"
                          onError={(e) => {
                            e.target.style.display = 'none'
                          }}
                        />
                      ) : (
                        <div className="profile-item-thumb thumb-placeholder">
                          <IconVideo className="w-5 h-5" />
                        </div>
                      )}
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
                        <span className="item-quality-pill">
                          {item.totalInPost > 1
                            ? `Ảnh ${item.indexInPost}/${item.totalInPost}${item.quality ? ` • ${item.quality}` : ''}`
                            : item.quality || 'HD'}
                        </span>
                        <button
                          type="button"
                          className="profile-download-btn"
                          onClick={(e) => {
                            e.stopPropagation()
                            handleDownloadProfileItem(item)
                          }}
                          disabled={isItemDownloading}
                          title={item.type === 'video' ? 'Tải video này' : 'Tải ảnh này'}
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
