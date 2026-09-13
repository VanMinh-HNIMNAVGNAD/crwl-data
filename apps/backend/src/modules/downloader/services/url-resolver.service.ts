import { Injectable, Logger } from '@nestjs/common';
import type { ResolveUrlResponseDto } from '../dto/media.dto.js';

export interface PlatformDomainDefinition {
  id: string;
  name: string;
  domains: string[];
}

export const SUPPORTED_PLATFORM_DOMAINS: PlatformDomainDefinition[] = [
  {
    id: 'youtube',
    name: 'YouTube',
    domains: ['youtube.com', 'youtu.be', 'youtube-nocookie.com'],
  },
  {
    id: 'instagram',
    name: 'Instagram',
    domains: ['instagram.com', 'instagr.am'],
  },
  {
    id: 'tiktok',
    name: 'TikTok',
    domains: ['tiktok.com'],
  },
  {
    id: 'facebook',
    name: 'Facebook',
    domains: ['facebook.com', 'fb.watch', 'fb.com', 'fb.me'],
  },
  {
    id: 'x',
    name: 'X (Twitter)',
    domains: ['twitter.com', 'x.com', 't.co'],
  },
  {
    id: 'pinterest',
    name: 'Pinterest',
    domains: [
      'pinterest.com',
      'pin.it',
      'pinterest.co.uk',
      'pinterest.ca',
      'pinterest.fr',
      'pinterest.de',
      'pinterest.jp',
    ],
  },
  {
    id: 'reddit',
    name: 'Reddit',
    domains: ['reddit.com', 'redd.it'],
  },
  {
    id: 'soundcloud',
    name: 'SoundCloud',
    domains: ['soundcloud.com', 'on.soundcloud.com'],
  },
  {
    id: 'twitch',
    name: 'Twitch',
    domains: ['twitch.tv'],
  },
  {
    id: 'dailymotion',
    name: 'Dailymotion',
    domains: ['dailymotion.com', 'dai.ly'],
  },
  {
    id: 'bilibili',
    name: 'Bilibili',
    domains: ['bilibili.com', 'b23.tv'],
  },
  {
    id: 'threads',
    name: 'Threads',
    domains: ['threads.net'],
  },
  {
    id: 'bluesky',
    name: 'Bluesky',
    domains: ['bsky.app'],
  },
  {
    id: 'movie',
    name: 'Phim & HLS',
    domains: [
      'motchill', 'phimmoi', 'ophim', 'kkphim', 'subnhanh', 'tvhay', 'bilutv',
      'dongphim', 'xemphim', 'rosetv', 'phim3s', 'hdonline', 'animehay', 'vuighe',
      'fmovies', '123movies', 'soap2day', 'bflix', 'gogoanime', 'aniwatch', 'hianime',
      'lookmovie', 'sflix', 'vidsrc', 'streamtape', 'doodstream',
    ],
  },
];

/**
 * Danh sách các domain rút gọn link phổ biến của bên thứ ba
 */
export const GENERIC_SHORTENER_DOMAINS = [
  'bit.ly',
  'tinyurl.com',
  't.ly',
  'cutt.ly',
  'is.gd',
  'v.gd',
  'rb.gy',
  'shorturl.at',
  'goo.gl',
  'ow.ly',
  'buff.ly',
  'clck.ru',
  'rebrand.ly',
  'bl.ink',
  'lnkd.in',
  'snip.ly',
  's.id',
  'linktr.ee',
  'shorte.st',
  'adf.ly',
];

@Injectable()
export class UrlResolverService {
  private readonly logger = new Logger(UrlResolverService.name);

  /**
   * Phân tích và trích xuất hostname từ URL một cách an toàn
   */
  parseHostname(rawUrl: string): { hostname: string; parsedUrl: URL } | null {
    if (!rawUrl || typeof rawUrl !== 'string') return null;
    let trimmed = rawUrl.trim();
    // Nếu là username (@username hoặc chuỗi không có dấu chấm domain) thì không phải là URL
    if (trimmed.startsWith('@') || (!trimmed.includes('.') && !trimmed.includes('/'))) {
      return null;
    }
    if (!/^https?:\/\//i.test(trimmed)) {
      trimmed = 'https://' + trimmed;
    }
    try {
      const parsed = new URL(trimmed);
      const hostname = parsed.hostname.toLowerCase().replace(/\.+$/, '');
      return { hostname, parsedUrl: parsed };
    } catch {
      return null;
    }
  }

  /**
   * Kiểm tra xem hostname có khớp với domain đã định nghĩa hay không
   * Quy tắc chuẩn: hostname === domain HOẶC hostname kết thúc bằng '.' + domain
   * Ngăn chặn hoàn toàn việc bypass chuỗi (ví dụ: evil-site.com?v=youtube.com hoặc fakeyoutube.com)
   */
  isMatchingDomain(hostname: string, targetDomain: string): boolean {
    const cleanTarget = targetDomain.toLowerCase().replace(/\.+$/, '');
    return hostname === cleanTarget || hostname.endsWith('.' + cleanTarget);
  }

  /**
   * Xác định nền tảng truyền thông từ hostname
   */
  detectPlatformFromHostname(hostname: string): string | null {
    for (const p of SUPPORTED_PLATFORM_DOMAINS) {
      for (const d of p.domains) {
        if (this.isMatchingDomain(hostname, d) || hostname.includes(d)) {
          return p.id;
        }
      }
    }
    const movieKeywords = ['phim', 'movie', 'cinema', 'stream', 'film', 'anime'];
    if (movieKeywords.some((k) => hostname.includes(k))) {
      return 'movie';
    }
    return null;
  }

  /**
   * Kiểm tra xem hostname có phải là dịch vụ rút gọn link chung hay không
   */
  isGenericShortener(hostname: string): boolean {
    return GENERIC_SHORTENER_DOMAINS.some((d) => this.isMatchingDomain(hostname, d));
  }

  /**
   * Kiểm tra tính an toàn SSRF trước khi gửi request tới URL
   */
  private isSafeUrl(parsedUrl: URL): boolean {
    const protocol = parsedUrl.protocol.toLowerCase();
    if (protocol !== 'http:' && protocol !== 'https:') {
      return false;
    }

    const host = parsedUrl.hostname.toLowerCase();

    // Chặn localhost, loopback, private IPs
    if (
      host === 'localhost' ||
      host === '127.0.0.1' ||
      host === '0.0.0.0' ||
      host === '::1' ||
      host === '0:0:0:0:0:0:0:1'
    ) {
      return false;
    }

    // Chặn dải IP nội bộ và AWS/Cloud metadata
    const privateIpRegex = /^(10\.\d{1,3}\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3}|172\.(1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}|169\.254\.\d{1,3}\.\d{1,3}|127\.\d{1,3}\.\d{1,3}\.\d{1,3})$/;
    if (privateIpRegex.test(host)) {
      return false;
    }

    return true;
  }

  /**
   * Giải mã link rút gọn bằng cách lần theo HTTP Redirects (an toàn)
   */
  async resolveUrl(rawUrl: string, expectedPlatform?: string): Promise<ResolveUrlResponseDto> {
    if (!rawUrl || typeof rawUrl !== 'string') {
      return {
        originalUrl: '',
        resolvedUrl: '',
        isShortened: false,
        platform: null,
        matchesExpected: false,
        error: 'Vui lòng cung cấp URL hợp lệ',
      };
    }

    const trimmed = rawUrl.trim();
    // Xử lý username / handle (@username hoặc tên không chứa domain)
    if (trimmed.startsWith('@') || (!trimmed.includes('.') && !trimmed.includes('/'))) {
      const username = trimmed.replace(/^@+/, '');
      const ep = expectedPlatform ? expectedPlatform.toLowerCase() : null;
      let fullUrl = `https://www.tiktok.com/@${username}`;
      if (ep === 'youtube') fullUrl = `https://www.youtube.com/@${username}`;
      else if (ep === 'instagram') fullUrl = `https://www.instagram.com/${username}/`;
      else if (ep === 'x' || ep === 'twitter') fullUrl = `https://x.com/${username}`;
      else if (ep === 'pinterest') fullUrl = `https://www.pinterest.com/${username}/`;
      else if (ep === 'reddit') fullUrl = `https://www.reddit.com/user/${username}/`;
      else if (ep === 'soundcloud') fullUrl = `https://soundcloud.com/${username}`;
      else if (ep === 'twitch') fullUrl = `https://www.twitch.tv/${username}`;

      const finalPlatform = ep || 'tiktok';
      return {
        originalUrl: rawUrl,
        resolvedUrl: fullUrl,
        isShortened: false,
        platform: finalPlatform,
        expectedPlatform,
        matchesExpected: expectedPlatform ? finalPlatform === ep : true,
      };
    }

    const parsedInfo = this.parseHostname(rawUrl);
    if (!parsedInfo) {
      return {
        originalUrl: rawUrl,
        resolvedUrl: rawUrl,
        isShortened: false,
        platform: null,
        expectedPlatform,
        matchesExpected: false,
        error: 'Liên kết không đúng định dạng URL hợp lệ',
      };
    }

    const { hostname, parsedUrl } = parsedInfo;

    const isDirectStream =
      parsedUrl.pathname.endsWith('.m3u8') ||
      parsedUrl.pathname.endsWith('.mpd') ||
      rawUrl.toLowerCase().includes('.m3u8') ||
      rawUrl.toLowerCase().includes('.mpd');

    // Kiểm tra trực tiếp nền tảng ban đầu
    const initialPlatform = isDirectStream ? 'movie' : this.detectPlatformFromHostname(hostname);
    const isGenericShort = this.isGenericShortener(hostname);

    const isRedditShare = initialPlatform === 'reddit' && (parsedUrl.pathname.includes('/s/') || hostname === 'redd.it');
    const isDomainShortener =
      hostname === 'youtu.be' ||
      hostname === 'fb.watch' ||
      hostname === 'pin.it' ||
      hostname === 'dai.ly' ||
      hostname === 'b23.tv' ||
      hostname.startsWith('vt.tiktok.com') ||
      hostname.startsWith('vm.tiktok.com') ||
      isRedditShare;

    // Nếu đã nhận diện được nền tảng chuẩn (và không phải link rút gọn cần giải mã)
    if (initialPlatform && !isGenericShort && !isDomainShortener) {
      const resolvedHref = parsedUrl.href;

      const matches = expectedPlatform
        ? initialPlatform === expectedPlatform.toLowerCase() || (expectedPlatform.toLowerCase() === 'movie' && initialPlatform === 'movie')
        : true;
      return {
        originalUrl: rawUrl,
        resolvedUrl: resolvedHref,
        isShortened: false,
        platform: initialPlatform,
        expectedPlatform,
        matchesExpected: matches,
      };
    }

    // Nếu không thuộc nền tảng nào và an toàn, tiến hành unshorten qua HTTP redirect
    if (!this.isSafeUrl(parsedUrl)) {
      return {
        originalUrl: rawUrl,
        resolvedUrl: rawUrl,
        isShortened: false,
        platform: null,
        expectedPlatform,
        matchesExpected: false,
        error: 'Địa chỉ IP hoặc URL không được phép truy cập (Bảo vệ bảo mật SSRF)',
      };
    }

    let finalUrl = parsedUrl.href;
    let didRedirect = false;

    try {
      this.logger.log(`Resolving shortened URL: ${parsedUrl.href}`);
      const headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
      };

      // Thử dùng HEAD trước để tiết kiệm băng thông
      let response: Response;
      try {
        response = await fetch(parsedUrl.href, {
          method: 'HEAD',
          headers,
          redirect: 'follow',
          signal: AbortSignal.timeout(6000),
        });
      } catch (headErr: any) {
        // Fallback sang GET nếu HEAD bị từ chối
        this.logger.debug(`HEAD failed (${headErr.message}), fallback to GET`);
        response = await fetch(parsedUrl.href, {
          method: 'GET',
          headers: { ...headers, Range: 'bytes=0-1024' },
          redirect: 'follow',
          signal: AbortSignal.timeout(6000),
        });
      }

      if (response && response.url) {
        finalUrl = response.url;
        didRedirect = finalUrl !== parsedUrl.href;
      }
    } catch (fetchErr: any) {
      this.logger.warn(`Unshorten HTTP error: ${fetchErr.message}`);
    }

    // Phân tích nền tảng của URL đích sau khi đã chuyển hướng
    const finalIsStream = finalUrl.toLowerCase().includes('.m3u8') || finalUrl.toLowerCase().includes('.mpd');
    const finalParsed = this.parseHostname(finalUrl);
    const resolvedPlatform = finalIsStream ? 'movie' : (finalParsed ? this.detectPlatformFromHostname(finalParsed.hostname) : null);
    const matchesExpected = expectedPlatform && resolvedPlatform
      ? resolvedPlatform === expectedPlatform.toLowerCase()
      : (expectedPlatform?.toLowerCase() === 'movie' && !resolvedPlatform ? true : Boolean(resolvedPlatform));

    return {
      originalUrl: rawUrl,
      resolvedUrl: finalUrl,
      isShortened: didRedirect || isGenericShort,
      platform: resolvedPlatform,
      expectedPlatform,
      matchesExpected,
    };
  }
}
