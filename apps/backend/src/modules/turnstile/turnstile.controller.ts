import {
  Controller,
  Post,
  Body,
  Headers,
  Req,
  HttpException,
  HttpStatus,
  Get,
} from '@nestjs/common';
import type { Request } from 'express';
import { TurnstileService } from './turnstile.service.js';

export class VerifyTurnstileDto {
  token!: string;
}

@Controller('api/turnstile')
export class TurnstileController {
  constructor(private readonly turnstileService: TurnstileService) {}

  /**
   * GET /api/turnstile/status — Kiểm tra backend có đang yêu cầu xác thực Turnstile không
   */
  @Get('status')
  getStatus() {
    return {
      enabled: this.turnstileService.hasSecretKey(),
    };
  }

  /**
   * POST /api/turnstile/verify — Xác thực token từ frontend với Cloudflare Siteverify
   */
  @Post('verify')
  async verify(
    @Body() body: VerifyTurnstileDto,
    @Headers('x-device-id') deviceId: string | undefined,
    @Req() req: Request,
  ) {
    const clientIp = (req.headers['cf-connecting-ip'] ||
      req.headers['x-forwarded-for'] ||
      req.socket.remoteAddress) as string | undefined;

    const result = await this.turnstileService.verifyToken(body.token, clientIp, deviceId);

    if (!result.success) {
      throw new HttpException(
        {
          success: false,
          message: result.message || 'Xác thực Turnstile không thành công',
          errors: result.errors,
        },
        HttpStatus.FORBIDDEN,
      );
    }

    return {
      success: true,
      bypassed: result.bypassed || false,
      message: 'Xác thực Turnstile thành công',
    };
  }
}
