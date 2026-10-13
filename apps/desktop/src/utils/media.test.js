import test from 'node:test'
import assert from 'node:assert/strict'
import { hasDownloadableContent, needsYtdlpDownload, noDownloadableContentReason, splitByDownloader } from './media.js'

test('kết quả có luồng hoặc tệp là tải được', () => {
  assert.equal(hasDownloadableContent({ streams: [{ formatId: 'best' }] }), true)
  assert.equal(hasDownloadableContent({ streams: null, images: [{ url: 'https://cdn/a.jpg' }] }), true)
})

test('trang DRM / không tìm được luồng không được coi là thành công', () => {
  const drm = {
    streams: [],
    images: null,
    description: '⚠️ Không tìm được nguồn video. Player của trang này có thể dùng DRM.',
  }
  assert.equal(hasDownloadableContent(drm), false)
  assert.equal(hasDownloadableContent({ images: [{ url: '' }, {}] }), false)
  assert.equal(hasDownloadableContent(null), false)
  assert.equal(noDownloadableContentReason(drm), 'Không tìm được nguồn video. Player của trang này có thể dùng DRM.')
  assert.match(noDownloadableContentReason({}), /Không tìm được nguồn/)
})

test('video nhúng / luồng HLS phải tải qua yt-dlp, tệp ảnh-video thường tải thẳng', () => {
  assert.equal(needsYtdlpDownload({ type: 'video', via: 'ytdlp', url: 'https://www.youtube.com/watch?v=abc' }), true)
  assert.equal(needsYtdlpDownload({ type: 'video', url: 'https://cdn.example.com/hls/master.m3u8?token=1' }), true)
  assert.equal(needsYtdlpDownload({ type: 'video', url: 'https://cdn.example.com/dash/manifest.mpd' }), true)
  assert.equal(needsYtdlpDownload({ type: 'video', url: 'https://cdn.example.com/clip.mp4' }), false)
  assert.equal(needsYtdlpDownload({ type: 'image', url: 'https://cdn.example.com/a.jpg' }), false)
  // Ảnh có chữ "m3u8" trong tên thư mục không bị nhận nhầm
  assert.equal(needsYtdlpDownload({ type: 'image', url: 'https://cdn.example.com/m3u8/a.jpg' }), false)
  assert.equal(needsYtdlpDownload({ type: 'video', via: 'ytdlp', url: '' }), false)
  assert.equal(needsYtdlpDownload(null), false)
})

test('chia album theo cách tải mà vẫn giữ thứ tự, bỏ mục không có URL', () => {
  const items = [
    { id: 1, type: 'image', url: 'https://a/1.jpg' },
    { id: 2, type: 'video', via: 'ytdlp', url: 'https://www.youtube.com/watch?v=x' },
    { id: 3, type: 'video', url: 'https://a/clip.mp4' },
    { id: 4, type: 'image', url: '' },
    { id: 5, type: 'video', url: 'https://a/live.m3u8' },
  ]
  const { direct, viaYtdlp } = splitByDownloader(items)
  assert.deepEqual(direct.map((i) => i.id), [1, 3])
  assert.deepEqual(viaYtdlp.map((i) => i.id), [2, 5])
  assert.deepEqual(splitByDownloader(null), { direct: [], viaYtdlp: [] })
})

test('ghi chú của engine: ⚠️ là không tải được, ℹ️ là lưu ý', async () => {
  const { mediaNote } = await import('./media.js')
  assert.deepEqual(mediaNote({ description: '⚠️ Đây chỉ là MỘT MẢNH vài giây.' }), {
    kind: 'warning',
    text: 'Đây chỉ là MỘT MẢNH vài giây.',
  })
  assert.deepEqual(mediaNote({ description: 'ℹ️ Liên kết này chỉ có HÌNH, không có tiếng.' }), {
    kind: 'info',
    text: 'Liên kết này chỉ có HÌNH, không có tiếng.',
  })
  assert.equal(mediaNote({ description: 'Mô tả video bình thường' }), null)
  assert.equal(mediaNote({ description: '🔍 Phát hiện 2 nguồn stream' }), null)
  assert.equal(mediaNote(null), null)
})

test('HLS/DASH tải song song nhiều mảnh kể cả khi không bật tăng tốc', async () => {
  const { concurrentFragmentsFor } = await import('./media.js')
  assert.ok(concurrentFragmentsFor(false) >= 4, 'tải tuần tự từng mảnh chậm gấp ~3 lần')
  assert.ok(concurrentFragmentsFor(true) > concurrentFragmentsFor(false))
})

test('không lấy chính link tệp/luồng làm Referer khi tải', async () => {
  const { downloadRefererFor, isDirectMediaUrl } = await import('./media.js')
  assert.equal(isDirectMediaUrl('https://cdn.x/hls/index.m3u8?token=1'), true)
  assert.equal(isDirectMediaUrl('https://cdn.x/v/seg-12.m4s'), true)
  assert.equal(isDirectMediaUrl('https://www.youtube.com/watch?v=abc'), false)
  assert.equal(isDirectMediaUrl('không phải url'), false)

  assert.equal(downloadRefererFor({ originalUrl: 'https://cdn.x/hls/index.m3u8' }), undefined)
  assert.equal(downloadRefererFor({ originalUrl: 'https://www.youtube.com/watch?v=abc' }), 'https://www.youtube.com/watch?v=abc')
  assert.equal(
    downloadRefererFor({ originalUrl: 'https://cdn.x/hls/index.m3u8', referer: 'https://phim.x/tap-1' }),
    'https://phim.x/tap-1',
    'luồng tìm thấy trong trang phim vẫn cần Referer là trang đó'
  )
})
