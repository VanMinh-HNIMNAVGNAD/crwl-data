import test from 'node:test'
import assert from 'node:assert/strict'
import { pendingToolUpdates, toolUpdateKey, toolUpdateToast } from './toolUpdates.js'

const report = {
  ytdlp: { name: 'yt-dlp', installed: '2026.09.01', latest: '2026.10.10', updateAvailable: true },
  galleryDl: { name: 'gallery-dl', installed: '1.32.12', latest: '1.32.12', updateAvailable: false },
  checkedAt: 1760000000,
}

test('chỉ liệt kê công cụ thật sự có bản mới', () => {
  assert.deepEqual(pendingToolUpdates(report).map((t) => t.name), ['yt-dlp'])
  assert.deepEqual(pendingToolUpdates(null), [])
  // Chưa tra được PyPI (latest null) thì không báo
  assert.deepEqual(pendingToolUpdates({ ytdlp: { name: 'yt-dlp', updateAvailable: true, latest: null } }), [])
})

test('khoá đổi khi có bản mới hơn nữa, để báo lại đúng một lần', () => {
  const first = toolUpdateKey(pendingToolUpdates(report))
  assert.equal(first, 'yt-dlp@2026.10.10')
  const newer = { ...report, ytdlp: { ...report.ytdlp, latest: '2026.10.20' } }
  assert.notEqual(toolUpdateKey(pendingToolUpdates(newer)), first)
})

test('thông báo nêu phiên bản và có nút mở Công cụ', () => {
  let opened = false
  const toast = toolUpdateToast(pendingToolUpdates(report), () => { opened = true })
  assert.equal(toast.title, 'Có bản mới của yt-dlp')
  assert.match(toast.message, /2026\.10\.10 \(đang dùng 2026\.09\.01\)/)
  toast.action.onClick()
  assert.ok(opened)

  const both = { ...report, galleryDl: { ...report.galleryDl, latest: '1.32.13', updateAvailable: true } }
  assert.equal(toolUpdateToast(pendingToolUpdates(both), () => {}).title, 'Có bản mới cho các công cụ tải')
})
