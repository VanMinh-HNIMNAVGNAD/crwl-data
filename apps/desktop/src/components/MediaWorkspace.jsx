import { useState, useMemo, useEffect } from 'react'
import {
  IconDownload,
  IconVideo,
  IconAudio,
  IconImage,
  IconZip,
  IconExternalLink,
  IconCopy,
  IconScissors,
  IconSubtitle,
} from './Icons'
import {
  isTauri,
  startNativeDownload,
  downloadThumbnail,
  downloadSubtitle,
  downloadZipArchive,
  downloadDirectFile,
  selectDownloadDirectory,
  getDefaultDownloadDirectory,
  openDownloadFolder,
  onDownloadProgress,
  buildProxyMediaUrl,
  buildProxyImageUrl,
} from '../services/api'

export default function MediaWorkspace({
  activeTab,
  onTabChange,
  singleMedia,
  batchMedias,
  profileResult,
  onShowToast,
}) {
  // Bộ lọc format trong khu vực xem chi tiết
  const [streamTab, setStreamTab] = useState('all') // 'all' | 'full' | 'mute' | 'audio' | 'subtitles'
  const [selectedImages, setSelectedImages] = useState({})
  const [selectedBatchIds, setSelectedBatchIds] = useState({})
  const [downloadingId, setDownloadingId] = useState(null)
  const [isZipDownloading, setIsZipDownloading] = useState(false)
  const [zipProgressText, setZipProgressText] = useState('')

  // Desktop Native Download States
  const [downloadFolder, setDownloadFolder] = useState('')
  const [lastDownloadedPath, setLastDownloadedPath] = useState(null)
  const [nativeProgress, setNativeProgress] = useState(null)

  useEffect(() => {
    if (isTauri()) {
      getDefaultDownloadDirectory().then((dir) => {
        if (dir) setDownloadFolder(dir)
      })
    }
  }, [])

  const handleChangeFolder = async () => {
    const dir = await selectDownloadDirectory()
    if (dir) {
      setDownloadFolder(dir)
      onShowToast?.(`Đã đổi thư mục lưu: ${dir}`)
    }
  }

  const handleOpenFolder = async () => {
    const target = lastDownloadedPath || downloadFolder
    if (target) {
      await openDownloadFolder(target)
    }
  }

  // Trimmer tool state (cắt clip thời gian)
  const [isTrimmerOpen, setIsTrimmerOpen] = useState(false)
  const [trimStart, setTrimStart] = useState('')
  const [trimEnd, setTrimEnd] = useState('')

  // Đếm số lượng tệp album đã chọn
  const albumImages = useMemo(() => singleMedia?.images || [], [singleMedia])
  const selectedImageCount = useMemo(
    () => Object.values(selectedImages).filter(Boolean).length,
    [selectedImages]
  )

  // Danh sách phương tiện profile quét được
  const profileMediaList = useMemo(() => profileResult?.media || [], [profileResult])
  const selectedProfileCount = useMemo(
    () => Object.values(selectedBatchIds).filter(Boolean).length,
    [selectedBatchIds]
  )

  // Copy link
  const handleCopy = (text) => {
    if (!text) return
    navigator.clipboard.writeText(text)
    onShowToast?.('Đã sao chép liên kết vào bộ nhớ tạm')
  }

  // Tải stream video/audio đơn
  const handleDownloadStream = async (stream) => {
    if (!singleMedia) return
    const streamId = stream.formatId || 'stream'
    setDownloadingId(streamId)
    try {
      const isAudioOnly = stream.streamType === 'audio'
      const isMute = stream.streamType === 'mute'

      onShowToast?.(`Bắt đầu tải: ${stream.quality || singleMedia.title}`)
      let unlisten = null
      try {
        unlisten = await onDownloadProgress((payload) => {
          setNativeProgress(payload)
        })
      } catch (e) {
        console.warn('Cannot attach progress listener:', e)
      }

      const res = await startNativeDownload({
        url: singleMedia.originalUrl,
        formatId: stream.formatId,
        isAudio: isAudioOnly,
        isMute: isMute,
        startTime: trimStart || undefined,
        endTime: trimEnd || undefined,
        title: singleMedia.title,
        destDir: downloadFolder || undefined,
      })

      if (typeof unlisten === 'function') unlisten()

      if (res && res.file_path) {
        setLastDownloadedPath(res.file_path)
        onShowToast?.(`Tải xong! Đã lưu: ${res.file_name}`)
      }
    } catch (err) {
      onShowToast?.(err.message || 'Lỗi khi bắt đầu tải stream')
    } finally {
      setTimeout(() => {
        setDownloadingId(null)
        setNativeProgress(null)
      }, 2000)
    }
  }

  // Tải thumbnail HD của bài viết
  const handleDownloadThumbnail = async () => {
    if (!singleMedia) return
    try {
      onShowToast?.('Đang tải ảnh bìa (thumbnail)...')
      const res = await downloadThumbnail({
        url: singleMedia.originalUrl,
        title: singleMedia.title,
      })
      if (res?.file_name) {
        onShowToast?.(`Đã lưu thumbnail: ${res.file_name}`)
      }
    } catch (err) {
      onShowToast?.(err.message || 'Lỗi khi tải thumbnail')
    }
  }

  // Tải ảnh đơn lẻ trong album — ưu tiên native download trong Tauri (chống 403), fallback browser
  const handleDownloadImage = async (img) => {
    try {
      if (isTauri()) {
        onShowToast?.(`Đang tải ảnh: ${img.title || 'photo'}...`)
        const res = await downloadDirectFile({
          url: img.url,
          filename: `${img.title || 'photo'}.${img.ext || 'jpg'}`,
          referer: singleMedia?.originalUrl,
          destDir: downloadFolder || undefined,
        })
        if (res?.file_name) {
          onShowToast?.(`Đã lưu ảnh: ${res.file_name}`)
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
      onShowToast?.('Đã bắt đầu tải tệp ảnh')
    } catch (err) {
      onShowToast?.(err.message || 'Lỗi khi tải ảnh')
    }
  }

  // Chọn tất cả ảnh trong album
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

  // Tải ZIP các ảnh đã chọn trong album
  const handleDownloadAlbumZip = async () => {
    const itemsToDownload = albumImages.filter((img) => selectedImages[img.id])
    if (itemsToDownload.length === 0) {
      onShowToast?.('Vui lòng chọn ít nhất 1 ảnh để tải ZIP')
      return
    }

    setIsZipDownloading(true)
    setZipProgressText('Đang nén file ZIP...')
    try {
      const zipPayload = itemsToDownload.map((img) => ({
        url: img.url,
        filename: `${img.title || 'image'}.${img.ext || 'jpg'}`,
        referer: singleMedia?.originalUrl,
      }))
      await downloadZipArchive(zipPayload, `${singleMedia?.title || 'Album'}_Media`)
      onShowToast?.(`Đã tải thành công file ZIP (${itemsToDownload.length} tệp)!`)
    } catch (err) {
      onShowToast?.(err.message || 'Lỗi khi tạo file ZIP')
    } finally {
      setIsZipDownloading(false)
      setZipProgressText('')
    }
  }

  // Chọn tất cả tệp trong danh sách profile crawl
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

  // Tải ZIP các tệp đã chọn từ Profile Crawl
  const handleDownloadProfileZip = async () => {
    const itemsToDownload = profileMediaList.filter((it) => selectedBatchIds[it.id])
    if (itemsToDownload.length === 0) {
      onShowToast?.('Vui lòng chọn ít nhất 1 tệp để tải ZIP')
      return
    }

    setIsZipDownloading(true)
    setZipProgressText(`Đang nén ${itemsToDownload.length} tệp vào file ZIP...`)
    try {
      const zipPayload = itemsToDownload.map((it) => ({
        url: it.url,
        filename: `${it.title || 'media'}_${it.id}.mp4`,
        referer: profileResult?.url,
      }))
      await downloadZipArchive(zipPayload, `Profile_${profileResult?.name || 'Media'}`)
      onShowToast?.(`Đã tải thành công file ZIP (${itemsToDownload.length} tệp)!`)
    } catch (err) {
      onShowToast?.(err.message || 'Lỗi khi tạo file ZIP')
    } finally {
      setIsZipDownloading(false)
      setZipProgressText('')
    }
  }

  // Tải từng mục trong danh sách profile
  const handleDownloadProfileItem = async (item) => {
    try {
      onShowToast?.(`Bắt đầu tải: ${item.title?.slice(0, 30)}...`)
      const res = await startNativeDownload({
        url: item.url,
        title: item.title,
      })
      if (res?.file_name) {
        onShowToast?.(`Tải xong: ${res.file_name}`)
      }
    } catch (err) {
      onShowToast?.(err.message || 'Lỗi khi tải video')
    }
  }

  // Lọc streams hiển thị theo tab
  const filteredStreams = useMemo(() => {
    if (!singleMedia?.streams) return []
    if (streamTab === 'all') return singleMedia.streams
    if (streamTab === 'full') return singleMedia.streams.filter((s) => s.streamType === 'full')
    if (streamTab === 'mute') return singleMedia.streams.filter((s) => s.streamType === 'mute')
    if (streamTab === 'audio') return singleMedia.streams.filter((s) => s.streamType === 'audio')
    return singleMedia.streams
  }, [singleMedia, streamTab])

  return (
    <div className="media-workspace-card">
      {/* Header Tabs của khu vực kết quả */}
      <div className="workspace-header-tabs">
        <div className="tab-buttons">
          <button
            type="button"
            className={`tab-btn ${activeTab === 'link' ? 'active' : ''}`}
            onClick={() => onTabChange?.('link')}
          >
            <span>Kết quả liên kết</span>
            {singleMedia && <span className="tab-pill-badge">{singleMedia.type === 'album' ? `${albumImages.length} ảnh` : '1 Video'}</span>}
          </button>
          <button
            type="button"
            className={`tab-btn ${activeTab === 'account' ? 'active' : ''}`}
            onClick={() => onTabChange?.('account')}
          >
            <span>Kết quả quét tài khoản</span>
            {profileMediaList.length > 0 && <span className="tab-pill-badge">{profileMediaList.length} tệp</span>}
          </button>
          {batchMedias && batchMedias.length > 0 && (
            <button
              type="button"
              className={`tab-btn ${activeTab === 'batch' ? 'active' : ''}`}
              onClick={() => onTabChange?.('batch')}
            >
              <span>Nhiều link đã giải mã</span>
              <span className="tab-pill-badge">{batchMedias.length} link</span>
            </button>
          )}
        </div>

        {/* Global Loading / Status */}
        {isZipDownloading && (
          <div className="zip-download-badge">
            <span className="clean-spinner" />
            <span>{zipProgressText}</span>
          </div>
        )}
      </div>

      {/* Nội dung tương ứng theo tab */}
      <div className="workspace-body-content">
        {activeTab === 'link' && singleMedia && (
          <div className="single-media-layout">
            {/* Cột trái: Xem trước Thumbnail / Video & Thông tin Metadata */}
            <div className="media-preview-column">
              <div className="media-thumb-box">
                <img
                  src={buildProxyImageUrl(singleMedia.thumbnail || singleMedia.highResThumbnail)}
                  alt={singleMedia.title}
                  className="preview-img"
                  onError={(e) => {
                    e.target.src = singleMedia.thumbnail || singleMedia.highResThumbnail
                  }}
                />
                <div className="thumb-badges-overlay">
                  {singleMedia.duration && <span className="pill-dark">{singleMedia.duration}</span>}
                  {singleMedia.platform && <span className="pill-platform">{singleMedia.platform.toUpperCase()}</span>}
                </div>
              </div>

              <div className="media-meta-details">
                <h3 className="media-title" title={singleMedia.title}>
                  {singleMedia.title}
                </h3>
                <div className="media-meta-sub">
                  <span>Tác giả: <strong>{singleMedia.author}</strong></span>
                  {singleMedia.views && <span>• {singleMedia.views}</span>}
                  {singleMedia.likes && <span>• {singleMedia.likes}</span>}
                </div>

                <div className="media-tools-inline">
                  <button
                    type="button"
                    className="tool-btn-small"
                    onClick={() => handleCopy(singleMedia.originalUrl)}
                    title="Sao chép liên kết gốc"
                  >
                    <IconCopy className="w-3.5 h-3.5" /> Sao chép link
                  </button>
                  <a
                    href={singleMedia.originalUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="tool-btn-small"
                    title="Mở liên kết gốc trong trình duyệt"
                  >
                    <IconExternalLink className="w-3.5 h-3.5" /> Mở trang gốc
                  </a>
                  <button
                    type="button"
                    className="tool-btn-small"
                    onClick={handleDownloadThumbnail}
                    title="Tải ảnh bìa / Thumbnail chất lượng cao"
                  >
                    <IconImage className="w-3.5 h-3.5" /> Tải Thumbnail
                  </button>
                  <button
                    type="button"
                    className={`tool-btn-small ${isTrimmerOpen ? 'active' : ''}`}
                    onClick={() => setIsTrimmerOpen(!isTrimmerOpen)}
                    title="Cắt khoảng thời gian video (Start - End)"
                  >
                    <IconScissors className="w-3.5 h-3.5" /> Cắt đoạn clip
                  </button>
                </div>

                {/* Box nhập thời gian cắt clip (nếu mở) */}
                {isTrimmerOpen && (
                  <div className="trimmer-box">
                    <span className="trimmer-label">Cắt clip từ:</span>
                    <input
                      type="text"
                      placeholder="00:00"
                      className="clean-input-tiny"
                      value={trimStart}
                      onChange={(e) => setTrimStart(e.target.value)}
                    />
                    <span className="trimmer-label">Đến:</span>
                    <input
                      type="text"
                      placeholder="01:30"
                      className="clean-input-tiny"
                      value={trimEnd}
                      onChange={(e) => setTrimEnd(e.target.value)}
                    />
                    {(trimStart || trimEnd) && (
                      <button
                        type="button"
                        className="btn-tiny"
                        onClick={() => { setTrimStart(''); setTrimEnd('') }}
                      >
                        Bỏ cắt
                      </button>
                    )}
                  </div>
                )}
              </div>
            </div>

            {/* Cột phải: Bảng chọn độ phân giải / Album ảnh */}
            <div className="media-streams-column">
              {singleMedia.type === 'album' && albumImages.length > 0 ? (
                <div className="album-workspace-view">
                  <div className="album-actions-bar">
                    <div className="select-all-group">
                      <input
                        type="checkbox"
                        id="select-all-images"
                        checked={selectedImageCount === albumImages.length && albumImages.length > 0}
                        onChange={handleToggleSelectAllImages}
                      />
                      <label htmlFor="select-all-images">
                        Chọn tất cả ({selectedImageCount}/{albumImages.length} ảnh)
                      </label>
                    </div>

                    <button
                      type="button"
                      className="zip-action-btn"
                      disabled={selectedImageCount === 0 || isZipDownloading}
                      onClick={handleDownloadAlbumZip}
                    >
                      <IconZip className="w-4 h-4" />
                      <span>Tải ZIP đã chọn ({selectedImageCount} ảnh)</span>
                    </button>
                  </div>

                  {/* Lưới hình ảnh Album */}
                  <div className="album-grid-scroll">
                    {albumImages.map((img) => (
                      <div
                        key={img.id}
                        className={`album-item-card ${selectedImages[img.id] ? 'is-selected' : ''}`}
                      >
                        <input
                          type="checkbox"
                          className="item-checkbox"
                          checked={Boolean(selectedImages[img.id])}
                          onChange={(e) => {
                            setSelectedImages((prev) => ({
                              ...prev,
                              [img.id]: e.target.checked,
                            }))
                          }}
                        />
                        <img
                          src={buildProxyImageUrl(img.thumb || img.url)}
                          alt={img.title}
                          className="album-card-img"
                        />
                        <div className="album-card-footer">
                          <span className="album-card-res">{img.resolution || 'HD'}</span>
                          <button
                            type="button"
                            className="download-icon-btn"
                            onClick={() => handleDownloadImage(img)}
                            title="Tải ảnh này về máy"
                          >
                            <IconDownload className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <div className="streams-workspace-view">
                  {/* Tabs phân loại stream */}
                  <div className="stream-filter-pills">
                    <button
                      type="button"
                      className={`pill ${streamTab === 'all' ? 'active' : ''}`}
                      onClick={() => setStreamTab('all')}
                    >
                      Tất cả ({singleMedia.streams?.length || 0})
                    </button>
                    <button
                      type="button"
                      className={`pill ${streamTab === 'full' ? 'active' : ''}`}
                      onClick={() => setStreamTab('full')}
                    >
                      <IconVideo className="w-3.5 h-3.5" /> Video + Tiếng
                    </button>
                    <button
                      type="button"
                      className={`pill ${streamTab === 'audio' ? 'active' : ''}`}
                      onClick={() => setStreamTab('audio')}
                    >
                      <IconAudio className="w-3.5 h-3.5" /> MP3 / Âm thanh
                    </button>
                    <button
                      type="button"
                      className={`pill ${streamTab === 'mute' ? 'active' : ''}`}
                      onClick={() => setStreamTab('mute')}
                    >
                      Chỉ hình (Mute)
                    </button>
                  </div>

                  {/* Thanh điều khiển Desktop Native: Thư mục lưu & Tiến trình tải */}
                  {isTauri() && (
                    <div className="desktop-download-toolbar">
                      <div className="dest-folder-info">
                        <span className="folder-label">Lưu tại:</span>
                        <span className="folder-path" title={downloadFolder || '~/Downloads'}>
                          {downloadFolder || '~/Downloads'}
                        </span>
                        <button
                          type="button"
                          className="btn-tiny-folder"
                          onClick={handleChangeFolder}
                          title="Chọn thư mục khác trên máy"
                        >
                          Đổi thư mục
                        </button>
                      </div>

                      {lastDownloadedPath && (
                        <button
                          type="button"
                          className="btn-open-folder"
                          onClick={handleOpenFolder}
                          title="Mở tệp vừa tải trong File Manager Linux"
                        >
                          📂 Mở thư mục
                        </button>
                      )}
                    </div>
                  )}

                  {/* Thanh tiến trình tải thời gian thực */}
                  {nativeProgress && (
                    <div className="desktop-live-progress-box">
                      <div className="progress-info-row">
                        <span className="progress-status">
                          Đang tải: <strong>{nativeProgress.percent.toFixed(1)}%</strong>
                        </span>
                        <span className="progress-metrics">
                          {nativeProgress.speed && <span>Tốc độ: {nativeProgress.speed}</span>}
                          {nativeProgress.eta && <span>• Còn: {nativeProgress.eta}</span>}
                        </span>
                      </div>
                      <div className="progress-track">
                        <div
                          className="progress-fill"
                          style={{ width: `${Math.min(Math.max(nativeProgress.percent, 0), 100)}%` }}
                        />
                      </div>
                    </div>
                  )}

                  {/* Danh sách các dòng stream chất lượng */}
                  <div className="streams-list-scroll">
                    {filteredStreams.map((stream, idx) => (
                      <div key={stream.formatId || idx} className="stream-row-card">
                        <div className="stream-info-main">
                          <span className="stream-quality-text">{stream.quality}</span>
                          <div className="stream-specs">
                            <span className="badge-spec format">{stream.format}</span>
                            <span className="badge-spec size">{stream.size || 'Tự động'}</span>
                            {stream.bitrate && <span className="badge-spec bitrate">{stream.bitrate}</span>}
                          </div>
                        </div>

                        <button
                          type="button"
                          className="stream-download-btn"
                          disabled={downloadingId === stream.formatId}
                          onClick={() => handleDownloadStream(stream)}
                        >
                          {downloadingId === stream.formatId ? (
                            <>
                              <span className="clean-spinner" />
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
                    ))}

                    {/* Phụ đề nếu có */}
                    {singleMedia.subtitles && singleMedia.subtitles.length > 0 && (
                      <div className="subtitles-section">
                        <div className="subtitles-title">
                          <IconSubtitle className="w-4 h-4 text-sky-400" />
                          <span>Phụ đề có sẵn:</span>
                        </div>
                        <div className="subtitles-buttons-row">
                          {singleMedia.subtitles.map((sub) => (
                            <button
                              key={sub.lang}
                              type="button"
                              className="subtitle-btn"
                              onClick={async () => {
                                try {
                                  onShowToast?.(`Đang tải phụ đề ${sub.name || sub.lang}...`)
                                  const res = await downloadSubtitle({
                                    url: singleMedia.originalUrl,
                                    lang: sub.lang,
                                    format: sub.ext || 'vtt',
                                    title: singleMedia.title,
                                  })
                                  if (res?.file_name) {
                                    onShowToast?.(`Đã lưu phụ đề: ${res.file_name}`)
                                  }
                                } catch (err) {
                                  onShowToast?.(err.message || 'Lỗi khi tải phụ đề')
                                }
                              }}
                            >
                              <IconDownload className="w-3 h-3" />
                              <span>{sub.name || sub.lang}</span>
                            </button>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Tab quét tài khoản */}
        {activeTab === 'account' && profileResult && (
          <div className="profile-crawl-layout">
            <div className="profile-actions-bar">
              <div className="select-all-group">
                <input
                  type="checkbox"
                  id="select-all-profile"
                  checked={selectedProfileCount === profileMediaList.length && profileMediaList.length > 0}
                  onChange={handleToggleSelectAllProfile}
                />
                <label htmlFor="select-all-profile">
                  Chọn tất cả ({selectedProfileCount}/{profileMediaList.length} tệp)
                </label>
              </div>

              <div className="account-meta-stats">
                <span>Tài khoản: <strong>@{profileResult.name}</strong></span>
                <span>• {profileResult.stats}</span>
              </div>

              <button
                type="button"
                className="zip-action-btn"
                disabled={selectedProfileCount === 0 || isZipDownloading}
                onClick={handleDownloadProfileZip}
              >
                <IconZip className="w-4 h-4" />
                <span>Tải ZIP đã chọn ({selectedProfileCount} tệp)</span>
              </button>
            </div>

            {/* Lưới thẻ video/ảnh quét được */}
            <div className="profile-media-grid-scroll">
              {profileMediaList.map((item) => (
                <div
                  key={item.id}
                  className={`profile-item-card ${selectedBatchIds[item.id] ? 'is-selected' : ''}`}
                >
                  <input
                    type="checkbox"
                    className="item-checkbox"
                    checked={Boolean(selectedBatchIds[item.id])}
                    onChange={(e) => {
                      setSelectedBatchIds((prev) => ({
                        ...prev,
                        [item.id]: e.target.checked,
                      }))
                    }}
                  />
                  <div className="thumb-container">
                    <img
                      src={buildProxyImageUrl(item.thumb || item.url)}
                      alt={item.title}
                      className="item-thumb-img"
                    />
                    {item.duration && <span className="duration-pill">{item.duration}</span>}
                  </div>
                  <div className="item-meta">
                    <h4 className="item-title" title={item.title}>
                      {item.title}
                    </h4>
                    <div className="item-bottom-row">
                      <span className="item-quality">{item.quality || 'HD'}</span>
                      <button
                        type="button"
                        className="download-icon-btn"
                        onClick={() => handleDownloadProfileItem(item)}
                        title="Tải video này"
                      >
                        <IconDownload className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Tab nhiều link đã bóc tách */}
        {activeTab === 'batch' && batchMedias && batchMedias.length > 0 && (
          <div className="profile-crawl-layout">
            <div className="profile-actions-bar">
              <span className="batch-header-title">
                Danh sách {batchMedias.length} liên kết đã bóc tách
              </span>
            </div>
            <div className="profile-media-grid-scroll">
              {batchMedias.map((m, idx) => (
                <div key={m.id || idx} className="profile-item-card">
                  <div className="thumb-container">
                    <img
                      src={buildProxyImageUrl(m.thumbnail || m.highResThumbnail)}
                      alt={m.title}
                      className="item-thumb-img"
                    />
                    {m.duration && <span className="duration-pill">{m.duration}</span>}
                  </div>
                  <div className="item-meta">
                    <h4 className="item-title" title={m.title}>
                      {m.title}
                    </h4>
                    <div className="item-bottom-row">
                      <span className="item-quality">{m.platform?.toUpperCase()}</span>
                      <button
                        type="button"
                        className="download-icon-btn"
                        onClick={() => {
                          const topStream = m.streams?.[0]
                          if (topStream) handleDownloadStream(topStream)
                        }}
                        title="Tải video chất lượng tốt nhất"
                      >
                        <IconDownload className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Trạng thái trống khi chưa có dữ liệu */}
        {((activeTab === 'link' && !singleMedia) ||
          (activeTab === 'account' && !profileResult) ||
          (activeTab === 'batch' && (!batchMedias || batchMedias.length === 0))) && (
          <div className="workspace-empty-placeholder">
            <div className="empty-icon-wrap">
              <IconVideo className="w-7 h-7 text-sky-400" />
            </div>
            <h3 className="empty-title">Khu vực xem trước &amp; tải tệp phương tiện</h3>
            <p className="empty-desc">
              Dán liên kết bài viết ở <strong>Cột 1</strong> hoặc nhập tài khoản ở <strong>Cột 2</strong> để bắt đầu tải video, tách âm thanh MP3, tải ảnh album hoặc nén ZIP.
            </p>

            <div className="supported-platforms-hint-bar">
              <span className="platform-chip yt">YouTube 4K / MP3</span>
              <span className="platform-chip tt">TikTok không logo</span>
              <span className="platform-chip ig">Instagram Reels &amp; Album</span>
              <span className="platform-chip fb">Facebook Reels &amp; HD</span>
              <span className="platform-chip x">X / Twitter Video</span>
              <span className="platform-chip pin">Pinterest Ảnh HD</span>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
