import {
  Controller,
  Post,
  Get,
  Delete,
  Body,
  Query,
  Req,
  Res,
  HttpException,
  HttpStatus,
  Logger,
} from '@nestjs/common';
import type { Request, Response } from 'express';
import { MediaDispatcherService } from './services/media-dispatcher.service.js';
import { YtDlpService } from './services/yt-dlp.service.js';
import { GalleryDlService } from './services/gallery-dl.service.js';
import { ProxyDownloadService } from './services/proxy-download.service.js';
import { BinaryManagerService } from './services/binary-manager.service.js';
import { UserTrackingService } from './services/user-tracking.service.js';
import { UrlResolverService } from './services/url-resolver.service.js';
import { CookieManagerService } from './services/cookie-manager.service.js';
import { loadDownloaderConfig, detectInstalledBrowsers } from './config/downloader.config.js';
import type {
  ExtractMediaRequest,
  CrawlProfileRequest,
  DownloadZipRequest,
  ResolveUrlRequest,
  SystemConfigDto,
  HealthCheckDto,
  BrowsersResponseDto,
} from './dto/media.dto.js';

@Controller('api/media')
export class DownloaderController {
  private readonly logger = new Logger(DownloaderController.name);
  private readonly cfg = loadDownloaderConfig();

  constructor(
    private readonly dispatcherService: MediaDispatcherService,
    private readonly ytDlpService: YtDlpService,
    private readonly galleryDlService: GalleryDlService,
    private readonly proxyService: ProxyDownloadService,
    private readonly binaryManager: BinaryManagerService,
    private readonly userTracking: UserTrackingService,
    private readonly urlResolver: UrlResolverService,
    private readonly cookieManager: CookieManagerService,
  ) {}

  // ─────────────────────────────────────────────────────────────────────────
  // Cookie Manager Endpoints
  // ─────────────────────────────────────────────────────────────────────────

  /**
   * GET /api/media/cookies — Trạng thái cookies đã lưu (không trả giá trị thực)
   */
  @Get('/cookies')
  getCookieStatus() {
    return this.cookieManager.getStatus();
  }

  /**
   * GET /api/media/cookies/platforms — Danh sách nền tảng được hỗ trợ
   */
  @Get('/cookies/platforms')
  getSupportedPlatforms() {
    return this.cookieManager.getSupportedPlatforms();
  }

  /**
   * POST /api/media/cookies — Lưu cookie từ chuỗi DevTools
   */
  @Post('/cookies')
  async saveCookies(@Body() body: { platform: string; cookieString: string; domain?: string }) {
    if (!body?.platform || !body?.cookieString) {
      throw new HttpException('Vui lòng cung cấp platform và cookieString', HttpStatus.BAD_REQUEST);
    }
    try {
      const result = await this.cookieManager.saveCookies({
        platform: body.platform,
        cookieString: body.cookieString,
        domain: body.domain,
      });
      return { success: true, ...result };
    } catch (err: any) {
      throw new HttpException(err.message || 'Lỗi khi lưu cookie', HttpStatus.BAD_REQUEST);
    }
  }

  /**
   * DELETE /api/media/cookies — Xóa cookie theo domain hoặc toàn bộ
   */
  @Delete('/cookies')
  deleteCookies(@Query('domain') domain?: string) {
    try {
      return { success: true, ...this.cookieManager.deleteCookies(domain) };
    } catch (err: any) {
      throw new HttpException(err.message || 'Lỗi khi xóa cookie', HttpStatus.BAD_REQUEST);
    }
  }

  // ─────────────────────────────────────────────────────────────────────────
  // System Endpoints
  // ─────────────────────────────────────────────────────────────────────────

  /**
   * GET /api/media/health — Trạng thái binary và dependencies
   */
  @Get('/health')
  getHealth(): HealthCheckDto {
    const info = this.binaryManager.getVersionInfo();
    const status =
      info.ytDlp.available && info.galleryDl.available ? 'ok' :
      info.ytDlp.available || info.galleryDl.available ? 'degraded' : 'error';
    return { status, ...info };
  }

  /**
   * GET /api/media/config — Cấu hình hiện tại của hệ thống
   */
  @Get('/config')
  getConfig(): SystemConfigDto {
    return {
      browserCookies: this.cfg.browserCookies,
      cookiesFileSet: !!(this.cfg.cookiesFilePath),
      enableSponsorBlock: this.cfg.enableSponsorBlock,
      embedThumbnail: this.cfg.embedThumbnail,
      embedMetadata: this.cfg.embedMetadata,
      maxPlaylistLimit: this.cfg.maxPlaylistLimit,
      downloadTimeoutMs: this.cfg.downloadTimeoutMs,
      pixivConfigured: !!(this.cfg.pixivToken),
      deviantartConfigured: !!(this.cfg.deviantartClientId && this.cfg.deviantartClientSecret),
    };
  }

  /**
   * GET /api/media/browsers — Danh sách trình duyệt và profile cookies khả dụng trên máy chủ
   */
  @Get('/browsers')
  getBrowsers(): BrowsersResponseDto {
    const installed = detectInstalledBrowsers();
    return {
      current: this.cfg.browserCookies,
      defaultConfigured: this.cfg.browserCookies,
      browsers: installed,
    };
  }

  /**
   * GET /api/media/history — Lấy lịch sử tải xuống gần nhất được lưu trong PostgreSQL
   */
  @Get('/history')
  async getHistory(@Req() req: Request, @Query('limit') limitStr?: string, @Query('deviceId') deviceIdQuery?: string) {
    const clientInfo = this.userTracking.extractClientInfo(req);
    const limit = limitStr ? Math.min(Math.max(parseInt(limitStr, 10) || 20, 1), 100) : 50;
    const targetDeviceId = deviceIdQuery || clientInfo.deviceId;

    const items = await this.userTracking.getRecentDownloads(limit, targetDeviceId);
    return {
      client: {
        ip: clientInfo.ip,
        deviceId: clientInfo.deviceId,
        browserName: clientInfo.browserName,
      },
      total: items.length,
      history: items,
    };
  }

  /**
   * DELETE /api/media/history — Xóa lịch sử tải xuống theo thiết bị
   */
  @Delete('/history')
  async deleteHistory(@Req() req: Request, @Query('deviceId') deviceIdQuery?: string) {
    const clientInfo = this.userTracking.extractClientInfo(req);
    const targetDeviceId = deviceIdQuery || clientInfo.deviceId;
    const deletedCount = await this.userTracking.clearDownloadHistory(targetDeviceId);
    return {
      success: true,
      deletedCount,
    };
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Media Extraction & URL Verification
  // ─────────────────────────────────────────────────────────────────────────

  /**
   * POST /api/media/resolve-url — Giải mã liên kết rút gọn và xác thực nền tảng
   */
  @Post('resolve-url')
  async resolveUrl(@Body() body: ResolveUrlRequest) {
    if (!body?.url || typeof body.url !== 'string') {
      throw new HttpException('Vui lòng cung cấp trường "url" hợp lệ', HttpStatus.BAD_REQUEST);
    }
    try {
      return await this.urlResolver.resolveUrl(body.url, body.expectedPlatform);
    } catch (err: any) {
      this.logger.error(`Resolve URL error: ${err.message}`);
      throw new HttpException(err.message || 'Lỗi khi giải mã liên kết', HttpStatus.BAD_REQUEST);
    }
  }

  /**
   * POST /api/media/extract — Trích xuất thông tin media từ 1 liên kết
   */
  @Post('extract')
  async extractMedia(@Body() body: ExtractMediaRequest, @Req() req: Request) {
    if (!body?.url || typeof body.url !== 'string') {
      throw new HttpException('Vui lòng cung cấp trường "url" hợp lệ', HttpStatus.BAD_REQUEST);
    }

    const clientInfo = this.userTracking.extractClientInfo(req);

    try {
      const result = await this.dispatcherService.extract(body.url, body.browser);

      // Tự động ghi nhận phiên trích xuất vào database (jobs & extracted_medias)
      const { jobId, mediaId } = await this.userTracking.recordSingleExtraction(clientInfo, {
        url: body.url,
        title: result.title,
        author: result.author,
        authorUrl: result.authorUrl,
        thumbnailUrl: result.thumbnail,
        duration: result.duration,
        viewCount: result.views,
        likeCount: result.likes,
        platform: result.platform,
        formats: result.streams || (result.images as any),
        mediaType: result.type,
      });

      return {
        ...result,
        jobId,
        mediaId,
      };
    } catch (err: any) {
      this.logger.error(`Extract error: ${err.message}`);
      throw new HttpException(err.message || 'Lỗi khi trích xuất thông tin media', HttpStatus.BAD_REQUEST);
    }
  }

  /**
   * POST /api/media/crawl-profile — Quét tài khoản / profile / playlist
   */
  @Post('crawl-profile')
  async crawlProfile(@Body() body: CrawlProfileRequest, @Req() req: Request) {
    if (!body?.url) {
      throw new HttpException('Vui lòng cung cấp url hoặc username', HttpStatus.BAD_REQUEST);
    }

    const clientInfo = this.userTracking.extractClientInfo(req);
    const limit = body.limit ? Math.min(Math.max(body.limit, 1), this.cfg.maxPlaylistLimit) : 50;

    let targetUrl = body.url.trim();
    if (!targetUrl.startsWith('@') && (targetUrl.startsWith('http://') || targetUrl.startsWith('https://') || targetUrl.includes('.'))) {
      try {
        const resolved = await this.urlResolver.resolveUrl(targetUrl, body.platform);
        if (resolved && resolved.resolvedUrl && resolved.isShortened) {
          targetUrl = resolved.resolvedUrl;
        }
      } catch {
        // Giữ nguyên targetUrl nếu resolveUrl có vấn đề
      }
    }

    try {
      const result = await this.dispatcherService.crawlProfile(
        targetUrl,
        limit,
        body.mediaType || 'all',
        body.platform,
        body.browser,
        body.rangeStart,
        body.rangeEnd,
      );

      // Tự động ghi nhận phiên quét profile vào database
      const { jobId, count } = await this.userTracking.recordProfileCrawl(clientInfo, {
        url: body.url,
        platform: body.platform,
        limit,
        mediaList: result.media,
      });

      return {
        ...result,
        jobId,
        recordedCount: count,
      };
    } catch (err: any) {
      this.logger.error(`Crawl profile error: ${err.message}`);
      throw new HttpException(err.message || 'Không thể quét tài khoản này!', HttpStatus.BAD_REQUEST);
    }
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Download Streams
  // ─────────────────────────────────────────────────────────────────────────

  /**
   * GET /api/media/download/stream — Stream tải trực tiếp video/audio
   */
  @Get('download/stream')
  streamDownload(
    @Query('url') url: string,
    @Query('formatId') formatId: string,
    @Query('isAudio') isAudio: string,
    @Query('title') customTitle: string,
    @Query('audioFormat') audioFormat: string,
    @Query('audioBitrate') audioBitrate: string,
    @Query('startTime') startTime: string,
    @Query('endTime') endTime: string,
    @Query('format') format: string,
    @Query('streamType') streamType: string,
    @Query('isMute') isMute: string,
    @Query('sponsorBlock') sponsorBlock: string,
    @Query('embedThumbnail') embedThumbnail: string,
    @Query('embedMetadata') embedMetadata: string,
    @Query('browser') browser: string,
    @Query('referer') referer: string,
    @Req() req: Request,
    @Res() res: Response,
  ) {
    if (!url) throw new HttpException('Thiếu tham số url', HttpStatus.BAD_REQUEST);

    const clientInfo = this.userTracking.extractClientInfo(req);
    const startTimeMs = Date.now();
    let bytesTransferred = 0;
    let isFinished = false;

    const isAudioOnly =
      isAudio === 'true' || isAudio === '1' ||
      streamType === 'audio' ||
      formatId === 'mp3' || formatId?.startsWith('audio') ||
      formatId?.startsWith('mp3') || formatId?.startsWith('m4a') ||
      formatId?.startsWith('flac') || formatId?.startsWith('wav');

    const isMuteStream = isMute === 'true' || isMute === '1' || streamType === 'mute';

    try {
      const resolvedAudioFormat =
        audioFormat ||
        (formatId?.includes('flac') ? 'flac' :
         formatId?.includes('m4a') ? 'm4a' :
         formatId?.includes('wav') ? 'wav' : 'mp3');

      const resolvedAudioBitrate =
        audioBitrate ||
        (formatId?.includes('192k') ? '192k' :
         formatId?.includes('256k') ? '256k' :
         formatId?.includes('128k') ? '128k' : '320k');

      const { stream, process: childProc, contentType } = this.ytDlpService.createDownloadStream(url, {
        formatId,
        isAudioOnly,
        audioFormat: resolvedAudioFormat,
        audioBitrate: resolvedAudioBitrate,
        startTime,
        endTime,
        isMute: isMuteStream,
        streamType,
        format,
        sponsorBlock: sponsorBlock === 'true' || sponsorBlock === '1',
        embedThumbnail: embedThumbnail === 'true' || embedThumbnail === '1',
        embedMetadata: embedMetadata === 'true' || embedMetadata === '1',
        browser,
        referer,
      });

      const fileExt = isAudioOnly ? resolvedAudioFormat : (format || 'mp4').toLowerCase();
      const cleanTitle = customTitle ? customTitle.replace(/[/\\?%*:|"<>]/g, '_').trim() : 'media';
      const downloadName = `${cleanTitle}.${fileExt}`;
      const asciiName = downloadName.replace(/[^\x20-\x7E]/g, '_');

      res.setHeader('Content-Disposition', `attachment; filename="${asciiName}"; filename*=UTF-8''${encodeURIComponent(downloadName)}`);
      res.setHeader('Content-Type', contentType);

      // Đếm lưu lượng thực tế truyền qua stream
      stream.on('data', (chunk: Buffer) => {
        bytesTransferred += chunk.length;
      });

      stream.pipe(res);

      res.on('finish', () => {
        isFinished = true;
        const durationMs = Date.now() - startTimeMs;
        this.userTracking.recordDownloadHistory(clientInfo, {
          mediaTitle: cleanTitle,
          fileName: downloadName,
          url,
          fileSizeBytes: bytesTransferred,
          durationMs,
          status: 'success',
        });
      });

      res.on('close', () => {
        if (!childProc.killed) {
          this.logger.log('Client disconnected, terminating download process.');
          childProc.kill('SIGTERM');
        }
        if (!isFinished && !res.writableEnded) {
          const durationMs = Date.now() - startTimeMs;
          this.userTracking.recordDownloadHistory(clientInfo, {
            mediaTitle: cleanTitle,
            fileName: downloadName,
            url,
            fileSizeBytes: bytesTransferred,
            durationMs,
            status: 'cancelled',
            errorReason: 'Client disconnected prematurely',
          });
        }
      });

      childProc.on('error', (err: any) => {
        this.logger.error(`Stream process error: ${err.message}`);
        this.userTracking.recordDownloadHistory(clientInfo, {
          mediaTitle: cleanTitle,
          fileName: downloadName,
          url,
          fileSizeBytes: bytesTransferred,
          durationMs: Date.now() - startTimeMs,
          status: 'failed',
          errorReason: err.message,
        });
        if (!res.headersSent) res.status(500).json({ message: 'Lỗi trong quá trình tải stream' });
      });
    } catch (err: any) {
      this.logger.error(`Stream init failed: ${err.message}`);
      throw new HttpException('Không thể bắt đầu stream', HttpStatus.INTERNAL_SERVER_ERROR);
    }
  }

  /**
   * GET /api/media/download/subtitle — Tải phụ đề (.srt / .vtt)
   */
  @Get('download/subtitle')
  async downloadSubtitle(
    @Query('url') url: string,
    @Query('lang') lang: string,
    @Query('format') format: 'vtt' | 'srt' = 'vtt',
    @Query('title') customTitle: string,
    @Query('browser') browser: string,
    @Req() req: Request,
    @Res() res: Response,
  ) {
    if (!url || !lang) {
      throw new HttpException('Vui lòng cung cấp đầy đủ url và lang', HttpStatus.BAD_REQUEST);
    }

    const clientInfo = this.userTracking.extractClientInfo(req);

    try {
      const { stream, filename: defaultName, contentType } = await this.ytDlpService.downloadSubtitle(url, lang, format, browser);
      const cleanTitle = customTitle ? customTitle.replace(/[/\\?%*:|"<>]/g, '_').trim() : 'subtitle';
      const actualExt = defaultName.split('.').pop() || format;
      const downloadName = `${cleanTitle}_${lang}.${actualExt}`;
      const asciiName = downloadName.replace(/[^\x20-\x7E]/g, '_');

      res.setHeader('Content-Disposition', `attachment; filename="${asciiName}"; filename*=UTF-8''${encodeURIComponent(downloadName)}`);
      res.setHeader('Content-Type', contentType);

      let bytes = 0;
      stream.on('data', (chunk: Buffer) => {
        bytes += chunk.length;
      });

      res.on('finish', () => {
        this.userTracking.recordDownloadHistory(clientInfo, {
          mediaTitle: `${cleanTitle} (Subtitle ${lang})`,
          fileName: downloadName,
          url,
          fileSizeBytes: bytes,
          status: 'success',
        });
      });

      stream.pipe(res);
    } catch (err: any) {
      this.logger.error(`Subtitle download failed: ${err.message}`);
      throw new HttpException(err.message || 'Không thể tải phụ đề', HttpStatus.INTERNAL_SERVER_ERROR);
    }
  }

  /**
   * GET /api/media/download/thumbnail — Tải thumbnail/cover art riêng
   */
  @Get('download/thumbnail')
  async downloadThumbnail(
    @Query('url') url: string,
    @Query('title') customTitle: string,
    @Query('browser') browser: string,
    @Req() req: Request,
    @Res() res: Response,
  ) {
    if (!url) throw new HttpException('Thiếu tham số url', HttpStatus.BAD_REQUEST);

    const clientInfo = this.userTracking.extractClientInfo(req);

    try {
      const { stream, contentType, filename: defaultName } = await this.ytDlpService.downloadThumbnail(url, browser);
      const cleanTitle = customTitle ? customTitle.replace(/[/\\?%*:|"<>]/g, '_').trim() : 'thumbnail';
      const ext = defaultName.split('.').pop() || 'jpg';
      const downloadName = `${cleanTitle}_thumbnail.${ext}`;
      const asciiName = downloadName.replace(/[^\x20-\x7E]/g, '_');

      res.setHeader('Content-Disposition', `attachment; filename="${asciiName}"; filename*=UTF-8''${encodeURIComponent(downloadName)}`);
      res.setHeader('Content-Type', contentType);

      let bytes = 0;
      stream.on('data', (chunk: Buffer) => {
        bytes += chunk.length;
      });

      res.on('finish', () => {
        this.userTracking.recordDownloadHistory(clientInfo, {
          mediaTitle: `${cleanTitle} (Thumbnail)`,
          fileName: downloadName,
          url,
          fileSizeBytes: bytes,
          status: 'success',
        });
      });

      stream.pipe(res);
    } catch (err: any) {
      this.logger.error(`Thumbnail download failed: ${err.message}`);
      throw new HttpException(err.message || 'Không thể tải thumbnail', HttpStatus.BAD_REQUEST);
    }
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Proxy / ZIP
  // ─────────────────────────────────────────────────────────────────────────

  /**
   * GET /api/media/proxy-image — Proxy hiển thị ảnh xem trước / avatar chống chặn CORP (ERR_BLOCKED_BY_RESPONSE.NotSameOrigin) & 403
   */
  @Get('proxy-image')
  async proxyImage(
    @Query('url') mediaUrl: string,
    @Res() res: Response,
  ) {
    if (!mediaUrl) throw new HttpException('Thiếu tham số url', HttpStatus.BAD_REQUEST);

    try {
      const { buffer, contentType } = await this.proxyService.fetchBuffer({ url: mediaUrl });
      res.setHeader('Content-Type', contentType || 'image/jpeg');
      res.setHeader('Cache-Control', 'public, max-age=86400, immutable');
      res.setHeader('Cross-Origin-Resource-Policy', 'cross-origin');
      res.setHeader('Access-Control-Allow-Origin', '*');
      res.setHeader('Content-Disposition', 'inline');
      res.send(buffer);
    } catch (err: any) {
      this.logger.warn(`Proxy image preview failed for ${mediaUrl}: ${err.message}`);
      res.setHeader('Content-Type', 'image/svg+xml');
      res.setHeader('Cross-Origin-Resource-Policy', 'cross-origin');
      res.setHeader('Access-Control-Allow-Origin', '*');
      res.setHeader('Cache-Control', 'no-cache');
      res.status(HttpStatus.OK).send(
        `<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100">
          <rect width="100%" height="100%" fill="#232734"/>
          <circle cx="50" cy="40" r="20" fill="#4B5563"/>
          <path d="M20 90 A 30 30 0 0 1 80 90 Z" fill="#4B5563"/>
        </svg>`
      );
    }
  }

  /**
   * GET /api/media/proxy-media — Proxy tải ảnh/video chống chặn 403
   */
  @Get('proxy-media')
  async proxyMedia(
    @Query('url') mediaUrl: string,
    @Query('filename') filename: string,
    @Req() req: Request,
    @Res() res: Response,
  ) {
    if (!mediaUrl) throw new HttpException('Thiếu tham số url', HttpStatus.BAD_REQUEST);

    const clientInfo = this.userTracking.extractClientInfo(req);

    try {
      const { buffer, contentType } = await this.proxyService.fetchBuffer({ url: mediaUrl });
      const cleanName = filename ? filename.replace(/[/\\?%*:|"<>]/g, '_').trim() : 'media_item.jpg';
      const asciiName = cleanName.replace(/[^\x20-\x7E]/g, '_');

      res.setHeader('Content-Disposition', `attachment; filename="${asciiName}"; filename*=UTF-8''${encodeURIComponent(cleanName)}`);
      res.setHeader('Content-Type', contentType);
      res.setHeader('Cross-Origin-Resource-Policy', 'cross-origin');
      res.setHeader('Access-Control-Allow-Origin', '*');

      this.userTracking.recordDownloadHistory(clientInfo, {
        mediaTitle: cleanName,
        fileName: cleanName,
        url: mediaUrl,
        fileSizeBytes: buffer.length,
        status: 'success',
      });

      res.send(buffer);
    } catch (err: any) {
      this.logger.error(`Proxy media failed: ${err.message}`);
      throw new HttpException('Không thể tải tệp qua proxy', HttpStatus.INTERNAL_SERVER_ERROR);
    }
  }

  /**
   * POST /api/media/download-zip — Đóng gói tệp đã chọn thành ZIP (Stream tốc độ cao, song song)
   */
  @Post('download-zip')
  async downloadZip(@Body() body: DownloadZipRequest, @Req() req: Request, @Res() res: Response) {
    if (!body?.items || !Array.isArray(body.items) || body.items.length === 0) {
      throw new HttpException('Danh sách tệp cần tải không được để trống', HttpStatus.BAD_REQUEST);
    }

    const clientInfo = this.userTracking.extractClientInfo(req);
    const rawZipName = body.zipName || `Album_Media_${Date.now()}`;
    const cleanZipName = rawZipName.replace(/[/\\?%*:|"<>]/g, '_').trim() || 'Album_Media';
    const zipFilename = `${cleanZipName}.zip`;
    const asciiZip = zipFilename.replace(/[^\x20-\x7E]/g, '_') || 'archive.zip';

    res.setHeader('Content-Disposition', `attachment; filename="${asciiZip}"; filename*=UTF-8''${encodeURIComponent(zipFilename)}`);
    res.setHeader('Content-Type', 'application/zip');
    res.setHeader('Cache-Control', 'no-cache, no-store, must-revalidate');

    let isClientClosed = false;
    res.on('close', () => {
      if (!res.writableFinished) {
        isClientClosed = true;
      }
    });

    try {
      const { totalBytes, successCount } = await this.proxyService.streamZip(
        body.items.map((item) => ({
          url: item.url,
          filename: item.filename,
          referer: item.referer,
        })),
        res,
        () => isClientClosed || res.destroyed || res.writableEnded,
        (done, total) => {
          this.logger.debug(`ZIP progress: ${done}/${total}`);
        },
      );

      this.userTracking.recordDownloadHistory(clientInfo, {
        mediaTitle: `Album ZIP (${successCount}/${body.items.length} tệp)`,
        fileName: zipFilename,
        fileSizeBytes: totalBytes,
        status: 'success',
      });
    } catch (err: any) {
      this.logger.error(`ZIP creation failed: ${err.message}`);
      if (!res.headersSent) {
        res.status(500).json({ message: `Lỗi khi tạo file ZIP: ${err.message}` });
      } else {
        res.end();
      }
    }
  }
}
