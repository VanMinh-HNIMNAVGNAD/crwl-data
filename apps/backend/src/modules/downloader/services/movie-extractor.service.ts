import { Injectable, Logger } from '@nestjs/common';
import { YtDlpService } from './yt-dlp.service.js';
import type { MediaMetadataDto, StreamFormatDto } from '../dto/media.dto.js';

/**
 * Danh sách domain trang phim / streaming phổ biến
 */
const KNOWN_MOVIE_DOMAINS = [
  'motchill',
  'phimmoi',
  'ophim',
  'kkphim',
  'subnhanh',
  'tvhay',
  'bilutv',
  'dongphim',
  'xemphim',
  'rosetv',
  'phim3s',
  'hdonline',
  'animehay',
  'vuighe',
  'animet',
  'fmovies',
  '123movies',
  'soap2day',
  'bflix',
  'hdtoday',
  'flixtor',
  'gogoanime',
  'aniwatch',
  'hianime',
  'lookmovie',
  'sflix',
  'hurawatch',
  'vumoo',
  'vidsrc',
  'superembed',
  '2embed',
  'streamtape',
  'doodstream',
  'mixdrop',
  'streamwish',
  'filemoon',
  'upstream',
  'voe',
  'rabbitstream',
  'megacloud',
  'vidcloud',
];

/**
 * Các domain mạng xã hội không phải là trang phim độc lập
 */
const EXCLUDED_SOCIAL_DOMAINS = [
  'youtube.com', 'youtu.be',
  'facebook.com', 'fb.watch', 'fb.com',
  'instagram.com', 'instagr.am',
  'tiktok.com',
  'twitter.com', 'x.com',
  'pinterest.com', 'pin.it',
  'reddit.com', 'redd.it',
  'soundcloud.com',
  'twitch.tv',
  'dailymotion.com', 'dai.ly',
  'bilibili.com', 'b23.tv',
  'threads.net',
  'bsky.app',
];

@Injectable()
export class MovieExtractorService {
  private readonly logger = new Logger(MovieExtractorService.name);

  constructor(private readonly ytDlpService: YtDlpService) {}

  /**
   * Kiểm tra xem URL có phải là luồng stream trực tiếp (.m3u8, .mpd)
   * hoặc liên kết từ một trang phim / movie / anime hay không
   */
  isMovieOrStreamUrl(rawUrl: string): boolean {
    if (!rawUrl || typeof rawUrl !== 'string') return false;
    const lower = rawUrl.trim().toLowerCase();

    // 1. Kiểm tra luồng stream trực tiếp
    if (
      lower.includes('.m3u8') ||
      lower.includes('.mpd') ||
      lower.includes('/hls/') ||
      lower.includes('master.m3u8') ||
      lower.includes('playlist.m3u8') ||
      lower.includes('index.m3u8')
    ) {
      return true;
    }

    // 2. Loại trừ các mạng xã hội đã có extractor riêng
    if (EXCLUDED_SOCIAL_DOMAINS.some((d) => lower.includes(d))) {
      return false;
    }

    // 3. Phân tích hostname
    try {
      const urlObj = new URL(rawUrl.startsWith('http') ? rawUrl : `https://${rawUrl}`);
      const hostname = urlObj.hostname.toLowerCase();
      const pathname = urlObj.pathname.toLowerCase();

      // Kiểm tra tên miền theo danh sách các trang phim lậu / streaming phổ biến
      if (KNOWN_MOVIE_DOMAINS.some((d) => hostname.includes(d))) {
        return true;
      }

      // Kiểm tra từ khóa đặc trưng trong tên miền
      const movieKeywords = ['phim', 'movie', 'cinema', 'stream', 'film', 'anime', 'tvshow'];
      if (movieKeywords.some((k) => hostname.includes(k))) {
        return true;
      }

      // Kiểm tra đường dẫn trang xem phim
      const pathKeywords = ['/phim/', '/xem-phim/', '/tap-', '/movie/', '/watch/', '/episode/', '/series/', '/embed/', '/player/'];
      if (pathKeywords.some((p) => pathname.includes(p))) {
        return true;
      }
    } catch {
      // Bỏ qua lỗi URL không hợp lệ
    }

    return false;
  }

  /**
   * Trích xuất thông tin phim / luồng HLS
   */
  async extract(url: string, browserOverride?: string): Promise<MediaMetadataDto> {
    const trimmed = url.trim();
    this.logger.log(`MovieExtractor analyzing: ${trimmed}`);

    const isDirectStream =
      trimmed.includes('.m3u8') ||
      trimmed.includes('.mpd') ||
      /\.(m3u8|mpd)($|\?)/i.test(trimmed);

    // TH1: Đã là link stream trực tiếp (.m3u8 / .mpd)
    if (isDirectStream) {
      this.logger.log(`Direct stream URL detected → yt-dlp: ${trimmed}`);
      return this.extractFromDirectStream(trimmed, browserOverride);
    }

    // TH2: Trang web phim — cào HTML tìm thông tin và quét luồng stream
    try {
      this.logger.log(`Movie webpage detected → scanning HTML: ${trimmed}`);
      return await this.extractFromMovieWebpage(trimmed, browserOverride);
    } catch (pageErr: any) {
      this.logger.warn(`HTML scanning failed (${pageErr.message}), falling back to direct yt-dlp...`);
      try {
        const res = await this.ytDlpService.extractMetadata(trimmed, browserOverride);
        res.platform = 'movie';
        return res;
      } catch (ytErr: any) {
        this.logger.error(`All movie extraction attempts failed for ${trimmed}: ${ytErr.message}`);
        throw new Error(
          'Không tìm thấy luồng phát video (m3u8/HLS) trực tiếp trên trang phim này. ' +
          'Trang có thể đang sử dụng cơ chế bảo vệ nhúng sâu, anti-bot hoặc mã hóa DRM. ' +
          '💡 Mẹo: Bạn có thể mở F12 > tab Network trên trình duyệt, gõ "m3u8", copy link .m3u8 và dán trực tiếp vào đây để tải!'
        );
      }
    }
  }

  /**
   * Bóc tách thông tin từ link stream trực tiếp (.m3u8 / .mpd)
   */
  private async extractFromDirectStream(streamUrl: string, browserOverride?: string, pageReferer?: string): Promise<MediaMetadataDto> {
    try {
      const res = await this.ytDlpService.extractMetadata(streamUrl, browserOverride);
      res.platform = 'movie';

      // Cải thiện tiêu đề nếu yt-dlp trả về tên tệp chung chung
      if (!res.title || res.title === 'index' || res.title === 'master' || res.title === 'x36xhzz' || /^[a-z0-9_-]{1,10}$/i.test(res.title)) {
        try {
          const parsed = new URL(streamUrl);
          const pathSegments = parsed.pathname.split('/').filter(Boolean);
          const lastSeg = pathSegments.pop() || '';
          res.title = `Luồng phát HLS (${parsed.hostname}/${lastSeg.replace(/\.(m3u8|mpd)$/i, '')})`;
        } catch {
          res.title = 'Luồng phát HLS (m3u8)';
        }
      }

      res.author = res.author || 'Nguồn Stream HLS';
      return res;
    } catch (err: any) {
      this.logger.warn(`yt-dlp extract on stream failed (${err.message}), creating fallback direct stream object...`);

      // Fallback: tạo metadata object trực tiếp cho luồng phát
      let streamTitle = 'Luồng phát HLS trực tiếp';
      try {
        const parsed = new URL(streamUrl);
        streamTitle = `Luồng video (${parsed.hostname})`;
      } catch {}

      const streams: StreamFormatDto[] = [
        {
          formatId: 'hls_best',
          quality: 'Chất lượng gốc (HLS / m3u8)',
          format: 'HLS',
          size: 'Tự động',
          streamType: 'full',
          hasAudio: true,
          hasVideo: true,
          url: streamUrl,
        },
      ];

      return {
        id: String(Date.now()),
        platform: 'movie',
        title: streamTitle,
        author: 'Nguồn Video Trực tuyến',
        authorUrl: pageReferer || streamUrl,
        duration: 'Stream',
        views: 'Trực tiếp',
        thumbnail: '',
        highResThumbnail: '',
        type: 'video',
        originalUrl: streamUrl,
        streams,
      };
    }
  }

  /**
   * Bóc tách từ trang web phim (cào HTML, tìm metadata & quét link m3u8)
   */
  private async extractFromMovieWebpage(pageUrl: string, browserOverride?: string): Promise<MediaMetadataDto> {
    const headers = {
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
      Accept: 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
      'Accept-Language': 'vi,en-US;q=0.9,en;q=0.8',
      Referer: pageUrl,
    };

    const resp = await fetch(pageUrl, {
      headers,
      signal: AbortSignal.timeout(10000),
    });

    if (!resp.ok) {
      throw new Error(`HTTP ${resp.status} khi truy cập trang phim`);
    }

    const html = await resp.text();

    // 1. Trích xuất metadata phim từ HTML (Title, Poster, Description)
    const titleMatch =
      html.match(/<meta\s+property=["']og:title["']\s+content=["'](.*?)["']/i) ||
      html.match(/<meta\s+name=["']twitter:title["']\s+content=["'](.*?)["']/i) ||
      html.match(/<title>(.*?)<\/title>/i);

    let rawTitle = titleMatch ? titleMatch[1].trim() : '';
    // Làm sạch tiêu đề (xóa hậu tố quảng cáo của các trang phim)
    rawTitle = rawTitle
      .replace(/&amp;/g, '&')
      .replace(/&#39;/g, "'")
      .replace(/&quot;/g, '"')
      .replace(/\s*[-|–]\s*(Xem phim|Phim mới|Full HD|Vietsub|Thuyết minh|HD Online|Motchill|Phimmoi|Ophim|KKphim|SubNhanh|TVHay|BiluTV).*$/i, '')
      .trim();

    const posterMatch =
      html.match(/<meta\s+property=["']og:image["']\s+content=["'](.*?)["']/i) ||
      html.match(/<meta\s+name=["']twitter:image["']\s+content=["'](.*?)["']/i) ||
      html.match(/<link\s+rel=["']image_src["']\s+href=["'](.*?)["']/i);
    let poster = posterMatch ? posterMatch[1].trim() : '';
    if (poster && !poster.startsWith('http')) {
      try {
        poster = new URL(poster, pageUrl).href;
      } catch {}
    }

    const descMatch =
      html.match(/<meta\s+property=["']og:description["']\s+content=["'](.*?)["']/i) ||
      html.match(/<meta\s+name=["']description["']\s+content=["'](.*?)["']/i);
    const description = descMatch ? descMatch[1].trim().replace(/&amp;/g, '&') : '';

    // 2. Quét tìm luồng stream trong mã nguồn HTML chính
    let streamUrl = this.findStreamUrlInHtml(html, pageUrl);

    // 3. Nếu chưa tìm thấy, quét tiếp trong các thẻ <iframe> player
    if (!streamUrl) {
      this.logger.log(`No stream URL in main HTML, checking iframe players...`);
      const iframeMatches = Array.from(html.matchAll(/<iframe\s+[^>]*src=["']([^"']+)["'][^>]*>/gi));

      // Lọc các iframe có khả năng là player
      const playerIframes: string[] = [];
      for (const m of iframeMatches) {
        const src = m[1].trim();
        const lowerSrc = src.toLowerCase();
        if (
          lowerSrc.includes('player') ||
          lowerSrc.includes('embed') ||
          lowerSrc.includes('stream') ||
          lowerSrc.includes('play') ||
          lowerSrc.includes('video') ||
          lowerSrc.includes('vidsrc') ||
          lowerSrc.includes('2embed') ||
          lowerSrc.includes('.m3u8')
        ) {
          playerIframes.push(src);
        }
      }

      // Thử tải iframe đầu tiên để tìm luồng stream
      for (const iframeSrc of playerIframes.slice(0, 2)) {
        try {
          const resolvedIframeUrl = new URL(iframeSrc, pageUrl).href;
          this.logger.log(`Fetching player iframe: ${resolvedIframeUrl}`);
          const iframeResp = await fetch(resolvedIframeUrl, {
            headers: { ...headers, Referer: pageUrl },
            signal: AbortSignal.timeout(7000),
          });

          if (iframeResp.ok) {
            const iframeHtml = await iframeResp.text();
            streamUrl = this.findStreamUrlInHtml(iframeHtml, resolvedIframeUrl);
            if (streamUrl) {
              this.logger.log(`Found stream URL inside iframe: ${streamUrl}`);
              break;
            }
          }
        } catch (e: any) {
          this.logger.warn(`Failed fetching iframe (${e.message})`);
        }
      }
    }

    // 4. Nếu tìm được streamUrl, dùng yt-dlp bóc tách độ phân giải
    if (streamUrl) {
      this.logger.log(`✅ Extracted stream URL: ${streamUrl}`);
      const res = await this.extractFromDirectStream(streamUrl, browserOverride, pageUrl);

      // Bổ sung thông tin phim đã lấy được từ trang web chính
      if (rawTitle) res.title = rawTitle;
      if (poster && (!res.thumbnail || res.thumbnail.length < 5)) {
        res.thumbnail = poster;
        res.highResThumbnail = poster;
      }
      if (description && !res.description) res.description = description;
      res.originalUrl = pageUrl;
      res.platform = 'movie';

      return res;
    }

    // Không tìm thấy trong HTML hoặc iframe
    throw new Error('Không tìm thấy luồng stream trực tiếp trong mã nguồn trang');
  }

  /**
   * Quét và trích xuất URL stream (.m3u8, .mpd, .mp4) từ chuỗi HTML
   */
  private findStreamUrlInHtml(html: string, baseUrl: string): string | null {
    if (!html) return null;

    // Pattern 1: URL m3u8 trực tiếp (http/https)
    const directM3u8Regex = /https?:\/\/[^\s"'<>]+\.m3u8(?:[^\s"'<>]*)?/i;
    const directMatch = html.match(directM3u8Regex);
    if (directMatch) {
      return directMatch[0];
    }

    // Pattern 2: URL m3u8 có dấu gạch chéo bị escape trong chuỗi JSON (https:\/\/...)
    const escapedM3u8Regex = /["'](https?:\\\/\\\/[^"']+\.m3u8[^"']*)["']/i;
    const escapedMatch = html.match(escapedM3u8Regex);
    if (escapedMatch) {
      return escapedMatch[1].replace(/\\\//g, '/');
    }

    // Pattern 3: Cấu hình player: file: "...", source: "...", url: "..."
    const playerConfigRegex = /(?:file|source|src|url)\s*:\s*["']([^"']+\.(?:m3u8|mpd)[^"']*)["']/i;
    const playerMatch = html.match(playerConfigRegex);
    if (playerMatch) {
      const matchUrl = playerMatch[1].replace(/\\\//g, '/');
      try {
        return new URL(matchUrl, baseUrl).href;
      } catch {
        return matchUrl;
      }
    }

    // Pattern 4: HLS.js hoặc Video.js player
    const hlsLoadRegex = /hls\.loadSource\(["']([^"']+)["']\)/i;
    const hlsMatch = html.match(hlsLoadRegex);
    if (hlsMatch) {
      try {
        return new URL(hlsMatch[1], baseUrl).href;
      } catch {
        return hlsMatch[1];
      }
    }

    // Pattern 5: HTML5 <source src="..."> hoặc <video src="...">
    const sourceTagRegex = /<(?:source|video)\s+[^>]*src=["']([^"']+\.(?:m3u8|mpd|mp4)[^"']*)["'][^>]*>/i;
    const tagMatch = html.match(sourceTagRegex);
    if (tagMatch) {
      try {
        return new URL(tagMatch[1], baseUrl).href;
      } catch {
        return tagMatch[1];
      }
    }

    return null;
  }
}
