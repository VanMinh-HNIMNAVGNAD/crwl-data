import { Injectable, Logger } from '@nestjs/common';
import { spawn } from 'node:child_process';
import * as fs from 'node:fs';
import * as path from 'node:path';
import * as os from 'node:os';
import { BinaryManagerService } from './binary-manager.service.js';
import { CookieManagerService } from './cookie-manager.service.js';
import { loadDownloaderConfig, BROWSER_USER_AGENTS, resolveBrowser } from '../config/downloader.config.js';
import type {
  MediaMetadataDto,
  MediaImageDto,
  ProfileCrawlResultDto,
  CrawlMediaItemDto,
  StreamFormatDto,
} from '../dto/media.dto.js';

@Injectable()
export class GalleryDlService {
  private readonly logger = new Logger(GalleryDlService.name);
  private readonly cfg = loadDownloaderConfig();

  constructor(
    private readonly binaryManager: BinaryManagerService,
    private readonly cookieManager: CookieManagerService,
  ) {}

  private get galleryDlPath(): string {
    return this.binaryManager.getGalleryDlPath();
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Cookie Handling — dùng CookieManagerService (auto-export từ browser)
  // ─────────────────────────────────────────────────────────────────────────

  /**
   * Tạo file config gallery-dl tạm thời với auth tokens tuỳ chọn
   * Giúp xác thực Pixiv, DeviantArt mà không cần browser cookies
   */
  private createTempConfig(): string | null {
    const config: any = {
      extractor: {
        'base-directory': os.tmpdir(),
      },
    };

    let hasAuth = false;

    if (this.cfg.pixivToken) {
      config.extractor.pixiv = { 'refresh-token': this.cfg.pixivToken };
      hasAuth = true;
    }

    if (this.cfg.deviantartClientId && this.cfg.deviantartClientSecret) {
      config.extractor.deviantart = {
        'client-id': this.cfg.deviantartClientId,
        'client-secret': this.cfg.deviantartClientSecret,
      };
      hasAuth = true;
    }

    if (!hasAuth) return null;

    const tmpPath = path.join(os.tmpdir(), `gallery-dl-config-${Date.now()}.json`);
    try {
      fs.writeFileSync(tmpPath, JSON.stringify(config), 'utf-8');
      return tmpPath;
    } catch {
      return null;
    }
  }

  /**
   * Tạo args cơ bản dùng chung (async vì cần export cookies)
   * Dùng CookieManagerService để auto-export cookies từ browser → file .txt
   * Tránh vấn đề gallery-dl đọc trực tiếp browser DB (lock, keyring, v.v.)
   */
  private async getBaseArgs(targetUrl = '', browserOverride?: string): Promise<string[]> {
    const args: string[] = [];

    // Tối ưu tốc độ request: gallery-dl mặc định chờ 6-12 giây giữa các request Instagram
    // Đặt --sleep-request 0 giúp tăng tốc trích xuất 10-15 lần và tránh bị timeout/treo ở 95%
    args.push('--sleep-request', '0');

    // Auto-export cookies từ browser qua yt-dlp → file Netscape .txt
    const cookiesFile = await this.cookieManager.getCookiesFilePath(targetUrl, browserOverride);
    if (cookiesFile) {
      args.push('--cookies', cookiesFile);
      this.logger.debug(`Dùng cookies file: ${cookiesFile}`);
    }

    // Thêm config file nếu có auth tokens
    const configPath = this.createTempConfig();
    if (configPath) {
      args.push('--config', configPath);
    }

    return args;
  }

  // ─────────────────────────────────────────────────────────────────────────
  // JSON Parser
  // ─────────────────────────────────────────────────────────────────────────

  private parseGalleryDlJson(stdoutData: string): any[] | null {
    if (!stdoutData || !stdoutData.trim()) return null;
    const trimmed = stdoutData.trim();
    const jsonStart = trimmed.indexOf('[');
    const jsonEnd = trimmed.lastIndexOf(']');
    if (jsonStart !== -1 && jsonEnd !== -1 && jsonEnd > jsonStart) {
      try {
        const jsonStr = trimmed.slice(jsonStart, jsonEnd + 1);
        const parsed = JSON.parse(jsonStr);
        return Array.isArray(parsed) ? parsed : null;
      } catch {
        return null;
      }
    }
    return null;
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Extract Gallery — Single Post / Album
  // ─────────────────────────────────────────────────────────────────────────

  /**
   * Trích xuất danh sách phương tiện (ảnh + video) từ bài viết / album đơn lẻ
   */
  async extractGallery(url: string, browserOverride?: string): Promise<MediaMetadataDto> {
    const baseArgs = await this.getBaseArgs(url, browserOverride);
    return new Promise((resolve, reject) => {
      const args = [...baseArgs, '-j', url];

      this.logger.log(`gallery-dl extract: ${url}`);
      const proc = spawn(this.galleryDlPath, args);

      let stdoutData = '';
      let stderrData = '';

      const timeoutTimer = setTimeout(() => {
        try {
          proc.kill('SIGKILL');
        } catch {}
      }, 60000);

      proc.stdout.on('data', (c) => { stdoutData += c.toString(); });
      proc.stderr.on('data', (c) => { stderrData += c.toString(); });

      proc.on('close', (code) => {
        clearTimeout(timeoutTimer);
        if (code !== 0 && !stdoutData.trim()) {
          this.logger.error(`gallery-dl failed code=${code}: ${stderrData}`);
          return reject(new Error(`Không thể trích xuất album: ${stderrData || `Mã lỗi ${code}`}`));
        }

        try {
          const rawEntries = this.parseGalleryDlJson(stdoutData);
          if (!rawEntries || rawEntries.length === 0) {
            return reject(new Error('Không tìm thấy tệp phương tiện nào từ liên kết'));
          }

          const images: MediaImageDto[] = [];
          const directoryEntries: any[] = [];
          let detectedError: string | null = null;
          let category = 'social';
          let author = 'Người dùng';
          let authorUrl = url;
          let title = 'Bộ sưu tập đa phương tiện';
          let description = '';

          let index = 1;
          for (const item of rawEntries) {
            if (!Array.isArray(item)) continue;
            const codeType = item[0];

            if (codeType === -1 && item[1]) {
              const errObj = item[1];
              detectedError = errObj.message || errObj.error || JSON.stringify(errObj);
              continue;
            }

            if (codeType === 2 && item[1]) {
              const meta = item[1];
              directoryEntries.push(meta);
              category = meta.category || category;
              const rawAuthor = this.extractAuthorFromMeta(meta);
              if (rawAuthor) author = rawAuthor;
              const rawAuthorUrl = meta.author?.url || meta.user?.url || meta.pinner?.profile_url;
              if (rawAuthorUrl) authorUrl = rawAuthorUrl;
              const rawTitle =
                (typeof meta.title === 'string' ? meta.title : null) ||
                (typeof meta.grid_title === 'string' ? meta.grid_title : null) ||
                (typeof meta.seo_title === 'string' ? meta.seo_title : null) ||
                (typeof meta.board?.name === 'string' ? meta.board.name : null) ||
                (typeof meta.text === 'string' ? meta.text : null);
              if (rawTitle) {
                title = rawTitle;
              }
              const rawDesc =
                (typeof meta.description === 'string' ? meta.description : null) ||
                (typeof meta.unified_user_note === 'string' ? meta.unified_user_note : null) ||
                (typeof meta.seo_description === 'string' ? meta.seo_description : null) ||
                (typeof meta.body === 'string' ? meta.body : null) ||
                (typeof meta.text === 'string' ? meta.text : null) ||
                '';
              if (rawDesc) {
                description = rawDesc;
              }
            } else if (codeType === 3 && item[1]) {
              const rawUrl = item[1];
              const meta = item[2] || {};
              let mediaUrl = meta.video_url || rawUrl;
              if (typeof mediaUrl === 'string' && mediaUrl.startsWith('ytdl:')) {
                const redditFallback = meta.media?.reddit_video?.fallback_url;
                mediaUrl = redditFallback || mediaUrl.replace(/^ytdl:/, '');
              }
              const ext = (meta.extension || meta.ext || this.extractExt(mediaUrl) || (meta.video_url || meta.is_video ? 'mp4' : 'jpg')).toLowerCase();
              const isVideo = ['mp4', 'webm', 'mov', 'm4v', 'm3u8', 'ts'].includes(ext) || Boolean(meta.video_url) || Boolean(meta.is_video) || Boolean(meta.media?.reddit_video);
              const isGif = ext === 'gif';
              const itemType: 'image' | 'video' | 'gif' = isVideo ? 'video' : isGif ? 'gif' : 'image';
              const mediaTitle = meta.filename || meta.title || meta.description?.slice(0, 50) || `${isVideo ? 'Video' : 'Ảnh'} ${index}`;
              const res = meta.width && meta.height ? `${meta.width}x${meta.height}` : isVideo ? 'Video HD' : 'Ảnh HD';
              const sizeStr = meta.filesize ? this.formatBytes(meta.filesize) : 'Tự động';
              const thumb = meta.display_url || meta.thumbnail || (isVideo ? undefined : mediaUrl);

              if (meta.category) category = meta.category;

              images.push({
                id: index,
                url: mediaUrl,
                title: mediaTitle,
                resolution: res,
                size: sizeStr,
                type: itemType,
                ext,
                thumb,
              });
              index++;
            }
          }

          // Fallback: Pinterest single pin, ArtStation post, Mastodon, v.v.
          if (images.length === 0 && directoryEntries.length > 0) {
            for (const meta of directoryEntries) {
              const directUrl =
                meta.url || meta.image_url || meta.file_url ||
                meta.images?.orig?.url || meta.images?.orig_file_url ||
                meta.images?.['736x']?.url ||
                meta.media_attachments?.[0]?.url;

              if (directUrl && typeof directUrl === 'string' && directUrl.startsWith('http')) {
                const ext = (meta.extension || meta.ext || this.extractExt(directUrl) || 'jpg').toLowerCase();
                const isVideo = ['mp4', 'webm', 'mov', 'm4v', 'm3u8', 'ts'].includes(ext);
                const isGif = ext === 'gif';
                const itemType: 'image' | 'video' | 'gif' = isVideo ? 'video' : isGif ? 'gif' : 'image';
                const mediaTitle = meta.grid_title || meta.filename || meta.title || meta.content?.slice(0, 50) || `${isVideo ? 'Video' : 'Ảnh'} ${index}`;
                const res = meta.width && meta.height ? `${meta.width}x${meta.height}` : isVideo ? 'Video HD' : 'Ảnh HD';
                const sizeStr = meta.filesize ? this.formatBytes(meta.filesize) : 'Tự động';

                images.push({ id: index, url: directUrl, title: mediaTitle, resolution: res, size: sizeStr, type: itemType, ext, thumb: meta.thumbnail || (isVideo ? undefined : directUrl) });
                index++;
              }

              // Mastodon/Misskey — xử lý media_attachments array
              if (Array.isArray(meta.media_attachments)) {
                for (const att of meta.media_attachments) {
                  const attUrl = att.url || att.remote_url;
                  if (!attUrl) continue;
                  const ext = this.extractExt(attUrl) || (att.type === 'video' ? 'mp4' : 'jpg');
                  const isVideo = att.type === 'video' || ['mp4', 'webm'].includes(ext);
                  images.push({ id: index, url: attUrl, title: att.description || `Media ${index}`, resolution: att.meta?.original?.width && att.meta?.original?.height ? `${att.meta.original.width}x${att.meta.original.height}` : 'HD', size: 'Tự động', type: isVideo ? 'video' : 'image', ext, thumb: att.preview_url || (isVideo ? undefined : attUrl) });
                  index++;
                }
              }
            }
          }

          if (images.length === 0) {
            this.handleGalleryDlError(detectedError, category);
          }

          const isReelUrl = url.toLowerCase().includes('/reel/') || url.toLowerCase().includes('/reels/');
          const isSingleVideo = (images.length === 1 && images[0].type === 'video') || (images.length > 0 && isReelUrl);

          const videoCount = images.filter((i) => i.type === 'video').length;
          const imageCount = images.filter((i) => i.type === 'image' || i.type === 'gif').length;
          let durationLabel = `Album ${images.length} tệp`;
          if (isReelUrl) durationLabel = 'Video Reels';
          else if (isSingleVideo) durationLabel = 'Video HD';
          else if (videoCount > 0 && imageCount > 0) durationLabel = `${imageCount} ảnh • ${videoCount} video`;
          else if (videoCount > 0) durationLabel = `${videoCount} video`;
          else durationLabel = `${imageCount} hình ảnh`;

          const firstThumb = images.find((i) => i.thumb)?.thumb || images[0]?.url || '';

          // Nếu là video đơn lẻ hoặc Reels, tạo các stream chuẩn để tải qua giao diện video
          const streams: StreamFormatDto[] = [];
          if (isSingleVideo && images[0]?.url) {
            const vid = images[0];
            streams.push({
              formatId: 'original_video',
              quality: `${vid.resolution || 'HD 1080p'} — Video gốc chất lượng cao`,
              format: (vid.ext || 'mp4').toUpperCase(),
              size: vid.size || 'Tự động',
              streamType: 'full',
              hasAudio: true,
              hasVideo: true,
              url: vid.url,
            });
            streams.push({
              formatId: 'mp3_320k',
              quality: 'MP3 Tách âm thanh (320 kbps)',
              format: 'MP3',
              size: 'Tự động',
              streamType: 'audio',
              hasAudio: true,
              hasVideo: false,
            });
          }

          let normalizedPlatform = category.toLowerCase();
          if (normalizedPlatform.includes('twitter') || normalizedPlatform.includes('x.com')) {
            normalizedPlatform = 'x';
          }

          resolve({
            id: String(Date.now()),
            platform: normalizedPlatform,
            title: isSingleVideo ? (images[0].title || title) : `${title} (${images.length} tệp)`,
            author,
            authorUrl,
            duration: durationLabel,
            views: 'Chất lượng gốc',
            thumbnail: firstThumb,
            highResThumbnail: firstThumb,
            type: isSingleVideo ? 'video' : 'album',
            isReel: isReelUrl || undefined,
            originalUrl: url,
            description: description || undefined,
            streams: streams.length > 0 ? streams : undefined,
            images,
          });
        } catch (err: any) {
          reject(new Error(`Lỗi đọc dữ liệu album: ${err.message}`));
        }
      });

      proc.on('error', (err) => reject(new Error(`Không thể chạy gallery-dl: ${err.message}`)));
    });
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Crawl Profile / Board / Subreddit
  // ─────────────────────────────────────────────────────────────────────────

  /**
   * Quét toàn bộ nội dung từ tài khoản mạng xã hội
   */
  async crawlProfile(
    profileUrl: string,
    limit = 50,
    mediaType: 'all' | 'video' | 'image' = 'all',
    browserOverride?: string,
    rangeStart?: number,
    rangeEnd?: number,
  ): Promise<ProfileCrawlResultDto> {
    const baseArgs = await this.getBaseArgs(profileUrl, browserOverride);
    return new Promise((resolve, reject) => {
      const rangeArg =
        rangeStart && rangeEnd && rangeEnd >= rangeStart
          ? `${rangeStart}-${rangeEnd}`
          : `1-${limit}`;
      const args = [...baseArgs, '-j', '--range', rangeArg, profileUrl];

      this.logger.log(`gallery-dl crawl: ${profileUrl} (limit: ${limit})`);
      const proc = spawn(this.galleryDlPath, args);

      let stdoutData = '';
      let stderrData = '';

      const timeoutTimer = setTimeout(() => {
        try {
          proc.kill('SIGKILL');
        } catch {}
      }, 120000);

      proc.stdout.on('data', (c) => { stdoutData += c.toString(); });
      proc.stderr.on('data', (c) => { stderrData += c.toString(); });

      proc.on('close', (code) => {
        clearTimeout(timeoutTimer);
        if (code !== 0 && !stdoutData.trim()) {
          this.logger.warn(`gallery-dl crawl code=${code}: ${stderrData}`);
        }

        try {
          const media: CrawlMediaItemDto[] = [];
          let platform = 'social';
          let author = 'Người dùng';
          let avatar = '';
          let detectedError: string | null = null;
          let hasChildExtractors = false;
          const childUrls: string[] = [];

          const rawEntries = this.parseGalleryDlJson(stdoutData);
          if (rawEntries && Array.isArray(rawEntries)) {
            let idx = 1;
            for (const item of rawEntries) {
              if (!Array.isArray(item)) continue;

              if (item[0] === -1 && item[1]) {
                const errObj = item[1];
                detectedError = errObj.message || errObj.error || JSON.stringify(errObj);
                continue;
              }

              if (item[0] === 2 && item[1]) {
                const meta = item[1];
                if (meta.category) platform = meta.category;
                const foundAuthor = this.extractAuthorFromMeta(meta);
                if (foundAuthor) author = foundAuthor;
                const foundAvatar = this.extractAvatarFromMeta(meta);
                if (foundAvatar) avatar = foundAvatar;
              } else if (item[0] === 3 && item[1]) {
                const rawUrl = item[1];
                const meta = item[2] || {};
                const mediaUrl = meta.video_url || rawUrl;
                const ext = (meta.extension || meta.ext || this.extractExt(mediaUrl) || (meta.video_url ? 'mp4' : '')).toLowerCase();
                const isVideo = ['mp4', 'webm', 'mov', 'm4v', 'm3u8', 'ts'].includes(ext) || Boolean(meta.video_url);

                if (mediaType === 'video' && !isVideo) continue;
                if (mediaType === 'image' && isVideo) continue;

                if (meta.category) platform = meta.category;
                const itemAuthor = this.extractAuthorFromMeta(meta);
                if (itemAuthor) author = itemAuthor;
                const itemAvatar = this.extractAvatarFromMeta(meta);
                if (itemAvatar) avatar = itemAvatar;

                const itemTitle = meta.title || meta.filename || meta.description?.slice(0, 80) || (isVideo ? `Video ${idx}` : `Hình ảnh ${idx}`);
                const thumbUrl = meta.display_url || (isVideo ? (meta.thumbnail || mediaUrl) : mediaUrl);
                const isReelItem = !!(meta.post_url?.includes('/reel/') || profileUrl.toLowerCase().includes('/reels'));

                media.push({
                  id: idx,
                  type: isVideo ? 'video' : 'image',
                  title: itemTitle,
                  duration: isVideo ? (meta.duration ? this.formatDuration(meta.duration) : 'Video') : 'Ảnh HD',
                  quality: meta.width && meta.height ? `${meta.width}x${meta.height}` : 'HD',
                  size: meta.filesize ? this.formatBytes(meta.filesize) : 'Tự động',
                  thumb: thumbUrl,
                  url: mediaUrl,
                  author: typeof author === 'string' ? author : 'Người dùng',
                  likes: meta.like_count ? `${meta.like_count} lượt thích` : undefined,
                  isReel: isReelItem || undefined,
                });
                idx++;
              } else if (item[0] === 6 && typeof item[1] === 'string') {
                hasChildExtractors = true;
                childUrls.push(item[1]);
              }
            }
          }

          // If gallery-dl dispatched child extractor queues (e.g. root profile URL dispatched /posts/ or /timeline)
          // and no media items were returned directly, automatically recurse into the child queue URL!
          if (media.length === 0 && childUrls.length > 0) {
            const nextChild =
              childUrls.find(
                (u) =>
                  u !== profileUrl &&
                  (u.includes('/posts') || u.includes('/media') || u.includes('/timeline') || u.includes('/reels')),
              ) || childUrls[0];

            if (nextChild && nextChild !== profileUrl) {
              this.logger.log(`Child extractor queue detected (${nextChild}), auto-crawling child queue...`);
              this.crawlProfile(nextChild, limit, mediaType, browserOverride, rangeStart, rangeEnd)
                .then((subResult) => {
                  resolve({
                    ...subResult,
                    url: profileUrl,
                  });
                })
                .catch(reject);
              return;
            }
          }

          if (media.length === 0) {
            this.handleCrawlError(detectedError, platform, hasChildExtractors, profileUrl, browserOverride, reject);
            return;
          }

          const safeAuthor = typeof author === 'string' && author.trim() ? author.trim() : 'Người dùng';
          const cleanHandle = safeAuthor.startsWith('@') ? safeAuthor : `@${safeAuthor}`;

          resolve({
            platform,
            name: safeAuthor.replace('@', '').toUpperCase(),
            handle: cleanHandle,
            url: profileUrl,
            avatar,
            stats: `Đã quét ${media.length} tệp phương tiện`,
            media,
            totalCount: media.length,
          });
        } catch (err: any) {
          reject(new Error(`Lỗi phân tích kết quả quét: ${err.message}`));
        }
      });

      proc.on('error', (err) => reject(new Error(`Lỗi khởi chạy gallery-dl: ${err.message}`)));
    });
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Error Handling
  // ─────────────────────────────────────────────────────────────────────────

  private handleGalleryDlError(detectedError: string | null, category: string): never {
    if (detectedError) {
      const lower = detectedError.toLowerCase();
      const browser = this.cfg.browserCookies !== 'none' ? this.cfg.browserCookies : 'Firefox';

      if (lower.includes('login') || lower.includes('auth') || lower.includes('cookies') ||
          lower.includes('redirect to login') || lower.includes("'result'") || lower.includes('401')) {
        this.cookieManager.invalidateCache();
        throw new Error(`Nền tảng này (${category}) yêu cầu cookies đăng nhập hợp lệ hoặc phiên đã hết hạn. Vui lòng đảm bảo bạn đã đăng nhập vào tài khoản trên trình duyệt (${browser} hoặc Firefox).`);
      }
      if (lower.includes('notfound') || lower.includes('not found') || lower.includes('404')) {
        throw new Error('Không tìm thấy bài viết hoặc hình ảnh (có thể đã bị xoá hoặc URL không chính xác).');
      }
      if (lower.includes('blocked') || lower.includes('security') || lower.includes('challenge') || lower.includes('403')) {
        if (category === 'reddit' || lower.includes('reddit')) {
          throw new Error('Reddit đã kích hoạt cơ chế bảo mật (Network Security / Bot Challenge). Vui lòng đảm bảo bạn đã đăng nhập Reddit trên trình duyệt Microsoft Edge hoặc Chrome, và chọn đúng nguồn cookies ở thanh trên cùng.');
        }
        throw new Error('Nền tảng kích hoạt cơ chế bảo mật (chặn truy cập tự động). Vui lòng thử lại sau.');
      }
      if (lower.includes('private')) {
        throw new Error('Bài viết hoặc tài khoản này đang ở chế độ Riêng tư (Private).');
      }
      throw new Error(`Lỗi từ nền tảng (${category}): ${detectedError}`);
    }
    throw new Error('Không tìm thấy tệp phương tiện nào có thể tải về từ liên kết này');
  }

  private handleCrawlError(
    detectedError: string | null,
    platform: string,
    hasChildExtractors: boolean,
    profileUrl: string,
    browserOverride: string | undefined,
    reject: (reason: Error) => void,
  ): void {
    const rawBrowser = browserOverride && browserOverride !== 'none' ? browserOverride : this.cfg.browserCookies;
    const browser = rawBrowser !== 'none' ? rawBrowser : 'Firefox';

    if (detectedError) {
      const lower = detectedError.toLowerCase();
      if (lower.includes('authrequired') || lower.includes('cookies') || lower.includes('401') || lower.includes('login')) {
        return reject(new Error(`Nền tảng này (${platform}) yêu cầu tài khoản có cookies đăng nhập từ ${browser}. Bạn hãy chuyển sang Đề mục 1 hoặc 2 để dán trực tiếp link bài viết/video cụ thể!`));
      }
      if (lower.includes('403') || lower.includes('blocked') || lower.includes('challenge')) {
        return reject(new Error(`Nền tảng (${platform}) kích hoạt cơ chế bảo mật chống quét tự động. Vui lòng dán link bài viết/video trực tiếp tại Đề mục 1 hoặc 2.`));
      }
      if (lower.includes('private')) {
        return reject(new Error('Tài khoản này đang ở chế độ Riêng tư, không thể quét công khai.'));
      }
      return reject(new Error(`Lỗi từ nền tảng: ${detectedError}`));
    }

    if (hasChildExtractors) {
      if (platform === 'instagram' || profileUrl.includes('instagram.com')) {
        return reject(new Error(`Instagram yêu cầu đăng nhập (cookies từ ${browser}) để quét toàn bộ trang cá nhân. Bạn hãy chuyển sang Đề mục 1 hoặc 2 để dán trực tiếp link bài viết/Reels!`));
      }
      if (platform === 'twitter' || platform === 'x' || profileUrl.includes('twitter.com') || profileUrl.includes('x.com')) {
        return reject(new Error(`X (Twitter) yêu cầu đăng nhập (cookies từ ${browser}) để quét trang cá nhân. Bạn hãy chuyển sang Đề mục 1 hoặc 2 để dán trực tiếp link bài đăng!`));
      }
      if (platform === 'pinterest' || profileUrl.includes('pinterest.com')) {
        return reject(new Error('Đối với Pinterest, vui lòng dán liên kết của một Bảng (Board) cụ thể để quét toàn bộ hình ảnh.'));
      }
      return reject(new Error('Liên kết này chứa danh mục con. Vui lòng cung cấp liên kết trực tiếp của bộ sưu tập hoặc bài viết cụ thể.'));
    }

    reject(new Error('Không tìm thấy tệp phương tiện công khai nào từ liên kết tài khoản này.'));
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Headers — dùng bởi ProxyDownloadService
  // ─────────────────────────────────────────────────────────────────────────

  /**
   * @deprecated Dùng ProxyDownloadService.getHeadersForUrl() thay thế
   * Giữ lại để backward compatibility với controller cũ
   */
  getHeadersForUrl(targetUrl: string): Record<string, string> {
    const ua = BROWSER_USER_AGENTS[this.cfg.browserCookies] || BROWSER_USER_AGENTS.chrome;
    const headers: Record<string, string> = {
      'User-Agent': ua,
      Accept: '*/*',
      'Accept-Language': 'en-US,en;q=0.9,vi;q=0.8',
    };

    const lower = targetUrl.toLowerCase();
    if (lower.includes('instagram.com') || lower.includes('cdninstagram.com')) headers.Referer = 'https://www.instagram.com/';
    else if (lower.includes('pinterest.com') || lower.includes('pinimg.com')) headers.Referer = 'https://www.pinterest.com/';
    else if (lower.includes('twitter.com') || lower.includes('twimg.com') || lower.includes('x.com')) headers.Referer = 'https://x.com/';
    else if (lower.includes('pixiv.net') || lower.includes('pximg.net')) headers.Referer = 'https://www.pixiv.net/';
    else if (lower.includes('artstation.com')) headers.Referer = 'https://www.artstation.com/';
    else if (lower.includes('reddit.com') || lower.includes('redd.it')) headers.Referer = 'https://www.reddit.com/';
    else if (lower.includes('weibo.com') || lower.includes('sinaimg.cn')) headers.Referer = 'https://weibo.com/';
    else if (lower.includes('bsky.app')) headers.Referer = 'https://bsky.app/';
    else if (lower.includes('tumblr.com')) headers.Referer = 'https://www.tumblr.com/';
    else if (lower.includes('tiktok.com')) headers.Referer = 'https://www.tiktok.com/';
    else if (lower.includes('deviantart.com')) headers.Referer = 'https://www.deviantart.com/';

    return headers;
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Helpers
  // ─────────────────────────────────────────────────────────────────────────

  private extractAuthorFromMeta(meta: any): string | null {
    if (!meta) return null;
    if (meta.author?.displayName) return String(meta.author.displayName);
    if (meta.author?.handle) return String(meta.author.handle);
    if (meta.pinner?.full_name) return String(meta.pinner.full_name);
    if (meta.pinner?.username) return String(meta.pinner.username);
    if (typeof meta.author === 'string') return meta.author;
    if (meta.author?.nick) return String(meta.author.nick);
    if (meta.author?.name) return String(meta.author.name);
    if (meta.author?.acct) return String(meta.author.acct); // Mastodon
    if (typeof meta.user === 'string') return meta.user;
    if (meta.user?.username) return String(meta.user.username);
    if (meta.user?.nick) return String(meta.user.nick);
    if (meta.user?.full_name) return String(meta.user.full_name);
    if (meta.user?.name) return String(meta.user.name);
    if (meta.user?.display_name) return String(meta.user.display_name); // Mastodon
    if (meta.uploader) return typeof meta.uploader === 'string' ? meta.uploader : String(meta.uploader?.name || '');
    if (meta.username) return String(meta.username);
    if (meta.creator) return typeof meta.creator === 'string' ? meta.creator : String(meta.creator?.name || '');
    if (meta.artist) return String(meta.artist); // Pixiv, ArtStation
    return null;
  }

  private extractAvatarFromMeta(meta: any): string | null {
    if (!meta) return null;
    return (
      meta.author?.avatar ||
      meta.avatar ||
      meta.pinner?.image_medium_url ||
      meta.pinner?.image_small_url ||
      meta.author?.profile_image ||
      meta.author?.avatar_url || // Mastodon
      meta.user?.profile_pic_url || // Instagram
      meta.user?.profile_image ||
      meta.user?.avatar_static || // Mastodon
      meta.creator?.avatar ||
      null
    );
  }

  private extractExt(url: string): string {
    try {
      const pathname = new URL(url).pathname;
      const lastSegment = pathname.split('/').pop() || '';
      if (lastSegment.includes('.')) return lastSegment.split('.').pop() || '';
    } catch {}
    return '';
  }

  private formatBytes(bytes: number): string {
    if (!bytes || bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
  }

  private formatDuration(seconds: number): string {
    if (!seconds || isNaN(seconds)) return '00:00';
    const secs = Math.floor(seconds % 60);
    const mins = Math.floor(seconds / 60);
    const hrs = Math.floor(mins / 60);
    if (hrs > 0) return `${String(hrs).padStart(2, '0')}:${String(mins % 60).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
    return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
  }
}
