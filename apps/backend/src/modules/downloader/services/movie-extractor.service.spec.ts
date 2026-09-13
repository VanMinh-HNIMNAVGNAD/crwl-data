import { describe, it, expect, vi } from 'vitest';
import { MovieExtractorService } from './movie-extractor.service.js';
import { UrlResolverService } from './url-resolver.service.js';
import { YtDlpService } from './yt-dlp.service.js';

describe('Movie & Stream Detection', () => {
  const mockYtDlpService = {
    extractMetadata: vi.fn(),
  } as unknown as YtDlpService;

  const movieExtractor = new MovieExtractorService(mockYtDlpService);
  const urlResolver = new UrlResolverService();

  describe('MovieExtractorService.isMovieOrStreamUrl', () => {
    it('should detect direct .m3u8 stream links', () => {
      expect(movieExtractor.isMovieOrStreamUrl('https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8')).toBe(true);
      expect(movieExtractor.isMovieOrStreamUrl('https://example.com/live/master.m3u8?token=xyz')).toBe(true);
      expect(movieExtractor.isMovieOrStreamUrl('https://cdn.server.com/hls/stream_1080p.m3u8')).toBe(true);
    });

    it('should detect direct .mpd DASH stream links', () => {
      expect(movieExtractor.isMovieOrStreamUrl('https://example.com/manifest.mpd')).toBe(true);
      expect(movieExtractor.isMovieOrStreamUrl('https://dash.example.com/stream.mpd?auth=123')).toBe(true);
    });

    it('should detect known pirate movie websites', () => {
      expect(movieExtractor.isMovieOrStreamUrl('https://motchill.tv/xem-phim/dau-pha-thuong-khung-tap-1')).toBe(true);
      expect(movieExtractor.isMovieOrStreamUrl('https://ophim17.cc/phim/nguoi-phan-xu')).toBe(true);
      expect(movieExtractor.isMovieOrStreamUrl('https://phimmoi.net/phim/avatar-2')).toBe(true);
      expect(movieExtractor.isMovieOrStreamUrl('https://animehay.club/thong-tin-phim/one-piece-tap-1000')).toBe(true);
      expect(movieExtractor.isMovieOrStreamUrl('https://fmovies.to/watch/oppenheimer')).toBe(true);
      expect(movieExtractor.isMovieOrStreamUrl('https://vidsrc.me/embed/movie?imdb=tt1234567')).toBe(true);
    });

    it('should NOT misclassify social media platforms as movie sites', () => {
      expect(movieExtractor.isMovieOrStreamUrl('https://www.youtube.com/watch?v=dQw4w9WgXcQ')).toBe(false);
      expect(movieExtractor.isMovieOrStreamUrl('https://www.tiktok.com/@user/video/123456789')).toBe(false);
      expect(movieExtractor.isMovieOrStreamUrl('https://www.instagram.com/reel/C12345/')).toBe(false);
      expect(movieExtractor.isMovieOrStreamUrl('https://www.facebook.com/watch/?v=12345')).toBe(false);
      expect(movieExtractor.isMovieOrStreamUrl('https://x.com/user/status/123456')).toBe(false);
    });
  });

  describe('UrlResolverService movie detection', () => {
    it('should identify movie platforms in detectPlatformFromHostname', () => {
      expect(urlResolver.detectPlatformFromHostname('motchill.tv')).toBe('movie');
      expect(urlResolver.detectPlatformFromHostname('phimmoichill.net')).toBe('movie');
      expect(urlResolver.detectPlatformFromHostname('animehay.club')).toBe('movie');
      expect(urlResolver.detectPlatformFromHostname('fmovies.to')).toBe('movie');
      expect(urlResolver.detectPlatformFromHostname('vidsrc.me')).toBe('movie');
    });

    it('should recognize .m3u8 in resolveUrl as movie platform', async () => {
      const res = await urlResolver.resolveUrl('https://cdn.stream.net/playlist.m3u8');
      expect(res.platform).toBe('movie');
      expect(res.matchesExpected).toBe(true);
    });
  });

  describe('Movie webpage HTML extraction', () => {
    it('should extract m3u8 stream from movie page HTML and call yt-dlp', async () => {
      const mockHtml = `
        <!DOCTYPE html>
        <html>
          <head>
            <title>Đấu Phá Thương Khung - Tập 1 - Xem phim HD</title>
            <meta property="og:image" content="https://motchill.tv/poster.jpg">
          </head>
          <body>
            <script>
              var player = jwplayer("player").setup({
                file: "https://stream.server.com/hls/ep1/master.m3u8",
                width: "100%"
              });
            </script>
          </body>
        </html>
      `;

      // Mock global fetch
      const originalFetch = globalThis.fetch;
      globalThis.fetch = vi.fn().mockImplementation((url: string) => {
        if (url.includes('motchill.tv')) {
          return Promise.resolve(new Response(mockHtml, { status: 200 }));
        }
        return Promise.reject(new Error('Network error'));
      });

      const spyYtDlp = vi.spyOn(mockYtDlpService, 'extractMetadata').mockResolvedValue({
        id: 'ep1',
        platform: 'generic',
        title: 'master',
        author: 'server',
        authorUrl: 'https://stream.server.com',
        duration: '24:00',
        views: '1000',
        thumbnail: '',
        type: 'video',
        originalUrl: 'https://stream.server.com/hls/ep1/master.m3u8',
        streams: [
          {
            formatId: '1080',
            quality: '1080p',
            format: 'MP4',
            size: '500 MB',
            streamType: 'full',
            hasAudio: true,
            hasVideo: true,
          },
        ],
      });

      const result = await movieExtractor.extract('https://motchill.tv/phim/dau-pha-thuong-khung/tap-1');

      expect(spyYtDlp).toHaveBeenCalledWith('https://stream.server.com/hls/ep1/master.m3u8', undefined);
      expect(result.platform).toBe('movie');
      expect(result.title).toBe('Đấu Phá Thương Khung - Tập 1');
      expect(result.thumbnail).toBe('https://motchill.tv/poster.jpg');
      expect(result.streams?.length).toBe(1);

      // Restore fetch
      globalThis.fetch = originalFetch;
    });
  });
});

