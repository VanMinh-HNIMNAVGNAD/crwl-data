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
      this.pool = new PgPool({ connectionString });
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
    } catch (err: any) {
      this.logger.error(` Failed to connect to PostgreSQL: ${err.message}`, err.stack);
      throw err;
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
