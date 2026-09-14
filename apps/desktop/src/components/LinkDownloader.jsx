import { useState, useEffect, useMemo } from 'react'
import {
  IconDownload,
  IconClose,
  IconPaste,
  IconTrash,
  IconSparkles,
} from './Icons'
import { FORMAT_OPTIONS, detectPlatform, validatePlatformUrl, getPlatform } from '../constants'
import { extractMedia, resolveShortUrl } from '../services/api'

export default function LinkDownloader({ onMediaExtracted, onBatchExtracted, isGlobalLoading, onShowToast }) {
  const [mode, setMode] = useState('single') // 'single' | 'batch'
  const [url, setUrl] = useState('')
  const [batchText, setBatchText] = useState('')
  const [selectedFormat, setSelectedFormat] = useState('all')
  const [isLoading, setIsLoading] = useState(false)
  const [batchProgress, setBatchProgress] = useState({ current: 0, total: 0, statusText: '' })

  // Synchronous validation state computed from URL
  const trimmedUrl = url.trim()
  const syncValidation = useMemo(() => {
    if (!trimmedUrl) return { valid: true, status: 'empty', message: '' }
    const detected = detectPlatform(trimmedUrl)
    const initial = validatePlatformUrl(trimmedUrl, detected)
    if (initial.status === 'needs_resolve') {
      return { valid: true, status: 'needs_resolve', message: 'Đang kiểm tra chuyển hướng link...' }
    } else if (initial.status === 'matched') {
      return {
        valid: true,
        status: 'matched',
        platform: detected,
        message: `✓ Hợp lệ: ${getPlatform(detected)?.name || detected}`,
      }
    } else {
      return { valid: true, status: 'unknown', message: 'Liên kết hợp lệ' }
    }
  }, [trimmedUrl])

  const [asyncResolved, setAsyncResolved] = useState(null)

  // Asynchronously resolve shortened links
  useEffect(() => {
    if (syncValidation.status === 'needs_resolve') {
      let active = true
      const timer = setTimeout(async () => {
        try {
          const res = await resolveShortUrl(trimmedUrl)
          if (active && res.platform) {
            const pObj = getPlatform(res.platform)
            setAsyncResolved({
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
            setAsyncResolved({ valid: true, status: 'manual', message: 'Liên kết cần phân tích trực tiếp' })
          }
        }
      }, 350)
      return () => {
        active = false
        clearTimeout(timer)
      }
    }
  }, [syncValidation.status, trimmedUrl])

  const validationState = asyncResolved && syncValidation.status === 'needs_resolve' ? asyncResolved : syncValidation
  const resolvedUrl = asyncResolved?.resolvedUrl || trimmedUrl

  // Phân tích danh sách link hàng loạt
  const parsedBatchLinks = useMemo(() => {
    return batchText
      .split('\n')
      .map((l) => l.trim())
      .filter((l) => l.startsWith('http://') || l.startsWith('https://') || (l.includes('.') && !l.includes(' ')))
      .slice(0, 10)
  }, [batchText])

  // Dán từ Clipboard
  const handlePaste = async (targetMode) => {
    try {
      const text = await navigator.clipboard.readText()
      if (text) {
        if (targetMode === 'single') {
          setUrl(text.trim())
        } else {
          setBatchText((prev) => (prev ? `${prev}\n${text.trim()}` : text.trim()))
        }
        onShowToast?.('Đã dán liên kết từ bộ nhớ tạm')
      }
    } catch {
      onShowToast?.('Vui lòng cấp quyền đọc clipboard hoặc dùng Ctrl+V')
    }
  }

  // Xử lý trích xuất 1 liên kết
  const handleSingleExtract = async (e) => {
    e?.preventDefault()
    const targetUrl = (resolvedUrl || url).trim()
    if (!targetUrl) {
      onShowToast?.('Vui lòng nhập đường dẫn liên kết!')
      return
    }

    setIsLoading(true)
    try {
      const data = await extractMedia(targetUrl)
      if (data) {
        onMediaExtracted?.(data)
        onShowToast?.(`Đã trích xuất thành công: ${data.title?.slice(0, 35)}...`)
      }
    } catch (err) {
      onShowToast?.(err.message || 'Lỗi khi trích xuất thông tin liên kết')
    } finally {
      setIsLoading(false)
    }
  }

  // Xử lý bóc tách nhiều liên kết
  const handleBatchExtract = async (e) => {
    e?.preventDefault()
    if (parsedBatchLinks.length === 0) {
      onShowToast?.('Vui lòng nhập ít nhất 1 đường dẫn hợp lệ!')
      return
    }

    setIsLoading(true)
    setBatchProgress({ current: 0, total: parsedBatchLinks.length, statusText: 'Đang chuẩn bị...' })

    const results = []
    for (let i = 0; i < parsedBatchLinks.length; i++) {
      const link = parsedBatchLinks[i]
      setBatchProgress({
        current: i + 1,
        total: parsedBatchLinks.length,
        statusText: `Đang xử lý liên kết ${i + 1}/${parsedBatchLinks.length}...`,
      })
      try {
        const item = await extractMedia(link)
        if (item) results.push(item)
      } catch (err) {
        console.warn(`Lỗi bóc tách link ${link}:`, err)
      }
    }

    setIsLoading(false)
    setBatchProgress({ current: 0, total: 0, statusText: '' })

    if (results.length > 0) {
      onBatchExtracted?.(results)
      onShowToast?.(`Đã giải mã thành công ${results.length}/${parsedBatchLinks.length} liên kết!`)
    } else {
      onShowToast?.('Không thể giải mã các liên kết đã nhập.')
    }
  }

  return (
    <div className="panel-card panel-left-link">
      {/* Header của Panel */}
      <div className="panel-card-header">
        <div className="panel-title-area">
          <div className="panel-badge-num">01</div>
          <div>
            <h2 className="panel-title">Tải theo liên kết</h2>
            <p className="panel-subtitle">Hỗ trợ tải 1 bài viết hoặc danh sách tối đa 10 link</p>
          </div>
        </div>

        {/* Tab chuyển đổi 1 link vs Nhiều link */}
        <div className="mode-toggle-group">
          <button
            type="button"
            className={`mode-toggle-btn ${mode === 'single' ? 'active' : ''}`}
            onClick={() => setMode('single')}
          >
            1 Liên kết
          </button>
          <button
            type="button"
            className={`mode-toggle-btn ${mode === 'batch' ? 'active' : ''}`}
            onClick={() => setMode('batch')}
          >
            Nhiều link ({parsedBatchLinks.length}/10)
          </button>
        </div>
      </div>

      {/* Thân nhập liệu */}
      <div className="panel-card-body">
        {mode === 'single' ? (
          <form onSubmit={handleSingleExtract} className="form-stack">
            {/* Hàng 1: Input URL đơn */}
            <div className="input-with-actions">
              <input
                type="text"
                className="clean-input"
                placeholder="Dán liên kết YouTube, TikTok, Facebook, Instagram, X, Pinterest..."
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                disabled={isLoading || isGlobalLoading}
              />
              {url ? (
                <button
                  type="button"
                  className="icon-tool-btn"
                  onClick={() => setUrl('')}
                  title="Xóa URL"
                >
                  <IconClose className="w-4 h-4" />
                </button>
              ) : (
                <button
                  type="button"
                  className="icon-tool-btn"
                  onClick={() => handlePaste('single')}
                  title="Dán từ bộ nhớ tạm"
                >
                  <IconPaste className="w-4 h-4" />
                </button>
              )}
            </div>

            {/* Hàng 2: Tùy chọn định dạng & Trạng thái phân tích */}
            <div className="config-grid-row">
              <div className="limit-selector-container">
                <span className="control-label">Định dạng:</span>
                <div className="format-pills">
                  {FORMAT_OPTIONS.slice(0, 3).map((f) => (
                    <button
                      key={f.id}
                      type="button"
                      className={`format-pill ${selectedFormat === f.id ? 'active' : ''}`}
                      onClick={() => setSelectedFormat(f.id)}
                    >
                      {f.label}
                    </button>
                  ))}
                </div>
              </div>

              {validationState.message ? (
                <div className={`validation-status-text ${validationState.status === 'matched' ? 'status-ok' : ''}`}>
                  {validationState.message}
                </div>
              ) : (
                <span className="action-hint">Tự động nhận diện nền tảng</span>
              )}
            </div>

            {/* Hàng 3: Gợi ý và Nút hành động */}
            <div className="action-row">
              <span className="action-hint">
                Hỗ trợ tải Video 4K, MP3, Reels, Shorts, Album ảnh
              </span>

              <button
                type="submit"
                className="primary-action-btn"
                disabled={!url.trim() || isLoading || isGlobalLoading}
              >
                {isLoading ? (
                  <>
                    <span className="clean-spinner" />
                    <span>Đang phân tích...</span>
                  </>
                ) : (
                  <>
                    <IconSparkles className="w-4 h-4" />
                    <span>Trích xuất liên kết</span>
                  </>
                )}
              </button>
            </div>
          </form>
        ) : (
          <form onSubmit={handleBatchExtract} className="form-stack">
            {/* Hàng 1: Textarea danh sách link */}
            <div className="textarea-container">
              <textarea
                className="clean-textarea"
                rows={2}
                placeholder="Điền mỗi dòng 1 liên kết (Tối đa 10 link cùng lúc)...&#10;https://www.youtube.com/watch?v=...&#10;https://www.tiktok.com/@user/video/..."
                value={batchText}
                onChange={(e) => setBatchText(e.target.value)}
                disabled={isLoading || isGlobalLoading}
              />
              <div className="textarea-footer">
                <span className="batch-counter">
                  Đã nhận diện: <strong>{parsedBatchLinks.length}</strong> / 10 liên kết
                </span>
                <div className="textarea-actions">
                  <button
                    type="button"
                    className="btn-tiny"
                    onClick={() => handlePaste('batch')}
                    title="Dán thêm link"
                  >
                    <IconPaste className="w-3.5 h-3.5" /> Dán
                  </button>
                  {batchText && (
                    <button
                      type="button"
                      className="btn-tiny"
                      onClick={() => setBatchText('')}
                      title="Xóa trắng"
                    >
                      <IconTrash className="w-3.5 h-3.5" /> Xóa
                    </button>
                  )}
                </div>
              </div>
            </div>

            {/* Hàng 2: Tùy chọn định dạng & Trạng thái bóc tách hàng loạt */}
            <div className="config-grid-row">
              <div className="limit-selector-container">
                <span className="control-label">Định dạng:</span>
                <div className="format-pills">
                  {FORMAT_OPTIONS.slice(0, 3).map((f) => (
                    <button
                      key={f.id}
                      type="button"
                      className={`format-pill ${selectedFormat === f.id ? 'active' : ''}`}
                      onClick={() => setSelectedFormat(f.id)}
                    >
                      {f.label}
                    </button>
                  ))}
                </div>
              </div>

              {isLoading && batchProgress.total > 0 ? (
                <div className="progress-mini-label">
                  <span>{batchProgress.statusText}</span>
                  <strong>{Math.round((batchProgress.current / batchProgress.total) * 100)}%</strong>
                </div>
              ) : (
                <span className="action-hint">Phân tích song song tối đa 10 liên kết</span>
              )}
            </div>

            {/* Hàng 3: Nút hành động bóc tách hàng loạt */}
            <div className="action-row">
              <span className="action-hint">
                {parsedBatchLinks.length > 0 ? `Sẵn sàng trích xuất ${parsedBatchLinks.length} liên kết` : 'Nhập danh sách liên kết để bắt đầu'}
              </span>

              <button
                type="submit"
                className="primary-action-btn"
                disabled={parsedBatchLinks.length === 0 || isLoading || isGlobalLoading}
              >
                {isLoading ? (
                  <>
                    <span className="clean-spinner" />
                    <span>Đang giải mã...</span>
                  </>
                ) : (
                  <>
                    <IconDownload className="w-4 h-4" />
                    <span>Bóc tách {parsedBatchLinks.length > 0 ? `${parsedBatchLinks.length} link` : 'hàng loạt'}</span>
                  </>
                )}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  )
}
