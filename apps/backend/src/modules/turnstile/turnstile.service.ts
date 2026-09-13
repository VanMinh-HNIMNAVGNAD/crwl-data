import { Injectable, Logger } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';

export interface TurnstileVerifyResult {
  success: boolean;
  bypassed?: boolean;
  errors?: string[];
  message?: string;
}

@Injectable()
export class TurnstileService {
  private readonly logger = new Logger(TurnstileService.name);
  private readonly verifiedSessions = new Map<string, number>(); // key -> timestamp
  private readonly SESSION_TTL_MS = 2 * 60 * 60 * 1000; // 2 giờ

  constructor(private readonly configService: ConfigService) {}

  private get secretKey(): string {
    return (this.configService.get<string>('TURNSTILE_SECRET_KEY') || process.env.TURNSTILE_SECRET_KEY || '').trim();
  }

  /**
   * Kiểm tra hệ thống có đang cấu hình Secret Key không
   */
  hasSecretKey(): boolean {
    return Boolean(this.secretKey);
  }

  /**
   * Xác thực Turnstile response token với Cloudflare Siteverify API
   */
  async verifyToken(token: string, remoteIp?: string, deviceId?: string): Promise<TurnstileVerifyResult> {
    const secret = this.secretKey;
    if (!secret) {
      this.logger.debug('Turnstile Secret Key không được cấu hình. Bỏ qua xác thực máy chủ.');
      return { success: true, bypassed: true };
    }

    if (!token || typeof token !== 'string') {
      return { success: false, message: 'Turnstile token không hợp lệ hoặc bị thiếu.' };
    }

    // Nếu token này hoặc deviceId này đã được xác thực gần đây trong phiên
    if (this.isVerified(token, deviceId)) {
      return { success: true };
    }

    try {
      const formData = new URLSearchParams();
      formData.append('secret', secret);
      formData.append('response', token);
      if (remoteIp) {
        formData.append('remoteip', remoteIp);
      }

      const response = await fetch('https://challenges.cloudflare.com/turnstile/v0/siteverify', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/x-www-form-urlencoded',
        },
        body: formData.toString(),
      });

      if (!response.ok) {
        this.logger.error(`Cloudflare API phản hồi lỗi HTTP status: ${response.status}`);
        return { success: false, message: `Lỗi kết nối máy chủ Cloudflare (${response.status})` };
      }

      const data = (await response.json()) as {
        success: boolean;
        'error-codes'?: string[];
        challenge_ts?: string;
        hostname?: string;
      };

      if (data.success) {
        this.logger.log(`Xác thực Turnstile thành công cho device: ${deviceId || 'unknown'}`);
        const now = Date.now();
        if (token) this.verifiedSessions.set(token, now);
        if (deviceId) this.verifiedSessions.set(`dev:${deviceId}`, now);
        this.cleanupExpiredSessions();
        return { success: true };
      }

      this.logger.warn(`Xác thực Turnstile thất bại. Error codes: ${JSON.stringify(data['error-codes'])}`);
      return {
        success: false,
        errors: data['error-codes'],
        message: 'Mã xác thực Turnstile không hợp lệ hoặc đã hết hạn.',
      };
    } catch (err: unknown) {
      this.logger.error(`Lỗi ngoại lệ khi gọi Cloudflare Turnstile API: ${err instanceof Error ? err.message : String(err)}`);
      return {
        success: false,
        message: 'Lỗi mạng khi kết nối tới Cloudflare verification service.',
      };
    }
  }

  /**
   * Kiểm tra xem token hoặc deviceId đã được xác thực an toàn trong bộ nhớ chưa
   */
  isVerified(token?: string, deviceId?: string): boolean {
    if (!this.hasSecretKey()) {
      return true;
    }

    const now = Date.now();
    if (token && this.verifiedSessions.has(token)) {
      const timestamp = this.verifiedSessions.get(token)!;
      if (now - timestamp < this.SESSION_TTL_MS) {
        return true;
      }
      this.verifiedSessions.delete(token);
    }

    if (deviceId && this.verifiedSessions.has(`dev:${deviceId}`)) {
      const timestamp = this.verifiedSessions.get(`dev:${deviceId}`)!;
      if (now - timestamp < this.SESSION_TTL_MS) {
        return true;
      }
      this.verifiedSessions.delete(`dev:${deviceId}`);
    }

    return false;
  }

  private cleanupExpiredSessions() {
    const now = Date.now();
    for (const [key, timestamp] of this.verifiedSessions.entries()) {
      if (now - timestamp > this.SESSION_TTL_MS) {
        this.verifiedSessions.delete(key);
      }
    }
  }
}
