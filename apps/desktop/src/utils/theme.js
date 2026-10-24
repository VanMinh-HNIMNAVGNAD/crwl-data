/**
 * Giao diện sáng / tối. Lựa chọn lưu ở localStorage và được áp lên <html data-theme>
 * TRƯỚC lần vẽ đầu tiên (main.jsx) để không loé nền trắng lúc mở app.
 */

export const THEMES = ['dark', 'light']
// Mặc định tối: nền trắng toàn màn hình gây mỏi mắt khi dùng lâu
export const DEFAULT_THEME = 'dark'
const STORAGE_KEY = 'ui_theme'

export function normalizeTheme(value) {
  return THEMES.includes(value) ? value : DEFAULT_THEME
}

export function getStoredTheme() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    return normalizeTheme(raw ? JSON.parse(raw) : null)
  } catch {
    return DEFAULT_THEME
  }
}

export function applyTheme(theme) {
  const value = normalizeTheme(theme)
  document.documentElement.dataset.theme = value
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(value))
  } catch {
    /* noop */
  }
  return value
}

export const nextTheme = (theme) => (normalizeTheme(theme) === 'dark' ? 'light' : 'dark')
