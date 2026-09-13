/**
 * Downloader Configuration
 * Centralized config cho tất cả services — đọc từ env vars với giá trị mặc định hợp lý
 */

import * as os from 'node:os';
import * as path from 'node:path';
import * as fs from 'node:fs';

export type SupportedBrowser =
  | 'firefox'
  | 'chrome'
  | 'chromium'
  | 'edge'
  | 'brave'
  | 'opera'
  | 'vivaldi'
  | 'safari'
  | 'none';

export const SUPPORTED_BROWSERS: SupportedBrowser[] = [
  'edge',
  'chrome',
  'firefox',
  'brave',
  'opera',
  'vivaldi',
  'chromium',
  'safari',
  'none',
];

export interface DetectedBrowserInfo {
  id: SupportedBrowser;
  name: string;
  detected: boolean;
  profilePath?: string;
}

/**
 * Tự động phát hiện các trình duyệt và profile cookies có sẵn trên máy chủ
 */
export function detectInstalledBrowsers(): DetectedBrowserInfo[] {
  const home = os.homedir();
  const isMac = process.platform === 'darwin';

  const check = (id: SupportedBrowser, name: string, paths: string[]): DetectedBrowserInfo => {
    if (id === 'none') return { id, name, detected: true };
    for (const p of paths) {
      if (fs.existsSync(p)) {
        return { id, name, detected: true, profilePath: p };
      }
    }
    return { id, name, detected: false };
  };

  return [
    check('edge', 'Microsoft Edge', [
      path.join(home, '.config/microsoft-edge'),
      path.join(home, 'Library/Application Support/Microsoft Edge'),
      '/usr/bin/microsoft-edge',
      '/opt/microsoft/msedge/msedge',
      '/opt/microsoft/msedge/microsoft-edge',
    ]),
    check('chrome', 'Google Chrome', [
      path.join(home, '.config/google-chrome'),
      path.join(home, 'Library/Application Support/Google/Chrome'),
      '/usr/bin/google-chrome',
    ]),
    check('firefox', 'Mozilla Firefox', [
      path.join(home, '.mozilla/firefox'),
      path.join(home, 'snap/firefox/common/.mozilla/firefox'),
      path.join(home, 'Library/Application Support/Firefox'),
      '/usr/bin/firefox',
    ]),
    check('brave', 'Brave Browser', [
      path.join(home, '.config/BraveSoftware/Brave-Browser'),
      path.join(home, 'Library/Application Support/BraveSoftware/Brave-Browser'),
      '/usr/bin/brave-browser',
    ]),
    check('opera', 'Opera', [
      path.join(home, '.config/opera'),
      path.join(home, 'Library/Application Support/com.operasoftware.Opera'),
      '/usr/bin/opera',
    ]),
    check('chromium', 'Chromium', [
      path.join(home, '.config/chromium'),
      '/usr/bin/chromium',
      '/usr/bin/chromium-browser',
    ]),
    check('vivaldi', 'Vivaldi', [
      path.join(home, '.config/vivaldi'),
      path.join(home, 'Library/Application Support/Vivaldi'),
      '/usr/bin/vivaldi',
    ]),
    check('safari', 'Apple Safari', isMac ? ['/Applications/Safari.app'] : []),
    check('none', 'Không sử dụng cookies', []),
  ];
}

/**
 * Giải quyết browser được chọn: ưu tiên override từ client request, fallback về server default
 */
export function resolveBrowser(override?: string, fallback?: SupportedBrowser): SupportedBrowser {
  if (override) {
    const clean = override.toLowerCase().trim() as SupportedBrowser;
    if (SUPPORTED_BROWSERS.includes(clean)) return clean;
  }
  return fallback || (process.env.BROWSER_COOKIES as SupportedBrowser) || 'edge';
}

export interface DownloaderConfig {
  /** Trình duyệt dùng để lấy cookies (none = không dùng cookies trình duyệt) */
  browserCookies: SupportedBrowser;

  /** Đường dẫn đến file cookies.txt (Netscape format) — override browserCookies nếu set */
  cookiesFilePath: string;

  /** Đường dẫn tới binary yt-dlp (để trống = tự động) */
  ytDlpPath: string;

  /** Đường dẫn tới binary gallery-dl (để trống = tự động) */
  galleryDlPath: string;

  /** Đường dẫn tới Node.js executable */
  nodePath: string;

  /** Bật/tắt SponsorBlock để tự động bỏ qua sponsor/intro trên YouTube */
  enableSponsorBlock: boolean;

  /** Tự động nhúng thumbnail vào file MP3/M4A */
  embedThumbnail: boolean;

  /** Tự động nhúng metadata (title/artist/album) vào file audio */
  embedMetadata: boolean;

  /** Số lượng tối đa item khi quét playlist/channel */
  maxPlaylistLimit: number;

  /** Timeout (ms) cho mỗi tiến trình tải/extract */
  downloadTimeoutMs: number;

  /** Pixiv OAuth refresh token (optional) */
  pixivToken: string;

  /** DeviantArt Client ID (optional) */
  deviantartClientId: string;

  /** DeviantArt Client Secret (optional) */
  deviantartClientSecret: string;
}

/**
 * Parse biến env thành boolean
 */
function parseBool(val: string | undefined, fallback: boolean): boolean {
  if (val === undefined || val === '') return fallback;
  return val === '1' || val.toLowerCase() === 'true' || val.toLowerCase() === 'yes';
}

/**
 * Parse biến env thành số nguyên
 */
function parseInt(val: string | undefined, fallback: number): number {
  if (!val) return fallback;
  const n = Number(val);
  return isNaN(n) ? fallback : n;
}

/**
 * Validate và normalize tên trình duyệt
 */
function parseBrowser(val: string | undefined): SupportedBrowser {
  const v = (val || process.env.BROWSER_COOKIES || 'edge').toLowerCase().trim() as SupportedBrowser;
  return SUPPORTED_BROWSERS.includes(v) ? v : 'edge';
}

/**
 * Singleton config instance — load một lần khi module khởi động
 */
export function loadDownloaderConfig(): DownloaderConfig {
  return {
    browserCookies: parseBrowser(process.env.BROWSER_COOKIES),
    cookiesFilePath: process.env.COOKIES_PATH || '',
    ytDlpPath: process.env.YT_DLP_PATH || '',
    galleryDlPath: process.env.GALLERY_DL_PATH || '',
    nodePath: process.env.NODE_PATH || process.execPath || '/usr/bin/node',
    enableSponsorBlock: parseBool(process.env.ENABLE_SPONSORBLOCK, false),
    embedThumbnail: parseBool(process.env.EMBED_THUMBNAIL, true),
    embedMetadata: parseBool(process.env.EMBED_METADATA, true),
    maxPlaylistLimit: parseInt(process.env.MAX_PLAYLIST_LIMIT, 200),
    downloadTimeoutMs: parseInt(process.env.DOWNLOAD_TIMEOUT_MS, 300000),
    pixivToken: process.env.PIXIV_TOKEN || '',
    deviantartClientId: process.env.DEVIANTART_CLIENT_ID || '',
    deviantartClientSecret: process.env.DEVIANTART_CLIENT_SECRET || '',
  };
}

/**
 * User-Agent strings theo trình duyệt — dùng cho HTTP headers
 */
export const BROWSER_USER_AGENTS: Record<SupportedBrowser, string> = {
  firefox:
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:132.0) Gecko/20100101 Firefox/132.0',
  chrome:
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
  chromium:
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
  edge: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0',
  brave:
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
  opera:
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36 OPR/117.0.0.0',
  vivaldi:
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
  safari:
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15',
  none: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
};
