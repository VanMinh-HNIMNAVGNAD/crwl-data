/**
 * API Client — Tauri Native Only
 * Tất cả các tính năng đều chạy qua Tauri IPC (invoke/listen).
 * NestJS backend đã được loại bỏ hoàn toàn.
 */

import { invoke, isTauri as checkIsTauri } from '@tauri-apps/api/core'
import { listen } from '@tauri-apps/api/event'

// ─────────────────────────────────────────────────────────────────────────────
// Core Helpers
// ─────────────────────────────────────────────────────────────────────────────

export function isTauri() {
  try {
    return Boolean(checkIsTauri && checkIsTauri())
  } catch {
    return false
  }
}

export function getDeviceId() {
  try {
    let id = localStorage.getItem('app_device_id')
    if (!id) {
      id =
        'dev_' +
        (typeof crypto !== 'undefined' && crypto.randomUUID
          ? crypto.randomUUID().replace(/-/g, '').slice(0, 16)
          : Math.random().toString(36).slice(2, 14))
      localStorage.setItem('app_device_id', id)
    }
    return id
  } catch {
    return 'dev_fallback'
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Turnstile (Removed — bypassed permanently)
// ─────────────────────────────────────────────────────────────────────────────

export function setTurnstileToken() {}
export function getTurnstileToken() {
  return null
}
export async function verifyTurnstileTokenWithBackend() {
  return { success: true, bypassed: true }
}

// ─────────────────────────────────────────────────────────────────────────────
// Browser & Download Directory preferences (localStorage)
// ─────────────────────────────────────────────────────────────────────────────

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
  } catch (err) {
    void err
  }
}

export function getCustomDownloadDir() {
  try {
    return localStorage.getItem('custom_download_dir') || ''
  } catch {
    return ''
  }
}

export function setCustomDownloadDir(dir) {
  try {
    if (dir) {
      localStorage.setItem('custom_download_dir', dir)
    } else {
      localStorage.removeItem('custom_download_dir')
    }
  } catch (err) {
    void err
  }
}

export function getAlwaysAskDownloadDir() {
  try {
    return localStorage.getItem('always_ask_download_dir') === 'true'
  } catch {
    return false
  }
}

export function setAlwaysAskDownloadDir(enabled) {
  try {
    localStorage.setItem('always_ask_download_dir', enabled ? 'true' : 'false')
  } catch (err) {
    void err
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// 1. Extract Media
// ─────────────────────────────────────────────────────────────────────────────

export async function extractMedia(url, browserOverride = null) {
  const browser = browserOverride !== null ? browserOverride : getActiveBrowser()
  try {
    return await invoke('extract_media', {
      url: url.trim(),
      browser: browser || undefined,
      deviceId: getDeviceId(),
    })
  } catch (err) {
    throw new Error(typeof err === 'string' ? err : err.message || 'Lỗi trích xuất media', { cause: err })
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// 2. Crawl Profile / Channel
// ─────────────────────────────────────────────────────────────────────────────

export async function crawlProfile({
  url,
  limit = 50,
  mediaType = 'all',
  platform = 'auto',
  browser = null,
  rangeStart,
  rangeEnd,
}) {
  try {
    const targetBrowser = browser !== null ? browser : getActiveBrowser()
    const finalLimit = limit === 0 || limit === '0' ? 0 : (Number(limit) || 50)
    const finalRangeStart = rangeStart != null && !isNaN(Number(rangeStart)) ? Number(rangeStart) : undefined
    const finalRangeEnd = rangeEnd != null && !isNaN(Number(rangeEnd)) ? Number(rangeEnd) : undefined

    return await invoke('crawl_profile', {
      url: url.trim(),
      limit: finalLimit,
      mediaType: mediaType || undefined,
      platform: platform && platform !== 'auto' ? platform : undefined,
      browser: targetBrowser === 'none' ? 'none' : (targetBrowser || 'auto'),
      rangeStart: finalRangeStart,
      rangeEnd: finalRangeEnd,
      deviceId: getDeviceId(),
    })
  } catch (err) {
    throw new Error(typeof err === 'string' ? err : err.message || 'Lỗi quét tài khoản', { cause: err })
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// 3. Resolve Short URL
// ─────────────────────────────────────────────────────────────────────────────

export async function resolveShortUrl(url, expectedPlatform = null) {
  try {
    return await invoke('resolve_short_url', {
      url: url.trim(),
      expectedPlatform: expectedPlatform || undefined,
    })
  } catch (err) {
    throw new Error(typeof err === 'string' ? err : err.message || 'Lỗi giải mã URL', { cause: err })
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// 4. Native Downloads (Video / Audio / Thumbnail / Subtitle)
// ─────────────────────────────────────────────────────────────────────────────

/**
 * Sinh mã tác vụ duy nhất cho mỗi lần tải, để thanh tiến trình của tác vụ này
 * không bị sự kiện của tác vụ khác ghi đè khi tải song song nhiều tệp.
 */
export function createTaskId() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID()
  return `task_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`
}

export async function startNativeDownload({
  url,
  formatId,
  isAudio = false,
  audioFormat,
  audioBitrate,
  title,
  destDir,
  browser = null,
  startTime = null,
  endTime = null,
  isMute = false,
  sponsorBlock = false,
  embedSubs = false,
  embedThumbnail = false,
  embedMetadata = false,
  splitChapters = false,
  concurrentFragments = null,
  videoFormat = null,
  proxy = null,
  referer = null,
  useAria2c = false,
  taskId = null,
}) {
  const targetBrowser = browser !== null ? browser : getActiveBrowser()
  const customDir = destDir || getCustomDownloadDir() || null

  return await invoke('start_download', {
    options: {
      url: url.trim(),
      format_id: formatId || null,
      is_audio: Boolean(isAudio),
      audio_format: audioFormat || null,
      audio_bitrate: audioBitrate || null,
      title: title || null,
      dest_dir: customDir,
      browser: targetBrowser && targetBrowser !== 'none' ? targetBrowser : null,
      device_id: getDeviceId(),
      start_time: startTime || null,
      end_time: endTime || null,
      is_mute: Boolean(isMute),
      sponsor_block: Boolean(sponsorBlock),
      // Các tuỳ chọn dưới đây trước kia bị bỏ rơi tại đây nên bật ở UI cũng không có tác dụng
      embed_subs: Boolean(embedSubs),
      embed_thumbnail: Boolean(embedThumbnail),
      embed_metadata: Boolean(embedMetadata),
      split_chapters: Boolean(splitChapters),
      concurrent_fragments: concurrentFragments ? Number(concurrentFragments) : null,
      video_format: videoFormat || null,
      proxy: proxy || null,
      referer: referer || null,
      use_aria2c: Boolean(useAria2c),
      task_id: taskId || null,
    },
  })
}

/**
 * Tải ảnh bìa (thumbnail) — thay thế /api/media/download/thumbnail
 */
export async function downloadThumbnail({ url, title, browser = null }) {
  const targetBrowser = browser !== null ? browser : getActiveBrowser()
  return await invoke('download_thumbnail', {
    url: url.trim(),
    title: title || null,
    browser: targetBrowser && targetBrowser !== 'none' ? targetBrowser : null,
    deviceId: getDeviceId(),
  })
}

/**
 * Tải phụ đề (subtitle) — thay thế /api/media/download/subtitle
 */
export async function downloadSubtitle({ url, lang, format = 'vtt', title, browser = null }) {
  const targetBrowser = browser !== null ? browser : getActiveBrowser()
  return await invoke('download_subtitle', {
    url: url.trim(),
    lang,
    format,
    title: title || null,
    browser: targetBrowser && targetBrowser !== 'none' ? targetBrowser : null,
    deviceId: getDeviceId(),
  })
}

/**
 * Hộp thoại chọn thư mục lưu tệp tải về
 */
export async function selectDownloadDirectory() {
  const dir = await invoke('select_download_directory')
  if (dir) {
    setCustomDownloadDir(dir)
  }
  return dir
}

export async function getDefaultDownloadDirectory() {
  const custom = getCustomDownloadDir()
  if (custom) return custom
  return await invoke('get_default_download_directory')
}

export async function openDownloadFolder(filePath) {
  if (filePath) {
    return await invoke('open_download_folder', { path: filePath })
  }
  return false
}

/**
 * Lắng nghe tiến trình % tải xuống từ Rust Core.
 * Truyền `taskId` để chỉ nhận sự kiện của đúng tác vụ đó — nếu không, mọi tệp
 * đang tải song song sẽ cùng ghi vào một thanh tiến trình.
 */
export function onDownloadProgress(callback, taskId = null) {
  return listen('download-progress', (event) => {
    const payload = event.payload
    if (taskId && payload?.id && payload.id !== taskId) return
    callback?.(payload)
  })
}

// ─────────────────────────────────────────────────────────────────────────────
// 5. System Info & Browsers
// ─────────────────────────────────────────────────────────────────────────────

export async function getBrowsersList() {
  try {
    const browsers = await invoke('get_browsers_list')
    return { browsers }
  } catch {
    return null
  }
}

export async function getSystemHealth() {
  try {
    return await invoke('get_system_health')
  } catch {
    return null
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// 6. Download History
// ─────────────────────────────────────────────────────────────────────────────

export async function getDownloadHistory(limit = 30) {
  const deviceId = getDeviceId()
  try {
    const list = await invoke('get_download_history', {
      limit: Number(limit) || 30,
      deviceId,
    })
    return { total: list?.length || 0, history: list || [] }
  } catch {
    return { total: 0, history: [] }
  }
}

export async function clearDownloadHistory() {
  const deviceId = getDeviceId()
  try {
    const ok = await invoke('clear_download_history', { deviceId })
    return { success: Boolean(ok) }
  } catch {
    return { success: false }
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// 7. Cookie Manager — Native (thay thế toàn bộ /api/media/cookies)
// ─────────────────────────────────────────────────────────────────────────────

export async function savePlatformCookies(platform, cookieString) {
  try {
    return await invoke('save_platform_cookies', { platform, cookieString })
  } catch (err) {
    throw new Error(typeof err === 'string' ? err : err.message || 'Lỗi khi lưu cookie', { cause: err })
  }
}

export async function getCookieStatus() {
  try {
    return await invoke('get_cookie_status')
  } catch {
    return null
  }
}

export async function getSupportedCookiePlatforms() {
  try {
    return await invoke('get_supported_cookie_platforms')
  } catch {
    return []
  }
}

export async function deletePlatformCookies(platform = null) {
  try {
    return await invoke('delete_platform_cookies', {
      platform: platform || null,
    })
  } catch (err) {
    throw new Error(typeof err === 'string' ? err : err.message || 'Lỗi khi xóa cookie', { cause: err })
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// 8. Proxy & Direct / Album Download — Native Desktop (không bị CORS/403)
// ─────────────────────────────────────────────────────────────────────────────

export function buildProxyImageUrl(rawUrl) {
  return rawUrl || ''
}

export function buildProxyMediaUrl(mediaUrl) {
  return mediaUrl || ''
}

/**
 * Tải một tệp ảnh trực tiếp về thư mục Downloads với header chống chặn 403
 */
export async function downloadDirectFile({ url, filename, referer, destDir }) {
  const customDir = destDir || getCustomDownloadDir() || null
  return await invoke('download_direct_file', {
    url: url.trim(),
    filename: filename || null,
    referer: referer || null,
    destDir: customDir,
    deviceId: getDeviceId(),
  })
}

/**
 * Tải album nhiều ảnh hoặc đóng gói thành file ZIP native trên máy
 */
export async function downloadAlbumBatch({ items, albumName = 'Album_Media', destDir, asZip = false, taskId = null }) {
  const customDir = destDir || getCustomDownloadDir() || null
  return await invoke('download_album_batch', {
    items: items.map((it) => ({
      url: it.url,
      filename: it.filename || it.title || null,
      referer: it.referer || null,
    })),
    albumName: albumName || 'Album_Media',
    destDir: customDir,
    asZip: Boolean(asZip),
    deviceId: getDeviceId(),
    taskId: taskId || null,
  })
}

/**
 * ZIP download: Trong Tauri dùng Native Rust (nhanh, chống 403, lưu trực tiếp ổ cứng).
 * Nếu chạy web thuần thì fallback sang JSZip.
 */
export async function downloadZipArchive(items, zipName = 'Album_Media', onProgress, taskId = null) {
  if (isTauri()) {
    onProgress?.({ receivedBytes: 0, total: items.length })
    const res = await downloadAlbumBatch({
      items,
      albumName: zipName,
      asZip: true,
      taskId,
    })
    onProgress?.({ receivedBytes: items.length, total: items.length })
    return res
  }

  // Dynamic import JSZip cho fallback browser
  let JSZip
  try {
    const mod = await import('jszip')
    JSZip = mod.default
  } catch (err) {
    throw new Error('JSZip chưa được cài. Chạy: pnpm add jszip', { cause: err })
  }

  const zip = new JSZip()
  const folder = zip.folder(zipName)
  let done = 0

  await Promise.all(
    items.map(async (item) => {
      try {
        const res = await fetch(item.url)
        if (!res.ok) return
        const blob = await res.blob()
        const ext = item.ext || 'jpg'
        const filename = `${item.title || `media_${done + 1}`}.${ext}`
        folder.file(filename, blob)
      } catch {
        // skip failed items
      } finally {
        done++
        onProgress?.({ receivedBytes: done, total: items.length })
      }
    })
  )

  const content = await zip.generateAsync({ type: 'blob' })
  const downloadUrl = URL.createObjectURL(content)
  const a = document.createElement('a')
  a.href = downloadUrl
  a.download = `${zipName}.zip`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  setTimeout(() => URL.revokeObjectURL(downloadUrl), 5000)
}

// ─────────────────────────────────────────────────────────────────────────────
// 9. Binary Manager — kiểm tra và cập nhật yt-dlp, gallery-dl, ffmpeg
// ─────────────────────────────────────────────────────────────────────────────

export async function getBinaryStatus() {
  try {
    return await invoke('get_binary_status')
  } catch {
    return null
  }
}

export async function updateYtdlp() {
  return await invoke('update_ytdlp')
}

export async function updateGalleryDl() {
  return await invoke('update_gallery_dl')
}

// ─────────────────────────────────────────────────────────────────────────────
// 10. App Settings — đọc/ghi cấu hình ~/.config/crwl/settings.json
// ─────────────────────────────────────────────────────────────────────────────

export async function getAppSettings() {
  try {
    return await invoke('get_app_settings')
  } catch {
    return null
  }
}

export async function saveAppSettings(settings) {
  return await invoke('save_app_settings', { settings })
}
