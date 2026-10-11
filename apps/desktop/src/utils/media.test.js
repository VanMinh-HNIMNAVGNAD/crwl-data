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
