import { Component } from 'react'

/**
 * Chặn lỗi hiển thị trong một khu vực của giao diện.
 *
 * Trước đây không có lớp chặn nào: một dữ liệu lạ (vd. tiêu đề không phải chuỗi) làm
 * lỗi khi vẽ là React gỡ TOÀN BỘ cây giao diện — cửa sổ trắng trơn, trông như app đã
 * crash, phải tắt mở lại. Nay chỉ khu vực lỗi hiện thông báo kèm nút hiển thị lại.
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    console.error(`[${this.props.name || 'UI'}] Lỗi hiển thị:`, error, info?.componentStack)
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children
    return (
      <div className="ui-crash-box" role="alert">
        <strong>{this.props.title || 'Khu vực này gặp lỗi hiển thị'}</strong>
        <p>Tệp đang tải vẫn tiếp tục được lưu ở nền. Bấm “Hiển thị lại” để tiếp tục dùng.</p>
        <details>
          <summary>Chi tiết lỗi</summary>
          <pre>{String(error?.stack || error)}</pre>
        </details>
        <button type="button" className="minimal-small-btn" onClick={() => this.setState({ error: null })}>
          Hiển thị lại
        </button>
      </div>
    )
  }
}
