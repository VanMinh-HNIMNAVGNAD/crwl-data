/**
 * Chèn văn bản vào ô nhập đang focus như thể người dùng gõ vào, nhờ vậy Ctrl+Z
 * vẫn hoàn tác được (gán thẳng `value` qua React thì mất lịch sử hoàn tác).
 * @returns false nếu trình duyệt không hỗ trợ — nơi gọi tự cập nhật state.
 */
export function insertTextAtCursor(text) {
  try {
    return document.execCommand('insertText', false, text)
  } catch {
    return false
  }
}
