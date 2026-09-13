import { Module } from '@nestjs/common';
import { TurnstileService } from './turnstile.service.js';
import { TurnstileController } from './turnstile.controller.js';
import { TurnstileGuard } from './turnstile.guard.js';

@Module({
  controllers: [TurnstileController],
  providers: [TurnstileService, TurnstileGuard],
  exports: [TurnstileService, TurnstileGuard],
})
export class TurnstileModule {}
