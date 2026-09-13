import { Module } from '@nestjs/common';
import { ConfigModule } from '@nestjs/config';
import { AppController } from './app.controller.js';
import { AppService } from './app.service.js';
import { DatabaseModule } from './modules/database/database.module.js';
import { DownloaderModule } from './modules/downloader/downloader.module.js';
import { TurnstileModule } from './modules/turnstile/turnstile.module.js';

@Module({
  imports: [
    ConfigModule.forRoot({
      isGlobal: true,
      envFilePath: ['.env'],
    }),
    DatabaseModule,
    DownloaderModule,
    TurnstileModule,
  ],
  controllers: [AppController],
  providers: [AppService],
})
export class AppModule {}
