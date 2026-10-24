import { memo, useLayoutEffect, useRef, useState } from 'react'
import { displayLink, extractLinks, newLinksOnly } from '../utils/links'
import { insertTextAtCursor } from '../utils/textInput'

const STATUS_GLYPH = { ok: '✓', error: '✕' }

// Nhãn ngắn cuối dòng của danh sách gọn cho các dòng sẽ bị bỏ qua / bị lỗi
const ROW_TAG = { duplicate: 'trùng', overflow: 'vượt giới hạn', invalid: 'không hợp lệ' }

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
 * - Khi không gõ (và lúc đang phân tích) khung hiện dạng DANH SÁCH GỌN: mỗi link
 *   đúng một dòng "tên miền + đường dẫn…", không gãy dòng — 10 link dài không còn
 *   chiếm hết chỗ của phần kết quả bên dưới. Bấm vào một dòng để sửa.
 */
function LinkListEditor({
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
  const [isEditing, setIsEditing] = useState(false)
  // Dòng cần chọn sau khi chuyển từ danh sách gọn sang khung sửa (-1 = con trỏ cuối)
  const pendingLine = useRef(null)

  // Bắt đầu phân tích (Ctrl+Enter ngay trong khung) thì thoát chế độ sửa: textarea bị
  // gỡ khỏi trang mà không có sự kiện blur, nên phân tích xong khung vẫn kẹt ở chế độ sửa.
  const [wasDisabled, setWasDisabled] = useState(disabled)
  if (disabled !== wasDisabled) {
    setWasDisabled(disabled)
    if (disabled) setIsEditing(false)
  }

  // Lối dự phòng khi trình duyệt không hỗ trợ execCommand: đặt lại con trỏ sau
  // khi React đã render giá trị mới.
  useLayoutEffect(() => {
    const pos = pendingCaret.current
    if (pos == null || !textareaRef.current) return
    pendingCaret.current = null
    textareaRef.current.setSelectionRange(pos, pos)
  }, [value, textareaRef])

  const lines = value.split('\n')
  const hasContent = analysis.rows.some((row) => row.kind !== 'empty')
  // Đang phân tích (khoá sửa) thì luôn hiện danh sách gọn để theo dõi ✓ / ✕ từng link
  const isCompact = hasContent && (!isEditing || disabled)

  // Bôi đen dòng `index` trong textarea; ngoài phạm vi thì đặt con trỏ ở cuối
  const focusLine = (textarea, index) => {
    textarea.focus()
    if (index < 0 || index >= lines.length) {
      textarea.setSelectionRange(value.length, value.length)
      return
    }
    const start = lines.slice(0, index).reduce((sum, line) => sum + line.length + 1, 0)
    textarea.setSelectionRange(start, start + lines[index].length)
  }

  useLayoutEffect(() => {
    const line = pendingLine.current
    if (line == null || isCompact || !textareaRef.current) return
    pendingLine.current = null
    focusLine(textareaRef.current, line)
  })

  const startEditing = (line = -1) => {
    if (disabled) return
    pendingLine.current = line
    setIsEditing(true)
  }

  const removeLine = (index) => {
    const next = lines.filter((_, i) => i !== index).join('\n')
    onChange(next)
  }

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
    focusLine(textarea, index)
  }

  if (isCompact) {
    return (
      <div
        className={`link-editor link-editor-compact ${disabled ? 'is-disabled' : ''}`}
        role="list"
        aria-label="Danh sách liên kết — bấm một dòng để sửa"
        tabIndex={disabled ? -1 : 0}
        onClick={() => startEditing(-1)}
        onKeyDown={(e) => {
          if (e.target !== e.currentTarget) return
          if (e.key === 'Enter' || e.key === 'F2') {
            e.preventDefault()
            startEditing(-1)
          }
        }}
      >
        {analysis.rows.map((row) => {
          if (row.kind === 'empty') return null
          const status = row.kind === 'link' ? statusByKey[row.key] : null
          const { label, title } = gutterInfo(row, status, maxLinks)
          const { host, rest } = displayLink(row.value)
          const tag = status?.state === 'error' ? status.note || 'lỗi' : ROW_TAG[row.kind]
          return (
            <div
              key={row.index}
              role="listitem"
              className={`lc-row le-${row.kind}${status ? ` le-status-${status.state}` : ''}`}
              title={`${row.value}\n${title}`}
              onClick={(e) => {
                e.stopPropagation()
                startEditing(row.index)
              }}
            >
              <span className="lc-num">{label}</span>
              <span className="le-status lc-status">{status ? STATUS_GLYPH[status.state] || '' : ''}</span>
              <span className="lc-link">
                {host && <span className="lc-host">{host}</span>}
                <span className="lc-rest">{rest}</span>
              </span>
              {tag && <span className="lc-tag">{tag}</span>}
              {!disabled && (
                <button
                  type="button"
                  className="lc-remove"
                  title="Bỏ dòng này khỏi danh sách"
                  aria-label={`Bỏ ${row.value}`}
                  onClick={(e) => {
                    e.stopPropagation()
                    removeLine(row.index)
                  }}
                >
                  ×
                </button>
              )}
            </div>
          )
        })}
      </div>
    )
  }

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
          onFocus={() => setIsEditing(true)}
          // Rời khung (bấm chỗ khác trong app) thì thu về danh sách gọn. Chuyển sang cửa
          // sổ khác để copy link thì KHÔNG: quay lại vẫn Ctrl+V tiếp được ngay.
          onBlur={() => {
            if (document.hasFocus()) setIsEditing(false)
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
              e.preventDefault()
              onSubmit?.()
            } else if (e.key === 'Escape') {
              e.currentTarget.blur()
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

// Lớp gương vẽ lại MỌI dòng: memo để sự kiện tiến trình tải ở khung cha không kéo
// theo việc vẽ lại danh sách link (LinkDownloader truyền callback ổn định).
export default memo(LinkListEditor)
