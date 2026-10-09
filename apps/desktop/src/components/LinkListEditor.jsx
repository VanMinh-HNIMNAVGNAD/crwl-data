import { useLayoutEffect, useRef } from 'react'
import { extractLinks, newLinksOnly } from '../utils/links'
import { insertTextAtCursor } from '../utils/textInput'

const STATUS_GLYPH = { ok: '✓', error: '✕' }

function gutterInfo(row, status, maxLinks) {
  switch (row.kind) {
    case 'link': {
      const base = `Liên kết #${row.number}`
      if (status?.state === 'running') return { label: row.number, title: `${base} · đang phân tích...` }
      if (status?.state === 'ok') return { label: row.number, title: `${base} · đã phân tích: ${status.note || 'xong'}` }
      if (status?.state === 'error') return { label: row.number, title: `${base} · lỗi: ${status.note || 'không phân tích được'}` }
      return { label: row.number, title: `${base} — bấm để chọn dòng` }
    }
    case 'duplicate':
      return { label: '=', title: `Trùng với liên kết #${row.duplicateOf} — sẽ bỏ qua` }
    case 'overflow':
      return { label: row.number, title: `Vượt quá ${maxLinks} liên kết mỗi lượt — sẽ bỏ qua` }
    case 'invalid':
      return { label: '!', title: 'Không phải liên kết hợp lệ — sẽ bỏ qua' }
    default:
      return { label: '', title: '' }
  }
}

/**
 * Khung nhập nhiều liên kết.
 *
 * - Số thứ tự vẽ ở lề trái bằng một lớp "gương" nằm SAU textarea, render đúng
 *   nội dung đó với cùng font / cỡ chữ / cách xuống dòng — nên số luôn khớp với
 *   dòng (kể cả link dài bị gãy dòng) mà không chèn ký tự nào vào nội dung.
 * - Lớp gương nằm trong luồng bình thường và quyết định chiều cao, textarea phủ
 *   lên trên: khung tự giãn theo số dòng tới giới hạn rồi mới cuộn (cả hai cuộn
 *   chung nên không cần đồng bộ vị trí cuộn).
 * - Dán một đoạn văn bản chứa link: tự tách thành mỗi link một dòng, bỏ link trùng.
 */
export default function LinkListEditor({
  value,
  onChange,
  analysis,
  statusByKey = {},
  maxLinks,
  disabled = false,
  placeholder,
  inputRef,
  onLinksPasted,
  onSubmit,
}) {
  const localRef = useRef(null)
  const textareaRef = inputRef || localRef
  const pendingCaret = useRef(null)

  // Lối dự phòng khi trình duyệt không hỗ trợ execCommand: đặt lại con trỏ sau
  // khi React đã render giá trị mới.
  useLayoutEffect(() => {
    const pos = pendingCaret.current
    if (pos == null || !textareaRef.current) return
    pendingCaret.current = null
    textareaRef.current.setSelectionRange(pos, pos)
  }, [value, textareaRef])

  const insertText = (textarea, text) => {
    textarea.focus()
    if (!insertTextAtCursor(text)) {
      const { selectionStart, selectionEnd } = textarea
      pendingCaret.current = selectionStart + text.length
      onChange(value.slice(0, selectionStart) + text + value.slice(selectionEnd))
    }
  }

  const handlePaste = (e) => {
    const pasted = e.clipboardData?.getData('text/plain') ?? ''
    const found = extractLinks(pasted)
    // Không có link nào (vd. dán một đoạn đường dẫn để sửa link): dán như bình thường
    if (found.length === 0) return
    e.preventDefault()

    const textarea = e.currentTarget
    const { selectionStart, selectionEnd } = textarea
    const before = value.slice(0, selectionStart)
    const after = value.slice(selectionEnd)
    const { links, skipped } = newLinksOnly(`${before}\n${after}`, found)
    if (links.length > 0) {
      // Mỗi link một dòng riêng, không dính vào nội dung trước/sau con trỏ
      const lead = before && !before.endsWith('\n') ? '\n' : ''
      const trail = after && !after.startsWith('\n') ? '\n' : ''
      insertText(textarea, `${lead}${links.join('\n')}${trail}`)
    }
    onLinksPasted?.({ added: links.length, skipped })
  }

  const selectLine = (e, index) => {
    e.preventDefault()
    const textarea = textareaRef.current
    if (!textarea || disabled) return
    const lines = value.split('\n')
    const start = lines.slice(0, index).reduce((sum, line) => sum + line.length + 1, 0)
    textarea.focus()
    textarea.setSelectionRange(start, start + lines[index].length)
  }

  const lines = value.split('\n')

  return (
    <div className={`link-editor ${disabled ? 'is-disabled' : ''}`}>
      <div className="link-editor-body">
        <div className="link-editor-mirror" aria-hidden="true">
          {lines.map((line, index) => {
            const row = analysis.rows[index] || { kind: 'empty' }
            const status = row.kind === 'link' ? statusByKey[row.key] : null
            const { label, title } = gutterInfo(row, status, maxLinks)
            return (
              <div key={index} className={`le-line le-${row.kind}${status ? ` le-status-${status.state}` : ''}`}>
                {row.kind !== 'empty' && (
                  <span className="le-gutter" title={title} onMouseDown={(e) => selectLine(e, index)}>
                    <span className="le-num">{label}</span>
                    {status && <span className="le-status">{STATUS_GLYPH[status.state] || ''}</span>}
                  </span>
                )}
                {/* Khoảng trắng độ rộng 0 giữ chiều cao cho dòng trống */}
                {line || '​'}
              </div>
            )
          })}
        </div>
        <textarea
          ref={textareaRef}
          className="link-editor-input"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onPaste={handlePaste}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
              e.preventDefault()
              onSubmit?.()
            }
          }}
          placeholder={placeholder}
          disabled={disabled}
          spellCheck={false}
          autoComplete="off"
          autoCorrect="off"
          autoCapitalize="off"
          aria-label="Danh sách liên kết, mỗi liên kết một dòng"
        />
      </div>
    </div>
  )
}
