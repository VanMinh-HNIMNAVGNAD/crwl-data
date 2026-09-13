import dns from 'node:dns';
import { NestFactory } from '@nestjs/core';
import { AppModule } from './app.module.js';

try {
  dns.setDefaultResultOrder('ipv4first');
} catch {}

async function bootstrap() {
  const app = await NestFactory.create(AppModule);
  app.enableCors({
    origin: true,
    exposedHeaders: ['Content-Disposition'],
  });
  const port = process.env.PORT ?? 3000;
  await app.listen(port);
  console.log(`Backend server is running on http://localhost:${port}`);
}
await bootstrap();

