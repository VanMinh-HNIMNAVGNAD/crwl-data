import { useState, useEffect, useRef } from 'react'
import { savePlatformCookies, getCookieStatus, deletePlatformCookies } from '../services/api'
import {
  IconInstagram,
  IconX,
  IconTikTok,
  IconFacebook,
  IconYouTube,
  IconReddit,
  IconSettings,
} from './Icons'

// ─────────────────────────────────────────────────────────────────────────────
// Platform configs
// ─────────────────────────────────────────────────────────────────────────────

const PLATFORMS = [
  {
    id: 'instagram',
    name: 'Instagram',
    icon: IconInstagram,
    domain: 'instagram.com',
    color: '#e1306c',
    requiredKeys: ['sessionid'],
    optionalKeys: ['csrftoken', 'ds_user_id'],
    guide: [
      { step: 1, text: 'Mở Instagram.com trên trình duyệt và đăng nhập tài khoản' },
      { step: 2, text: 'Nhấn F12 → chọn tab "Application" (Chrome/Edge) hoặc "Storage" (Firefox)' },
      { step: 3, text: 'Bên trái: Cookies → https://www.instagram.com' },
      { step: 4, text: 'Tìm dòng "sessionid" → copy toàn bộ giá trị trong cột "Value"' },
      { step: 5, text: 'Hoặc copy toàn bộ cookie string (dạng key=val; key2=val2) / file Netscape / JSON đều được' },
    ],
    placeholder: 'sessionid=abc123def456...\ncsrftoken=xyz789...',
    hint: 'Cần nhất: sessionid. Dán cả chuỗi "key=value; key2=value2" hoặc JSON Cookie-Editor đều được.',
  },
  {
    id: 'twitter',
    name: 'X (Twitter)',
    icon: IconX,
    domain: 'x.com',
    color: '#1d9bf0',
    requiredKeys: ['auth_token'],
    optionalKeys: ['ct0', 'guest_id'],
    guide: [
      { step: 1, text: 'Mở x.com trên trình duyệt và đăng nhập tài khoản' },
      { step: 2, text: 'Nhấn F12 → chọn tab "Application" (Chrome/Edge) hoặc "Storage" (Firefox)' },
      { step: 3, text: 'Bên trái: Cookies → https://x.com' },
      { step: 4, text: 'Tìm "auth_token" và "ct0" → copy giá trị của cả hai' },
      { step: 5, text: 'Dán vào ô bên dưới: auth_token=VALUE; ct0=VALUE' },
    ],
    placeholder: 'auth_token=abc123...\nct0=def456...',
    hint: 'Cần nhất: auth_token. Nên kèm ct0 để tránh bị hạn chế.',
  },
  {
    id: 'tiktok',
    name: 'TikTok',
    icon: IconTikTok,
    domain: 'tiktok.com',
    color: '#fe2c55',
    requiredKeys: ['sessionid'],
    optionalKeys: ['s_v_web_id', 'ttwid'],
    guide: [
      { step: 1, text: 'Mở tiktok.com và đăng nhập tài khoản' },
      { step: 2, text: 'F12 → Application → Cookies → https://www.tiktok.com' },
      { step: 3, text: 'Tìm và copy giá trị "sessionid"' },
    ],
    placeholder: 'sessionid=abc123...\ns_v_web_id=verify_...',
    hint: 'Cần nhất: sessionid. Giúp tải các video riêng tư hoặc profile hạn chế.',
  },
  {
    id: 'facebook',
    name: 'Facebook',
    icon: IconFacebook,
    domain: 'facebook.com',
    color: '#1877f2',
    requiredKeys: ['c_user', 'xs'],
    optionalKeys: ['fr', 'datr'],
    guide: [
      { step: 1, text: 'Mở facebook.com và đăng nhập' },
      { step: 2, text: 'F12 → Application → Cookies → https://www.facebook.com' },
      { step: 3, text: 'Tìm và copy 2 giá trị "c_user" và "xs"' },
      { step: 4, text: 'Dán dạng: c_user=1000...; xs=2%3A...' },
    ],
    placeholder: 'c_user=1000...\nxs=2%3A...',
    hint: 'Cần nhất: c_user và xs để tải bài viết trong Group kín hoặc Story riêng tư.',
  },
  {
    id: 'youtube',
    name: 'YouTube',
    icon: IconYouTube,
    domain: 'youtube.com',
    color: '#ff0000',
    requiredKeys: ['__Secure-3PSID'],
    optionalKeys: ['LOGIN_INFO', 'SID'],
    guide: [
      { step: 1, text: 'Mở youtube.com và đăng nhập tài khoản Google' },
      { step: 2, text: 'F12 → Application → Cookies → https://www.youtube.com' },
      { step: 3, text: 'Copy chuỗi cookie hoặc xuất từ tiện ích Get cookies.txt / Cookie-Editor' },
    ],
    placeholder: '__Secure-3PSID=...\nLOGIN_INFO=...',
    hint: 'Cần thiết để tải video 18+ (Age-restricted), Private hoặc video hội viên (Members-only).',
  },
  {
    id: 'reddit',
    name: 'Reddit',
    icon: IconReddit,
    domain: 'reddit.com',
    color: '#ff4500',
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
    requiredKeys: [],
    optionalKeys: [],
    guide: [
      { step: 1, text: 'Nhập tên nền tảng (ví dụ: pixiv, threads, bilibili, soundcloud...)' },
      { step: 2, text: 'F12 → Application → Cookies → chọn domain mong muốn' },
      { step: 3, text: 'Copy toàn bộ cookie string hoặc JSON và dán vào ô bên dưới' },
    ],
    placeholder: 'key1=value1; key2=value2; ...',
    hint: 'Hỗ trợ các nền tảng: pixiv, threads, bilibili, douyin, soundcloud, tumblr, linkedin, pinterest. Tự động chuẩn hóa domain (vd: pixiv.net → pixiv).',
  },
]

const SUPPORTED_CUSTOM_PLATFORMS = [
  'pixiv',
  'threads',
  'bilibili',
  'douyin',
  'soundcloud',
  'tumblr',
  'linkedin',
  'pinterest',
]

// ─────────────────────────────────────────────────────────────────────────────
// CookieManager Modal
// ─────────────────────────────────────────────────────────────────────────────

export default function CookieManager({ isOpen, onClose, onCookieUpdated }) {
  const [activeTab, setActiveTab] = useState('instagram')
  const [cookieInputs, setCookieInputs] = useState({})
  const [customDomain, setCustomDomain] = useState('')
  const [saving, setSaving] = useState(false)
  const [toast, setToast] = useState(null) // { type: 'success'|'error', msg }
  const [status, setStatus] = useState(null)
  const [confirmDelete, setConfirmDelete] = useState(null) // platform string
  const modalRef = useRef(null)
  const toastTimer = useRef(null)

  const showToast = (type, msg) => {
    setToast({ type, msg })
    if (toastTimer.current) {
      clearTimeout(toastTimer.current)
    }
    toastTimer.current = setTimeout(() => {
      setToast(null)
    }, 3000)
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

  const activePlatform = PLATFORMS.find((p) => p.id === activeTab) || PLATFORMS[0]

  const getPlatformStatus = (platformId) => {
    if (!status?.platforms) return null
    return status.platforms.find((p) => p.platform?.toLowerCase() === platformId.toLowerCase()) || null
  }

  const handleSave = async () => {
    const cookieStr = cookieInputs[activeTab]?.trim()
    if (!cookieStr) {
      showToast('error', 'Vui lòng dán cookie vào ô nhập liệu')
      return
    }
    if (activeTab === 'custom' && !customDomain.trim()) {
      showToast('error', 'Vui lòng nhập tên nền tảng cho cookie tùy chỉnh')
      return
    }

    setSaving(true)
    try {
      const rawPlatformKey = activeTab === 'custom'
        ? customDomain.trim()
        : activeTab

      const result = await savePlatformCookies(
        rawPlatformKey,
        cookieStr,
      )
      showToast('success', `✅ ${result.message || `Đã lưu ${result.cookie_count || 1} cookie cho ${result.platform}`}`)
      setCookieInputs((prev) => ({ ...prev, [activeTab]: '' }))
      if (activeTab === 'custom') {
        setCustomDomain('')
      }
      await loadStatus()
      if (onCookieUpdated) onCookieUpdated()
    } catch (err) {
      showToast('error', typeof err === 'string' ? err : err?.message || 'Lỗi khi lưu cookie')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (platform) => {
    try {
      await deletePlatformCookies(platform)
      showToast('success', `🗑️ Đã xóa cookies của ${platform}`)
      setConfirmDelete(null)
      await loadStatus()
      if (onCookieUpdated) onCookieUpdated()
    } catch (err) {
      showToast('error', typeof err === 'string' ? err : err?.message || 'Lỗi khi xóa cookie')
    }
  }

  const activeSavedPlatforms = status?.platforms?.filter((p) => p.has_cookies) || []

  return (
    <div className="cm-overlay" onClick={(e) => { if (e.target === e.currentTarget) onClose() }}>
      <div className="cm-modal" ref={modalRef}>
        {/* Header */}
        <div className="cm-header">
          <div className="cm-header-left">
            <span className="cm-header-icon">🍪</span>
            <div>
              <h2 className="cm-title">Cookie Manager</h2>
              <p className="cm-subtitle">Lưu trữ Cookie thủ công — Luôn được ưu tiên khi tải nội dung yêu cầu đăng nhập</p>
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
        {activeSavedPlatforms.length > 0 && (
          <div className="cm-saved-section">
            <p className="cm-saved-label">Cookie thủ công đang hoạt động (Ưu tiên số 1):</p>
            <div className="cm-saved-chips">
              {activeSavedPlatforms.map((p) => (
                <div key={p.platform} className="cm-saved-chip valid">
                  <span className="cm-chip-dot" />
                  <span className="cm-chip-text font-medium">{p.platform}</span>
                  <span className="cm-chip-count">
                    {p.size_bytes ? `${(p.size_bytes / 1024).toFixed(1)} KB` : 'Đã nạp'}
                  </span>
                  {confirmDelete === p.platform ? (
                    <div className="cm-confirm-delete">
                      <span>Xóa?</span>
                      <button onClick={() => handleDelete(p.platform)} className="cm-confirm-yes">Có</button>
                      <button onClick={() => setConfirmDelete(null)} className="cm-confirm-no">Không</button>
                    </div>
                  ) : (
                    <button
                      className="cm-chip-delete"
                      onClick={() => setConfirmDelete(p.platform)}
                      title={`Xóa cookie ${p.platform}`}
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
                {ps?.has_cookies && <span className="cm-tab-badge">✓</span>}
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
              <label className="cm-field-label">Tên nền tảng (ví dụ: pixiv, threads, bilibili...)</label>
              <input
                type="text"
                className="cm-input"
                placeholder="pixiv (hoặc pixiv.net, threads, bilibili...)"
                value={customDomain}
                onChange={(e) => setCustomDomain(e.target.value)}
              />
              <div className="cm-suggest-chips">
                <span className="cm-suggest-label">Gợi ý:</span>
                {SUPPORTED_CUSTOM_PLATFORMS.map((plat) => (
                  <button
                    key={plat}
                    type="button"
                    className="cm-suggest-btn"
                    onClick={() => setCustomDomain(plat)}
                  >
                    {plat}
                  </button>
                ))}
              </div>
            </div>
          )}

          {/* Cookie input */}
          <div className="cm-field">
            <label className="cm-field-label">
              Dán cookie vào đây (Hỗ trợ dạng key=value; hoặc JSON Cookie-Editor hoặc file Netscape)
              {activePlatform?.requiredKeys?.length > 0 && (
                <span className="cm-required-hint">
                  — Khuyến nghị có: <strong>{activePlatform.requiredKeys.join(', ')}</strong>
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
        </div>
      </div>
    </div>
  )
}
