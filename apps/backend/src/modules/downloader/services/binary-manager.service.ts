import { Injectable, Logger, OnModuleInit } from '@nestjs/common';
import * as fs from 'node:fs';
import * as path from 'node:path';
import * as https from 'node:https';
import { chmod, writeFile } from 'node:fs/promises';
import { execSync, spawnSync } from 'node:child_process';

const YT_DLP_RELEASES_API = 'https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest';

/**
 * Tải file từ URL (hỗ trợ HTTP redirect tự động)
 */
function downloadFile(url: string, destPath: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const file = fs.createWriteStream(destPath);

    const request = (reqUrl: string) => {
      https
        .get(reqUrl, { headers: { 'User-Agent': 'crwl-on-socialmedia/1.0' } }, (res) => {
          // Xử lý redirect (301, 302, 307, 308)
          if (res.statusCode && res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
            request(res.headers.location);
            return;
          }

          if (res.statusCode !== 200) {
            file.close();
            fs.unlink(destPath, () => {});
            return reject(new Error(`HTTP ${res.statusCode} khi tải từ ${reqUrl}`));
          }

          res.pipe(file);
          file.on('finish', () => {
            file.close();
            resolve();
          });
        })
        .on('error', (err) => {
          file.close();
          fs.unlink(destPath, () => {});
          reject(err);
        });
    };

    request(url);
  });
}

/**
 * Lấy URL download yt-dlp binary cho Linux từ GitHub API
 */
async function getYtDlpDownloadUrl(): Promise<string> {
  return new Promise((resolve) => {
    https
      .get(
        YT_DLP_RELEASES_API,
        {
          headers: {
            'User-Agent': 'crwl-on-socialmedia/1.0',
            Accept: 'application/vnd.github.v3+json',
          },
        },
        (res) => {
          let data = '';
          res.on('data', (chunk) => {
            data += chunk;
          });
          res.on('end', () => {
            try {
              const json = JSON.parse(data);
              const assets: any[] = json.assets || [];
              // Tìm binary "yt-dlp" (không có extension - là binary Linux/macOS)
              const linuxAsset = assets.find((a: any) => a.name === 'yt-dlp');
              if (linuxAsset) {
                resolve(linuxAsset.browser_download_url);
              } else {
                resolve('https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp');
              }
            } catch {
              resolve('https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp');
            }
          });
          res.on('error', () => {
            resolve('https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp');
          });
        },
      )
      .on('error', () => {
        resolve('https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp');
      });
  });
}

/**
 * Tìm Python3 executable trên hệ thống
 */
function findPython(): string {
  for (const cmd of ['python3', 'python']) {
    try {
      const result = spawnSync(cmd, ['--version'], { encoding: 'utf-8' });
      if (result.status === 0 && result.stdout?.includes('Python 3')) {
        return cmd;
      }
    } catch {
      // tiếp tục thử
    }
  }
  return 'python3';
}

@Injectable()
export class BinaryManagerService implements OnModuleInit {
  private readonly logger = new Logger(BinaryManagerService.name);

  private ytDlpBinaryPath: string;
  private galleryDlBinaryPath: string;

  private readonly binDir: string;
  private readonly galleryDlVenvDir: string;
  private readonly toolsVenvDir: string;
  private readonly toolsPythonPath: string;
  private readonly tiktokResolverPath: string;
  private hasCurlCffi = false;

  constructor() {
    const projectRoot = process.cwd();
    this.binDir = path.join(projectRoot, 'bin');
    this.ytDlpBinaryPath = path.join(this.binDir, 'yt-dlp');
    this.galleryDlBinaryPath = path.join(this.binDir, 'gallery-dl');
    this.galleryDlVenvDir = path.join(this.binDir, 'gallery-dl-venv');
    this.toolsVenvDir = path.join(this.binDir, 'tools-venv');
    this.toolsPythonPath = path.join(this.toolsVenvDir, 'bin', 'python3');
    this.tiktokResolverPath = path.join(this.binDir, 'tiktok_resolver.py');
  }

  async onModuleInit(): Promise<void> {
    // Đảm bảo thư mục bin tồn tại
    if (!fs.existsSync(this.binDir)) {
      fs.mkdirSync(this.binDir, { recursive: true });
    }

    // Đảm bảo tools-venv với curl_cffi sẵn sàng cho TLS impersonation
    await this.ensureToolsVenv();

    // Nếu env var đã set sẵn (backward compatible), dùng luôn
    const envYtDlp = process.env.YT_DLP_PATH;
    const envGalleryDl = process.env.GALLERY_DL_PATH;

    await Promise.all([this.ensureYtDlp(envYtDlp), this.ensureGalleryDl(envGalleryDl)]);
  }

  /**
   * Trả về đường dẫn tuyệt đối đến binary yt-dlp (đã bọc wrapper nếu có python impersonation)
   */
  getYtDlpPath(): string {
    return this.ytDlpBinaryPath;
  }

  /**
   * Trả về đường dẫn tuyệt đối đến binary gallery-dl
   */
  getGalleryDlPath(): string {
    return this.galleryDlBinaryPath;
  }

  /**
   * Trả về Python binary có curl_cffi
   */
  getToolsPythonPath(): string {
    return fs.existsSync(this.toolsPythonPath) ? this.toolsPythonPath : findPython();
  }

  /**
   * Trả về script giải mã TikTok profile
   */
  getTiktokResolverPath(): string {
    return this.tiktokResolverPath;
  }

  /**
   * Kiểm tra xem môi trường đã sẵn sàng hỗ trợ impersonation chưa
   */
  hasImpersonation(): boolean {
    return this.hasCurlCffi;
  }

  /**
   * Kiểm tra ffmpeg có sẵn trong hệ thống không
   */
  hasFfmpeg(): boolean {
    try {
      const result = spawnSync('ffmpeg', ['-version'], { encoding: 'utf-8' });
      return result.status === 0;
    } catch {
      return false;
    }
  }

  /**
   * Kiểm tra AtomicParsley có sẵn không (dùng để embed thumbnail vào MP3/M4A)
   */
  hasAtomicParsley(): boolean {
    try {
      const result = spawnSync('AtomicParsley', ['--version'], { encoding: 'utf-8' });
      return result.status === 0;
    } catch {
      return false;
    }
  }

  /**
   * Lấy thông tin phiên bản của yt-dlp, gallery-dl, ffmpeg
   */
  getVersionInfo(): {
    ytDlp: { available: boolean; version?: string; path?: string };
    galleryDl: { available: boolean; version?: string; path?: string };
    ffmpeg: { available: boolean; version?: string };
    atomicParsley: { available: boolean };
  } {
    const getVersion = (cmd: string, versionArgs: string[]): { available: boolean; version?: string } => {
      try {
        const result = spawnSync(cmd, versionArgs, { encoding: 'utf-8', timeout: 5000 });
        if (result.status === 0) {
          const output = (result.stdout || result.stderr || '').split('\n')[0].trim();
          return { available: true, version: output };
        }
        return { available: false };
      } catch {
        return { available: false };
      }
    };

    const ytDlpAvailable = !!this.ytDlpBinaryPath && fs.existsSync(this.ytDlpBinaryPath);
    const galleryDlAvailable = !!this.galleryDlBinaryPath && fs.existsSync(this.galleryDlBinaryPath);

    return {
      ytDlp: ytDlpAvailable
        ? { ...getVersion(this.ytDlpBinaryPath, ['--version']), path: this.ytDlpBinaryPath }
        : { available: false, path: this.ytDlpBinaryPath },
      galleryDl: galleryDlAvailable
        ? { ...getVersion(this.galleryDlBinaryPath, ['--version']), path: this.galleryDlBinaryPath }
        : { available: false, path: this.galleryDlBinaryPath },
      ffmpeg: getVersion('ffmpeg', ['-version']),
      atomicParsley: { available: this.hasAtomicParsley() },
    };
  }

  // ─────────────────────────────────────────────────────────────────────────
  // Private helpers
  // ─────────────────────────────────────────────────────────────────────────

  private async ensureToolsVenv(): Promise<void> {
    try {
      if (!fs.existsSync(this.toolsVenvDir)) {
        this.logger.log(`⬇️  Đang tạo Python venv cho công cụ tại ${this.toolsVenvDir}...`);
        const python = findPython();
        const venvResult = spawnSync(python, ['-m', 'venv', this.toolsVenvDir], { encoding: 'utf-8' });
        if (venvResult.status !== 0) {
          this.logger.warn(`Không thể tạo tools-venv: ${venvResult.stderr}`);
          return;
        }
      }

      const pipPath = path.join(this.toolsVenvDir, 'bin', 'pip');
      const testCffi = spawnSync(this.toolsPythonPath, ['-c', 'import curl_cffi, secretstorage'], { encoding: 'utf-8' });
      if (testCffi.status !== 0) {
        this.logger.log(`⬇️  Đang cài đặt curl_cffi, secretstorage vào tools-venv...`);
        spawnSync(pipPath, ['install', 'curl_cffi', 'secretstorage', 'jeepney'], { encoding: 'utf-8', timeout: 60000 });
      }

      const verifyCffi = spawnSync(this.toolsPythonPath, ['-c', 'import curl_cffi'], { encoding: 'utf-8' });
      this.hasCurlCffi = verifyCffi.status === 0;
      if (this.hasCurlCffi) {
        this.logger.log(`✅ tools-venv: curl_cffi đã sẵn sàng hỗ trợ TLS impersonation cho TikTok`);
      }
    } catch (err: any) {
      this.logger.warn(`tools-venv init warning: ${err.message}`);
    }
  }

  private async ensureYtDlp(envPath?: string): Promise<void> {
    let resolvedRawBinary: string | null = null;

    // 1. Ưu tiên env var nếu file tồn tại
    if (envPath && fs.existsSync(envPath)) {
      resolvedRawBinary = envPath;
      this.logger.log(`✅ yt-dlp: sử dụng binary từ env (${envPath})`);
    } else {
      // 2. Kiểm tra system PATH
      try {
        const systemPath = execSync('which yt-dlp', { encoding: 'utf-8' }).trim();
        if (systemPath) {
          resolvedRawBinary = systemPath;
          this.logger.log(`✅ yt-dlp: tìm thấy trong system PATH (${systemPath})`);
        }
      } catch {
        // không có trong PATH
      }

      // 3. Kiểm tra binary đã được tải vào bin/
      if (!resolvedRawBinary && fs.existsSync(this.ytDlpBinaryPath)) {
        resolvedRawBinary = this.ytDlpBinaryPath;
        this.logger.log(`✅ yt-dlp: đã có binary tại ${this.ytDlpBinaryPath}`);
      }

      // 4. Tải binary từ GitHub nếu chưa có
      if (!resolvedRawBinary) {
        this.logger.log(`⬇️  yt-dlp: chưa có binary, đang tải từ GitHub...`);
        try {
          const downloadUrl = await getYtDlpDownloadUrl();
          this.logger.log(`⬇️  yt-dlp URL: ${downloadUrl}`);
          await downloadFile(downloadUrl, this.ytDlpBinaryPath);
          await chmod(this.ytDlpBinaryPath, 0o755);
          resolvedRawBinary = this.ytDlpBinaryPath;
          this.logger.log(`✅ yt-dlp: đã tải thành công vào ${this.ytDlpBinaryPath}`);
        } catch (err: any) {
          this.logger.error(`❌ yt-dlp: không thể tải binary - ${err.message}`);
          this.logger.warn(`   Hãy đặt YT_DLP_PATH trong .env để trỏ đến binary yt-dlp thủ công.`);
        }
      }
    }

    if (resolvedRawBinary) {
      this.ytDlpBinaryPath = resolvedRawBinary;
      // Nếu có tools-venv với curl_cffi, bọc yt-dlp bằng runner script để chạy với python impersonation
      if (this.hasCurlCffi && fs.existsSync(resolvedRawBinary)) {
        try {
          const runnerPath = path.join(this.binDir, 'yt-dlp-runner');
          await writeFile(
            runnerPath,
            `#!/bin/sh\nexec "${this.toolsPythonPath}" "${resolvedRawBinary}" "$@"\n`,
            { mode: 0o755 },
          );
          this.ytDlpBinaryPath = runnerPath;
          this.logger.log(`✅ yt-dlp: đã gắn wrapper với python impersonation (${runnerPath})`);
        } catch (e: any) {
          this.logger.warn(`Không thể tạo runner cho yt-dlp: ${e.message}`);
        }
      }
    }
  }

  private async ensureGalleryDl(envPath?: string): Promise<void> {
    // 1. Ưu tiên env var nếu file tồn tại
    if (envPath && fs.existsSync(envPath)) {
      this.galleryDlBinaryPath = envPath;
      this.logger.log(`✅ gallery-dl: sử dụng binary từ env (${envPath})`);
      return;
    }

    // 2. Kiểm tra system PATH (ví dụ ~/.local/bin/gallery-dl)
    try {
      const systemPath = execSync('which gallery-dl', { encoding: 'utf-8' }).trim();
      if (systemPath) {
        this.galleryDlBinaryPath = systemPath;
        this.logger.log(`✅ gallery-dl: tìm thấy trong system PATH (${systemPath})`);
        return;
      }
    } catch {
      // không có trong PATH
    }

    // 3. Kiểm tra binary đã được cài vào bin/
    if (fs.existsSync(this.galleryDlBinaryPath)) {
      this.logger.log(`✅ gallery-dl: đã có binary tại ${this.galleryDlBinaryPath}`);
      return;
    }

    // 4. Cài gallery-dl qua pip vào thư mục venv riêng của project
    this.logger.log(`⬇️  gallery-dl: chưa có, đang cài via pip vào ${this.galleryDlVenvDir}...`);
    try {
      const python = findPython();

      // Tạo virtual environment
      if (!fs.existsSync(this.galleryDlVenvDir)) {
        this.logger.log(`⬇️  gallery-dl: tạo Python venv tại ${this.galleryDlVenvDir}...`);
        const venvResult = spawnSync(python, ['-m', 'venv', this.galleryDlVenvDir], { encoding: 'utf-8' });
        if (venvResult.status !== 0) {
          throw new Error(`Không thể tạo venv: ${venvResult.stderr}`);
        }
      }

      // Pip trong venv
      const pipPath = path.join(this.galleryDlVenvDir, 'bin', 'pip');
      const galleryDlInVenv = path.join(this.galleryDlVenvDir, 'bin', 'gallery-dl');

      // Cài gallery-dl
      this.logger.log(`⬇️  gallery-dl: pip install gallery-dl...`);
      const pipResult = spawnSync(pipPath, ['install', '--upgrade', 'gallery-dl'], {
        encoding: 'utf-8',
        timeout: 120000,
      });

      if (pipResult.status !== 0) {
        throw new Error(`pip install thất bại: ${pipResult.stderr}`);
      }

      if (fs.existsSync(galleryDlInVenv)) {
        // Tạo symlink hoặc wrapper script tại bin/gallery-dl
        await writeFile(
          this.galleryDlBinaryPath,
          `#!/bin/sh\nexec "${galleryDlInVenv}" "$@"\n`,
          { mode: 0o755 },
        );
        this.logger.log(`✅ gallery-dl: đã cài thành công, wrapper tại ${this.galleryDlBinaryPath}`);
      } else {
        throw new Error(`gallery-dl binary không tìm thấy sau khi cài: ${galleryDlInVenv}`);
      }
    } catch (err: any) {
      this.logger.error(`❌ gallery-dl: không thể cài - ${err.message}`);
      this.logger.warn(`   Hãy đặt GALLERY_DL_PATH trong .env hoặc cài thủ công: pip install gallery-dl`);
    }
  }
}
