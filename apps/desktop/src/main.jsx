import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.jsx'
import ErrorBoundary from './components/ErrorBoundary.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    {/* Lưới an toàn cuối cùng: lỗi hiển thị ở thanh trên cùng / các hộp thoại không
        còn để lại một cửa sổ trắng trơn */}
    <ErrorBoundary name="App" title="Ứng dụng gặp lỗi hiển thị">
      <App />
    </ErrorBoundary>
  </StrictMode>,
)
