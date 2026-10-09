/**
 * Phân tích danh sách liên kết của khung "Nhiều link".
 *
 * Số thứ tự hiển thị ở lề trái (không chèn vào nội dung), nên việc đánh số không
 * bao giờ làm thay đổi liên kết người dùng đã dán. Module thuần JS — chạy test
 * bằng `node --test`.
 */

export const MAX_BATCH_LINKS = 10

const SCHEME_RE = /^https?:\/\//i
const SCHEME_ANYWHERE_RE = /https?:\/\//i
// tên-miền.tld[:cổng][/đường-dẫn] — liên kết dán thiếu "https://" (vd. youtu.be/abc)
const BARE_DOMAIN_RE = /^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}(?::\d{2,5})?(?:[/?#]\S*)?$/i
// Trong một đoạn văn bản tự do thì chỉ nhận tên miền trần khi có đường dẫn,
// để "v.v." hay "file.txt" không bị coi là liên kết.
const BARE_WITH_PATH_RE = /^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}(?::\d{2,5})?\/\S*$/i

/** Một dòng có phải là MỘT liên kết hợp lệ (không chứa khoảng trắng) không. */
export function isLikelyLink(text) {
  const value = String(text || '').trim()
  if (!value || /\s/.test(value)) return false
  if (SCHEME_RE.test(value)) {
    try {
      const { hostname } = new URL(value)
      return hostname.includes('.') || hostname === 'localhost'
    } catch {
      return false
    }
  }
  return BARE_DOMAIN_RE.test(value)
}

/** Thêm "https://" cho liên kết thiếu giao thức — chỉ dùng khi GỬI đi, không sửa nội dung khung nhập. */
export function normalizeLink(link) {
  const value = String(link || '').trim()
  return SCHEME_RE.test(value) ? value : `https://${value}`
}

/** Khoá so sánh trùng lặp: bỏ giao thức, "www.", dấu "/" cuối và phần "#...". */
export function linkKey(link) {
  const value = normalizeLink(link)
  try {
    const u = new URL(value)
    const host = u.hostname.toLowerCase().replace(/^www\./, '')
    const port = u.port ? `:${u.port}` : ''
    return `${host}${port}${u.pathname.replace(/\/+$/, '')}${u.search}`
  } catch {
    return value.toLowerCase()
  }
}

const CLOSERS = { ')': '(', ']': '[', '}': '{', '>': '<' }
const count = (text, ch) => text.split(ch).length - 1

/** Bỏ dấu câu dính ở cuối liên kết khi dán từ một đoạn chat: "https://x.com/a)." */
function trimTrailing(url) {
  let value = url
  for (;;) {
    const last = value.slice(-1)
    if (/[.,;:!?…'"»]/.test(last)) {
      value = value.slice(0, -1)
      continue
    }
    // Ngoặc đóng chỉ là dấu câu khi không có ngoặc mở tương ứng (giữ "Foo_(bar)")
    if (CLOSERS[last] && count(value, last) > count(value, CLOSERS[last])) {
      value = value.slice(0, -1)
      continue
    }
    return value
  }
}

/**
 * Tách các liên kết dính liền nhau ("https://a.com/1https://b.com/2", "a,https://b").
 * Không tách liên kết lồng trong tham số ("...?u=https://...").
 */
function splitGlued(token) {
  const parts = []
  const re = /https?:\/\//gi
  let cut = 0
  let m
  while ((m = re.exec(token))) {
    if (m.index === 0) continue
    const prev = token[m.index - 1]
    if ('=%/:?&#'.includes(prev)) continue
    parts.push(token.slice(cut, m.index).replace(/[,;|]+$/, ''))
    cut = m.index
  }
  parts.push(token.slice(cut))
  return parts.filter(Boolean)
}

/**
 * Lấy mọi liên kết trong một đoạn văn bản bất kỳ (tin nhắn chat, danh sách có
 * đánh số "1. https://...", link cách nhau bằng dấu phẩy/khoảng trắng...).
 */
export function extractLinks(text) {
  const out = []
  for (const token of String(text || '').split(/[\s<>"'`]+/)) {
    if (!token) continue
    for (const part of splitGlued(token)) {
      const at = part.search(SCHEME_ANYWHERE_RE)
      let piece = at > 0 ? part.slice(at) : part
      piece = trimTrailing(piece.replace(/^[([{<'"«]+/, ''))
      if (at >= 0 ? isLikelyLink(piece) : BARE_WITH_PATH_RE.test(piece)) out.push(piece)
    }
  }
  return out
}

/**
 * Phân tích từng dòng của khung nhập.
 *
 * - `link`: liên kết hợp lệ thứ `number` (sẽ được phân tích)
 * - `duplicate`: trùng với liên kết số `duplicateOf` — bỏ qua
 * - `overflow`: hợp lệ nhưng vượt quá `max` liên kết mỗi lượt — bỏ qua
 * - `invalid`: không phải liên kết — bỏ qua
 * - `empty`: dòng trống
 */
export function analyzeLinkLines(text, max = MAX_BATCH_LINKS) {
  const seen = new Map()
  const links = []
  const counts = { valid: 0, invalid: 0, duplicate: 0, overflow: 0 }

  const rows = String(text ?? '')
    .split('\n')
    .map((raw, index) => {
      const value = raw.trim()
      if (!value) return { index, kind: 'empty', value }
      if (!isLikelyLink(value)) {
        counts.invalid++
        return { index, kind: 'invalid', value }
      }
      const key = linkKey(value)
      if (seen.has(key)) {
        counts.duplicate++
        return { index, kind: 'duplicate', value, key, duplicateOf: seen.get(key) }
      }
      const number = seen.size + 1
      seen.set(key, number)
      if (number > max) {
        counts.overflow++
        return { index, kind: 'overflow', value, key, number }
      }
      links.push(value)
      return { index, kind: 'link', value, key, number }
    })

  counts.valid = links.length
  return { rows, links, counts }
}

/**
 * Liên kết trong `incoming` chưa có trong `existingText` (và không trùng nhau).
 * @returns `{ links, skipped }`
 */
export function newLinksOnly(existingText, incoming) {
  const keys = new Set(
    String(existingText || '')
      .split('\n')
      .map((line) => line.trim())
      .filter(isLikelyLink)
      .map(linkKey)
  )
  const links = []
  let skipped = 0
  for (const link of incoming) {
    const key = linkKey(link)
    if (keys.has(key)) {
      skipped++
      continue
    }
    keys.add(key)
    links.push(link)
  }
  return { links, skipped }
}

/**
 * Ghép thêm liên kết vào cuối nội dung khung nhập (mỗi link một dòng), bỏ link đã có.
 * @returns `{ text, added, skipped }`
 */
export function mergeLinks(existingText, incoming) {
  const existing = String(existingText || '')
  const { links, skipped } = newLinksOnly(existing, incoming)
  if (!links.length) return { text: existing, added: 0, skipped }
  const base = existing.replace(/\s+$/, '')
  return { text: base ? `${base}\n${links.join('\n')}` : links.join('\n'), added: links.length, skipped }
}

/** Nội dung đã dọn: chỉ giữ liên kết hợp lệ, không trùng, mỗi link một dòng. */
export function cleanLinkText(text) {
  const { rows } = analyzeLinkLines(text, Infinity)
  return rows
    .filter((row) => row.kind === 'link')
    .map((row) => row.value)
    .join('\n')
}
