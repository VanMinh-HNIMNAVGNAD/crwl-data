# Hướng Dẫn Phát Triển (Localhost) & Đóng Gói (Build) Ứng Dụng Social Media Crawler

> **Dự án**: Social Media Crawler (Desktop Application)  
> **Kiến trúc**: Tauri 2.x (WebKitGTK) + Rust (Core & DB) + Python 3 (Extraction Sidecar) + React 19 (Vite UI)  
> **Hệ điều hành mục tiêu chính**: Linux (Ubuntu/Debian, Fedora, Arch Linux)

---

## Mục Lục
1. [Kiến Trúc & Giải Đáp Về Mã Nguồn HTML](#1-kiến-trúc--giải-đáp-về-mã-nguồn-html)
2. [Yêu Cầu Môi Trường (Prerequisites)](#2-yêu-cầu-môi-trường-prerequisites)
3. [Cấu Hình Môi Trường (.env & Database)](#3-cấu-hình-môi-trường-env--database)
4. [Hướng Dẫn Chạy Môi Trường Phát Triển (Localhost & Dev)](#4-hướng-dẫn-chạy-môi-trường-phát-triển-localhost--dev)
   - [Cách 1: Chạy Full Ứng Dụng Desktop (Khuyên Dùng)](#cách-1-chạy-full-ứng-dụng-desktop-khuyên-dùng)
   - [Cách 2: Chạy Preview Giao Diện Web trên Localhost Trình Duyệt](#cách-2-chạy-preview-giao-diện-web-trên-localhost-trình-duyệt)
   - [Cách 3: Chạy & Kiểm Thử Độc Lập Lõi Python Engine](#cách-3-chạy--kiểm-thử-độc-lập-lõi-python-engine)
5. [Hướng Dẫn Đóng Gói & Build Ứng Dụng (Production Build)](#5-hướng-dẫn-đóng-gói--build-ứng-dụng-production-build)
   - [Bước 1: Build Frontend Web Assets](#bước-1-build-frontend-web-assets)
   - [Bước 2: Đóng Gói Bộ Cài Desktop (.deb, .AppImage, Binary)](#bước-2-đóng-gói-bộ-cài-desktop-deb-appimage-binary)
6. [Quản Lý Công Cụ Đi Kèm (yt-dlp, ffmpeg, gallery-dl)](#6-quản-lý-công-cụ-đi-kèm-yt-dlp-ffmpeg-gallery-dl)
7. [Xử Lý Sự Cố Thường Gặp (Troubleshooting FAQ)](#7-xử-lý-sự-cố-thường-gặp-troubleshooting-faq)
8. [Bảng Tra Cứu Lệnh Nhanh (Cheatsheet)](#8-bảng-tra-cứu-lệnh-nhanh-cheatsheet)

---

---

## 2. Yêu Cầu Môi Trường (Prerequisites)

Trước khi chạy hoặc build ứng dụng trên Linux, hãy đảm bảo hệ thống đã cài đặt các công cụ sau:

### 2.1. Node.js & pnpm
- **Node.js**: Phiên bản `>= 22.12.0` (khuyên dùng Node 22 LTS).
- **pnpm**: Phiên bản `>= 9.15.5`.

```bash
# Kiểm tra phiên bản
node -v
pnpm -v
```

### 2.2. Rust Toolchain (Bắt buộc cho Tauri)
Cài đặt Rust và Cargo (khuyên dùng phiên bản stable mới nhất):

```bash
# Cài đặt qua rustup nếu chưa có
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
source "$HOME/.cargo/env"

# Kiểm tra
rustc --version
cargo --version
```

### 2.3. Các Thư Viện Hệ Thống Linux (WebKitGTK & Build Tools)
Tauri yêu cầu một số thư viện C/GTK để render giao diện và tương tác hệ thống:

- **Ubuntu / Debian / Linux Mint**:
  ```bash
  sudo apt update
  sudo apt install -y \
    build-essential \
    curl \
    wget \
    file \
    libssl-dev \
    libgtk-3-dev \
    libwebkit2gtk-4.1-dev \
    libayatana-appindicator3-dev \
    librsvg2-dev \
    libxdo-dev
  ```
- **Fedora / RHEL**:
  ```bash
  sudo dnf install \
    gcc \
    gcc-c++ \
    webkit2gtk4.1-devel \
    openssl-devel \
    curl \
    wget \
    file \
    libappindicator-gtk3-devel \
    librsvg2-devel
  ```
- **Arch Linux / Manjaro**:
  ```bash
  sudo pacman -S --needed \
    base-devel \
    curl \
    wget \
    openssl \
    webkit2gtk-4.1 \
    libappindicator-gtk3 \
    librsvg
  ```

### 2.4. Python & Các Tiện Ích Media
- **Python**: `>= 3.10`
  ```bash
  sudo apt install -y python3 python3-pip python3-venv ffmpeg
  ```
- Cài đặt các thư viện bóc tách lõi Python:
  ```bash
  pip install --upgrade yt-dlp curl_cffi requests gallery-dl
  ```

---

## 3. Cấu Hình Môi Trường (.env & Database)

Ứng dụng hỗ trợ kết nối PostgreSQL (Supabase Cloud hoặc PostgreSQL local).

### 3.1. Vị trí đọc file cấu hình:
Hệ thống ưu tiên đọc file cấu hình theo thứ tự:
1. `.env` ở thư mục gốc của project: `/home/minh/code/crwl-on-socialmedia/.env`
2. `~/.config/crwl/.env` (Tự động khởi tạo trong thư mục cấu hình desktop của người dùng).

### 3.2. Nội dung file `.env` mẫu:
```ini
# Kết nối Supabase PostgreSQL qua IPv4 Pooler (Khuyên dùng để tránh lỗi IPv6)
DATABASE_URL=postgresql://postgres.[PROJECT_REF]:[PASSWORD]@aws-0-ap-northeast-2.pooler.supabase.com:5432/postgres

# Hoặc nếu sử dụng PostgreSQL cục bộ (Localhost):
# DATABASE_URL=postgresql://postgres:[PASSWORD]@localhost:5432/postgres
```

> [!CAUTION]
> **Không bao giờ commit chuỗi kết nối thật vào repo.** File `.env` đã nằm trong
> `.gitignore` — hãy điền thông tin thật ở đó, hoặc ở `~/.config/crwl/.env`, hoặc qua
> mục cấu hình của ứng dụng. Tài liệu này chỉ chứa giá trị mẫu.

> [!TIP]
> Nếu mật khẩu của bạn có ký tự đặc biệt như `@` hay `#`, hãy URL-encode chúng (ví dụ: `@` thành `%40`, `#` thành `%23`).

---

## 4. Hướng Dẫn Chạy Môi Trường Phát Triển (Localhost & Dev)

Dự án hỗ trợ 3 chế độ chạy tùy theo nhu cầu làm việc:

### Cách 1: Chạy Full Ứng Dụng Desktop (Khuyên Dùng)
Đây là cách tiêu chuẩn để phát triển, cửa sổ ứng dụng Linux Native sẽ bật lên cùng với Hot-Reload cho cả giao diện và Rust backend:

```bash
# Chạy từ thư mục gốc của dự án:
pnpm dev:tauri

# Hoặc chạy lệnh tương đương:
pnpm --filter desktop tauri dev
```

**Cơ chế hoạt động:**
1. Tauri tự động chạy `pnpm dev` bên trong `apps/desktop` để bật Vite dev server tại `http://localhost:5173`.
2. Khởi tạo Rust backend, kết nối Database PostgreSQL.
3. Kích hoạt Python Sidecar Worker (`core/extractor_cli.py`) để sẵn sàng phân tích và bóc tách link.
4. Mở cửa sổ ứng dụng desktop. Khi sửa đổi code trong `apps/desktop/src/` (React), giao diện sẽ tự động cập nhật ngay lập tức (HMR) mà không cần khởi động lại.

---

### Cách 2: Chạy Preview Giao Diện Web trên Localhost Trình Duyệt
Nếu bạn chỉ muốn sửa giao diện, căn chỉnh CSS, component React mà không cần mở cửa sổ desktop hoặc không cần biên dịch Rust:

```bash
# Chạy từ thư mục gốc:
pnpm dev

# Hoặc từ thư mục apps/desktop:
cd apps/desktop
pnpm dev
```

- Mở trình duyệt tại: **`http://localhost:5173`**
- **Lưu ý quan trọng**:
  - Trình duyệt thông thường không có môi trường Tauri Native (`window.__TAURI_INTERNALS__`).
  - Các thao tác gọi Tauri IPC (`invoke`) như cào link thật, tải video về ổ đĩa, chọn thư mục native sẽ không thể thực thi được trên trình duyệt web thuần. Hãy sử dụng **Cách 1** khi cần kiểm thử tính năng hoàn chỉnh.

---

### Cách 3: Chạy & Kiểm Thử Độc Lập Lõi Python Engine
Nếu bạn cần thêm extractor mới, tối ưu thuật toán bóc tách video/ảnh hoặc debug URL:

```bash
# 1. Chạy bài kiểm thử toàn diện hệ sinh thái extractors:
pnpm test:core
# Hoặc:
python3 scripts/test_desktop_system.py

# 2. Bóc tách một liên kết bất kỳ qua CLI:
python3 core/extractor_cli.py "https://www.youtube.com/watch?v=dQw4w9WgXcQ" --json

# 3. Quét toàn bộ tài khoản/kênh (Profile Crawl):
python3 core/extractor_cli.py "@username" --platform tiktok --type profile --limit 20 --json
```

---

## 5. Hướng Dẫn Đóng Gói & Build Ứng Dụng (Production Build)

Khi hoàn thiện các tính năng và muốn đóng gói ứng dụng để sử dụng hoặc phân phối:

### Bước 1: Build Frontend Web Assets
Biên dịch mã nguồn React + CSS thành bộ tệp tĩnh siêu tối ưu:

```bash
pnpm build
# Hoặc:
pnpm --filter desktop build
```
- Kết quả tạo ra nằm tại: `apps/desktop/dist/`
  - `dist/index.html`
  - `dist/assets/*.css`
  - `dist/assets/*.js`

---

### Bước 2: Đóng Gói Bộ Cài Desktop (.deb, .AppImage, Binary)
Chạy lệnh đóng gói toàn bộ ứng dụng (Vite assets + Rust Native + Python core resource):

```bash
# Chạy từ thư mục gốc:
pnpm build:tauri

# Hoặc từ apps/desktop:
pnpm --filter desktop tauri build
```

**Quá trình build tự động thực hiện:**
1. Chạy lệnh `beforeBuildCommand` (`pnpm build`).
2. Biên dịch mã nguồn Rust ở chế độ `--release` với tối ưu hóa cao nhất (`lto`, `opt-level = 3`, `strip = true`).
3. Đóng gói thư mục `core/` (Python engine) vào thư mục `resources` của bundle.
4. Đóng gói icon ứng dụng và tạo desktop entry.

### 📍 Thư mục chứa kết quả build:
Sau khi build thành công, các file cài đặt sẽ nằm tại:
- **Gói Debian/Ubuntu (.deb)**:
  `apps/desktop/src-tauri/target/release/bundle/deb/social-media-crawler_0.1.0_amd64.deb`
- **Gói Chạy Nhanh (.AppImage)**:
  `apps/desktop/src-tauri/target/release/bundle/appimage/social-media-crawler_0.1.0_amd64.AppImage`
- **File nhị phân độc lập (Standalone Binary)**:
  `apps/desktop/src-tauri/target/release/social-media-crawler`

### Cách cài đặt và chạy file sau khi build:
- **Cài đặt file `.deb`**:
  ```bash
  sudo dpkg -i apps/desktop/src-tauri/target/release/bundle/deb/social-media-crawler_*.deb
  # Mở ứng dụng từ menu hoặc gõ:
  social-media-crawler
  ```
- **Chạy trực tiếp file `.AppImage`**:
  ```bash
  chmod +x apps/desktop/src-tauri/target/release/bundle/appimage/social-media-crawler_*.AppImage
  ./apps/desktop/src-tauri/target/release/bundle/appimage/social-media-crawler_*.AppImage
  ```

---

## 6. Quản Lý Công Cụ Đi Kèm (yt-dlp, ffmpeg, gallery-dl)

Ứng dụng tích hợp sẵn bộ quản lý nhị phân thông minh (`binary_manager.rs`):
- Khi mở ứng dụng, bạn có thể bấm vào icon bánh răng / công cụ ở góc trên giao diện để mở **Tools Manager Modal**.
- Tại đây, bạn có thể:
  - Xem phiên bản hiện tại của `yt-dlp`, `ffmpeg`, `gallery-dl`.
  - Bấm **Cập nhật yt-dlp** chỉ với 1 cú click (tự động thử release binary mới nhất từ GitHub, tự động fallback sang `pip install -U yt-dlp` nếu bị giới hạn rate limit).
  - Tải tự động các công cụ còn thiếu về thư mục `~/.config/crwl/bin/`.

---

## 7. Xử Lý Sự Cố Thường Gặp (Troubleshooting FAQ)

### Lỗi 1: `failed to run 'pkg-config' ... webkit2gtk-4.1`
- **Nguyên nhân**: Thiếu thư viện WebKitGTK trên máy Linux.
- **Cách khắc phục**:
  ```bash
  sudo apt install -y libwebkit2gtk-4.1-dev libgtk-3-dev
  ```

### Lỗi 2: `Database Connection Failed: No route to host`
- **Nguyên nhân**: Supabase sử dụng địa chỉ IPv6 mặc định, một số nhà mạng tại Việt Nam không định tuyến được IPv6.
- **Cách khắc phục**:
  - Dùng kết nối **IPv4 Pooler** của Supabase (`aws-0-[region].pooler.supabase.com:5432`).
  - Kiểm tra lại biến `DATABASE_URL` trong file `.env`.

### Lỗi 3: Chạy `pnpm dev` mở trình duyệt bị lỗi `invoke is not defined`
- **Nguyên nhân**: Bạn đang mở trên trình duyệt web thông thường.
- **Cách khắc phục**: Chạy lệnh `pnpm dev:tauri` để mở trong môi trường desktop native.

### Lỗi 4: Lỗi `yt-dlp: command not found` hoặc tải video không ghép được âm thanh
- **Nguyên nhân**: Hệ thống chưa cài đặt `ffmpeg`.
- **Cách khắc phục**:
  ```bash
  sudo apt install ffmpeg
  ```

---

## 8. Bảng Tra Cứu Lệnh Nhanh (Cheatsheet)

| Tác vụ | Lệnh thực hiện |
| :--- | :--- |
| **Chạy app desktop (Dev)** | `pnpm dev:tauri` |
| **Chạy giao diện web (Localhost)** | `pnpm dev` |
| **Kiểm tra cú pháp code (Lint)** | `pnpm lint` |
| **Test độc lập Python Core** | `pnpm test:core` |
| **Build Web Assets (dist)** | `pnpm build` |
| **Đóng gói Linux App (.deb / .AppImage)** | `pnpm build:tauri` |
| **Dọn dẹp thư mục build** | `cargo clean` (trong `src-tauri`) & `rm -rf apps/desktop/dist` |
