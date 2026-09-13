import { useEffect, useRef, useState, useCallback } from 'react'
import { IconShield, IconShieldCheck, IconRefresh } from './Icons'
import { verifyTurnstileTokenWithBackend } from '../services/api'
import './TurnstileGate.css'

const SCRIPT_ID = 'cf-turnstile-script'
const TURNSTILE_URL = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit'

// Site key kiểm thử của Cloudflare (luôn pass) làm fallback nếu chưa cấu hình
const DEFAULT_TEST_SITE_KEY = '1x00000000000000000000AA'

export default function TurnstileGate({ onVerify }) {
  const containerRef = useRef(null)
  const widgetIdRef = useRef(null)
  const [status, setStatus] = useState('loading') // 'loading' | 'ready' | 'verifying' | 'success' | 'error' | 'expired'
  const [errorMsg, setErrorMsg] = useState('')
  const [isExiting, setIsExiting] = useState(false)

  const siteKey = import.meta.env.VITE_TURNSTILE_SITE_KEY || DEFAULT_TEST_SITE_KEY

  const removeCurrentWidget = useCallback(() => {
    if (widgetIdRef.current !== null && window.turnstile) {
      try {
        window.turnstile.remove(widgetIdRef.current)
      } catch (err) {
        void err
      }
      widgetIdRef.current = null
    }
  }, [])

  const renderWidget = useCallback(() => {
    if (!containerRef.current || !window.turnstile) return

    removeCurrentWidget()

    try {
      setStatus('ready')
      setErrorMsg('')

      const id = window.turnstile.render(containerRef.current, {
        sitekey: siteKey,
        theme: 'dark',
        size: 'normal',
        action: 'page_access',
        callback: async (token) => {
          setStatus('verifying')
          const backendCheck = await verifyTurnstileTokenWithBackend(token)
          if (!backendCheck.success) {
            setStatus('error')
            setErrorMsg(backendCheck.message || 'Xác thực với máy chủ thất bại.')
            return
          }

          setStatus('success')
          // Hiệu ứng hoàn tất xác minh trước khi mở khoá giao diện
          setTimeout(() => {
            setIsExiting(true)
            setTimeout(() => {
              onVerify(token)
            }, 350)
          }, 600)
        },
        'error-callback': (errorCode) => {
          console.warn('[Turnstile] Verification error:', errorCode)
          setStatus('error')
          setErrorMsg(
            errorCode === '110200' || errorCode === '400000'
              ? 'Site Key không hợp lệ hoặc tên miền chưa được cấp phép trong Cloudflare.'
              : `Không thể hoàn tất xác thực Turnstile (${errorCode || 'mạng gián đoạn'}).`
          )
        },
        'expired-callback': () => {
          console.warn('[Turnstile] Token expired')
          setStatus('expired')
          if (widgetIdRef.current !== null && window.turnstile) {
            window.turnstile.reset(widgetIdRef.current)
          }
        },
      })

      widgetIdRef.current = id
    } catch (err) {
      console.error('[Turnstile] Error rendering widget:', err)
      setStatus('error')
      setErrorMsg('Không thể khởi tạo widget Cloudflare Turnstile.')
    }
  }, [siteKey, onVerify, removeCurrentWidget])

  useEffect(() => {
    let checkInterval = null
    let timeout = null

    const init = () => {
      if (window.turnstile) {
        renderWidget()
        return
      }

      let script = document.getElementById(SCRIPT_ID)
      if (!script) {
        script = document.createElement('script')
        script.id = SCRIPT_ID
        script.src = TURNSTILE_URL
        script.async = true
        script.defer = true
        document.head.appendChild(script)
      }

      checkInterval = setInterval(() => {
        if (window.turnstile) {
          clearInterval(checkInterval)
          renderWidget()
        }
      }, 100)

      timeout = setTimeout(() => {
        clearInterval(checkInterval)
        if (!window.turnstile) {
          setStatus('error')
          setErrorMsg('Không thể tải thư viện Cloudflare Turnstile. Vui lòng kiểm tra kết nối mạng hoặc tiện ích chặn quảng cáo.')
        }
      }, 10000)
    }

    // Trì hoãn một tick để tránh trigger setState đồng bộ ngay trong render phase
    const timer = setTimeout(init, 0)

    return () => {
      clearTimeout(timer)
      if (checkInterval) clearInterval(checkInterval)
      if (timeout) clearTimeout(timeout)
      removeCurrentWidget()
    }
  }, [renderWidget, removeCurrentWidget])

  const handleRetry = () => {
    setStatus('loading')
    setErrorMsg('')
    renderWidget()
  }

  return (
    <div className={`turnstile-overlay ${isExiting ? 'is-exiting' : ''}`}>
      <div className="turnstile-card">
        <div className="turnstile-header">
          <div
            className={`turnstile-shield-badge ${
              status === 'success' ? 'is-success' : status === 'error' ? 'is-error' : ''
            }`}
          >
            {status === 'success' ? (
              <IconShieldCheck className="w-8 h-8 text-emerald-400" />
            ) : (
              <IconShield className="w-8 h-8" />
            )}
            <div className="turnstile-glow-ring" />
          </div>

          <h2 className="turnstile-title">
            {status === 'success' ? 'Xác minh thành công' : 'Bảo mật hệ thống'}
          </h2>
          <p className="turnstile-subtitle">
            {status === 'success'
              ? 'Trình duyệt đã được xác thực an toàn. Đang chuyển vào ứng dụng...'
              : 'Vui lòng hoàn tất xác thực Cloudflare Turnstile bên dưới để tiếp tục truy cập.'}
          </p>
        </div>

        {/* Container hiển thị Cloudflare Turnstile Widget */}
        <div className="turnstile-widget-container">
          {(status === 'loading' || status === 'verifying') && (
            <div className="turnstile-loading-placeholder">
              <div className="turnstile-spinner" />
              <span>
                {status === 'verifying'
                  ? 'Đang xác thực với máy chủ bảo mật...'
                  : 'Đang tải cổng bảo mật Cloudflare...'}
              </span>
            </div>
          )}

          {status === 'success' && (
            <div className="turnstile-success-notice">
              <IconShieldCheck className="w-5 h-5 text-emerald-400" />
              <span>Phiên làm việc đã được bảo vệ</span>
            </div>
          )}

          <div
            ref={containerRef}
            style={{ display: status === 'success' ? 'none' : 'block' }}
          />
        </div>

        {/* Thông báo lỗi & nút thử lại */}
        {status === 'error' && (
          <div className="turnstile-error-notice">
            <span>{errorMsg || 'Xác thực không thành công.'}</span>
            <button type="button" className="turnstile-retry-btn" onClick={handleRetry}>
              <IconRefresh className="w-4 h-4" />
              <span>Thử lại</span>
            </button>
          </div>
        )}

        <div className="turnstile-footer">
          <span className="turnstile-meta-tag">
            <IconShield className="w-3.5 h-3.5 text-blue-400" />
            Được bảo vệ bởi Cloudflare Turnstile
          </span>
          <span className="turnstile-f5-hint">
            Phiên bảo mật đặt lại mỗi khi <kbd>F5</kbd> hoặc tải lại trang
          </span>
        </div>
      </div>
    </div>
  )
}
