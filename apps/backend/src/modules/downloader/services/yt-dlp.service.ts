import { Injectable, Logger } from '@nestjs/common';
import { spawn } from 'node:child_process';
import { Readable, PassThrough } from 'node:stream';
import * as fs from 'node:fs';
import * as path from 'node:path';
import * as os from 'node:os';
import { BinaryManagerService } from './binary-manager.service.js';
import { loadDownloaderConfig, resolveBrowser } from '../config/downloader.config.js';
import type {
  StreamFormatDto,
  MediaMetadataDto,
  SubtitleDto,
  ChapterDto,
  ProfileCrawlResultDto,
  CrawlMediaItemDto,
} from '../dto/media.dto.js';

export interface DownloadStreamOptions {
  formatId?: string;
  isAudioOnly?: boolean;
  audioFormat?: string; // 'mp3' | 'm4a' | 'flac' | 'wav' | 'opus'
  audioBitrate?: string; // '320k' | '256k' | '192k' | '128k'
  startTime?: string;
  endTime?: string;
  isMute?: boolean;
  streamType?: string;
  format?: string;
  /** Bật SponsorBlock để tự động bỏ qua sponsor/intro/outro (YouTube) */
  sponsorBlock?: boolean;
  /** Nhúng thumbnail vào file audio (yêu cầu ffmpeg + AtomicParsley) */
  embedThumbnail?: boolean;
  /** Nhúng metadata vào file audio */
  embedMetadata?: boolean;
  /** Trình duyệt dùng để lấy cookies (tùy chọn) */
  browser?: string;
  /** Referer header truyền vào yt-dlp khi tải video/m3u8 */
  referer?: string;
}

@Injectable()
export class YtDlpService {
  private readonly logger = new Logger(YtDlpService.name);
  private readonly cfg = loadDownloaderConfig();

  constructor(private readonly binaryManager: BinaryManagerService) {}

  private get ytDlpPath(): string {
    return this.binaryManager.getYtDlpPath();
  }

  /**
   * Build các arg cơ bản dùng chung
   */
  private getBaseArgs(allowPlaylist = false, browserOverride?: string, targetUrl?: string): string[] {
    const args: string[] = [
      '--no-warnings',
      '--ignore-errors',
    ];

    // Chỉ bật --js-runtimes khi cần (như YouTube n-sig); KHÔNG dùng cho TikTok vì Node.js runtime làm hỏng signature challenge của TikTok
    const isTikTok = targetUrl ? (targetUrl.includes('tiktok.com') || targetUrl.includes('vt.tiktok') || targetUrl.includes('vm.tiktok')) : false;
    if (!isTikTok && this.cfg.nodePath) {
      args.push('--js-runtimes', `node:${this.cfg.nodePath}`);
    }

    // Với Facebook, dùng impersonate Chrome để tránh checkpoint và block Cannot parse data
    const isFacebook = targetUrl ? (targetUrl.includes('facebook.com') || targetUrl.includes('fb.watch') || targetUrl.includes('fb.com')) : false;
    if (isFacebook && this.binaryManager.hasImpersonation()) {
      args.push('--impersonate', 'Chrome-120:Macos-14');
    }

    if (!allowPlaylist) {
      args.push('--no-playlist');
    }

    const targetBrowser = resolveBrowser(browserOverride, this.cfg.browserCookies);

    // Nếu người gọi yêu cầu 'none' hoặc browser chọn 'none', KHÔNG dùng bất kỳ cookies nào
    if (browserOverride === 'none' || targetBrowser === 'none') {
      return args;
    }

    // Cookies từ file (.txt) ưu tiên hơn browser
    const cookiesFile = this.cfg.cookiesFilePath;
    if (cookiesFile && fs.existsSync(cookiesFile)) {
      args.push('--cookies', cookiesFile);
    } else if (targetBrowser) {
      args.push('--cookies-from-browser', targetBrowser);
    }

    return args;
  }

  /**
   * Chạy tiến trình yt-dlp với timeout — tránh treo vô hạn
   */
  private spawnWithTimeout(
    ytDlpPath: string,
    args: string[],
    timeoutMs: number,
  ): { proc: ReturnType<typeof spawn>; timeoutId: NodeJS.Timeout } {
    const proc = spawn(ytDlpPath, args);
    const timeoutId = setTimeout(() => {
      if (!proc.killed) {
        this.logger.warn(`yt-dlp timeout (${timeoutMs}ms) — killing process`);
        proc.kill('SIGTERM');
      }
    }, timeoutMs);
    proc.on('close', () => clearTimeout(timeoutId));
    return { proc, timeoutId };
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Extract / Metadata
  // ─────────────────────────────────────────────────────────────────────────

  /**
   * Trích xuất toàn bộ metadata chi tiết dưới dạng JSON
   * Tự động fallback sang không dùng cookies nếu cookies phiên bị lỗi
   */
  async extractMetadata(url: string, browserOverride?: string): Promise<MediaMetadataDto> {
    try {
      return await this.doExtractMetadata(url, browserOverride);
    } catch (err: any) {
      if (browserOverride !== 'none') {
        this.logger.warn(`yt-dlp extract thất bại với cookies (${browserOverride || 'default'}). Đang tự động thử lại không dùng cookies...`);
        return await this.doExtractMetadata(url, 'none');
      }
      throw err;
    }
  }

  private async doExtractMetadata(url: string, browserOverride?: string): Promise<MediaMetadataDto> {
    return new Promise((resolve, reject) => {
      const args = [...this.getBaseArgs(false, browserOverride, url), '--dump-json', url];

      this.logger.log(`yt-dlp extract: ${url}`);
      const { proc, timeoutId } = this.spawnWithTimeout(this.ytDlpPath, args, this.cfg.downloadTimeoutMs);

      let stdoutData = '';
      let stderrData = '';

      proc.stdout?.on('data', (chunk) => { stdoutData += chunk.toString(); });
      proc.stderr?.on('data', (chunk) => { stderrData += chunk.toString(); });

      proc.on('close', (code) => {
        clearTimeout(timeoutId);
        if (code !== 0 && !stdoutData.trim()) {
          this.logger.error(`yt-dlp failed code=${code}: ${stderrData}`);
          return reject(new Error(`Không thể trích xuất thông tin video: ${stderrData || `Exit code ${code}`}`));
        }

        try {
          const firstLine = stdoutData.trim().split('\n').find((l) => l.startsWith('{'));
          if (!firstLine) {
            return reject(new Error('Dữ liệu trả về từ yt-dlp không phải JSON hợp lệ'));
          }
          const raw = JSON.parse(firstLine);
          resolve(this.normalizeYtDlpOutput(raw, url));
        } catch (err: any) {
          reject(new Error(`Lỗi phân tích cú pháp dữ liệu: ${err.message}`));
        }
      });

      proc.on('error', (err) => reject(new Error(`Không thể khởi chạy yt-dlp: ${err.message}`)));
    });
  }

  /**
   * Trích xuất danh sách video từ Playlist / Kênh
   */
  async extractPlaylist(url: string, limit = 50, browserOverride?: string): Promise<ProfileCrawlResultDto> {
    try {
      return await this.doExtractPlaylist(url, limit, browserOverride);
    } catch (err: any) {
      if (browserOverride !== 'none') {
        this.logger.warn(`yt-dlp playlist thất bại với cookies (${browserOverride || 'default'}). Thử lại không dùng cookies...`);
        return await this.doExtractPlaylist(url, limit, 'none');
      }
      throw err;
    }
  }

  private async doExtractPlaylist(url: string, limit = 50, browserOverride?: string): Promise<ProfileCrawlResultDto> {
    return new Promise((resolve, reject) => {
      const args = [
        ...this.getBaseArgs(true, browserOverride, url),
        '--flat-playlist',
        '--dump-json',
        '--playlist-items', `1-${limit}`,
        url,
      ];

      this.logger.log(`yt-dlp playlist: ${url} (limit: ${limit}, browser: ${browserOverride || 'default'})`);
      const { proc } = this.spawnWithTimeout(this.ytDlpPath, args, this.cfg.downloadTimeoutMs);

      let stdoutData = '';
      let stderrData = '';

      proc.stdout?.on('data', (c) => { stdoutData += c.toString(); });
      proc.stderr?.on('data', (c) => { stderrData += c.toString(); });

      proc.on('close', (code) => {
        if (code !== 0 && !stdoutData.trim()) {
          return reject(new Error(`Không thể trích xuất danh sách video: ${stderrData || `Exit code ${code}`}`));
        }

        try {
          resolve(this.parsePlaylistData(stdoutData, url));
        } catch (err: any) {
          reject(new Error(`Lỗi phân tích playlist: ${err.message}`));
        }
      });

      proc.on('error', (err) => reject(new Error(`Không thể khởi chạy yt-dlp: ${err.message}`)));
    });
  }

  /**
   * Trích xuất playlist với range tùy chỉnh (from-to)
   */
  async extractPlaylistRange(url: string, from: number, to: number, browserOverride?: string): Promise<ProfileCrawlResultDto> {
    try {
      return await this.doExtractPlaylistRange(url, from, to, browserOverride);
    } catch (err: any) {
      if (browserOverride !== 'none') {
        this.logger.warn(`yt-dlp playlist range thất bại với cookies (${browserOverride || 'default'}). Thử lại không dùng cookies...`);
        return await this.doExtractPlaylistRange(url, from, to, 'none');
      }
      throw err;
    }
  }

  private async doExtractPlaylistRange(url: string, from: number, to: number, browserOverride?: string): Promise<ProfileCrawlResultDto> {
    return new Promise((resolve, reject) => {
      const args = [
        ...this.getBaseArgs(true, browserOverride, url),
        '--flat-playlist',
        '--dump-json',
        '--playlist-items', `${from}-${to}`,
        url,
      ];

      this.logger.log(`yt-dlp playlist range (${from}-${to}): ${url} (browser: ${browserOverride || 'default'})`);
      const { proc } = this.spawnWithTimeout(this.ytDlpPath, args, this.cfg.downloadTimeoutMs);

      let stdoutData = '';
      let stderrData = '';

      proc.stdout?.on('data', (c) => { stdoutData += c.toString(); });
      proc.stderr?.on('data', (c) => { stderrData += c.toString(); });

      proc.on('close', (code) => {
        if (code !== 0 && !stdoutData.trim()) {
          return reject(new Error(`Không thể trích xuất playlist range: ${stderrData || `Exit code ${code}`}`));
        }

        try {
          resolve(this.parsePlaylistData(stdoutData, url));
        } catch (err: any) {
          reject(new Error(`Lỗi phân tích playlist: ${err.message}`));
        }
      });

      proc.on('error', (err) => reject(new Error(`Không thể khởi chạy yt-dlp: ${err.message}`)));
    });
  }

  /**
   * Helper parse danh sách JSON lines từ flat-playlist
   */
  private parsePlaylistData(stdoutData: string, url: string): ProfileCrawlResultDto {
    const lines = stdoutData.trim().split('\n').filter((l) => l.startsWith('{'));
    const media: CrawlMediaItemDto[] = [];
    let channelName = 'YouTube Channel / Playlist';
    let channelHandle = '@youtube';
    let avatar = '';

    let idx = 1;
    for (const line of lines) {
      try {
        const item = JSON.parse(line);
        if (item.channel || item.uploader) {
          channelName = item.channel || item.uploader;
          channelHandle = `@${item.channel_id || item.uploader_id || channelName.toLowerCase().replace(/\s+/g, '')}`;
        }

        const bestThumb = item.thumbnails?.length > 0
          ? item.thumbnails[item.thumbnails.length - 1].url
          : (item.thumbnail || '');

        const videoUrl = item.url?.startsWith('http')
          ? item.url
          : `https://www.youtube.com/watch?v=${item.id}`;

        const isShort = videoUrl.includes('/shorts/') || Boolean(item.webpage_url?.includes('/shorts/'));
        const isReel = videoUrl.includes('/reel/') || videoUrl.includes('/reels/') || videoUrl.includes('/share/r/');

        media.push({
          id: idx,
          type: item.live_status === 'is_live' ? 'video' : 'video',
          title: item.title || `Video ${idx}`,
          duration: item.duration ? this.formatDuration(item.duration) : 'Video',
          quality: 'HD',
          size: 'Tự động',
          thumb: bestThumb,
          url: videoUrl,
          author: item.uploader || item.channel || channelName,
          views: item.view_count ? `${this.formatNumber(item.view_count)} lượt xem` : undefined,
          isLive: item.live_status === 'is_live',
          isReel: isReel || undefined,
          isShort: isShort || undefined,
        });
        idx++;
      } catch {
        // Bỏ qua dòng lỗi lẻ
      }
    }

    const lowerUrl = url.toLowerCase();
    let detectedPlatform = 'youtube';
    if (lowerUrl.includes('tiktok.com')) detectedPlatform = 'tiktok';
    else if (lowerUrl.includes('instagram.com')) detectedPlatform = 'instagram';
    else if (lowerUrl.includes('twitter.com') || lowerUrl.includes('x.com')) detectedPlatform = 'twitter';
    else if (lowerUrl.includes('facebook.com') || lowerUrl.includes('fb.watch')) detectedPlatform = 'facebook';
    else if (lowerUrl.includes('soundcloud.com')) detectedPlatform = 'soundcloud';
    else if (lowerUrl.includes('twitch.tv')) detectedPlatform = 'twitch';
    else if (lowerUrl.includes('bilibili.com')) detectedPlatform = 'bilibili';
    else if (lowerUrl.includes('pinterest.com')) detectedPlatform = 'pinterest';

    return {
      platform: detectedPlatform,
      name: channelName,
      handle: channelHandle,
      url,
      avatar,
      stats: `Đã tìm thấy ${media.length} video`,
      media,
      totalCount: media.length,
    };
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Download Streams
  // ─────────────────────────────────────────────────────────────────────────

  /**
   * Tạo stream tải trực tiếp với đầy đủ tùy chọn
   */
  createDownloadStream(
    url: string,
    options: DownloadStreamOptions = {},
  ): { stream: Readable; process: any; filename: string; contentType: string } {
    const {
      formatId,
      isAudioOnly,
      audioFormat = 'mp3',
      audioBitrate = '320k',
      startTime,
      endTime,
      isMute,
      streamType,
      format,
      sponsorBlock = this.cfg.enableSponsorBlock,
      embedThumbnail = this.cfg.embedThumbnail,
      embedMetadata = this.cfg.embedMetadata,
      browser,
      referer,
    } = options;

    const isAudio = isAudioOnly || formatId === 'mp3' || formatId === 'audio'
      || formatId?.startsWith('audio') || formatId?.startsWith('mp3')
      || formatId?.startsWith('m4a') || formatId?.startsWith('flac')
      || formatId?.startsWith('wav') || streamType === 'audio';
    const isMuteStream = isMute || streamType === 'mute';

    const args = [...this.getBaseArgs(false, browser, url), '-o', '-'];

    if (referer) {
      args.push('--add-header', `Referer:${referer}`);
    }

    // Cắt clip theo mốc thời gian
    const normalizeTime = (t?: string) => {
      if (!t) return null;
      const clean = t.trim();
      if (!clean) return null;
      const parts = clean.split(':');
      if (parts.length === 1) return clean;
      if (parts.length === 2) return `00:${parts[0].padStart(2, '0')}:${parts[1].padStart(2, '0')}`;
      return `${parts[0].padStart(2, '0')}:${parts[1].padStart(2, '0')}:${parts[2].padStart(2, '0')}`;
    };

    const startNorm = normalizeTime(startTime);
    const endNorm = normalizeTime(endTime);
    if (startNorm || endNorm) {
      args.push('--download-sections', `*${startNorm || '00:00:00'}-${endNorm || 'inf'}`);
    }

    // SponsorBlock — chỉ có tác dụng với YouTube
    if (sponsorBlock && (url.includes('youtube.com') || url.includes('youtu.be'))) {
      args.push('--sponsorblock-remove', 'default');
    }

    let contentType = 'video/mp4';
    let ext = 'mp4';

    if (isAudio) {
      const selectedExt = ['mp3', 'm4a', 'flac', 'wav', 'opus'].includes(audioFormat.toLowerCase())
        ? audioFormat.toLowerCase() : 'mp3';
      ext = selectedExt;

      const audioContentTypes: Record<string, string> = {
        mp3: 'audio/mpeg', m4a: 'audio/mp4', flac: 'audio/flac',
        wav: 'audio/wav', opus: 'audio/opus',
      };
      contentType = audioContentTypes[ext] || 'audio/mpeg';

      // Nếu embed thumbnail hoặc metadata, cần output ra file tạm (không thể stream trực tiếp)
      const needsPostProcess = (embedThumbnail || embedMetadata) && this.binaryManager.hasFfmpeg();

      if (needsPostProcess) {
        // Output ra file tạm, xử lý xong rồi stream
        return this.createAudioWithEmbedStream(url, ext, audioBitrate, embedThumbnail, embedMetadata, browser);
      }

      // Stream trực tiếp qua FFmpeg (không embed)
      args.push('-f', 'bestaudio/best');
      args.push(url);

      this.logger.log(`yt-dlp audio stream → FFmpeg (${ext.toUpperCase()} ${audioBitrate}): ${url}`);
      const ytProc = spawn(this.ytDlpPath, args);
      ytProc.stderr?.on('data', (d) => {
        const msg = d.toString().trim();
        if (msg && !msg.includes('[download]')) this.logger.debug(`yt-dlp audio: ${msg}`);
      });

      // Spawn FFmpeg để convert audio sang format và bitrate mong muốn
      const ffmpegArgs = [
        '-i', 'pipe:0',
        '-b:a', audioBitrate,
        '-f', ext === 'm4a' ? 'mp4' : ext,
        'pipe:1',
      ];
      const ffmpegProc = spawn('ffmpeg', ffmpegArgs);

      ytProc.stdout?.pipe(ffmpegProc.stdin as any);

      ffmpegProc.stderr.on('data', (d) => {
        const msg = d.toString().trim();
        if (msg && !msg.includes('frame=') && !msg.includes('size=')) {
          this.logger.debug(`ffmpeg: ${msg}`);
        }
      });

      const filename = `audio_${Date.now()}.${ext}`;

      const compositeProc = {
        kill: (signal?: NodeJS.Signals) => {
          ffmpegProc.kill(signal);
          ytProc.kill(signal);
        },
        get killed() {
          return ffmpegProc.killed || ytProc.killed;
        },
        on: (event: string, listener: any) => {
          ffmpegProc.on(event, listener);
          ytProc.on(event, listener);
        },
      };

      return { stream: ffmpegProc.stdout, process: compositeProc, filename, contentType };
    } else {
      // Video stream
      let formatSpec = formatId;

      if (isMuteStream) {
        formatSpec = formatId || 'bestvideo';
        ext = (format || 'mp4').toLowerCase();
        contentType = ext === 'webm' ? 'video/webm' : 'video/mp4';
      } else if (!formatSpec || formatSpec === 'best' || formatSpec === 'all') {
        formatSpec = 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/bestvideo+bestaudio/best';
        ext = 'mp4'; contentType = 'video/mp4';
      } else if (formatSpec.startsWith('bestvideo') || formatSpec.includes('+')) {
        ext = (format || 'mp4').toLowerCase();
        contentType = ext === 'webm' ? 'video/webm' : 'video/mp4';
      } else {
        formatSpec = `${formatSpec}+bestaudio/best`;
        ext = 'mp4'; contentType = 'video/mp4';
      }

      args.push('-f', formatSpec!);
      args.push(url);

      this.logger.log(`yt-dlp video stream (${formatSpec}): ${url}`);
      const child = spawn(this.ytDlpPath, args);
      child.stderr.on('data', (d) => {
        const msg = d.toString().trim();
        if (msg && !msg.includes('[download]')) this.logger.warn(`yt-dlp stream: ${msg}`);
      });
      child.on('close', (code) => {
        this.logger.warn(`yt-dlp stream process closed code=${code}`);
      });

      const filename = `media_${Date.now()}.${ext}`;
      return { stream: child.stdout, process: child, filename, contentType };
    }
  }

  /**
   * Tạo stream audio với embed thumbnail + metadata (cần output file tạm)
   * Dùng yt-dlp với --embed-thumbnail --embed-metadata ra file tạm rồi stream
   */
  private createAudioWithEmbedStream(
    url: string,
    ext: string,
    audioBitrate: string,
    embedThumbnail: boolean,
    embedMetadata: boolean,
    browserOverride?: string,
  ): { stream: Readable; process: any; filename: string; contentType: string } {
    const tmpDir = os.tmpdir();
    const tmpBase = path.join(tmpDir, `ytdlp_audio_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`);
    const outputTemplate = `${tmpBase}.%(ext)s`;

    const args = [
      ...this.getBaseArgs(false, browserOverride, url),
      '-f', 'bestaudio/best',
      '-x',
      '--audio-format', ext,
      '--audio-quality', audioBitrate.replace('k', ''),
      '-o', outputTemplate,
    ];

    if (embedThumbnail) args.push('--embed-thumbnail');
    if (embedMetadata) args.push('--embed-metadata', '--add-metadata');
    args.push(url);

    this.logger.log(`yt-dlp audio+embed → tmp file (${ext}): ${url}`);

    const audioContentTypes: Record<string, string> = {
      mp3: 'audio/mpeg', m4a: 'audio/mp4', flac: 'audio/flac',
      wav: 'audio/wav', opus: 'audio/opus',
    };
    const contentType = audioContentTypes[ext] || 'audio/mpeg';
    const filename = `audio_${Date.now()}.${ext}`;

    // Tạo readable stream giả — sẽ pipe từ file tạm khi yt-dlp xong
    const passThroughStream = new PassThrough();

    const child = spawn(this.ytDlpPath, args);

    child.stderr.on('data', (d) => {
      const msg = d.toString().trim();
      if (msg && !msg.includes('[download]')) this.logger.debug(`yt-dlp embed: ${msg}`);
    });

    child.on('close', (code) => {
      // Tìm file đã tạo
      try {
        const files = fs.readdirSync(tmpDir);
        const found = files.find(
          (f) => f.startsWith(path.basename(tmpBase)) && f.endsWith(`.${ext}`),
        );
        if (found) {
          const fullPath = path.join(tmpDir, found);
          const fileStream = fs.createReadStream(fullPath);
          fileStream.pipe(passThroughStream);
          fileStream.on('end', () => {
            try { fs.unlinkSync(fullPath); } catch {}
          });
        } else {
          passThroughStream.destroy(new Error(`File audio không tìm thấy sau khi tải (code ${code})`));
        }
      } catch (err: any) {
        passThroughStream.destroy(err);
      }
    });

    child.on('error', (err) => passThroughStream.destroy(err));

    return { stream: passThroughStream, process: child, filename, contentType };
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Subtitle Download
  // ─────────────────────────────────────────────────────────────────────────

  /**
   * Tải file phụ đề (.srt / .vtt)
   */
  async downloadSubtitle(url: string, lang: string, format: 'vtt' | 'srt' = 'vtt', browserOverride?: string): Promise<{
    stream: Readable;
    filename: string;
    contentType: string;
  }> {
    const tmpDir = os.tmpdir();
    const tmpBase = path.join(tmpDir, `sub_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`);
    const outputTemplate = `${tmpBase}.%(ext)s`;

    return new Promise((resolve, reject) => {
      const args = [
        ...this.getBaseArgs(false, browserOverride, url),
        '--skip-download',
        '--write-sub',
        '--write-auto-sub',
        '--sub-lang', lang,
        '--sub-format', format,
        '-o', outputTemplate,
        url,
      ];

      const proc = spawn(this.ytDlpPath, args);

      proc.on('close', (code) => {
        const expectedFile = `${tmpBase}.${lang}.${format}`;
        let targetPath = '';

        if (fs.existsSync(expectedFile)) {
          targetPath = expectedFile;
        } else {
          const files = fs.readdirSync(tmpDir);
          const found = files.find((f) => f.startsWith(path.basename(tmpBase)));
          if (found) targetPath = path.join(tmpDir, found);
        }

        if (targetPath && fs.existsSync(targetPath)) {
          const fileStream = fs.createReadStream(targetPath);
          fileStream.on('close', () => { try { fs.unlinkSync(targetPath); } catch {} });
          const actualExt = targetPath.split('.').pop() || format;
          const contentType = actualExt === 'srt' ? 'application/x-subrip' : 'text/vtt';
          resolve({ stream: fileStream, filename: `subtitle_${lang}_${Date.now()}.${actualExt}`, contentType });
        } else {
          reject(new Error(`Không tìm thấy phụ đề "${lang}" (code ${code})`));
        }
      });

      proc.on('error', (err) => reject(new Error(`yt-dlp subtitle error: ${err.message}`)));
    });
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Thumbnail Download
  // ─────────────────────────────────────────────────────────────────────────

  /**
   * Tải thumbnail/cover art riêng ra file .jpg
   * Tự động fallback sang không dùng cookies nếu bị từ chối
   */
  async downloadThumbnail(url: string, browserOverride?: string): Promise<{
    stream: Readable;
    filename: string;
    contentType: string;
  }> {
    const runThumb = (bOverride?: string): Promise<{ stream: Readable; filename: string; contentType: string }> => {
      const tmpDir = os.tmpdir();
      const tmpBase = path.join(tmpDir, `thumb_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`);
      const outputTemplate = `${tmpBase}.%(ext)s`;

      return new Promise((resolve, reject) => {
        const args = [
          ...this.getBaseArgs(false, bOverride, url),
          '--skip-download',
          '--write-thumbnail',
          '--convert-thumbnails', 'jpg',
          '-o', outputTemplate,
          url,
        ];

        const proc = spawn(this.ytDlpPath, args);

        proc.on('close', (code) => {
          try {
            const files = fs.readdirSync(tmpDir);
            const found = files
              .filter((f) => f.startsWith(path.basename(tmpBase)))
              .find((f) => /\.(jpg|jpeg|png|webp)$/i.test(f));

            if (found) {
              const fullPath = path.join(tmpDir, found);
              const fileStream = fs.createReadStream(fullPath);
              fileStream.on('close', () => { try { fs.unlinkSync(fullPath); } catch {} });
              const ext = found.split('.').pop()?.toLowerCase() || 'jpg';
              const contentType = ext === 'png' ? 'image/png' : ext === 'webp' ? 'image/webp' : 'image/jpeg';
              resolve({ stream: fileStream, filename: `thumbnail_${Date.now()}.${ext}`, contentType });
            } else {
              reject(new Error(`Không tìm thấy thumbnail (code ${code})`));
            }
          } catch (err: any) {
            reject(err);
          }
        });

        proc.on('error', (err) => reject(new Error(`yt-dlp thumbnail error: ${err.message}`)));
      });
    };

    try {
      return await runThumb(browserOverride);
    } catch (err: any) {
      if (browserOverride !== 'none') {
        this.logger.warn(`yt-dlp thumbnail thất bại với cookies (${browserOverride || 'default'}). Đang thử lại không dùng cookies...`);
        return await runThumb('none');
      }
      throw err;
    }
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Live Stream Detection
  // ─────────────────────────────────────────────────────────────────────────

  /**
   * Kiểm tra và lấy thông tin live stream (HLS/DASH URL)
   * Trả về stream URL nếu đang live, null nếu không phải live
   */
  async getLiveStreamInfo(url: string, browserOverride?: string): Promise<{ liveUrl: string; title: string; thumbnail: string } | null> {
    return new Promise((resolve) => {
      const args = [
        ...this.getBaseArgs(false, browserOverride, url),
        '--dump-json',
        '--no-playlist',
        url,
      ];

      const { proc } = this.spawnWithTimeout(this.ytDlpPath, args, 30000);
      let stdoutData = '';

      proc.stdout?.on('data', (c) => { stdoutData += c.toString(); });
      proc.on('close', () => {
        try {
          const firstLine = stdoutData.trim().split('\n').find((l) => l.startsWith('{'));
          if (!firstLine) { resolve(null); return; }
          const raw = JSON.parse(firstLine);
          if (!raw.is_live && !raw.was_live) { resolve(null); return; }

          // Lấy HLS URL
          const formats = raw.formats || [];
          const hlsFormat = formats.find((f: any) =>
            f.protocol === 'm3u8' || f.protocol === 'm3u8_native' || f.ext === 'm3u8',
          );
          const liveUrl = hlsFormat?.url || raw.url || '';

          resolve({
            liveUrl,
            title: raw.title || 'Live Stream',
            thumbnail: raw.thumbnail || '',
          });
        } catch {
          resolve(null);
        }
      });

      proc.on('error', () => resolve(null));
    });
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Normalize Output
  // ─────────────────────────────────────────────────────────────────────────

  private normalizeYtDlpOutput(raw: any, originalUrl: string): MediaMetadataDto {
    const rawPlatform = (raw.extractor_key || raw.extractor || 'media').toLowerCase();
    let platform = rawPlatform;
    if (rawPlatform.includes('youtube')) platform = 'youtube';
    else if (rawPlatform.includes('twitch')) platform = 'twitch';
    else if (rawPlatform.includes('facebook')) platform = 'facebook';
    else if (rawPlatform.includes('tiktok')) platform = 'tiktok';
    else if (rawPlatform.includes('instagram')) platform = 'instagram';
    else if (rawPlatform.includes('twitter') || rawPlatform.includes('x.com')) platform = 'x';
    else if (rawPlatform.includes('pinterest')) platform = 'pinterest';
    else if (rawPlatform.includes('reddit')) platform = 'reddit';
    else if (rawPlatform.includes('soundcloud')) platform = 'soundcloud';
    else if (rawPlatform.includes('dailymotion')) platform = 'dailymotion';
    else if (rawPlatform.includes('bilibili')) platform = 'bilibili';
    else if (rawPlatform.includes('bluesky') || rawPlatform.includes('bsky')) platform = 'bluesky';
    else if (
      rawPlatform === 'generic' ||
      rawPlatform.includes('hls') ||
      rawPlatform.includes('movie') ||
      originalUrl.includes('.m3u8') ||
      originalUrl.includes('.mpd')
    ) {
      platform = 'movie';
    }

    const title = raw.title || 'Không có tiêu đề';
    const author = raw.uploader || raw.channel || raw.creator || 'Tác giả';
    const authorUrl = raw.uploader_url || raw.channel_url || originalUrl;
    const duration = this.formatDuration(raw.duration || 0);
    const views = raw.view_count ? `${this.formatNumber(raw.view_count)} lượt xem` : 'Không xác định';
    const likes = raw.like_count ? `${this.formatNumber(raw.like_count)} lượt thích` : undefined;
    const comments = raw.comment_count ? `${this.formatNumber(raw.comment_count)} bình luận` : undefined;
    const description = raw.description || '';
    const tags = Array.isArray(raw.tags) ? raw.tags.slice(0, 10) : undefined;
    const uploadDate = raw.upload_date
      ? `${raw.upload_date.slice(0, 4)}-${raw.upload_date.slice(4, 6)}-${raw.upload_date.slice(6, 8)}`
      : undefined;
    const isLive = !!(raw.is_live || raw.live_status === 'is_live');

    // Thumbnail cao nhất
    let thumbnail = raw.thumbnail || '';
    let highResThumbnail = thumbnail;
    if (Array.isArray(raw.thumbnails) && raw.thumbnails.length > 0) {
      const sorted = [...raw.thumbnails].sort((a, b) => (b.width || 0) - (a.width || 0));
      highResThumbnail = sorted[0]?.url || thumbnail;
      thumbnail = sorted.find((t) => t.width && t.width >= 480)?.url || highResThumbnail;
    }

    // Subtitles
    const subtitles: SubtitleDto[] = [];
    const seenLangs = new Set<string>();

    if (raw.subtitles && typeof raw.subtitles === 'object') {
      for (const [lang, list] of Object.entries(raw.subtitles)) {
        if (!seenLangs.has(lang)) {
          seenLangs.add(lang);
          const first = Array.isArray(list) ? list[0] : null;
          subtitles.push({ lang, name: first?.name || lang.toUpperCase(), ext: first?.ext || 'vtt', url: first?.url, isAutoGenerated: false });
        }
      }
    }

    if (raw.automatic_captions && typeof raw.automatic_captions === 'object') {
      for (const [lang, list] of Object.entries(raw.automatic_captions)) {
        if (!seenLangs.has(lang)) {
          seenLangs.add(lang);
          const first = Array.isArray(list) ? list[0] : null;
          subtitles.push({ lang, name: `${first?.name || lang.toUpperCase()} (Tự động)`, ext: first?.ext || 'vtt', url: first?.url, isAutoGenerated: true });
        }
      }
    }

    // Chapters
    const chapters: ChapterDto[] = [];
    if (Array.isArray(raw.chapters)) {
      for (const ch of raw.chapters) {
        chapters.push({ title: ch.title || 'Chương', startTime: ch.start_time || 0, endTime: ch.end_time || 0, startFormatted: this.formatDuration(ch.start_time || 0) });
      }
    }

    // Stream formats
    const streams: StreamFormatDto[] = [];
    const formats = raw.formats || [];

    if (isLive) {
      // Live stream — chỉ hiển thị 1 tùy chọn
      streams.push({
        formatId: 'live_best',
        quality: 'Live Stream (Chất lượng tốt nhất)',
        format: 'HLS',
        size: 'Live',
        streamType: 'full',
        hasAudio: true,
        hasVideo: true,
      });
    } else {
      // Video formats theo độ phân giải
      const videoFormats = formats.filter(
        (f: any) =>
          (f.vcodec && f.vcodec !== 'none') ||
          (f.video_ext && f.video_ext !== 'none') ||
          (!f.acodec && f.ext === 'mp4') ||
          ['hd', 'sd', 'best', 'default'].includes(f.format_id?.toLowerCase()),
      );
      const availableHeights = Array.from(
        new Set(videoFormats.map((f: any) => f.height).filter(Boolean)),
      ).sort((a: any, b: any) => b - a) as number[];

      const targetHeights = [2160, 1440, 1080, 720, 480, 360];
      for (const h of targetHeights) {
        const matched = availableHeights.find((ah) => ah >= h * 0.95);
        if (matched || (h === 720 && availableHeights.length > 0)) {
          let label = `${h}p`;
          if (h >= 2160) label = '4K (2160p Ultra HD)';
          else if (h >= 1440) label = '2K (1440p Quad HD)';
          else if (h >= 1080) label = 'Full HD (1080p)';
          else if (h >= 720) label = 'HD (720p)';
          else if (h >= 480) label = 'Chuẩn SD (480p)';
          else label = 'Tiết kiệm (360p)';

          const muxedSpec = `bestvideo[height<=${h}]+bestaudio/best[height<=${h}]`;
          const approxBitrate = h >= 2160 ? 12000 : h >= 1440 ? 6000 : h >= 1080 ? 3000 : h >= 720 ? 1500 : 800;
          const approxSize = raw.duration ? this.formatBytes((raw.duration * approxBitrate * 1000) / 8) : 'Tự động';

          streams.push({
            formatId: muxedSpec,
            quality: `${label} — Có âm thanh đầy đủ`,
            format: 'MP4',
            size: approxSize,
            streamType: 'full',
            hasAudio: true,
            hasVideo: true,
            fps: h >= 1080 ? '60fps' : '30fps',
            bitrate: `${approxBitrate}kbps`,
          });
        }
      }

      // Xử lý khi các stream không có height (ví dụ: Facebook với format_id: 'hd', 'sd')
      if (availableHeights.length === 0 && videoFormats.length > 0) {
        const hdFormat = videoFormats.find((f: any) => f.format_id?.toLowerCase() === 'hd' || f.format_note?.toLowerCase() === 'hd');
        const sdFormat = videoFormats.find((f: any) => f.format_id?.toLowerCase() === 'sd' || f.format_note?.toLowerCase() === 'sd');

        if (hdFormat) {
          const sizeNum = hdFormat.filesize || hdFormat.filesize_approx;
          streams.push({
            formatId: 'hd',
            quality: 'HD (Độ phân giải cao) — Có âm thanh đầy đủ',
            format: (hdFormat.ext || 'mp4').toUpperCase(),
            size: sizeNum ? this.formatBytes(sizeNum) : 'Tự động',
            rawSize: sizeNum,
            streamType: 'full',
            hasAudio: true,
            hasVideo: true,
            fps: '30fps',
            url: hdFormat.url,
          });
        }

        if (sdFormat) {
          const sizeNum = sdFormat.filesize || sdFormat.filesize_approx;
          streams.push({
            formatId: 'sd',
            quality: 'SD (Tiêu chuẩn) — Có âm thanh đầy đủ',
            format: (sdFormat.ext || 'mp4').toUpperCase(),
            size: sizeNum ? this.formatBytes(sizeNum) : 'Tự động',
            rawSize: sizeNum,
            streamType: 'full',
            hasAudio: true,
            hasVideo: true,
            fps: '30fps',
            url: sdFormat.url,
          });
        }

        if (!hdFormat && !sdFormat) {
          const bestFormat = videoFormats[videoFormats.length - 1];
          const sizeNum = bestFormat.filesize || bestFormat.filesize_approx;
          streams.push({
            formatId: bestFormat.format_id || 'best',
            quality: 'Chất lượng tốt nhất — Có âm thanh đầy đủ',
            format: (bestFormat.ext || 'mp4').toUpperCase(),
            size: sizeNum ? this.formatBytes(sizeNum) : 'Tự động',
            rawSize: sizeNum,
            streamType: 'full',
            hasAudio: true,
            hasVideo: true,
            fps: '30fps',
            url: bestFormat.url,
          });
        }
      }

      // Mute streams (video gốc không tiếng)
      const seenMute = new Set<number>();
      for (const f of videoFormats) {
        if ((!f.acodec || f.acodec === 'none') && f.height && !seenMute.has(f.height)) {
          seenMute.add(f.height);
          const sizeNum = f.filesize || f.filesize_approx;
          streams.push({
            formatId: f.format_id,
            quality: `${f.height}p (Chỉ video / Không tiếng)`,
            format: (f.ext || 'mp4').toUpperCase(),
            size: sizeNum ? this.formatBytes(sizeNum) : 'Tự động',
            rawSize: sizeNum,
            streamType: 'mute',
            hasAudio: false,
            hasVideo: true,
            fps: f.fps ? `${Math.round(f.fps)}fps` : undefined,
            bitrate: f.tbr ? `${Math.round(f.tbr)}kbps` : undefined,
            vcodec: f.vcodec,
            url: f.url,
          });
        }
      }

      // Audio formats
      const audioFormats: Array<{ formatId: string; quality: string; format: string; size: string; bitrate: string }> = [
        { formatId: 'mp3_320k', quality: 'MP3 Chất lượng cao (320 kbps)', format: 'MP3', size: raw.duration ? this.formatBytes((raw.duration * 320 * 1000) / 8) : 'Tự động', bitrate: '320kbps' },
        { formatId: 'mp3_192k', quality: 'MP3 Chuẩn phổ biến (192 kbps)', format: 'MP3', size: raw.duration ? this.formatBytes((raw.duration * 192 * 1000) / 8) : 'Tự động', bitrate: '192kbps' },
        { formatId: 'm4a_aac', quality: 'M4A / AAC Gốc (256 kbps)', format: 'M4A', size: raw.duration ? this.formatBytes((raw.duration * 256 * 1000) / 8) : 'Tự động', bitrate: '256kbps' },
        { formatId: 'flac_lossless', quality: 'FLAC Âm thanh lossless (phòng thu)', format: 'FLAC', size: raw.duration ? this.formatBytes((raw.duration * 900 * 1000) / 8) : 'Tự động', bitrate: 'Lossless' },
        { formatId: 'wav_lossless', quality: 'WAV Bản ghi không nén (Uncompressed)', format: 'WAV', size: raw.duration ? this.formatBytes((raw.duration * 1411 * 1000) / 8) : 'Tự động', bitrate: '1411kbps' },
      ];

      for (const af of audioFormats) {
        streams.push({ ...af, streamType: 'audio', hasAudio: true, hasVideo: false });
      }
    }

    const isShort =
      originalUrl.includes('/shorts/') ||
      Boolean(raw.webpage_url?.includes('/shorts/')) ||
      (platform === 'youtube' && (raw.duration || 0) <= 65 && raw.height > raw.width);
    const isReel =
      originalUrl.includes('/reel/') ||
      originalUrl.includes('/reels/') ||
      originalUrl.includes('/share/r/') ||
      Boolean(raw.webpage_url?.includes('/reel/') || raw.webpage_url?.includes('/reels/'));

    return {
      id: raw.id || String(Date.now()),
      platform,
      title,
      author,
      authorUrl,
      duration,
      views,
      likes,
      comments,
      thumbnail,
      highResThumbnail,
      type: isLive ? 'live' : 'video',
      isReel: isReel || undefined,
      isShort: isShort || undefined,
      originalUrl,
      description,
      tags,
      uploadDate,
      subtitles: subtitles.length > 0 ? subtitles : undefined,
      chapters: chapters.length > 0 ? chapters : undefined,
      streams,
      isLive,
      liveStreamUrl: isLive ? (raw.url || '') : undefined,
    };
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Formatters
  // ─────────────────────────────────────────────────────────────────────────

  private formatDuration(seconds: number): string {
    if (!seconds || isNaN(seconds)) return '00:00';
    const secs = Math.floor(seconds % 60);
    const mins = Math.floor(seconds / 60);
    const hrs = Math.floor(mins / 60);
    if (hrs > 0) return `${String(hrs).padStart(2, '0')}:${String(mins % 60).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
    return `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
  }

  private formatBytes(bytes: number): string {
    if (!bytes || bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
  }

  private formatNumber(num: number): string {
    if (num >= 1000000000) return (num / 1000000000).toFixed(1) + 'B';
    if (num >= 1000000) return (num / 1000000).toFixed(1) + 'M';
    if (num >= 1000) return (num / 1000).toFixed(1) + 'K';
    return String(num);
  }
}
