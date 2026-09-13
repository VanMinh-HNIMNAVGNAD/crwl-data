import { Injectable, Logger } from '@nestjs/common';
import type { Request } from 'express';
import { createHash } from 'node:crypto';
import { DatabaseService } from '../../database/database.service.js';

export interface ClientInfo {
  ip: string;
  deviceId: string;
  userAgent: string;
  browserName: string;
}

export type MediaPlatform =
  | 'youtube'
  | 'tiktok'
  | 'instagram'
  | 'twitter'
  | 'reddit'
  | 'pixiv'
  | 'deviantart'
  | 'facebook'
  | 'other';

@Injectable()
export class UserTrackingService {
  private readonly logger = new Logger(UserTrackingService.name);

  constructor(private readonly databaseService: DatabaseService) {}

  /**
   * Trích xuất thông tin định danh Client từ Express Request
   */
  extractClientInfo(req: Request): ClientInfo {
    // 1. Lấy địa chỉ IP (hỗ trợ cả Cloudflare & Reverse Proxy)
    const cfIp = req.headers['cf-connecting-ip'] as string;
    const xForwardedFor = req.headers['x-forwarded-for'] as string;
    const rawIp =
      cfIp ||
      (xForwardedFor ? xForwardedFor.split(',')[0].trim() : '') ||
      req.socket?.remoteAddress ||
      req.ip ||
      '127.0.0.1';

    let cleanIp = rawIp.replace(/^::ffff:/, '');
    if (cleanIp === '::1') cleanIp = '127.0.0.1';

    // 2. Lấy User-Agent & Tên Trình Duyệt
    const userAgent = (req.headers['user-agent'] as string) || 'Unknown Browser';
    const browserName = this.detectBrowserName(userAgent);

    // 3. Lấy Device ID duy nhất (từ Header x-device-id hoặc Query hoặc Body)
    const headerDeviceId = req.headers['x-device-id'] as string;
    const queryDeviceId = req.query?.deviceId as string;
    const bodyDeviceId = (req.body as any)?.deviceId as string;

    let deviceId = headerDeviceId || queryDeviceId || bodyDeviceId;

    if (!deviceId || deviceId.trim() === '') {
      // Fallback: Tạo hash cố định từ IP + User-Agent nếu client không gửi
      const hash = createHash('sha256').update(`${cleanIp}_${userAgent}`).digest('hex').slice(0, 24);
      deviceId = `anon_${hash}`;
    }

    return {
      ip: cleanIp,
      deviceId: deviceId.trim(),
      userAgent,
      browserName,
    };
  }

  /**
   * Phân tích chuỗi User-Agent để xác định tên trình duyệt
   */
  private detectBrowserName(ua: string): string {
    if (!ua || ua === 'Unknown Browser') return 'Unknown';
    if (ua.includes('Edg/')) return 'Microsoft Edge';
    if (ua.includes('OPR/') || ua.includes('Opera/')) return 'Opera';
    if (ua.includes('Brave')) return 'Brave';
    if (ua.includes('Chrome/') && !ua.includes('Edg/')) return 'Google Chrome';
    if (ua.includes('Firefox/')) return 'Mozilla Firefox';
    if (ua.includes('Safari/') && !ua.includes('Chrome/')) return 'Apple Safari';
    if (ua.includes('Mobile') || ua.includes('Android') || ua.includes('iPhone')) return 'Mobile Browser';
    return 'Web Browser';
  }

  /**
   * Parse thời lượng (giây) an toàn từ chuỗi dạng "03:45", "1:20:10" hoặc số
   */
  private parseDurationSeconds(duration?: string | number): number | null {
    if (duration === undefined || duration === null || duration === '') return null;
    if (typeof duration === 'number') return Math.round(duration);
    const parts = String(duration).split(':').map((p) => parseInt(p, 10));
    if (parts.some(isNaN)) return null;
    if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
    if (parts.length === 2) return parts[0] * 60 + parts[1];
    if (parts.length === 1) return parts[0];
    return null;
  }

  /**
   * Parse số lượng view/like từ chuỗi dạng "1.2M", "500K", "1,200" hoặc số
   */
  private parseNumberCount(val?: string | number): number | null {
    if (val === undefined || val === null || val === '') return null;
    if (typeof val === 'number') return Math.round(val);
    const str = String(val).replace(/,/g, '').trim().toLowerCase();
    if (str.endsWith('m')) return Math.round(parseFloat(str) * 1_000_000);
    if (str.endsWith('k')) return Math.round(parseFloat(str) * 1_000);
    const parsed = parseInt(str, 10);
    return isNaN(parsed) ? null : parsed;
  }

  /**
   * Tự động tạo hoặc cập nhật bản ghi khách vãng lai trong bảng `users`
   */
  async getOrCreateUser(client: ClientInfo): Promise<string> {
    try {
      const username = `guest_${client.deviceId.slice(0, 10)}`;
      const query = `
        INSERT INTO users (
          username, role, is_active, device_id, ip_address, browser_name, user_agent, last_active_at
        ) VALUES ($1, 'user', true, $2, $3, $4, $5, NOW())
        ON CONFLICT (device_id) DO UPDATE SET
          ip_address = EXCLUDED.ip_address,
          browser_name = EXCLUDED.browser_name,
          user_agent = EXCLUDED.user_agent,
          last_active_at = NOW()
        RETURNING id;
      `;

      const result = await this.databaseService.query<{ id: string }>(query, [
        username,
        client.deviceId,
        client.ip,
        client.browserName,
        client.userAgent,
      ]);

      return result.rows[0].id;
    } catch (err: any) {
      this.logger.error(`Error in getOrCreateUser: ${err.message}`);
      const existing = await this.databaseService.query<{ id: string }>('SELECT id FROM users LIMIT 1;');
      if (existing.rows.length > 0) return existing.rows[0].id;
      throw err;
    }
  }

  /**
   * Chuẩn hóa platform theo enum Postgres: media_platform
   */
  normalizePlatform(platformStr?: string, url?: string): MediaPlatform {
    const raw = (platformStr || '').toLowerCase();
    const link = (url || '').toLowerCase();

    if (raw === 'youtube' || link.includes('youtube.com') || link.includes('youtu.be')) return 'youtube';
    if (raw === 'tiktok' || link.includes('tiktok.com')) return 'tiktok';
    if (raw === 'instagram' || link.includes('instagram.com')) return 'instagram';
    if (raw === 'twitter' || raw === 'x' || link.includes('twitter.com') || link.includes('x.com')) return 'twitter';
    if (raw === 'reddit' || link.includes('reddit.com')) return 'reddit';
    if (raw === 'facebook' || link.includes('facebook.com') || link.includes('fb.watch')) return 'facebook';
    if (raw === 'pixiv' || link.includes('pixiv.net')) return 'pixiv';
    if (raw === 'deviantart' || link.includes('deviantart.com')) return 'deviantart';

    return 'other';
  }

  /**
   * Ghi nhận lượt trích xuất đơn lẻ vào `jobs` và `extracted_medias`
   */
  async recordSingleExtraction(
    client: ClientInfo,
    data: {
      url: string;
      title?: string;
      author?: string;
      authorUrl?: string;
      thumbnailUrl?: string;
      duration?: string | number;
      viewCount?: string | number;
      likeCount?: string | number;
      platform?: string;
      formats?: any[];
      mediaType?: string;
    },
  ): Promise<{ jobId: string; mediaId: string }> {
    try {
      const userId = await this.getOrCreateUser(client);
      const platform = this.normalizePlatform(data.platform, data.url);
      const mediaType = data.mediaType === 'audio' ? 'audio' : data.mediaType === 'image' ? 'image' : 'video';
      const durationSec = this.parseDurationSeconds(data.duration);
      const viewNum = this.parseNumberCount(data.viewCount);
      const likeNum = this.parseNumberCount(data.likeCount);

      // 1. Tạo bản ghi job
      const jobRes = await this.databaseService.query<{ id: string }>(
        `INSERT INTO jobs (
          user_id, title, job_type, engine, status, options, total_items, extracted_items,
          client_ip, device_id, started_at, finished_at
        ) VALUES ($1, $2, 'single', 'auto', 'ready', $3, 1, 1, $4, $5, NOW(), NOW())
        RETURNING id;`,
        [userId, data.title || 'Single Extraction', JSON.stringify({ url: data.url }), client.ip, client.deviceId],
      );
      const jobId = jobRes.rows[0].id;

      // 2. Tạo bản ghi extracted_medias
      const mediaRes = await this.databaseService.query<{ id: string }>(
        `INSERT INTO extracted_medias (
          job_id, user_id, platform, media_type, original_url, title, author, author_url,
          thumbnail_url, duration_seconds, view_count, like_count, formats, download_status
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, 'pending')
        RETURNING id;`,
        [
          jobId,
          userId,
          platform,
          mediaType,
          data.url,
          data.title || 'Untitled',
          data.author || null,
          data.authorUrl || null,
          data.thumbnailUrl || null,
          durationSec,
          viewNum,
          likeNum,
          JSON.stringify(data.formats || []),
        ],
      );
      const mediaId = mediaRes.rows[0].id;

      this.logger.log(`Recorded single extract: Job ${jobId}, Media ${mediaId} by Device ${client.deviceId.slice(0, 8)}`);
      return { jobId, mediaId };
    } catch (err: any) {
      this.logger.error(`Failed to record single extraction: ${err.message}`);
      return { jobId: '', mediaId: '' };
    }
  }

  /**
   * Ghi nhận lượt quét profile/kênh vào `jobs`, `job_items` và `extracted_medias`
   */
  async recordProfileCrawl(
    client: ClientInfo,
    data: {
      url: string;
      platform?: string;
      limit?: number;
      mediaList?: Array<{
        id?: string | number;
        url: string;
        title?: string;
        type?: string;
        thumb?: string;
        thumbnail?: string;
        duration?: string | number;
        author?: string;
        uploader?: string;
      }>;
    },
  ): Promise<{ jobId: string; count: number }> {
    try {
      const userId = await this.getOrCreateUser(client);
      const platform = this.normalizePlatform(data.platform, data.url);
      const totalItems = data.mediaList?.length || 0;

      // 1. Tạo bản ghi job
      const jobRes = await this.databaseService.query<{ id: string }>(
        `INSERT INTO jobs (
          user_id, title, job_type, engine, status, options, total_items, extracted_items,
          client_ip, device_id, started_at, finished_at
        ) VALUES ($1, $2, 'profile', 'auto', 'ready', $3, $4, $4, $5, $6, NOW(), NOW())
        RETURNING id;`,
        [
          userId,
          `Crawl: ${data.url}`,
          JSON.stringify({ url: data.url, limit: data.limit }),
          totalItems,
          client.ip,
          client.deviceId,
        ],
      );
      const jobId = jobRes.rows[0].id;

      // 2. Tạo bản ghi job_item đại diện cho URL nguồn
      const itemRes = await this.databaseService.query<{ id: string }>(
        `INSERT INTO job_items (
          job_id, source_url, item_type, status, extracted_count, started_at, finished_at
        ) VALUES ($1, $2, 'profile', 'completed', $3, NOW(), NOW())
        RETURNING id;`,
        [jobId, data.url, totalItems],
      );
      const jobItemId = itemRes.rows[0].id;

      // 3. Ghi các media items (tối đa 50 item đầu tiên để tối ưu DB)
      if (data.mediaList && data.mediaList.length > 0) {
        const topItems = data.mediaList.slice(0, 50);
        for (const m of topItems) {
          const mType = m.type === 'image' ? 'image' : m.type === 'audio' ? 'audio' : 'video';
          const durationSec = this.parseDurationSeconds(m.duration);
          await this.databaseService.query(
            `INSERT INTO extracted_medias (
              job_id, job_item_id, user_id, platform, media_type, original_url, title, author,
              thumbnail_url, duration_seconds, formats, download_status
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, '[]'::jsonb, 'pending');`,
            [
              jobId,
              jobItemId,
              userId,
              platform,
              mType,
              m.url,
              m.title || 'Untitled',
              m.author || m.uploader || null,
              m.thumb || m.thumbnail || null,
              durationSec,
            ],
          );
        }
      }

      this.logger.log(`Recorded profile crawl: Job ${jobId}, ${totalItems} items by Device ${client.deviceId.slice(0, 8)}`);
      return { jobId, count: totalItems };
    } catch (err: any) {
      this.logger.error(`Failed to record profile crawl: ${err.message}`);
      return { jobId: '', count: 0 };
    }
  }

  /**
   * Ghi nhận lịch sử tải xuống tệp vào `download_history`
   */
  async recordDownloadHistory(
    client: ClientInfo,
    data: {
      mediaTitle: string;
      fileName: string;
      url?: string;
      platform?: string;
      fileSizeBytes?: number;
      durationMs?: number;
      status: 'success' | 'failed' | 'cancelled';
      errorReason?: string;
      jobId?: string;
      extractedMediaId?: string;
    },
  ): Promise<number | null> {
    try {
      const userId = await this.getOrCreateUser(client);
      const platform = this.normalizePlatform(data.platform, data.url);

      const query = `
        INSERT INTO download_history (
          user_id, job_id, extracted_media_id, platform, media_title, file_name,
          file_size_bytes, client_type, duration_ms, status, error_reason,
          client_ip, device_id, browser_name, downloaded_at
        ) VALUES (
          $1, $2, $3, $4, $5, $6, $7, 'browser', $8, $9, $10, $11, $12, $13, NOW()
        ) RETURNING id;
      `;

      const result = await this.databaseService.query<{ id: string }>(query, [
        userId,
        data.jobId || null,
        data.extractedMediaId || null,
        platform,
        data.mediaTitle || 'Untitled Media',
        data.fileName || 'media_download',
        data.fileSizeBytes || null,
        data.durationMs || null,
        data.status,
        data.errorReason || null,
        client.ip,
        client.deviceId,
        client.browserName,
      ]);

      const downloadId = Number(result.rows[0].id);
      this.logger.log(
        `Recorded download history #${downloadId}: "${data.fileName}" (${data.fileSizeBytes || 0} bytes) [${data.status}] for IP ${client.ip} / ${client.browserName}`,
      );
      return downloadId;
    } catch (err: any) {
      this.logger.error(`Failed to record download history: ${err.message}`);
      return null;
    }
  }

  /**
   * Lấy danh sách lịch sử tải xuống gần nhất
   */
  async getRecentDownloads(limit = 50, deviceId?: string) {
    try {
      const params: any[] = [limit];
      let whereClause = '';

      if (deviceId && deviceId.trim()) {
        params.push(deviceId.trim());
        whereClause = 'WHERE dh.device_id = $2';
      }

      const query = `
        SELECT
          dh.id,
          dh.media_title,
          dh.file_name,
          dh.file_size_bytes,
          dh.platform,
          dh.status,
          dh.client_ip,
          dh.device_id,
          dh.browser_name,
          dh.downloaded_at,
          u.username
        FROM download_history dh
        LEFT JOIN users u ON dh.user_id = u.id
        ${whereClause}
        ORDER BY dh.downloaded_at DESC
        LIMIT $1;
      `;

      const result = await this.databaseService.query(query, params);
      return result.rows;
    } catch (err: any) {
      this.logger.error(`Failed to get recent downloads: ${err.message}`);
      return [];
    }
  }

  /**
   * Xóa lịch sử tải xuống theo deviceId hoặc toàn bộ
   */
  async clearDownloadHistory(deviceId?: string): Promise<number> {
    try {
      let query = 'DELETE FROM download_history';
      const params: any[] = [];
      if (deviceId && deviceId.trim()) {
        query += ' WHERE device_id = $1';
        params.push(deviceId.trim());
      }
      const res = await this.databaseService.query(query, params);
      return res.rowCount || 0;
    } catch (err: any) {
      this.logger.error(`Failed to clear download history: ${err.message}`);
      return 0;
    }
  }
}

