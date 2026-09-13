import { Injectable, Logger } from '@nestjs/common';
import * as fs from 'node:fs';
import dns from 'node:dns';
import { loadDownloaderConfig, BROWSER_USER_AGENTS } from '../config/downloader.config.js';

try {
  dns.setDefaultResultOrder('ipv4first');
} catch {}


export interface ProxyFetchOptions {
  /** URL của tệp cần proxy */
  url: string;
  /** Ghi đè Referer header nếu cần */
  referer?: string;
  /** Override User-Agent */
  userAgent?: string;
}

export interface ZipItemInput {
  url: string;
  filename: string;
  referer?: string;
}

/**
 * ProxyDownloadService — tập trung toàn bộ logic fetch/proxy tệp media
 * từ các nền tảng có cơ chế anti-hotlink (403 Forbidden)
 */
@Injectable()
export class ProxyDownloadService {
  private readonly logger = new Logger(ProxyDownloadService.name);
  private readonly config = loadDownloaderConfig();

  /**
   * Tạo HTTP Headers chuẩn (Referer, User-Agent, Accept, Origin)
   * phù hợp với từng nền tảng để tránh bị chặn 403 Forbidden
   */
  getHeadersForUrl(targetUrl: string, overrideReferer?: string): Record<string, string> {
    const ua = BROWSER_USER_AGENTS[this.config.browserCookies] || BROWSER_USER_AGENTS.chrome;

    const headers: Record<string, string> = {
      'User-Agent': ua,
      Accept: 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
      'Accept-Language': 'en-US,en;q=0.9,vi;q=0.8',
      'Accept-Encoding': 'gzip, deflate, br',
      Connection: 'keep-alive',
    };

    if (overrideReferer) {
      headers.Referer = overrideReferer;
      return headers;
    }

    const lower = targetUrl.toLowerCase();

    if (lower.includes('instagram') || lower.includes('cdninstagram.com')) {
      headers.Referer = 'https://www.instagram.com/';
      headers.Origin = 'https://www.instagram.com';
    } else if (lower.includes('pinterest.com') || lower.includes('pinimg.com')) {
      headers.Referer = 'https://www.pinterest.com/';
    } else if (lower.includes('twitter.com') || lower.includes('twimg.com') || lower.includes('x.com')) {
      headers.Referer = 'https://x.com/';
      headers.Origin = 'https://x.com';
    } else if (lower.includes('pixiv.net') || lower.includes('pximg.net')) {
      headers.Referer = 'https://www.pixiv.net/';
    } else if (lower.includes('artstation.com')) {
      headers.Referer = 'https://www.artstation.com/';
    } else if (lower.includes('reddit.com') || lower.includes('redd.it') || lower.includes('redditmedia.com') || lower.includes('redditstatic.com')) {
      headers.Referer = 'https://www.reddit.com/';
    } else if (lower.includes('weibo.com') || lower.includes('sinaimg.cn')) {
      headers.Referer = 'https://weibo.com/';
    } else if (lower.includes('tumblr.com')) {
      headers.Referer = 'https://www.tumblr.com/';
    } else if (lower.includes('deviantart.com')) {
      headers.Referer = 'https://www.deviantart.com/';
    } else if (lower.includes('tiktok.com') || lower.includes('tiktokcdn.com')) {
      headers.Referer = 'https://www.tiktok.com/';
    } else if (lower.includes('facebook.com') || lower.includes('fbcdn.net')) {
      headers.Referer = 'https://www.facebook.com/';
    } else if (lower.includes('bsky.app') || lower.includes('cdn.bsky.app')) {
      headers.Referer = 'https://bsky.app/';
    } else if (lower.includes('threads.net')) {
      headers.Referer = 'https://www.threads.net/';
    } else if (lower.includes('soundcloud.com')) {
      headers.Referer = 'https://soundcloud.com/';
    }

    return headers;
  }

  /**
   * Fetch an toàn có cơ chế thử lại (retry) khi mạng CDN gặp sự cố kết nối tạm thời
   */
  private async safeFetch(url: string, headers: Record<string, string>, retries = 2): Promise<Response> {
    for (let attempt = 1; attempt <= retries; attempt++) {
      try {
        const response = await fetch(url, { headers });
        if (response.ok) return response;
        if (attempt < retries && (response.status === 403 || response.status === 429 || response.status >= 500)) {
          await new Promise((r) => setTimeout(r, 400 * attempt));
          continue;
        }
        return response;
      } catch (err: any) {
        if (attempt >= retries) throw err;
        await new Promise((r) => setTimeout(r, 300 * attempt));
      }
    }
    throw new Error(`Không thể kết nối đến ${url}`);
  }

  /**
   * Fetch một tệp từ URL với headers chống 403, trả về Buffer
   */
  async fetchBuffer(opts: ProxyFetchOptions): Promise<{ buffer: Buffer; contentType: string; filename?: string }> {
    const { url, referer, userAgent } = opts;
    const headers = this.getHeadersForUrl(url, referer);

    if (userAgent) {
      headers['User-Agent'] = userAgent;
    }

    this.logger.debug(`Proxy fetch: ${url}`);
    const response = await this.safeFetch(url, headers);

    if (!response.ok) {
      throw new Error(`HTTP ${response.status} khi tải ${url}`);
    }

    const contentType = response.headers.get('content-type') || 'application/octet-stream';
    const buffer = Buffer.from(await response.arrayBuffer());

    return { buffer, contentType };
  }

  /**
   * Stream các tệp được tải song song vào ZIP archive, ghi trực tiếp ra WritableStream (res hoặc buffer)
   * Sử dụng STORE mode (level 0) chuyên dụng cho media (ảnh/video đã nén sẵn) -> tăng tốc độ đóng gói 20x-30x
   */
  async streamZip(
    items: ZipItemInput[],
    destinationStream: NodeJS.WritableStream,
    isAborted?: () => boolean,
    onProgress?: (done: number, total: number) => void,
  ): Promise<{ totalBytes: number; successCount: number; errorCount: number }> {
    const archiverModule: any = await import('archiver');

    let archive: any;
    if (archiverModule.ZipArchive) {
      archive = new archiverModule.ZipArchive({ store: true });
    } else {
      const createArchiver = archiverModule.default ?? archiverModule;
      archive = createArchiver('zip', { store: true });
    }

    const finishPromise = new Promise<void>((resolve, reject) => {
      destinationStream.on('finish', resolve);
      destinationStream.on('error', reject);
      archive.on('error', reject);
    });

    archive.pipe(destinationStream);

    const CONCURRENCY = 6;
    let successCount = 0;
    let errorCount = 0;
    let completedCount = 0;
    let cursor = 0;
    const usedNames = new Set<string>();

    const sanitizeFilename = (rawName: string, index: number): string => {
      let name = (rawName || `media_${index + 1}.jpg`).replace(/[/\\?%*:|"<>]/g, '_').trim();
      if (!name) name = `media_${index + 1}.jpg`;
      const lower = name.toLowerCase();
      if (usedNames.has(lower)) {
        const dotIdx = name.lastIndexOf('.');
        const base = dotIdx > 0 ? name.substring(0, dotIdx) : name;
        const ext = dotIdx > 0 ? name.substring(dotIdx) : '';
        let counter = 2;
        while (usedNames.has(`${base}_${counter}${ext}`.toLowerCase())) {
          counter++;
        }
        name = `${base}_${counter}${ext}`;
      }
      usedNames.add(name.toLowerCase());
      return name;
    };

    const worker = async () => {
      while (cursor < items.length) {
        if (isAborted && isAborted()) {
          break;
        }
        const index = cursor++;
        const item = items[index];
        try {
          const headers = this.getHeadersForUrl(item.url, item.referer);
          const response = await this.safeFetch(item.url, headers);
          const ctype = response.headers.get('content-type') || '';

          if (response.ok && response.body && !ctype.includes('text/html')) {
            const buf = Buffer.from(await response.arrayBuffer());
            const filename = sanitizeFilename(item.filename, index);
            archive.append(buf, { name: filename });
            successCount++;
          } else {
            errorCount++;
            this.logger.warn(`Bỏ qua tệp (${response.status}, type ${ctype}): ${item.url}`);
          }
        } catch (fetchErr: any) {
          errorCount++;
          this.logger.warn(`Lỗi khi tải ${item.url}: ${fetchErr.message}`);
        } finally {
          completedCount++;
          if (onProgress) {
            onProgress(completedCount, items.length);
          }
        }
      }
    };

    const workerCount = Math.min(CONCURRENCY, items.length);
    const workers = Array.from({ length: workerCount }, () => worker());
    await Promise.all(workers);

    if (isAborted && isAborted()) {
      archive.abort();
      return { totalBytes: 0, successCount, errorCount };
    }

    await archive.finalize();
    await finishPromise;

    return {
      totalBytes: archive.pointer ? archive.pointer() : 0,
      successCount,
      errorCount,
    };
  }

  /**
   * Fetch nhiều tệp và đóng gói vào ZIP archive buffer
   * Trả về Buffer của file ZIP
   */
  async fetchAndZip(
    items: ZipItemInput[],
    onProgress?: (done: number, total: number) => void,
  ): Promise<Buffer> {
    const { Writable } = await import('node:stream');
    const chunks: Buffer[] = [];
    const writableStream = new Writable({
      write(chunk, _enc, callback) {
        chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
        callback();
      },
    });

    await this.streamZip(items, writableStream, undefined, onProgress);
    return Buffer.concat(chunks);
  }

  /**
   * Kiểm tra file cookies.txt có tồn tại và có thể đọc không
   */
  getCookiesFilePath(): string | null {
    const p = this.config.cookiesFilePath;
    if (p && fs.existsSync(p)) return p;
    return null;
  }
}
