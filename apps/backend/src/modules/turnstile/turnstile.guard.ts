import {
  Injectable,
  CanActivate,
  ExecutionContext,
  HttpException,
  HttpStatus,
} from '@nestjs/common';
import type { Request } from 'express';
import { TurnstileService } from './turnstile.service.js';

@Injectable()
export class TurnstileGuard implements CanActivate {
  constructor(private readonly turnstileService: TurnstileService) {}

  canActivate(context: ExecutionContext): boolean {
    if (!this.turnstileService.hasSecretKey()) {
      return true;
    }

    const request = context.switchToHttp().getRequest<Request>();
    const token = (request.headers['x-turnstile-token'] as string) || '';
    const deviceId = (request.headers['x-device-id'] as string) || '';

    if (this.turnstileService.isVerified(token, deviceId)) {
      return true;
    }

    throw new HttpException(
      {
        statusCode: HttpStatus.FORBIDDEN,
        error: 'Forbidden',
        message: 'Yêu cầu xác thực Cloudflare Turnstile trước khi thực hiện thao tác này.',
      },
      HttpStatus.FORBIDDEN,
    );
  }
}
