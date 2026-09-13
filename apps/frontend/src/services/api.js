/**
 * API Client & Services Layer
 * Kết nối đồng bộ 100% chức năng Frontend với Backend API & PostgreSQL Tracking
 */

const API_BASE = import.meta.env.VITE_API_BASE_URL || ''

// ─────────────────────────────────────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Lấy hoặc tự động khởi tạo Device ID duy nhất trong localStorage
 */
export function getDeviceId() {
  try {
    let id = localStorage.getItem('app_device_id')
    if (!id) {
      id = 'dev_' + (typeof crypto !== 'undefined' && crypto.randomUUID ? crypto.randomUUID().replace(/-/g, '').slice(0, 16) : Math.random().toString(36).slice(2, 14))
      localStorage.setItem('app_device_id', id)
    }
    return id
  } catch {
    return 'dev_fallback'
  }
}

/**
 * Header mặc định kèm Device ID để backend nhận diện và lưu vết
 */
function getDefaultHeaders() {
  return {
    'Content-Type': 'application/json',
    'x-device-id': getDeviceId(),
  }
}

/**
 * Lưu trữ & đọc trình duyệt cookie người dùng lựa chọn từ localStorage
 */
export function getActiveBrowser() {
  try {
    return localStorage.getItem('selected_browser') || ''
  } catch {
    return ''
  }
}

export function setActiveBrowser(browserId) {
  try {
    if (browserId) {
      localStorage.setItem('selected_browser', browserId)
    } else {
      localStorage.removeItem('selected_browser')
    }
  } catch {}
}

/**
 * Helper kích hoạt tải file an toàn trên trình duyệt qua thẻ <a> ẩn
 */
export function triggerFileDownload(downloadUrl, filename = '') {
  const a = document.createElement('a')
  a.href = downloadUrl
  if (filename) a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
}

// ─────────────────────────────────────────────────────────────────────────────
// 1. Extract
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Phân tích liên kết đơn lẻ
 * POST /api/media/extract
 */
export async function extractMedia(url, browserOverride = null) {
  const browser = browserOverride !== null ? browserOverride : getActiveBrowser()
  const response = await fetch(`${API_BASE}/api/media/extract`, {
    method: 'POST',
    headers: getDefaultHeaders(),
    body: JSON.stringify({
      url: url.trim(),
      browser: browser || undefined,
    }),
  })

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}))
    throw new Error(errorData.message || `Lỗi máy chủ (${response.status})`)
  }

  return await response.json()
}

// ─────────────────────────────────────────────────────────────────────────────
// 2. Stream Download
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Tạo URL tải stream video / audio
 * GET /api/media/download/stream
 */
export function buildStreamDownloadUrl({
  url,
  formatId,
  isAudio = false,
  title = 'media',
  audioFormat,
  audioBitrate,
  startTime,
  endTime,
  format,
  streamType,
  isMute = false,
  sponsorBlock = false,
  embedThumbnail = false,
  embedMetadata = false,
  browser = null,
  referer = null,
}) {
  const params = new URLSearchParams()
  params.append('url', url)
  params.append('deviceId', getDeviceId())
  if (formatId) params.append('formatId', formatId)
  if (referer) params.append('referer', referer)
  params.append('isAudio', String(Boolean(isAudio)))
  if (title) params.append('title', title)
  if (audioFormat) params.append('audioFormat', audioFormat)
  if (audioBitrate) params.append('audioBitrate', audioBitrate)
  if (startTime) params.append('startTime', startTime)
  if (endTime) params.append('endTime', endTime)
  if (format) params.append('format', format)
  if (streamType) params.append('streamType', streamType)
  if (isMute) params.append('isMute', 'true')
  if (sponsorBlock) params.append('sponsorBlock', 'true')
  if (embedThumbnail) params.append('embedThumbnail', 'true')
  if (embedMetadata) params.append('embedMetadata', 'true')

  const targetBrowser = browser !== null ? browser : getActiveBrowser()
  if (targetBrowser && targetBrowser !== 'none') {
    params.append('browser', targetBrowser)
  }

  return `${API_BASE}/api/media/download/stream?${params.toString()}`
}

// ─────────────────────────────────────────────────────────────────────────────
// 3. Subtitle Download
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Tạo URL tải file phụ đề (.srt / .vtt)
 * GET /api/media/download/subtitle
 */
export function buildSubtitleDownloadUrl({ url, lang, format = 'vtt', title = 'subtitle', browser = null }) {
  const params = new URLSearchParams()
  params.append('url', url)
  params.append('deviceId', getDeviceId())
  params.append('lang', lang)
  params.append('format', format)
  if (title) params.append('title', title)

  const targetBrowser = browser !== null ? browser : getActiveBrowser()
  if (targetBrowser && targetBrowser !== 'none') {
    params.append('browser', targetBrowser)
  }

  return `${API_BASE}/api/media/download/subtitle?${params.toString()}`
}

// ─────────────────────────────────────────────────────────────────────────────
// 4. Thumbnail Download
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Tạo URL tải thumbnail / cover art riêng
 * GET /api/media/download/thumbnail
 */
export function buildThumbnailDownloadUrl({ url, title = 'thumbnail', browser = null }) {
  const params = new URLSearchParams()
  params.append('url', url)
  params.append('deviceId', getDeviceId())
  if (title) params.append('title', title)

  const targetBrowser = browser !== null ? browser : getActiveBrowser()
  if (targetBrowser && targetBrowser !== 'none') {
    params.append('browser', targetBrowser)
  }

  return `${API_BASE}/api/media/download/thumbnail?${params.toString()}`
}

// ─────────────────────────────────────────────────────────────────────────────
// 5. Proxy Media
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Tạo URL tải ảnh/tệp đơn qua Proxy chống chặn 403
 * GET /api/media/proxy-media
 */
export function buildProxyMediaUrl(mediaUrl, filename = 'media.jpg') {
  const params = new URLSearchParams()
  params.append('url', mediaUrl)
  params.append('deviceId', getDeviceId())
  params.append('filename', filename)
  return `${API_BASE}/api/media/proxy-media?${params.toString()}`
}

/**
 * Tạo URL hiển thị ảnh/avatar an toàn chống chặn CORP (ERR_BLOCKED_BY_RESPONSE.NotSameOrigin) và 403 Forbidden
 * GET /api/media/proxy-image
 */
export function buildProxyImageUrl(rawUrl) {
  if (!rawUrl) return ''
  if (
    rawUrl.startsWith('data:') ||
    rawUrl.startsWith('blob:') ||
    rawUrl.includes('/api/media/proxy-')
  ) {
    return rawUrl
  }
  const params = new URLSearchParams()
  params.append('url', rawUrl)
  return `${API_BASE}/api/media/proxy-image?${params.toString()}`
}


// ─────────────────────────────────────────────────────────────────────────────
// 6. ZIP Download
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Đóng gói các tệp đã chọn thành file ZIP và tải về
 * Hỗ trợ nhận luồng stream dữ liệu và báo cáo tiến trình theo thời gian thực
 * POST /api/media/download-zip
 */
export async function downloadZipArchive(items, zipName = 'Album_Media', onProgress) {
  const response = await fetch(`${API_BASE}/api/media/download-zip`, {
    method: 'POST',
    headers: getDefaultHeaders(),
    body: JSON.stringify({ items, zipName }),
  })

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}))
    throw new Error(errorData.message || 'Lỗi khi tạo và tải tệp nén ZIP từ máy chủ')
  }

  let blob
  if (response.body && response.body.getReader) {
    const reader = response.body.getReader()
    const chunks = []
    let receivedBytes = 0

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      if (value) {
        chunks.push(value)
        receivedBytes += value.length
        if (onProgress) {
          onProgress({ receivedBytes })
        }
      }
    }
    blob = new Blob(chunks, { type: 'application/zip' })
  } else {
    blob = await response.blob()
  }

  const downloadUrl = URL.createObjectURL(blob)
  triggerFileDownload(downloadUrl, `${zipName}.zip`)
  setTimeout(() => URL.revokeObjectURL(downloadUrl), 5000)
}

// ─────────────────────────────────────────────────────────────────────────────
// 7. Crawl Profile
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Quét toàn bộ hồ sơ / tài khoản / playlist
 * POST /api/media/crawl-profile
 */
export async function crawlProfile({
  url,
  limit = 50,
  mediaType = 'all',
  platform = 'auto',
  browser = null,
  rangeStart,
  rangeEnd,
}) {
  const targetBrowser = browser !== null ? browser : getActiveBrowser()
  const payload = {
    url: url.trim(),
    limit,
    mediaType,
    platform,
  }
  if (targetBrowser && targetBrowser !== 'none') {
    payload.browser = targetBrowser
  }
  if (rangeStart && rangeEnd && rangeEnd >= rangeStart) {
    payload.rangeStart = Number(rangeStart)
    payload.rangeEnd = Number(rangeEnd)
  }

  const response = await fetch(`${API_BASE}/api/media/crawl-profile`, {
    method: 'POST',
    headers: getDefaultHeaders(),
    body: JSON.stringify(payload),
  })

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}))
    throw new Error(errorData.message || 'Lỗi khi quét tài khoản')
  }

  return await response.json()
}

// ─────────────────────────────────────────────────────────────────────────────
// 8. System Info & Browsers
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Lấy danh sách trình duyệt khả dụng trên máy chủ
 * GET /api/media/browsers
 */
export async function getBrowsersList() {
  const response = await fetch(`${API_BASE}/api/media/browsers`, {
    headers: { 'x-device-id': getDeviceId() },
  })
  if (!response.ok) return null
  return await response.json()
}

/**
 * Lấy cấu hình hiện tại của hệ thống
 * GET /api/media/config
 */
export async function getSystemConfig() {
  const response = await fetch(`${API_BASE}/api/media/config`, {
    headers: { 'x-device-id': getDeviceId() },
  })
  if (!response.ok) return null
  return await response.json()
}

/**
 * Kiểm tra sức khỏe hệ thống (binary status)
 * GET /api/media/health
 */
export async function getSystemHealth() {
  const response = await fetch(`${API_BASE}/api/media/health`, {
    headers: { 'x-device-id': getDeviceId() },
  })
  if (!response.ok) return null
  return await response.json()
}

/**
 * Lấy lịch sử tải xuống gần nhất từ PostgreSQL
 * GET /api/media/history
 */
export async function getDownloadHistory(limit = 30) {
  try {
    const deviceId = getDeviceId()
    const response = await fetch(`${API_BASE}/api/media/history?limit=${limit}&deviceId=${deviceId}`, {
      headers: { 'x-device-id': deviceId },
    })
    if (!response.ok) return { total: 0, history: [] }
    return await response.json()
  } catch {
    return { total: 0, history: [] }
  }
}

/**
 * Xóa lịch sử tải xuống của thiết bị hiện tại
 * DELETE /api/media/history
 */
export async function clearDownloadHistory() {
  try {
    const deviceId = getDeviceId()
    const response = await fetch(`${API_BASE}/api/media/history?deviceId=${deviceId}`, {
      method: 'DELETE',
      headers: { 'x-device-id': deviceId },
    })
    if (!response.ok) return { success: false }
    return await response.json()
  } catch {
    return { success: false }
  }
}

/**
 * Giải mã liên kết rút gọn và xác thực nền tảng trên backend
 * POST /api/media/resolve-url
 */
export async function resolveShortUrl(url, expectedPlatform = null) {
  const response = await fetch(`${API_BASE}/api/media/resolve-url`, {
    method: 'POST',
    headers: getDefaultHeaders(),
    body: JSON.stringify({
      url: url.trim(),
      expectedPlatform: expectedPlatform || undefined,
    }),
  })

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}))
    throw new Error(errorData.message || `Lỗi khi giải mã URL (${response.status})`)
  }

  return await response.json()
}

// ─────────────────────────────────────────────────────────────────────────────
// 9. Cookie Manager
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Lưu cookie của một nền tảng từ chuỗi DevTools
 * POST /api/media/cookies
 */
export async function savePlatformCookies(platform, cookieString, domain = null) {
  const response = await fetch(`${API_BASE}/api/media/cookies`, {
    method: 'POST',
    headers: getDefaultHeaders(),
    body: JSON.stringify({ platform, cookieString, domain: domain || undefined }),
  })
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}))
    throw new Error(errorData.message || 'Lỗi khi lưu cookie')
  }
  return await response.json()
}

/**
 * Lấy trạng thái cookies đã lưu (không trả giá trị thực)
 * GET /api/media/cookies
 */
export async function getCookieStatus() {
  const response = await fetch(`${API_BASE}/api/media/cookies`, {
    headers: { 'x-device-id': getDeviceId() },
  })
  if (!response.ok) return null
  return await response.json()
}

/**
 * Lấy danh sách nền tảng được hỗ trợ với thông tin cookie cần thiết
 * GET /api/media/cookies/platforms
 */
export async function getSupportedCookiePlatforms() {
  const response = await fetch(`${API_BASE}/api/media/cookies/platforms`, {
    headers: { 'x-device-id': getDeviceId() },
  })
  if (!response.ok) return []
  return await response.json()
}

/**
 * Xóa cookie theo domain hoặc toàn bộ
 * DELETE /api/media/cookies
 */
export async function deletePlatformCookies(domain = null) {
  const url = domain
    ? `${API_BASE}/api/media/cookies?domain=${encodeURIComponent(domain)}`
    : `${API_BASE}/api/media/cookies`
  const response = await fetch(url, {
    method: 'DELETE',
    headers: { 'x-device-id': getDeviceId() },
  })
  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}))
    throw new Error(errorData.message || 'Lỗi khi xóa cookie')
  }
  return await response.json()
}

