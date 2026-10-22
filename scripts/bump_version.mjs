#!/usr/bin/env node
// Đồng bộ version của app ở mọi nơi khai báo nó.
//
//   node scripts/bump_version.mjs 3.7.4   -> ghi version mới vào tất cả file
//   node scripts/bump_version.mjs --check -> báo lỗi (exit 1) nếu các file lệch version
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');

// Mỗi mục: file + regex bắt version (nhóm 1 = phần trước, nhóm 2 = version, nhóm 3 = phần sau).
// Regex chỉ khớp lần xuất hiện đầu tiên, nên không đụng tới version của dependency.
const TARGETS = [
  { file: 'package.json', pattern: /^(\s*"version":\s*")([^"]+)(")/m },
  { file: 'apps/desktop/package.json', pattern: /^(\s*"version":\s*")([^"]+)(")/m },
  { file: 'apps/desktop/src-tauri/tauri.conf.json', pattern: /^(\s*"version":\s*")([^"]+)(")/m },
  { file: 'apps/desktop/src-tauri/Cargo.toml', pattern: /^(version\s*=\s*")([^"]+)(")/m },
  { file: 'apps/desktop/src-tauri/Cargo.lock', pattern: /(name = "app"\nversion = ")([^"]+)(")/ },
  { file: 'README.md', pattern: /(badge\/version-)([^-]+)(-blue)/ },
];

const SEMVER = /^\d+\.\d+\.\d+$/;

function readVersions() {
  return TARGETS.map(({ file, pattern }) => {
    const path = join(ROOT, file);
    const text = readFileSync(path, 'utf8');
    const match = text.match(pattern);
    if (!match) throw new Error(`Không tìm thấy version trong ${file}`);
    return { file, path, pattern, text, version: match[2] };
  });
}

function check() {
  const entries = readVersions();
  const versions = new Set(entries.map((e) => e.version));
  for (const { file, version } of entries) console.log(`${version.padEnd(12)} ${file}`);
  if (versions.size > 1) {
    console.error('\n❌ Version không khớp. Chạy: pnpm bump <version>');
    process.exit(1);
  }
  console.log('\n✅ Tất cả version khớp nhau.');
}

function bump(next) {
  if (!SEMVER.test(next)) {
    console.error(`Version "${next}" không hợp lệ (cần dạng X.Y.Z).`);
    process.exit(1);
  }
  for (const { file, path, pattern, text, version } of readVersions()) {
    writeFileSync(path, text.replace(pattern, `$1${next}$3`));
    console.log(`${file}: ${version} -> ${next}`);
  }
}

const arg = process.argv[2];
if (!arg) {
  console.error('Cách dùng: node scripts/bump_version.mjs <version> | --check');
  process.exit(1);
}
if (arg === '--check') check();
else bump(arg);
