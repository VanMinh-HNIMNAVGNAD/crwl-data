import { Controller, Get } from '@nestjs/common';
import { AppService } from './app.service.js';
import { DatabaseService } from './modules/database/database.service.js';

@Controller()
export class AppController {
  constructor(
    private readonly appService: AppService,
    private readonly databaseService: DatabaseService,
  ) {}

  @Get()
  getHello(): string {
    return this.appService.getHello();
  }

  @Get('api/health/db')
  async getDatabaseHealth() {
    return this.databaseService.healthCheck();
  }

  @Get('api/health')
  getSystemHealth() {
    return { status: 'ok', timestamp: new Date().toISOString() };
  }
}
