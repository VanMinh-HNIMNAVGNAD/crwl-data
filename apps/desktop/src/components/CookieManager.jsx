import { useState, useEffect, useRef } from 'react'
import { savePlatformCookies, getCookieStatus, deletePlatformCookies } from '../services/api'
import {
  IconInstagram,
  IconX,
  IconTikTok,
  IconReddit,
  IconSettings,
} from './Icons'

// ─────────────────────────────────────────────────────────────────────────────
// Platform configs (mirrored từ backend)
// ─────────────────────────────────────────────────────────────────────────────

const PLATFORMS = [
  {
    id: 'instagram',
    name: 'Instagram',
    icon: IconInstagram,
    domain: 'instagram.com',
    color: '#e1306c',
    gradientClass: 'ig-gradient',
    requiredKeys: ['sessionid'],
    optionalKeys: ['csrftoken', 'ds_user_id'],
    guide: [
      { step: 1, text: 'Mở Instagram.com trên trình duyệt và đăng nhập tài khoản' },
      { step: 2, text: 'Nhấn F12 → chọn tab "Application" (Chrome/Edge) hoặc "Storage" (Firefox)' },
      { step: 3, text: 'Bên trái: Cookies → https://www.instagram.com' },
      { step: 4, text: 'Tìm dòng "sessionid" → copy toàn bộ giá trị trong cột "Value"' },
      { step: 5, text: 'Hoặc copy toàn bộ: nhấp phải vào tên Cookie → Copy all as cURL → lấy phần sau -H "Cookie:"' },
    ],
    placeholder: 'sessionid=abc123def456...\ncsrftoken=xyz789...',
    hint: 'Cần nhất: sessionid. Dán cả dòng "key=value; key2=value2" đều được.',
  },
  {
    id: 'twitter',
    name: 'X (Twitter)',
    icon: IconX,
    domain: 'x.com',
    color: '#1d9bf0',
    gradientClass: 'x-gradient',
    requiredKeys: ['auth_token'],
    optionalKeys: ['ct0', 'guest_id'],
    guide: [
      { step: 1, text: 'Mở x.com trên trình duyệt và đăng nhập tài khoản' },
      { step: 2, text: 'Nhấn F12 → chọn tab "Application" (Chrome/Edge) hoặc "Storage" (Firefox)' },
      { step: 3, text: 'Bên trái: Cookies → https://x.com' },
      { step: 4, text: 'Tìm "auth_token" và "ct0" → copy giá trị của cả hai' },
      { step: 5, text: 'Dán vào ô bên dưới theo dạng: auth_token=VALUE; ct0=VALUE' },
    ],
    placeholder: 'auth_token=abc123...\nct0=def456...',
    hint: 'Cần nhất: auth_token. Nên thêm ct0 để tránh bị khóa.',
  },
  {
    id: 'tiktok',
    name: 'TikTok',
    icon: IconTikTok,
    domain: 'tiktok.com',
    color: '#fe2c55',
    gradientClass: 'tt-gradient',
    requiredKeys: ['sessionid'],
    optionalKeys: ['s_v_web_id', 'ttwid'],
    guide: [
      { step: 1, text: 'Mở tiktok.com và đăng nhập tài khoản' },
      { step: 2, text: 'F12 → Application → Cookies → https://www.tiktok.com' },
      { step: 3, text: 'Tìm và copy "sessionid"' },
    ],
    placeholder: 'sessionid=abc123...\ns_v_web_id=verify_...',
    hint: 'Cần nhất: sessionid',
  },
  {
    id: 'reddit',
    name: 'Reddit',
    icon: IconReddit,
    domain: 'reddit.com',
    color: '#ff4500',
    gradientClass: 'rd-gradient',
    requiredKeys: ['reddit_session'],
    optionalKeys: ['token_v2', 'csv'],
    guide: [
      { step: 1, text: 'Mở reddit.com và đăng nhập' },
      { step: 2, text: 'F12 → Application → Cookies → https://www.reddit.com' },
      { step: 3, text: 'Tìm và copy "reddit_session" hoặc "token_v2"' },
    ],
    placeholder: 'reddit_session=abc123...',
    hint: 'Cần nhất: reddit_session hoặc token_v2',
  },
  {
    id: 'custom',
    name: 'Tùy chỉnh',
    icon: IconSettings,
    domain: null,
    color: '#6366f1',
    gradientClass: 'custom-gradient',
    requiredKeys: [],
    optionalKeys: [],
    guide: [
      { step: 1, text: 'Nhập domain của website (ví dụ: pixiv.net, deviantart.com)' },
      { step: 2, text: 'F12 → Application → Cookies → chọn domain mong muốn' },
      { step: 3, text: 'Copy toàn bộ cookie string và dán vào ô bên dưới' },
    ],
    placeholder: 'key1=value1; key2=value2; ...',
    hint: 'Hỗ trợ mọi website. Nhớ nhập đúng domain.',
  },
]

// ─────────────────────────────────────────────────────────────────────────────
// CookieManager Modal
// ─────────────────────────────────────────────────────────────────────────────

export default function CookieManager({ isOpen, onClose }) {
  const [activeTab, setActiveTab] = useState('instagram')
  const [cookieInputs, setCookieInputs] = useState({})
  const [customDomain, setCustomDomain] = useState('')
  const [saving, setSaving] = useState(false)
  const [toast, setToast] = useState(null) // { type: 'success'|'error', msg }
  const [status, setStatus] = useState(null)
  const [confirmDelete, setConfirmDelete] = useState(null) // domain string
  const modalRef = useRef(null)

  const showToast = (type, msg) => {
    setToast({ type, msg })
    setTimeout(() => setToast(null), 3500)
  }

  const loadStatus = async () => {
    try {
      const s = await getCookieStatus()
      setStatus(s)
    } catch (err) {
      void err
    }
  }

  useEffect(() => {
    let active = true
    if (isOpen) {
      getCookieStatus()
        .then((s) => {
          if (active) setStatus(s)
        })
        .catch(() => {})
    }
    return () => {
      active = false
    }
  }, [isOpen])

  // Close on ESC / backdrop
  useEffect(() => {
    if (!isOpen) return
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [isOpen, onClose])

  if (!isOpen) return null

  const activePlatform = PLATFORMS.find((p) => p.id === activeTab)

  const getPlatformStatus = (platformId) => {
    if (!status?.platforms) return null
    const domain = PLATFORMS.find((p) => p.id === platformId)?.domain
    if (!domain) return null
    return status.platforms.find((p) => p.domain === domain) || null
  }

  const handleSave = async () => {
    const cookieStr = cookieInputs[activeTab]?.trim()
    if (!cookieStr) {
      showToast('error', 'Vui lòng dán cookie vào ô nhập liệu')
      return
    }
    if (activeTab === 'custom' && !customDomain.trim()) {
      showToast('error', 'Vui lòng nhập domain cho cookie tùy chỉnh')
      return
    }

    setSaving(true)
    try {
      const result = await savePlatformCookies(
        activeTab,
        cookieStr,
        activeTab === 'custom' ? customDomain.trim() : null,
      )
      showToast('success', `✅ Đã lưu ${result.saved} cookies cho ${result.domain}`)
      setCookieInputs((prev) => ({ ...prev, [activeTab]: '' }))
      await loadStatus()
    } catch (err) {
      showToast('error', err.message || 'Lỗi khi lưu cookie')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (domain) => {
    try {
      await deletePlatformCookies(domain)
      showToast('success', `🗑️ Đã xóa cookies của ${domain}`)
      setConfirmDelete(null)
      await loadStatus()
    } catch (err) {
      showToast('error', err.message)
    }
  }

  return (
    <div className="cm-overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="cm-modal" ref={modalRef}>
        {/* Header */}
        <div className="cm-header">
          <div className="cm-header-left">
            <span className="cm-header-icon">🍪</span>
            <div>
              <h2 className="cm-title">Cookie Manager</h2>
              <p className="cm-subtitle">Xác thực tài khoản để tải nội dung cần đăng nhập</p>
            </div>
          </div>
          <button className="cm-close-btn" onClick={onClose} title="Đóng">✕</button>
        </div>

        {/* Toast */}
        {toast && (
          <div className={`cm-toast cm-toast-${toast.type}`}>
            {toast.msg}
          </div>
        )}

        {/* Trạng thái đã lưu */}
        {status?.platforms?.length > 0 && (
          <div className="cm-saved-section">
            <p className="cm-saved-label">Cookie đang hoạt động:</p>
            <div className="cm-saved-chips">
              {status.platforms.map((p) => (
                <div key={p.domain} className={`cm-saved-chip ${p.isValid ? 'valid' : 'invalid'}`}>
                  <span className="cm-chip-dot" />
                  <span className="cm-chip-text">{p.domain}</span>
                  <span className="cm-chip-count">{p.cookieNames.length} cookies</span>
                  {confirmDelete === p.domain ? (
                    <div className="cm-confirm-delete">
                      <span>Xóa?</span>
                      <button onClick={() => handleDelete(p.domain)} className="cm-confirm-yes">Có</button>
                      <button onClick={() => setConfirmDelete(null)} className="cm-confirm-no">Không</button>
                    </div>
                  ) : (
                    <button
                      className="cm-chip-delete"
                      onClick={() => setConfirmDelete(p.domain)}
                      title={`Xóa cookie ${p.domain}`}
                    >✕</button>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Tab selector */}
        <div className="cm-tabs">
          {PLATFORMS.map((p) => {
            const ps = getPlatformStatus(p.id)
            const Icon = p.icon
            return (
              <button
                key={p.id}
                className={`cm-tab cm-tab-${p.id} ${activeTab === p.id ? 'active' : ''}`}
                onClick={() => setActiveTab(p.id)}
              >
                <span className="cm-tab-icon">
                  <Icon className="w-4 h-4" />
                </span>
                <span className="cm-tab-name">{p.name}</span>
                {ps?.isValid && <span className="cm-tab-badge">✓</span>}
              </button>
            )
          })}
        </div>

        {/* Content */}
        <div className="cm-content">
          {/* Hướng dẫn */}
          <div className="cm-guide">
            <p className="cm-guide-title">📋 Hướng dẫn lấy cookie từ DevTools:</p>
            <ol className="cm-guide-steps">
              {activePlatform?.guide.map((g) => (
                <li key={g.step} className="cm-guide-step">
                  <span className="cm-step-num">{g.step}</span>
                  <span>{g.text}</span>
                </li>
              ))}
            </ol>
          </div>

          {/* Domain input (custom only) */}
          {activeTab === 'custom' && (
            <div className="cm-field">
              <label className="cm-field-label">Domain (ví dụ: pixiv.net)</label>
              <input
                type="text"
                className="cm-input"
                placeholder="pixiv.net"
                value={customDomain}
                onChange={(e) => setCustomDomain(e.target.value)}
              />
            </div>
          )}

          {/* Cookie input */}
          <div className="cm-field">
            <label className="cm-field-label">
              Dán cookie vào đây
              {activePlatform?.requiredKeys?.length > 0 && (
                <span className="cm-required-hint">
                  — Bắt buộc: <strong>{activePlatform.requiredKeys.join(', ')}</strong>
                </span>
              )}
            </label>
            <textarea
              className="cm-textarea"
              placeholder={activePlatform?.placeholder}
              value={cookieInputs[activeTab] || ''}
              onChange={(e) => setCookieInputs((prev) => ({ ...prev, [activeTab]: e.target.value }))}
              rows={5}
            />
            <p className="cm-hint">💡 {activePlatform?.hint}</p>
          </div>

          {/* Actions */}
          <div className="cm-actions">
            <button
              className="cm-save-btn"
              onClick={handleSave}
              disabled={saving}
            >
              {saving ? (
                <>
                  <span className="cm-spinner" />
                  Đang lưu...
                </>
              ) : (
                <>
                  <span>💾</span>
                  Lưu Cookie
                </>
              )}
            </button>
            <button className="cm-cancel-btn" onClick={onClose}>Đóng</button>
          </div>

          {/* Security note */}
          
        </div>
      </div>
    </div>
  )
}
