import { useCallback, useLayoutEffect, useRef } from 'react'

/**
 * Hàm có danh tính KHÔNG đổi giữa các lần vẽ nhưng luôn gọi phiên bản mới nhất của `fn`.
 *
 * Dùng cho callback truyền xuống các ô đã `memo` (lưới tài khoản, album, danh sách
 * nhiều link): nếu mỗi lần vẽ lại tạo hàm mới thì `memo` vô tác dụng, và mỗi sự kiện
 * tiến trình tải lại vẽ lại toàn bộ hàng nghìn ô.
 */
export function useStableCallback(fn) {
  const ref = useRef(fn)
  useLayoutEffect(() => {
    ref.current = fn
  })
  return useCallback((...args) => ref.current(...args), [])
}
