/**
 * Dựng nội dung cho hệ thống thông báo (components/ToastStack).
 * Tách khỏi file component để giữ Fast Refresh của Vite hoạt động.
 */
import { openDownloadFolder } from '../services/api'
import { describeError, fileNameOf, shortenMiddle } from './messages'

const BASE_DURATION = { success: 4500, info: 3200, warning: 6000, error: 7000 }

/**
 * Chuẩn hoá đầu vào của `showToast`: chuỗi (cách gọi cũ) hoặc
 * `{ type, title, message, detail, action: { label, onClick } }`.
 */
export function normalizeToast(input) {
  const toast = typeof input === 'string' ? { title: input } : { ...(input || {}) }
  if (!toast.title && !toast.message) return null
  const type = BASE_DURATION[toast.type] ? toast.type : 'info'
  const textLength = `${toast.title || ''}${toast.message || ''}`.length
  return {
    type,
    title: toast.title || toast.message,
    message: toast.title ? toast.message || '' : '',
    detail: toast.detail || '',
    action: toast.action?.label && typeof toast.action.onClick === 'function' ? toast.action : null,
    // Câu dài cần thêm thời gian đọc, nhưng không để thông báo treo quá lâu
    duration: Math.min(12000, BASE_DURATION[type] + textLength * 30),
    key: `${type}|${toast.title || ''}|${toast.message || ''}`,
  }
}

/** Thông báo lỗi ngắn gọn; nguyên văn lỗi nằm sau nút "Chi tiết". */
export function errorToast(err, fallback) {
  return { type: 'error', ...describeError(err, fallback) }
}

/** Nút "Mở thư mục" chứa tệp / thư mục vừa lưu. */
export function openFolderAction(path) {
  if (!path) return null
  return {
    label: 'Mở thư mục',
    onClick: () => {
      openDownloadFolder(path).catch((err) => console.warn('Không mở được thư mục:', err))
    },
  }
}

/**
 * Thông báo "đã lưu": chỉ hiện TÊN tệp (đường dẫn đầy đủ dài và khó đọc),
 * kèm nút mở thư mục chứa nó.
 */
export function savedToast(path, { title = 'Đã tải xong', name, message, type = 'success' } = {}) {
  return {
    type,
    title,
    message: message ?? shortenMiddle(name || fileNameOf(path), 64),
    action: openFolderAction(path),
  }
}
