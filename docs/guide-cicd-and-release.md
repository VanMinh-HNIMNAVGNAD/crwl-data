# Hướng Dẫn CI/CD & Quy Trình Phát Hành Bản Cập Nhật (GitHub Release)

> **Dự án**: Social Media Crawler (Desktop App)  
> **Nền tảng hỗ trợ**: Windows (10/11) & Linux (Ubuntu, Debian, Fedora, Arch...)  
> **Công cụ tự động hóa**: GitHub Actions + Tauri Action v0  
> **Cơ sở dữ liệu**: SQLite Cục Bộ (Local Embedded Database, Bảo mật 100%)  

---

## Mục Lục
1. [Cơ Chế Hoạt Động Của CI/CD](#1-cơ-chế-hoạt-động-của-cicd)
2. [Quy Trình Phát Triển Hằng Ngày](#2-quy-trình-phát-triển-hằng-ngày)
3. [Quy Trình Phát Hành Bản Mới (Tạo Release)](#3-quy-trình-phát-hành-bản-mới-tạo-release)
4. [Cách Phát Hành Thủ Công Trên Giao Diện Web](#4-cách-phát-hành-thủ-công-trên-giao-diện-web)
5. [Cơ Sở Dữ Liệu SQLite Cục Bộ](#5-cơ-sở-dữ-liệu-sqlite-cục-bộ)
6. [Danh Sách Tệp Đóng Gói & Tải Về](#6-danh-sách-tệp-đóng-gói--tải-về)
7. [Bảng Tra Cứu Lệnh Nhanh (Cheatsheet)](#7-bảng-tra-cứu-lệnh-nhanh-cheatsheet)

---

## 1. Cơ Chế Hoạt Động Của CI/CD

Workflow CI/CD được thiết lập tại tệp [`.github/workflows/release.yml`](../.github/workflows/release.yml).

### ❓ Khi nào CI/CD tự động chạy?
- ✅ **Khi bạn đẩy một Git Tag mới bắt đầu bằng chữ `v`** (ví dụ: `v0.1.2`, `v0.1.3`...).
- ✅ **Khi bạn bấm nút "Run workflow" thủ công** trên tab Actions của GitHub.
- ❌ **KHÔNG CHẠY** khi bạn chỉ `git push` code bình thường lên nhánh `main`.

### 💡 Vì sao không tự động build mỗi khi push code?
- Mỗi lần build đa nền tảng, GitHub phải khởi chạy 2 máy ảo độc lập (Windows Runner và Ubuntu Runner) và mất khoảng **5–8 phút** để biên dịch toàn bộ Rust.
- Nếu mỗi commit nhỏ (sửa chữ, sửa style) đều kích hoạt build sẽ rất tốn thời gian, lãng phí số phút miễn phí của GitHub và làm mục **Releases** bị rác bởi các bản build thử nghiệm.

---

## 2. Quy Trình Phát Triển Hằng Ngày

Trong quá trình viết code, sửa lỗi, thêm giao diện:

1. Chạy thử nghiệm và kiểm tra tại máy local:
   ```bash
   pnpm dev:tauri
   ```
2. Lưu code và đẩy lên GitHub bình thường:
   ```bash
   git add .
   git commit -m "feat: thêm tính năng tải video định dạng mới"
   git push origin main
   ```
*(Lúc này GitHub chỉ lưu trữ code của bạn an toàn, **hoàn toàn không kích hoạt build release**).*

---

## 3. Quy Trình Phát Hành Bản Mới (Tạo Release)

Khi bạn đã gom đủ các tính năng hoặc sửa lỗi xong và muốn **tung ra bản cài đặt mới** cho người dùng (ví dụ lên bản `v0.1.2`):

### Bước 1: Đồng bộ số phiên bản (Version Bump)
Cập nhật số phiên bản từ `0.1.1` lên `0.1.2` ở 4 tệp sau:
1. `package.json` (thư mục gốc): dòng `"version": "0.1.2"`
2. `apps/desktop/package.json`: dòng `"version": "0.1.2"`
3. `apps/desktop/src-tauri/tauri.conf.json`: dòng `"version": "0.1.2"`
4. `apps/desktop/src-tauri/Cargo.toml`: dòng `version = "0.1.2"`

### Bước 2: Commit và đẩy Tag phiên bản lên GitHub
Mở terminal và chạy 4 lệnh sau:

```bash
# 1. Commit các thay đổi và số phiên bản mới
git add .
git commit -m "chore: release v0.1.2"
git push origin main

# 2. Tạo tag phiên bản và đẩy lên GitHub
git tag v0.1.2
git push origin v0.1.2
```

### Bước 3: GitHub Actions tự động thực hiện
Ngay sau khi lệnh `git push origin v0.1.2` hoàn thành:
1. GitHub Actions sẽ tự động bật 2 máy ảo:
   - 🪟 **Máy Windows**: Build bộ cài đặt `.exe` và `.msi`.
   - 🐧 **Máy Linux**: Build gói `.deb` và `.AppImage`.
2. Sau khoảng 5–8 phút, toàn bộ các file cài đặt sẽ được gom chung và xuất bản vào **một trang Release duy nhất** trên GitHub.

---

## 4. Cách Phát Hành Thủ Công Trên Giao Diện Web

Nếu bạn không muốn gõ lệnh tạo tag trên terminal:

1. Truy cập vào mục **Actions** của repo:  
   👉 [https://github.com/VanMinh-HNIMNAVGNAD/crwl-data/actions](https://github.com/VanMinh-HNIMNAVGNAD/crwl-data/actions)
2. Ở cột bên trái, bấm vào workflow **Release App**.
3. Ở góc trên bên phải, bấm vào nút **Run workflow**:
   - Nhập tag phiên bản mong muốn (ví dụ: `v0.1.2`).
   - Bấm nút xanh **Run workflow**.

---

## 5. Cơ Sở Dữ Liệu SQLite Cục Bộ

Ứng dụng sử dụng cơ sở dữ liệu **SQLite cục bộ** (Local Embedded Database), đảm bảo tính riêng tư và bảo mật dữ liệu tuyệt đối:

### 1. Cơ Chế Hoạt Động & Bảo Mật
- **Không gửi dữ liệu ra ngoài**: Lịch sử tải, thông tin media và thiết bị chỉ được lưu trực tiếp trên máy của người dùng.
- **Không lưu/nhúng credential**: Không có bất kỳ chuỗi mật khẩu, access token hay connection string nào được nhúng vào file cài đặt, loại bỏ 100% rủi ro rò rỉ cơ sở dữ liệu.
- **Hoạt động Offline hoàn toàn**: Ứng dụng tự tạo và quản lý tệp SQLite ngay khi khởi động mà không phụ thuộc vào kết nối mạng hay trạng thái server.

### 2. Vị Trí Lưu Tệp Cơ Sở Dữ Liệu (`crwl.db`)
- **Linux**: `~/.config/crwl/crwl.db`
- **Windows**: `%APPDATA%\crwl\crwl.db` (ví dụ `C:\Users\<User>\AppData\Roaming\crwl\crwl.db`)

---

## 6. Danh Sách Tệp Đóng Gói & Tải Về

Tất cả các bản phát hành được lưu trữ tại:  
👉 **[https://github.com/VanMinh-HNIMNAVGNAD/crwl-data/releases](https://github.com/VanMinh-HNIMNAVGNAD/crwl-data/releases)**  
👉 **Bản mới nhất (Latest)**: **[https://github.com/VanMinh-HNIMNAVGNAD/crwl-data/releases/latest](https://github.com/VanMinh-HNIMNAVGNAD/crwl-data/releases/latest)**

Trong mục **Assets** của mỗi bản release sẽ có:

| Hệ điều hành | Tên tệp | Định dạng | Mục đích sử dụng |
| :--- | :--- | :--- | :--- |
| **Windows** | `social-media-crawler_..._x64-setup.exe` | NSIS Setup | **Khuyên dùng cho Windows**: Trình cài đặt tự động, tạo shortcut màn hình & Start menu. |
| **Windows** | `social-media-crawler_..._x64_en-US.msi` | MSI | Gói cài đặt chuẩn Windows Installer dành cho doanh nghiệp hoặc quản trị hệ thống. |
| **Linux** | `social-media-crawler_..._amd64.AppImage` | AppImage | **Khuyên dùng cho Linux**: Chạy trực tiếp trên mọi bản Linux (Ubuntu, Fedora, Arch...) không cần cài đặt. |
| **Linux** | `social-media-crawler_..._amd64.deb` | Debian Package | Gói cài đặt tiêu chuẩn cho Ubuntu, Debian, Linux Mint (`sudo dpkg -i ...`). |

---

## 7. Bảng Tra Cứu Lệnh Nhanh (Cheatsheet)

| Nhu cầu | Lệnh thực hiện |
| :--- | :--- |
| **Chạy dev app desktop** | `pnpm dev:tauri` |
| **Chạy dev giao diện web** | `pnpm dev` |
| **Build thử app local (Linux)** | `pnpm build:tauri` |
| **Lưu code hằng ngày (không build CI)** | `git commit -am "nội dung" && git push origin main` |
| **Phát hành phiên bản mới (kích hoạt CI/CD)** | `git tag v0.1.x && git push origin v0.1.x` |
| **Xóa một tag bị lỗi trên local & GitHub** | `git tag -d v0.1.x && git push origin --delete v0.1.x` |
