import { Module } from '@nestjs/common';
import { DatabaseModule } from '../database/database.module.js';
import { DownloaderController } from './downloader.controller.js';
import { YtDlpService } from './services/yt-dlp.service.js';
import { GalleryDlService } from './services/gallery-dl.service.js';
import { MediaDispatcherService } from './services/media-dispatcher.service.js';
import { BinaryManagerService } from './services/binary-manager.service.js';
import { ProxyDownloadService } from './services/proxy-download.service.js';
import { UserTrackingService } from './services/user-tracking.service.js';
import { UrlResolverService } from './services/url-resolver.service.js';
import { CookieManagerService } from './services/cookie-manager.service.js';
import { MovieExtractorService } from './services/movie-extractor.service.js';

@Module({
  imports: [DatabaseModule],
  controllers: [DownloaderController],
  providers: [
    BinaryManagerService,
    YtDlpService,
    GalleryDlService,
    MediaDispatcherService,
    MovieExtractorService,
    ProxyDownloadService,
    UserTrackingService,
    UrlResolverService,
    CookieManagerService,
  ],
  exports: [
    BinaryManagerService,
    YtDlpService,
    GalleryDlService,
    MediaDispatcherService,
    MovieExtractorService,
    ProxyDownloadService,
    UserTrackingService,
    UrlResolverService,
    CookieManagerService,
  ],
})
export class DownloaderModule {}
