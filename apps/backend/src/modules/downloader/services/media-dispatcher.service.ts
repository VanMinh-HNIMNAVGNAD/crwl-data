import { Injectable, Logger } from '@nestjs/common';
import { spawn } from 'node:child_process';
import * as fs from 'node:fs';
import { YtDlpService } from './yt-dlp.service.js';
import { GalleryDlService } from './gallery-dl.service.js';
import { BinaryManagerService } from './binary-manager.service.js';
import { MovieExtractorService } from './movie-extractor.service.js';
import type { MediaMetadataDto, ProfileCrawlResultDto } from '../dto/media.dto.js';

/**
 * MediaDispatcherService — bộ điều phối thông minh
 * Tự động chọn engine phù hợp dựa trên URL
 */
@Injectable()
export class MediaDispatcherService {
  private readonly logger = new Logger(MediaDispatcherService.name);

  constructor(
    private readonly ytDlpService: YtDlpService,
    private readonly galleryDlService: GalleryDlService,
    private readonly binaryManager: BinaryManagerService,
    private readonly movieExtractor: MovieExtractorService,
  ) {}

  // ─────────────────────────────────────────────────────────────────────────
  // URL Classification
  // ─────────────────────────────────────────────────────────────────────────

  /**
   * URL chứa video / audio — ưu tiên yt-dlp
   */
  private isVideoAudioPlatform(url: string): boolean {
    const lower = url.toLowerCase();
    const videoPlatforms = [
      'youtube.com', 'youtu.be',
      'tiktok.com',
      'twitch.tv', 'clips.twitch.tv',
      'dailymotion.com', 'dai.ly',
      'soundcloud.com',
      'nicovideo.jp', 'nico.ms',
      'bilibili.com', 'b23.tv',
      'crunchyroll.com',
      'funimation.com',
      'nebula.app',
      'rumble.com',
      'odysee.com', 'lbry.tv',
      'peertube',
      'bitchute.com',
      'facebook.com/reel', 'facebook.com/reels', 'facebook.com/watch', 'facebook.com/share/r', 'facebook.com/share/v', 'facebook.com/video', 'facebook.com/videos', '/videos/', 'fb.watch',
      'v.redd.it',
    ];
    return videoPlatforms.some((d) => lower.includes(d));
  }

  /**
   * URL là gallery / album / hình ảnh — ưu tiên gallery-dl
   */
  private isGalleryPlatform(url: string): boolean {
    const lower = url.toLowerCase();
    // Instagram: nếu là bài post / reel / stories
    if (lower.includes('instagram.com') || lower.includes('instagr.am')) {
      return (
        lower.includes('/p/') ||
        lower.includes('/reel/') ||
        lower.includes('/reels/') ||
        lower.includes('/stories/') ||
        lower.includes('/tv/')
      );
    }

    const galleryDomains = [
      'pinterest.com', 'pin.it',
      'imgur.com',
      'flickr.com',
      'deviantart.com',
      'artstation.com',
      'danbooru', 'gelbooru', 'safebooru',
      'pixiv.net',
      'reddit.com/gallery', 'reddit.com/r/', 'redd.it',
      'threads.net',
      'bsky.app',
      'mastodon', 'fosstodon', 'hachyderm', 'mstdn',
      'tumblr.com',
      'weibo.com',
      '/photo/', // TikTok photo slideshow
      'x.com/', 'twitter.com/',
      'facebook.com/photo', 'facebook.com/posts',
      'fanbox.cc',
      'patreon.com',
      'ko-fi.com',
      'newgrounds.com',
    ];
    return galleryDomains.some((d) => lower.includes(d));
  }

  /**
   * URL là tệp ảnh trực tiếp (CDN)
   */
  private isDirectImageUrl(url: string): boolean {
    const cleanUrl = url.split('?')[0].toLowerCase();
    const imageExtensions = ['.jpg', '.jpeg', '.png', '.webp', '.gif', '.svg', '.bmp', '.avif', '.heic'];
    if (imageExtensions.some((ext) => cleanUrl.endsWith(ext))) return true;

    const lower = url.toLowerCase();
    return (
      lower.includes('images.unsplash.com') ||
      lower.includes('i.pinimg.com') ||
      lower.includes('pbs.twimg.com/media') ||
      lower.includes('i.imgur.com') ||
      lower.includes('fbcdn.net') ||
      lower.includes('cdninstagram.com') ||
      lower.includes('pximg.net') ||
      lower.includes('redd.it/media') ||
      lower.includes('preview.redd.it')
    );
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Main Extract
  // ─────────────────────────────────────────────────────────────────────────

  /**
   * Điều phối trích xuất thông minh
   */
  async extract(url: string, browserOverride?: string): Promise<MediaMetadataDto> {
    const trimmed = url.trim();
    let result: MediaMetadataDto;

    // 1. Tệp ảnh trực tiếp (CDN)
    if (this.isDirectImageUrl(trimmed)) {
      try {
        this.logger.log(`Direct image: ${trimmed}`);
        result = await this.extractDirectImage(trimmed);
        return this.enhanceMetadata(result, trimmed);
      } catch (err: any) {
        this.logger.warn(`Direct image failed (${err.message}), fallback...`);
      }
    }

    // 2. Trang phim trực tuyến / luồng HLS (.m3u8) / DASH (.mpd)
    if (this.movieExtractor.isMovieOrStreamUrl(trimmed)) {
      try {
        this.logger.log(`Movie / Stream detected → movieExtractor: ${trimmed}`);
        result = await this.movieExtractor.extract(trimmed, browserOverride);
        return this.enhanceMetadata(result, trimmed);
      } catch (movieErr: any) {
        this.logger.warn(`Movie extractor error (${movieErr.message})`);
        throw movieErr;
      }
    }

    // 3. Nền tảng video/audio — dùng yt-dlp trực tiếp
    if (this.isVideoAudioPlatform(trimmed)) {
      try {
        this.logger.log(`Video platform → yt-dlp: ${trimmed}`);
        // Với TikTok video, ưu tiên chạy không cookies trước nếu không chỉ định rõ browser
        const bOverride = trimmed.includes('tiktok.com') && (!browserOverride || browserOverride === 'auto') ? 'none' : browserOverride;
        result = await this.ytDlpService.extractMetadata(trimmed, bOverride);
        return this.enhanceMetadata(result, trimmed);
      } catch (ytErr: any) {
        if (trimmed.includes('tiktok.com') && browserOverride && browserOverride !== 'none') {
          try {
            this.logger.log(`TikTok single video thử lại không dùng cookies ('none')...`);
            result = await this.ytDlpService.extractMetadata(trimmed, 'none');
            return this.enhanceMetadata(result, trimmed);
          } catch {}
        }
        // Nếu yt-dlp gặp lỗi (chặn IP/challenge), fallback sang gallery-dl cho TikTok video/photo
        if (trimmed.includes('tiktok.com')) {
          this.logger.warn(`TikTok → gallery-dl fallback: ${trimmed}`);
          try {
            result = await this.galleryDlService.extractGallery(trimmed, browserOverride);
            return this.enhanceMetadata(result, trimmed);
          } catch {}
        }
        throw ytErr;
      }
    }

    // 3. Nền tảng gallery/album — dùng gallery-dl trực tiếp
    if (this.isGalleryPlatform(trimmed)) {
      try {
        this.logger.log(`Gallery platform → gallery-dl: ${trimmed}`);
        result = await this.galleryDlService.extractGallery(trimmed, browserOverride);

        // Đối với Reddit: Nếu bài viết được trích xuất là video đơn lẻ,
        // thử tăng cường bằng yt-dlp để lấy toàn bộ các stream đa độ phân giải và audio MP3 chất lượng cao
        const isReddit = trimmed.includes('reddit.com') || trimmed.includes('redd.it');
        if (isReddit && result.type === 'video') {
          try {
            const ytResult = await this.ytDlpService.extractMetadata(trimmed, browserOverride);
            if (ytResult && ytResult.streams && ytResult.streams.length > 0) {
              return this.enhanceMetadata(ytResult, trimmed);
            }
          } catch {
            // Giữ kết quả video từ gallery-dl nếu yt-dlp không thành công
          }
        }

        return this.enhanceMetadata(result, trimmed);
      } catch (galleryErr: any) {
        this.logger.warn(`gallery-dl failed (${galleryErr.message}), yt-dlp fallback...`);
        try {
          result = await this.ytDlpService.extractMetadata(trimmed, browserOverride);
          return this.enhanceMetadata(result, trimmed);
        } catch (ytErr: any) {
          throw new Error(galleryErr.message || ytErr.message || 'Không thể trích xuất nội dung từ liên kết này');
        }
      }
    }

    // 4. URL không rõ — thử yt-dlp trước, nếu fail thì gallery-dl
    try {
      this.logger.log(`Unknown URL → yt-dlp first: ${trimmed}`);
      result = await this.ytDlpService.extractMetadata(trimmed, browserOverride);
      return this.enhanceMetadata(result, trimmed);
    } catch (ytError: any) {
      this.logger.warn(`yt-dlp failed (${ytError.message}), gallery-dl fallback...`);
      try {
        result = await this.galleryDlService.extractGallery(trimmed, browserOverride);
        return this.enhanceMetadata(result, trimmed);
      } catch (galleryError: any) {
        this.logger.error(`Both engines failed for ${trimmed}`);
        const finalMsg =
          galleryError.message?.includes('Lỗi từ nền tảng') ||
          galleryError.message?.includes('cookies') ||
          galleryError.message?.includes('Riêng tư')
            ? galleryError.message
            : `Không thể phân tích nội dung từ liên kết này. Vui lòng kiểm tra lại URL hoặc quyền riêng tư của bài viết. (Chi tiết: ${galleryError.message || ytError.message})`;
        throw new Error(finalMsg);
      }
    }
  }

  /**
   * Bổ sung nhãn Reels/Shorts cho metadata nếu khớp đường dẫn
   */
  private enhanceMetadata(res: MediaMetadataDto, url: string): MediaMetadataDto {
    const lower = url.toLowerCase();
    const isReel =
      lower.includes('/reel/') ||
      lower.includes('/reels/') ||
      lower.includes('/share/r/') ||
      Boolean(res.isReel);
    const isShort = lower.includes('/shorts/') || Boolean(res.isShort);
    if (isReel) res.isReel = true;
    if (isShort) res.isShort = true;

    if (res.platform) {
      const p = res.platform.toLowerCase();
      if (p.includes('twitter') || p.includes('x.com')) res.platform = 'x';
      else if (p.includes('twitch')) res.platform = 'twitch';
      else if (p.includes('facebook')) res.platform = 'facebook';
      else if (p.includes('youtube')) res.platform = 'youtube';
      else if (p.includes('tiktok')) res.platform = 'tiktok';
      else if (p.includes('instagram')) res.platform = 'instagram';
      else if (p.includes('pinterest')) res.platform = 'pinterest';
      else if (p.includes('reddit')) res.platform = 'reddit';
      else if (p.includes('soundcloud')) res.platform = 'soundcloud';
      else if (p.includes('dailymotion')) res.platform = 'dailymotion';
      else if (p.includes('bilibili')) res.platform = 'bilibili';
      else if (p.includes('bluesky') || p.includes('bsky')) res.platform = 'bluesky';
      else if (p.includes('movie') || p.includes('hls') || p.includes('phim') || p === 'generic') res.platform = 'movie';
    }

    return res;
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Profile Crawl
  // ─────────────────────────────────────────────────────────────────────────

  /**
   * Quét toàn bộ hồ sơ (Profile / Channel / Playlist / Board)
   */
  async crawlProfile(
    profileUrl: string,
    limit = 50,
    mediaType: 'all' | 'video' | 'image' = 'all',
    platformHint?: string,
    browserOverride?: string,
    rangeStart?: number,
    rangeEnd?: number,
  ): Promise<ProfileCrawlResultDto> {
    let targetUrl = profileUrl.trim();
    const cleanHint = (platformHint || '').toLowerCase();

    const isYouTube =
      cleanHint === 'youtube' || targetUrl.includes('youtube.com') || targetUrl.includes('youtu.be');
    const isTikTok = cleanHint === 'tiktok' || targetUrl.includes('tiktok.com');
    const isInstagram = cleanHint === 'instagram' || targetUrl.includes('instagram.com');
    const isPinterest = cleanHint === 'pinterest' || targetUrl.includes('pinterest.com') || targetUrl.includes('pin.it');
    const isReddit = cleanHint === 'reddit' || targetUrl.includes('reddit.com') || targetUrl.includes('redd.it');
    const isTwitter = cleanHint === 'x' || cleanHint === 'twitter' || targetUrl.includes('twitter.com') || targetUrl.includes('x.com');
    const isSoundCloud = cleanHint === 'soundcloud' || targetUrl.includes('soundcloud.com');
    const isTwitch = cleanHint === 'twitch' || targetUrl.includes('twitch.tv');

    // Xử lý username (@username) hoặc URL thiếu giao thức (http/https)
    const hasDomain = targetUrl.includes('.com') || targetUrl.includes('.be') || targetUrl.includes('.it') || targetUrl.includes('.tv') || targetUrl.includes('.net') || targetUrl.includes('.org');
    if (targetUrl.startsWith('@') || (!targetUrl.startsWith('http://') && !targetUrl.startsWith('https://'))) {
      if (hasDomain) {
        targetUrl = `https://${targetUrl}`;
      } else {
        const username = targetUrl.replace(/^@+/, '');
        if (cleanHint === 'tiktok') targetUrl = `https://www.tiktok.com/@${username}`;
        else if (cleanHint === 'instagram') {
          // Tự động chuyển sang tab /reels/ nếu người dùng lọc video, ngược lại /posts/
          targetUrl = mediaType === 'video'
            ? `https://www.instagram.com/${username}/reels/`
            : `https://www.instagram.com/${username}/posts/`;
        }
        else if (cleanHint === 'pinterest') targetUrl = `https://www.pinterest.com/${username}/`;
        else if (cleanHint === 'reddit') targetUrl = `https://www.reddit.com/user/${username}/`;
        else if (cleanHint === 'x' || cleanHint === 'twitter') targetUrl = `https://x.com/${username}/media`;
        else if (cleanHint === 'soundcloud') targetUrl = `https://soundcloud.com/${username}`;
        else if (cleanHint === 'twitch') targetUrl = `https://www.twitch.tv/${username}/videos`;
        else targetUrl = `https://www.youtube.com/@${username}/videos`;
      }
    } else if (isTwitter) {
      const cleanUrl = targetUrl.split('?')[0].replace(/\/+$/, '');
      if (!cleanUrl.includes('/media') && !cleanUrl.includes('/status')) {
        targetUrl = `${cleanUrl}/media`;
      }
    } else if (isInstagram) {
      const cleanUrl = targetUrl.split('?')[0].replace(/\/+$/, '');
      if (
        !cleanUrl.includes('/p/') &&
        !cleanUrl.includes('/reel/') &&
        !cleanUrl.includes('/stories/') &&
        !cleanUrl.endsWith('/posts') &&
        !cleanUrl.endsWith('/reels') &&
        !cleanUrl.endsWith('/tagged')
      ) {
        // Tự động chuyển sang tab /reels/ nếu người dùng lọc video, ngược lại /posts/
        targetUrl = mediaType === 'video' ? `${cleanUrl}/reels/` : `${cleanUrl}/posts/`;
      }
    }

    const hasCustomRange = rangeStart && rangeEnd && rangeEnd >= rangeStart;

    // YouTube / Playlist — dùng yt-dlp
    if (isYouTube || targetUrl.includes('youtube.com') || targetUrl.includes('youtu.be') || targetUrl.includes('/playlist')) {
      try {
        if (hasCustomRange) {
          this.logger.log(`YouTube range (${rangeStart}-${rangeEnd}): ${targetUrl}`);
          return await this.ytDlpService.extractPlaylistRange(targetUrl, rangeStart!, rangeEnd!, browserOverride);
        }
        this.logger.log(`YouTube → yt-dlp playlist: ${targetUrl}`);
        return await this.ytDlpService.extractPlaylist(targetUrl, limit, browserOverride);
      } catch (ytErr: any) {
        this.logger.warn(`yt-dlp playlist failed: ${ytErr.message}`);
        throw ytErr;
      }
    }

    // SoundCloud — dùng yt-dlp (hỗ trợ tốt)
    if (isSoundCloud) {
      try {
        this.logger.log(`SoundCloud → yt-dlp: ${targetUrl}`);
        return await this.ytDlpService.extractPlaylist(targetUrl, limit, browserOverride);
      } catch (ytErr: any) {
        this.logger.warn(`SoundCloud yt-dlp failed: ${ytErr.message}`);
        throw ytErr;
      }
    }


    // Twitch VOD list — dùng yt-dlp
    if (isTwitch) {
      try {
        this.logger.log(`Twitch → yt-dlp: ${targetUrl}`);
        return await this.ytDlpService.extractPlaylist(targetUrl, limit, browserOverride);
      } catch (ytErr: any) {
        this.logger.warn(`Twitch yt-dlp failed: ${ytErr.message}`);
        throw ytErr;
      }
    }

    // TikTok profile — ưu tiên yt-dlp không dùng cookies, tự động fallback sang tiktok_resolver nếu bị WAF/rate-limit
    if (isTikTok) {
      if (targetUrl.includes('/@') && !targetUrl.includes('/video/')) {
        targetUrl = targetUrl.split('?')[0].replace(/\/+$/, '');
      }
      // Ưu tiên 'none' cho TikTok để crawl ổn định
      const tiktokBrowser = (browserOverride === 'none' || !browserOverride || browserOverride === 'auto' || browserOverride === 'edge') ? 'none' : browserOverride;
      try {
        this.logger.log(`TikTok → yt-dlp playlist: ${targetUrl} (limit: ${limit}, browser: ${tiktokBrowser})`);
        const res = hasCustomRange
          ? await this.ytDlpService.extractPlaylistRange(targetUrl, rangeStart!, rangeEnd!, tiktokBrowser)
          : await this.ytDlpService.extractPlaylist(targetUrl, limit, tiktokBrowser);
        if (res.media && res.media.length > 0) return { ...res, platform: 'tiktok' };
        throw new Error('Không tìm thấy tệp phương tiện nào từ TikTok');
      } catch (ytErr: any) {
        if (tiktokBrowser !== 'none') {
          try {
            this.logger.log(`TikTok thử lại hoàn toàn không dùng cookies ('none')...`);
            const resNoCookies = hasCustomRange
              ? await this.ytDlpService.extractPlaylistRange(targetUrl, rangeStart!, rangeEnd!, 'none')
              : await this.ytDlpService.extractPlaylist(targetUrl, limit, 'none');
            if (resNoCookies.media && resNoCookies.media.length > 0) return { ...resNoCookies, platform: 'tiktok' };
          } catch {}
        }

        // Cứu hộ bằng TikTok embed resolver (curl_cffi với TLS impersonation)
        this.logger.warn(`TikTok yt-dlp thất bại (${ytErr.message}), đang thử TikTok embed resolver...`);
        try {
          const embedRes = await this.resolveTikTokWithEmbed(targetUrl, limit);
          if (embedRes && embedRes.media && embedRes.media.length > 0) {
            this.logger.log(`✅ TikTok embed resolver thành công: ${embedRes.media.length} videos`);
            return embedRes;
          }
        } catch (resolverErr: any) {
          this.logger.warn(`TikTok embed resolver thất bại: ${resolverErr.message}`);
        }

        this.logger.warn(`Thử tiếp gallery-dl fallback...`);
        try {
          const res = await this.galleryDlService.crawlProfile(targetUrl, limit, mediaType, browserOverride, rangeStart, rangeEnd);
          if (res.media && res.media.length > 0) return { ...res, platform: 'tiktok' };
        } catch (galleryErr: any) {
          this.logger.warn(`TikTok gallery-dl cũng thất bại: ${galleryErr.message}`);
        }
        throw ytErr;
      }
    }

    // Mạng xã hội hình ảnh — gallery-dl
    try {
      this.logger.log(`Social → gallery-dl: ${targetUrl} (limit: ${limit})`);
      const res = await this.galleryDlService.crawlProfile(targetUrl, limit, mediaType, browserOverride, rangeStart, rangeEnd);
      if (res.media && res.media.length > 0) return res;
      throw new Error('Không tìm thấy tệp phương tiện công khai nào');
    } catch (galleryErr: any) {
      // Fallback sang yt-dlp cho Facebook (có video)
      if (targetUrl.includes('facebook.com')) {
        this.logger.warn(`gallery-dl failed (${galleryErr.message}), yt-dlp fallback...`);
        try {
          return await this.ytDlpService.extractPlaylist(targetUrl, limit, browserOverride);
        } catch {
          // Ném lỗi chi tiết từ gallery-dl
        }
      }
      throw galleryErr;
    }
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Direct Image
  // ─────────────────────────────────────────────────────────────────────────

  private async extractDirectImage(url: string): Promise<MediaMetadataDto> {
    const headers = this.galleryDlService.getHeadersForUrl(url);
    let contentType = 'image/jpeg';
    let sizeStr = 'Tự động';

    try {
      const resp = await fetch(url, { method: 'HEAD', headers });
      if (resp.ok) {
        contentType = resp.headers.get('content-type') || contentType;
        const contentLength = resp.headers.get('content-length');
        if (contentLength) {
          const bytes = Number(contentLength);
          if (!isNaN(bytes)) {
            const k = 1024;
            const sizes = ['B', 'KB', 'MB', 'GB'];
            const i = Math.floor(Math.log(bytes) / Math.log(k));
            sizeStr = `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
          }
        }
      }
    } catch {
      // Tiếp tục với mặc định
    }

    let ext = 'jpg';
    if (contentType.includes('png')) ext = 'png';
    else if (contentType.includes('webp')) ext = 'webp';
    else if (contentType.includes('gif')) ext = 'gif';
    else if (contentType.includes('svg')) ext = 'svg';
    else if (contentType.includes('avif')) ext = 'avif';

    let filename = `image_${Date.now()}`;
    try {
      const parsedPath = new URL(url).pathname;
      const lastSeg = parsedPath.split('/').pop();
      if (lastSeg && lastSeg.length > 2) {
        filename = lastSeg.split('.')[0].replace(/[/\\?%*:|"<>]/g, '_');
      }
    } catch {}

    return {
      id: String(Date.now()),
      platform: 'direct',
      title: `${filename} (Hình ảnh trực tiếp)`,
      author: 'Hình ảnh Web',
      authorUrl: url,
      duration: '1 hình ảnh',
      views: 'Chất lượng gốc',
      thumbnail: url,
      highResThumbnail: url,
      type: 'album',
      originalUrl: url,
      images: [{
        id: 1, url, title: filename,
        resolution: 'Ảnh gốc HD', size: sizeStr,
        type: ext === 'gif' ? 'gif' : 'image', ext, thumb: url,
      }],
    };
  }

  /**
   * Cứu hộ quét profile TikTok bằng công cụ Frontity Embed + curl_cffi TLS impersonation
   */
  private async resolveTikTokWithEmbed(targetUrl: string, limit: number): Promise<ProfileCrawlResultDto | null> {
    const pythonPath = this.binaryManager.getToolsPythonPath();
    const resolverScript = this.binaryManager.getTiktokResolverPath();

    if (!fs.existsSync(resolverScript)) {
      this.logger.warn(`TikTok resolver script không tồn tại: ${resolverScript}`);
      return null;
    }

    return new Promise((resolve) => {
      const proc = spawn(pythonPath, [resolverScript, targetUrl, String(limit)]);
      let stdout = '';
      let stderr = '';

      proc.stdout.on('data', (d) => { stdout += d.toString(); });
      proc.stderr.on('data', (d) => { stderr += d.toString(); });

      const timeout = setTimeout(() => {
        if (!proc.killed) proc.kill('SIGTERM');
        resolve(null);
      }, 25000);

      proc.on('close', (code) => {
        clearTimeout(timeout);
        if (code === 0 && stdout.trim()) {
          try {
            const data = JSON.parse(stdout.trim());
            if (data && data.media && data.media.length > 0) {
              return resolve(data);
            }
          } catch (e: any) {
            this.logger.warn(`Lỗi parse output từ tiktok_resolver: ${e.message}`);
          }
        } else {
          this.logger.warn(`tiktok_resolver thoát với mã ${code}: ${stderr.trim()}`);
        }
        resolve(null);
      });
    });
  }
}

