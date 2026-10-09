import test from 'node:test'
import assert from 'node:assert/strict'
import {
  analyzeLinkLines,
  cleanLinkText,
  extractLinks,
  isLikelyLink,
  linkKey,
  mergeLinks,
  normalizeLink,
} from './links.js'

test('lấy liên kết từ đoạn chat, danh sách đánh số, link dính liền nhau', () => {
  assert.deepEqual(
    extractLinks('Xem 2 video này nhé: https://youtu.be/abc, https://www.tiktok.com/@u/video/123. Cảm ơn!'),
    ['https://youtu.be/abc', 'https://www.tiktok.com/@u/video/123']
  )
  assert.deepEqual(extractLinks('1. https://a.com/x\n2) https://b.com/y'), ['https://a.com/x', 'https://b.com/y'])
  assert.deepEqual(extractLinks('https://a.com/1https://b.com/2'), ['https://a.com/1', 'https://b.com/2'])
  assert.deepEqual(extractLinks('https://a.com/1,https://b.com/2'), ['https://a.com/1', 'https://b.com/2'])
  assert.deepEqual(extractLinks('Link:https://a.com/x'), ['https://a.com/x'])
})

test('không tách liên kết lồng trong tham số, giữ ngoặc thuộc về liên kết', () => {
  assert.deepEqual(extractLinks('https://www.google.com/url?q=https://example.com/x'), [
    'https://www.google.com/url?q=https://example.com/x',
  ])
  assert.deepEqual(extractLinks('(https://en.wikipedia.org/wiki/Foo_(bar))'), ['https://en.wikipedia.org/wiki/Foo_(bar)'])
})

test('tên miền trần chỉ được nhận khi có đường dẫn', () => {
  assert.deepEqual(extractLinks('link youtu.be/abc đây'), ['youtu.be/abc'])
  assert.deepEqual(extractLinks('v.v. và file.txt'), [])
  assert.deepEqual(extractLinks('không có gì'), [])
})

test('isLikelyLink / normalizeLink / linkKey', () => {
  assert.ok(isLikelyLink('https://www.youtube.com/watch?v=1'))
  assert.ok(isLikelyLink('youtu.be/abc'))
  assert.ok(isLikelyLink('http://localhost:3000/x'))
  assert.ok(!isLikelyLink('hello'))
  assert.ok(!isLikelyLink('https://'))
  assert.ok(!isLikelyLink('xem https://a.com/x'))

  assert.equal(normalizeLink('youtu.be/x'), 'https://youtu.be/x')
  assert.equal(normalizeLink(' https://a.com/x '), 'https://a.com/x')
  assert.equal(linkKey('http://www.YouTube.com/watch?v=1'), linkKey('https://youtube.com/watch?v=1'))
  assert.equal(linkKey('https://a.com/x/'), linkKey('https://a.com/x#frag'))
  assert.notEqual(linkKey('https://a.com/x?v=1'), linkKey('https://a.com/x?v=2'))
})

test('đánh số chỉ tính liên kết hợp lệ, không trùng; nội dung từng dòng giữ nguyên', () => {
  const text = 'https://a.com/1\n\nkhông phải link\nhttps://a.com/1/\n  https://b.com/2  '
  const { rows, links, counts } = analyzeLinkLines(text)
  assert.deepEqual(
    rows.map((r) => [r.kind, r.number ?? r.duplicateOf ?? null]),
    [['link', 1], ['empty', null], ['invalid', null], ['duplicate', 1], ['link', 2]]
  )
  assert.deepEqual(links, ['https://a.com/1', 'https://b.com/2'])
  assert.deepEqual(counts, { valid: 2, invalid: 1, duplicate: 1, overflow: 0 })
  assert.equal(rows.length, text.split('\n').length)
})

test('liên kết vượt giới hạn được đánh dấu, không bị bỏ trong im lặng', () => {
  const text = Array.from({ length: 12 }, (_, i) => `https://a.com/${i + 1}`).join('\n')
  const { rows, links, counts } = analyzeLinkLines(text, 10)
  assert.equal(links.length, 10)
  assert.equal(counts.overflow, 2)
  assert.deepEqual(rows.slice(-2).map((r) => [r.kind, r.number]), [['overflow', 11], ['overflow', 12]])
})

test('ghép thêm liên kết bỏ link đã có / trùng nhau', () => {
  const res = mergeLinks('https://a.com/1\n', ['https://a.com/1', 'https://b.com/2', 'b.com/2'])
  assert.deepEqual(res, { text: 'https://a.com/1\nhttps://b.com/2', added: 1, skipped: 2 })
  assert.deepEqual(mergeLinks('', ['https://a.com/1']), { text: 'https://a.com/1', added: 1, skipped: 0 })
  assert.deepEqual(mergeLinks('x', []), { text: 'x', added: 0, skipped: 0 })
})

test('dọn dẹp giữ đúng các liên kết, bỏ dòng rác và dòng trùng', () => {
  assert.equal(cleanLinkText('rác\nhttps://a.com/1\n\nhttps://a.com/1\n youtu.be/x '), 'https://a.com/1\nyoutu.be/x')
})
