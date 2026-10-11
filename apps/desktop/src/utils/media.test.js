import test from 'node:test'
import assert from 'node:assert/strict'
import { hasDownloadableContent, noDownloadableContentReason } from './media.js'

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
