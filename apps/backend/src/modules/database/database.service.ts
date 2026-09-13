import { Injectable, Logger, type OnModuleInit, type OnModuleDestroy } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import pg from 'pg';
import type { Pool, PoolClient, QueryResult, QueryResultRow } from 'pg';

const { Pool: PgPool } = pg;

@Injectable()
export class DatabaseService implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(DatabaseService.name);
  private pool!: Pool;

  constructor(private readonly configService: ConfigService) {}

  async onModuleInit(): Promise<void> {
    const connectionString = this.configService.get<string>('DATABASE_URL');
    const host = this.configService.get<string>('DB_HOST', 'localhost');
    const port = Number(this.configService.get<number>('DB_PORT', 5432));
    const user = this.configService.get<string>('DB_USERNAME', 'postgres');
    const password = this.configService.get<string>('DB_PASSWORD', '123456');
    const database = this.configService.get<string>('DB_DATABASE', 'postgres');

    if (connectionString) {
      const isRemote =
        !connectionString.includes('localhost') && !connectionString.includes('127.0.0.1');
      this.pool = new PgPool({
        connectionString,
        ...(isRemote ? { ssl: { rejectUnauthorized: false } } : {}),
      });
    } else {
      this.pool = new PgPool({
        host,
        port,
        user,
        password,
        database,
        max: 20,
        idleTimeoutMillis: 30000,
        connectionTimeoutMillis: 5000,
      });
    }

    this.pool.on('error', (err) => {
      this.logger.error(`Unexpected error on idle PostgreSQL client: ${err.message}`, err.stack);
    });

    try {
      const res = await this.pool.query<{ connected_time: string; db_name: string; pg_version: string }>(
        'SELECT NOW() AS connected_time, current_database() AS db_name, version() AS pg_version;',
      );
      const row = res.rows[0];
      this.logger.log(
        ` Connected to PostgreSQL successfully! Database: "${row.db_name}", Server Time: ${row.connected_time}`,
      );

      await this.initSchema();
    } catch (err: any) {
      this.logger.error(` Failed to connect to PostgreSQL: ${err.message}`, err.stack);
      throw err;
    }
  }

  /**
   * Tự động khởi tạo cấu trúc bảng nếu chưa tồn tại
   */
  private async initSchema(): Promise<void> {
    const ddl = `
      CREATE EXTENSION IF NOT EXISTS "pgcrypto";

      CREATE TABLE IF NOT EXISTS users (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        username VARCHAR(100) NOT NULL,
        role VARCHAR(50) DEFAULT 'user',
        is_active BOOLEAN DEFAULT TRUE,
        device_id VARCHAR(255) UNIQUE NOT NULL,
        ip_address VARCHAR(64),
        browser_name VARCHAR(100),
        user_agent TEXT,
        last_active_at TIMESTAMPTZ DEFAULT NOW(),
        created_at TIMESTAMPTZ DEFAULT NOW()
      );

      CREATE TABLE IF NOT EXISTS jobs (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        user_id UUID REFERENCES users(id) ON DELETE SET NULL,
        title TEXT NOT NULL,
        job_type VARCHAR(50) DEFAULT 'single',
        engine VARCHAR(50) DEFAULT 'auto',
        status VARCHAR(50) DEFAULT 'ready',
        options JSONB DEFAULT '{}'::jsonb,
        total_items INTEGER DEFAULT 1,
        extracted_items INTEGER DEFAULT 0,
        client_ip VARCHAR(64),
        device_id VARCHAR(255),
        started_at TIMESTAMPTZ DEFAULT NOW(),
        finished_at TIMESTAMPTZ
      );

      CREATE TABLE IF NOT EXISTS job_items (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        job_id UUID REFERENCES jobs(id) ON DELETE CASCADE,
        source_url TEXT NOT NULL,
        item_type VARCHAR(50) DEFAULT 'profile',
        status VARCHAR(50) DEFAULT 'completed',
        extracted_count INTEGER DEFAULT 0,
        started_at TIMESTAMPTZ DEFAULT NOW(),
        finished_at TIMESTAMPTZ
      );

      CREATE TABLE IF NOT EXISTS extracted_medias (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        job_id UUID REFERENCES jobs(id) ON DELETE CASCADE,
        job_item_id UUID REFERENCES job_items(id) ON DELETE SET NULL,
        user_id UUID REFERENCES users(id) ON DELETE SET NULL,
        platform VARCHAR(50),
        media_type VARCHAR(50),
        original_url TEXT NOT NULL,
        title TEXT,
        author TEXT,
        author_url TEXT,
        thumbnail_url TEXT,
        duration_seconds INTEGER,
        view_count BIGINT,
        like_count BIGINT,
        formats JSONB DEFAULT '[]'::jsonb,
        download_status VARCHAR(50) DEFAULT 'pending',
        created_at TIMESTAMPTZ DEFAULT NOW()
      );

      CREATE TABLE IF NOT EXISTS download_history (
        id BIGSERIAL PRIMARY KEY,
        user_id UUID REFERENCES users(id) ON DELETE SET NULL,
        job_id UUID REFERENCES jobs(id) ON DELETE SET NULL,
        extracted_media_id UUID REFERENCES extracted_medias(id) ON DELETE SET NULL,
        platform VARCHAR(50),
        media_title TEXT,
        file_name TEXT,
        file_size_bytes BIGINT,
        client_type VARCHAR(50) DEFAULT 'browser',
        duration_ms INTEGER,
        status VARCHAR(50) DEFAULT 'success',
        error_reason TEXT,
        client_ip VARCHAR(64),
        device_id VARCHAR(255),
        browser_name VARCHAR(100),
        downloaded_at TIMESTAMPTZ DEFAULT NOW()
      );

      CREATE INDEX IF NOT EXISTS idx_users_device_id ON users(device_id);
      CREATE INDEX IF NOT EXISTS idx_download_history_device_id ON download_history(device_id);
      CREATE INDEX IF NOT EXISTS idx_download_history_downloaded_at ON download_history(downloaded_at DESC);
    `;

    try {
      await this.pool.query(ddl);
      this.logger.log(' Database tables initialized/verified successfully.');
    } catch (err: any) {
      this.logger.warn(` Auto-init schema note: ${err.message}`);
    }
  }

  async onModuleDestroy(): Promise<void> {
    if (this.pool) {
      this.logger.log('Draining and closing PostgreSQL connection pool...');
      await this.pool.end();
      this.logger.log('PostgreSQL connection pool closed.');
    }
  }

  /**
   * Thực thi câu lệnh SQL với parameters (chống SQL Injection)
   */
  async query<R extends QueryResultRow = any>(
    sqlText: string,
    params: any[] = [],
  ): Promise<QueryResult<R>> {
    return this.pool.query<R>(sqlText, params);
  }

  /**
   * Lấy một client từ pool để thực thi Transaction (BEGIN, COMMIT, ROLLBACK)
   */
  async getClient(): Promise<PoolClient> {
    return this.pool.connect();
  }

  /**
   * Kiểm tra tình trạng kết nối DB
   */
  async healthCheck(): Promise<{ status: 'ok' | 'down'; db: string; time: string; tablesCount: number }> {
    try {
      const result = await this.pool.query(
        `SELECT current_database() AS db, NOW() AS time,
         (SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public') AS tables_count`,
      );
      const row = result.rows[0];
      return {
        status: 'ok',
        db: row.db,
        time: row.time,
        tablesCount: Number(row.tables_count),
      };
    } catch (err) {
      return {
        status: 'down',
        db: '',
        time: '',
        tablesCount: 0,
      };
    }
  }
}
