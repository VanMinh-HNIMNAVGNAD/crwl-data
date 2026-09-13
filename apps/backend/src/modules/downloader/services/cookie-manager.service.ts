import { Injectable, Logger } from '@nestjs/common';
import { spawnSync } from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';
import * as os from 'node:os';
import { loadDownloaderConfig, resolveBrowser, type SupportedBrowser } from '../config/downloader.config.js';

/**
 * Thông tin cache cookies cho một browser
 */
interface CookieCacheEntry {
  filePath: string;
  exportedAt: number;
  browser: SupportedBrowser;
  isValid: boolean;
}

@Injectable()
export class CookieManagerService {
  private readonly logger = new Logger(CookieManagerService.name);
  private readonly cfg = loadDownloaderConfig();

  /**
   * Cache: browser -> entry (file + timestamp)
   * TTL mặc định 10 phút — đủ để dùng cho 1 phiên tải
   */
  private readonly cache = new Map<string, CookieCacheEntry>();
  private readonly CACHE_TTL_MS = 10 * 60 * 1000; // 10 phút

  private readonly cookieCacheDir: string;

  constructor() {
    this.cookieCacheDir = path.join(os.tmpdir(), 'crwl-cookies');
    if (!fs.existsSync(this.cookieCacheDir)) {
      fs.mkdirSync(this.cookieCacheDir, { recursive: true });
    }
  }

  /**
   * Lấy đường dẫn file cookies.txt sẵn sàng dùng cho gallery-dl / yt-dlp
   *
   * Logic ưu tiên:
   * 1. COOKIES_PATH env var (file tĩnh do người dùng upload)
   * 2. Auto-export từ browser (có cache TTL 10 phút)
   * 3. null nếu không thể lấy
   */
  async getCookiesFilePath(targetUrl = '', browserOverride?: string): Promise<string | null> {
    // 1. Ưu tiên file cookies tĩnh từ env
    if (this.cfg.cookiesFilePath && fs.existsSync(this.cfg.cookiesFilePath)) {
      this.logger.debug(`Dùng cookies file từ env: ${this.cfg.cookiesFilePath}`);
      return this.cfg.cookiesFilePath;
    }

    const platform = this.detectPlatform(targetUrl);
    const browser = resolveBrowser(browserOverride, this.cfg.browserCookies);

    if (!browser || browser === 'none') {
      // Đối với Reddit, mạng xã hội này bắt buộc phải có cookies để vượt qua Network Security Challenge
      // Nếu người dùng chọn 'none', tự động tìm cookies từ trình duyệt mặc định có sẵn (edge / chrome)
      if (platform === 'reddit') {
        const fallbackBrowser = (this.cfg.browserCookies !== 'none' ? this.cfg.browserCookies : 'edge') as SupportedBrowser;
        const cachedFallback = this.getFromCache(fallbackBrowser);
        if (cachedFallback) return cachedFallback;
        return this.exportCookiesFromBrowser(fallbackBrowser, 'reddit');
      }
      return null;
    }

    // 2. Trả về từ cache nếu còn hợp lệ
    const cached = this.getFromCache(browser);
    if (cached) {
      this.logger.debug(`Cookie cache hit (${browser})`);
      return cached;
    }

    // 3. Export cookies mới từ browser
    return this.exportCookiesFromBrowser(browser, platform);
  }

  /**
   * Xuất cookies từ browser ra file Netscape format.
   * Ưu tiên dùng script Python chuyên dụng (nhanh, giải mã GNOME Keyring chuẩn xác, hỗ trợ Smart Merge).
   * Fallback về yt-dlp nếu script không chạy được.
   */
  private exportCookiesFromBrowser(browser: SupportedBrowser, platform: string | null): string | null {
    const outputPath = path.join(this.cookieCacheDir, `cookies-${browser}-${Date.now()}.txt`);

    // 1. Ưu tiên script Python chuyên dụng
    if (this.exportCookiesWithPythonScript(browser, platform, outputPath)) {
      this.saveToCache(browser, outputPath);
      return outputPath;
    }

    // 2. Fallback về yt-dlp
    const ytDlpPath = this.findYtDlp();
    if (!ytDlpPath) {
      this.logger.warn('Không tìm thấy yt-dlp để export cookies.');
      return null;
    }

    const dummyUrl = this.getDummyUrlForPlatform(platform);
    const args = [
      '--cookies-from-browser', browser,
      '--cookies', outputPath,
      '--skip-download',
      '--no-warnings',
      '--quiet',
      dummyUrl,
    ];

    this.logger.log(`🍪 Auto-export cookies qua yt-dlp từ ${browser} (platform=${platform ?? 'all'})...`);

    const result = spawnSync(ytDlpPath, args, {
      encoding: 'utf-8',
      timeout: 15000,
      env: {
        ...process.env,
        DBUS_SESSION_BUS_ADDRESS: process.env.DBUS_SESSION_BUS_ADDRESS || '',
      },
    });

    if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100) {
      const size = fs.statSync(outputPath).size;
      this.logger.log(`✅ Đã export ${size} bytes cookies từ ${browser} → ${outputPath}`);
      this.saveToCache(browser, outputPath);
      return outputPath;
    }

    if (fs.existsSync(outputPath)) fs.unlinkSync(outputPath);

    const errMsg = (result.stderr || result.stdout || '').trim();
    this.logger.warn(`⚠️  Export cookies yt-dlp thất bại (${browser}): ${errMsg.substring(0, 200)}`);

    // Fallback: dùng YouTube làm dummy URL
    return this.exportAllCookiesFallback(ytDlpPath, browser);
  }

  /**
   * Gọi script Python export_browser_cookies.py chuyên dụng
   */
  private exportCookiesWithPythonScript(browser: SupportedBrowser, platform: string | null, outputPath: string): boolean {
    const pythonCandidates = [
      process.env.PYTHON_PATH,
      '/usr/bin/python3',
      'python3',
    ].filter(Boolean) as string[];

    const pythonBin = pythonCandidates.find((p) => {
      try {
        const res = spawnSync(p, ['--version']);
        return res.status === 0;
      } catch {
        return false;
      }
    }) || 'python3';

    const scriptCandidates = [
      path.join(process.cwd(), 'bin', 'export_browser_cookies.py'),
      path.join(process.cwd(), 'apps', 'backend', 'bin', 'export_browser_cookies.py'),
      '/home/minh/code/crwl-on-socialmedia/apps/backend/bin/export_browser_cookies.py',
    ];
    const scriptPath = scriptCandidates.find((p) => fs.existsSync(p));
    if (!scriptPath) {
      this.logger.debug('Không tìm thấy script export_browser_cookies.py, fallback về yt-dlp');
      return false;
    }

    const args = [
      scriptPath,
      '--browser', browser,
      '--output', outputPath,
      '--platform', 'all',
    ];

    this.logger.log(`🍪 Auto-export cookies từ ${browser} qua python script (all platforms)...`);

    const result = spawnSync(pythonBin, args, {
      encoding: 'utf-8',
      timeout: 10000,
      env: {
        ...process.env,
        DBUS_SESSION_BUS_ADDRESS: process.env.DBUS_SESSION_BUS_ADDRESS || '',
      },
    });

    if (result.status === 0 && fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100) {
      try {
        const info = JSON.parse(result.stdout.trim());
        const sources = info.sources?.length ? info.sources.join(', ') : browser;
        this.logger.log(`✅ Python export thành công: ${info.cookie_count} cookies (${sources}) → ${outputPath}`);
      } catch {
        this.logger.log(`✅ Python export thành công → ${outputPath}`);
      }
      return true;
    }

    const errMsg = (result.stderr || result.stdout || '').trim();
    this.logger.warn(`Python export cookies thất bại (${browser}): ${errMsg.substring(0, 200)}`);
    return false;
  }

  private exportAllCookiesFallback(ytDlpPath: string, browser: SupportedBrowser): string | null {
    const outputPath = path.join(this.cookieCacheDir, `cookies-${browser}-all-${Date.now()}.txt`);

    const args = [
      '--cookies-from-browser', browser,
      '--cookies', outputPath,
      '--flat-playlist',
      '--skip-download',
      '--no-warnings',
      '--quiet',
      'https://www.youtube.com',
    ];

    const result = spawnSync(ytDlpPath, args, {
      encoding: 'utf-8',
      timeout: 15000,
      env: { ...process.env },
    });

    if (fs.existsSync(outputPath) && fs.statSync(outputPath).size > 100) {
      this.logger.log(`✅ Fallback export thành công (${browser})`);
      this.saveToCache(browser, outputPath);
      return outputPath;
    }

    this.logger.error(`❌ Không thể export cookies từ ${browser}: ${(result.stderr || '').substring(0, 200)}`);
    return null;
  }

  private getDummyUrlForPlatform(platform: string | null): string {
    switch (platform) {
      case 'twitter': return 'https://x.com';
      case 'instagram': return 'https://www.instagram.com';
      case 'facebook': return 'https://www.facebook.com';
      case 'tiktok': return 'https://www.tiktok.com';
      case 'reddit': return 'https://www.reddit.com';
      case 'pixiv': return 'https://www.pixiv.net';
      case 'tumblr': return 'https://www.tumblr.com';
      default: return 'https://www.youtube.com';
    }
  }

  private detectPlatform(url: string): string | null {
    const lower = url.toLowerCase();
    if (lower.includes('twitter.com') || lower.includes('x.com')) return 'twitter';
    if (lower.includes('instagram.com')) return 'instagram';
    if (lower.includes('facebook.com')) return 'facebook';
    if (lower.includes('tiktok.com')) return 'tiktok';
    if (lower.includes('reddit.com') || lower.includes('redd.it')) return 'reddit';
    if (lower.includes('youtube.com') || lower.includes('youtu.be')) return 'youtube';
    if (lower.includes('pixiv.net')) return 'pixiv';
    if (lower.includes('tumblr.com')) return 'tumblr';
    return null;
  }

  private findYtDlp(): string | null {
    const envPath = process.env.YT_DLP_PATH;
    if (envPath && fs.existsSync(envPath)) return envPath;

    try {
      const result = spawnSync('which', ['yt-dlp'], { encoding: 'utf-8' });
      if (result.status === 0 && result.stdout?.trim()) return result.stdout.trim();
    } catch { /* ignore */ }

    const localBin = path.join(process.cwd(), 'bin', 'yt-dlp');
    if (fs.existsSync(localBin)) return localBin;

    return null;
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Cache management
  // ─────────────────────────────────────────────────────────────────────────

  private getFromCache(browser: SupportedBrowser): string | null {
    const entry = this.cache.get(browser);
    if (!entry) return null;

    const age = Date.now() - entry.exportedAt;
    if (age > this.CACHE_TTL_MS) {
      this.invalidateCache(browser);
      return null;
    }

    if (!fs.existsSync(entry.filePath)) {
      this.cache.delete(browser);
      return null;
    }

    return entry.filePath;
  }

  private saveToCache(browser: SupportedBrowser, filePath: string): void {
    const existing = this.cache.get(browser);
    if (existing && existing.filePath !== filePath && fs.existsSync(existing.filePath)) {
      try { fs.unlinkSync(existing.filePath); } catch { /* ignore */ }
    }

    this.cache.set(browser, {
      filePath,
      exportedAt: Date.now(),
      browser,
      isValid: true,
    });
  }

  /**
   * Xoá cache — gọi khi nhận lỗi 401/auth để force re-export
   */
  invalidateCache(browser?: SupportedBrowser): void {
    if (browser) {
      const entry = this.cache.get(browser);
      if (entry && fs.existsSync(entry.filePath)) {
        try { fs.unlinkSync(entry.filePath); } catch { /* ignore */ }
      }
      this.cache.delete(browser);
      this.logger.log(`🗑️  Đã xoá cookie cache cho ${browser}`);
    } else {
      for (const [key, entry] of this.cache.entries()) {
        if (fs.existsSync(entry.filePath)) {
          try { fs.unlinkSync(entry.filePath); } catch { /* ignore */ }
        }
        this.cache.delete(key);
      }
      this.logger.log('🗑️  Đã xoá toàn bộ cookie cache');
    }
  }

  /**
   * Trạng thái cookie cache — dùng để debug hoặc hiển thị trong UI
   */
  getCacheStatus(): Array<{
    browser: string;
    hasCache: boolean;
    ageSeconds?: number;
    fileSizeBytes?: number;
  }> {
    const browsers: SupportedBrowser[] = ['edge', 'chrome', 'firefox', 'brave', 'chromium'];
    return browsers.map((browser) => {
      const entry = this.cache.get(browser);
      if (!entry) return { browser, hasCache: false };

      const age = Math.round((Date.now() - entry.exportedAt) / 1000);
      let fileSizeBytes: number | undefined;
      try {
        fileSizeBytes = fs.existsSync(entry.filePath) ? fs.statSync(entry.filePath).size : undefined;
      } catch { /* ignore */ }

      return { browser, hasCache: true, ageSeconds: age, fileSizeBytes };
    });
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Controller-compatible API (backward compat)
  // ─────────────────────────────────────────────────────────────────────────

  /**
   * GET /api/media/cookies — Trạng thái hiện tại (dùng bởi controller)
   */
  getStatus(): {
    mode: string;
    staticFile?: string;
    autoExport: boolean;
    browser: string;
    cache: ReturnType<CookieManagerService['getCacheStatus']>;
  } {
    const cfg = loadDownloaderConfig();
    const hasStaticFile = !!(cfg.cookiesFilePath && fs.existsSync(cfg.cookiesFilePath));

    return {
      mode: hasStaticFile ? 'static-file' : cfg.browserCookies !== 'none' ? 'auto-export' : 'none',
      staticFile: hasStaticFile ? cfg.cookiesFilePath : undefined,
      autoExport: !hasStaticFile && cfg.browserCookies !== 'none',
      browser: cfg.browserCookies,
      cache: this.getCacheStatus(),
    };
  }

  /**
   * GET /api/media/cookies/platforms — Danh sách platforms hỗ trợ (dùng bởi controller)
   */
  getSupportedPlatforms(): { platforms: string[] } {
    return {
      platforms: ['twitter', 'x', 'instagram', 'facebook', 'tiktok', 'reddit', 'youtube', 'pixiv', 'tumblr', 'patreon', 'deviantart', 'fanbox', 'weibo', 'nicovideo'],
    };
  }

  /**
   * POST /api/media/cookies — Lưu cookie thủ công từ chuỗi DevTools (dùng bởi controller)
   * Hỗ trợ 2 format: "name=value; name2=value2" hoặc Netscape txt
   */
  async saveCookies(input: { platform: string; cookieString: string; domain?: string }): Promise<{ message: string; cookieCount: number }> {
    const { platform, cookieString, domain } = input;

    // Xác định domain từ platform nếu không được cung cấp
    const resolvedDomain = domain || this.platformToDomain(platform);
    if (!resolvedDomain) {
      throw new Error(`Không xác định được domain cho platform: ${platform}`);
    }

    // Chuyển đổi cookie string sang Netscape format
    const netscapeLines: string[] = ['# Netscape HTTP Cookie File', '# Manually imported by user.', ''];
    const pairs = cookieString.split(';').map((s) => s.trim()).filter(Boolean);

    for (const pair of pairs) {
      const eqIdx = pair.indexOf('=');
      if (eqIdx === -1) continue;
      const name = pair.slice(0, eqIdx).trim();
      const value = pair.slice(eqIdx + 1).trim();
      const expiry = Math.floor(Date.now() / 1000) + 86400 * 30; // 30 ngày
      // domain  flag  path  secure  expiry  name  value
      netscapeLines.push(`${resolvedDomain}\tTRUE\t/\tFALSE\t${expiry}\t${name}\t${value}`);
    }

    if (netscapeLines.length <= 3) {
      throw new Error('Không parse được cookies. Hãy kiểm tra định dạng "name=value; name2=value2".');
    }

    // Lưu ra file tạm và cache
    const outputPath = path.join(this.cookieCacheDir, `cookies-manual-${platform}-${Date.now()}.txt`);
    fs.writeFileSync(outputPath, netscapeLines.join('\n'), 'utf-8');

    // Ghi vào COOKIES_PATH nếu được cấu hình, hoặc lưu riêng
    const cfg = loadDownloaderConfig();
    if (cfg.cookiesFilePath) {
      // Merge vào file tĩnh
      const existing = fs.existsSync(cfg.cookiesFilePath) ? fs.readFileSync(cfg.cookiesFilePath, 'utf-8') : '';
      const merged = existing + '\n' + netscapeLines.slice(3).join('\n');
      fs.writeFileSync(cfg.cookiesFilePath, merged, 'utf-8');
      fs.unlinkSync(outputPath);
      this.logger.log(`✅ Đã lưu ${pairs.length} cookies vào ${cfg.cookiesFilePath}`);
    } else {
      // Lưu tạm, override cache cho browser active
      const browser = cfg.browserCookies !== 'none' ? cfg.browserCookies : 'edge';
      this.saveToCache(browser, outputPath);
      this.logger.log(`✅ Đã lưu ${pairs.length} cookies tạm cho ${browser}`);
    }

    return { message: `Đã lưu ${pairs.length} cookies cho ${platform} (${resolvedDomain})`, cookieCount: pairs.length };
  }

  /**
   * DELETE /api/media/cookies — Xoá cookies (dùng bởi controller)
   */
  deleteCookies(domain?: string): { message: string } {
    if (domain) {
      // Xoá toàn bộ cache (đơn giản nhất, export lại sẽ lấy fresh cookies)
      this.invalidateCache();
      return { message: `Đã xoá cookie cache (domain: ${domain})` };
    }
    this.invalidateCache();
    return { message: 'Đã xoá toàn bộ cookie cache. Lần tải tiếp theo sẽ tự động export lại.' };
  }

  private platformToDomain(platform: string): string | null {
    const map: Record<string, string> = {
      twitter: '.x.com',
      x: '.x.com',
      instagram: '.instagram.com',
      facebook: '.facebook.com',
      tiktok: '.tiktok.com',
      reddit: '.reddit.com',
      youtube: '.youtube.com',
      pixiv: '.pixiv.net',
      tumblr: '.tumblr.com',
      patreon: '.patreon.com',
      deviantart: '.deviantart.com',
      weibo: '.weibo.com',
    };
    return map[platform.toLowerCase()] ?? null;
  }
}

