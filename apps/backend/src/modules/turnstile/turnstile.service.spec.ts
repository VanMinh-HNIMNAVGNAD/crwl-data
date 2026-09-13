import { describe, it, expect, beforeEach, vi } from 'vitest';
import { ConfigService } from '@nestjs/config';
import { TurnstileService } from './turnstile.service.js';
import { TurnstileController } from './turnstile.controller.js';
import { HttpException, HttpStatus } from '@nestjs/common';

describe('TurnstileService & Controller', () => {
  let service: TurnstileService;
  let controller: TurnstileController;
  let configService: ConfigService;

  beforeEach(() => {
    configService = new ConfigService({
      TURNSTILE_SECRET_KEY: 'test-secret-key-12345',
    });
    service = new TurnstileService(configService);
    controller = new TurnstileController(service);
    vi.restoreAllMocks();
  });

  it('should detect when secret key is configured', () => {
    expect(service.hasSecretKey()).toBe(true);
    expect(controller.getStatus()).toEqual({ enabled: true });
  });

  it('should return error when token is empty', async () => {
    const res = await service.verifyToken('');
    expect(res.success).toBe(false);
    expect(res.message).toContain('không hợp lệ');
  });

  it('should successfully verify token and cache session', async () => {
    // Mock fetch Cloudflare API success
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        success: true,
        challenge_ts: new Date().toISOString(),
        hostname: 'localhost',
      }),
    });
    vi.stubGlobal('fetch', mockFetch);

    const token = 'valid-cf-token-abc';
    const deviceId = 'dev-test-123';

    // Lần 1: Gọi API
    const res1 = await service.verifyToken(token, '127.0.0.1', deviceId);
    expect(res1.success).toBe(true);
    expect(mockFetch).toHaveBeenCalledTimes(1);

    // Lần 2: Token đã được cache trong phiên -> không gọi lại Cloudflare (tránh lỗi token single-use)
    expect(service.isVerified(token, deviceId)).toBe(true);
    const res2 = await service.verifyToken(token, '127.0.0.1', deviceId);
    expect(res2.success).toBe(true);
    expect(mockFetch).toHaveBeenCalledTimes(1);
  });

  it('should reject invalid token and return error message', async () => {
    // Mock fetch Cloudflare API failure
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        success: false,
        'error-codes': ['invalid-input-response'],
      }),
    });
    vi.stubGlobal('fetch', mockFetch);

    const res = await service.verifyToken('fake-token-xyz');
    expect(res.success).toBe(false);
    expect(res.errors).toContain('invalid-input-response');
  });

  it('should throw HttpException 403 in controller when verification fails', async () => {
    const mockFetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        success: false,
        'error-codes': ['timeout-or-duplicate'],
      }),
    });
    vi.stubGlobal('fetch', mockFetch);

    const mockReq = {
      headers: {},
      socket: { remoteAddress: '127.0.0.1' },
    } as any;

    await expect(
      controller.verify({ token: 'expired-token' }, 'dev-999', mockReq),
    ).rejects.toThrow(HttpException);
  });
});
