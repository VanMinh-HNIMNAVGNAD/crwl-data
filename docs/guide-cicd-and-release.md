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
   - [Quy tắc đánh số phiên bản](#quy-tắc-đánh-số-phiên-bản)
4. [Cách Phát Hành Thủ Công Trên Giao Diện Web](#4-cách-phát-hành-thủ-công-trên-giao-diện-web)
5. [Cơ Sở Dữ Liệu SQLite Cục Bộ](#5-cơ-sở-dữ-liệu-sqlite-cục-bộ)
6. [Danh Sách Tệp Đóng Gói & Tải Về](#6-danh-sách-tệp-đóng-gói--tải-về)
7. [Bảng Tra Cứu Lệnh Nhanh (Cheatsheet)](#7-bảng-tra-cứu-lệnh-nhanh-cheatsheet)
8. [Lịch Sử Đánh Lại Số Phiên Bản (11/2026)](#8-lịch-sử-đánh-lại-số-phiên-bản-112026)

---

## 1. Cơ Chế Hoạt Động Của CI/CD

Workflow CI/CD được thiết lập tại tệp [`.github/workflows/release.yml`](../.github/workflows/release.yml).

### ❓ Khi nào CI/CD tự động chạy?
- ✅ **Khi bạn đẩy một Git Tag mới bắt đầu bằng chữ `v`** (ví dụ: `v1.10.4`, `v1.11.0`...).
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

### Quy tắc đánh số phiên bản

App dùng [Semantic Versioning](https://semver.org/lang/vi/) dạng `MAJOR.MINOR.PATCH`. Chọn số mới dựa trên **các commit kể từ bản trước**:

| Trong các commit kể từ bản trước có… | Tăng | Ví dụ (từ `1.10.3`) |
| :--- | :--- | :--- |
| Thay đổi phá vỡ: dữ liệu/cài đặt cũ không dùng được nữa, bỏ hẳn chức năng (`feat!:`, `BREAKING CHANGE`) | **MAJOR**, MINOR và PATCH về 0 | `2.0.0` |
| Ít nhất một `feat:`: tính năng mới hoặc thay đổi người dùng thấy rõ (giao diện, hành vi) | **MINOR**, PATCH về 0 | `1.11.0` |
| Chỉ có `fix:`, `perf:`, `refactor:`, `chore(deps):`… | **PATCH** | `1.10.4` |

Không được làm:
- Nhảy cóc số (không đi từ `1.10.3` lên `1.13.0` hay `2.5.1`); mỗi lần chỉ tăng đúng **1** ở một vị trí.
- Gắn tag vào commit mà version trong code chưa đổi: workflow sẽ **dừng** nếu tag ≠ `v` + version trong code.
- Phát hành lại cùng một số: sửa gấp thì ra bản PATCH tiếp theo.

### Bước 1: Đồng bộ số phiên bản (Version Bump)

Một lệnh ghi version mới vào mọi nơi (`package.json`, `apps/desktop/package.json`, `tauri.conf.json`, `Cargo.toml`, `Cargo.lock`, badge trong `README.md`):

```bash
pnpm bump 1.10.4        # thay bằng số mới theo quy tắc ở trên
pnpm version:check      # kiểm tra mọi file đã khớp
```

### Bước 2: Commit và đẩy Tag phiên bản lên GitHub

```bash
git commit -am "chore: bump version to 1.10.4"
git push origin main

# Tag phải gắn vào ĐÚNG commit vừa bump ở trên
git tag -a v1.10.4 -m "Release v1.10.4"
git push origin v1.10.4
```

### Bước 3: GitHub Actions tự động thực hiện

Ngay sau khi tag được đẩy lên:
1. Job **prepare** kiểm tra tag khớp version trong code, soạn ghi chú phát hành từ danh sách commit kể từ tag trước (bỏ qua commit bump version), rồi tạo **một** trang Release.
2. Job **build** chạy song song trên 2 máy ảo và tải file cài đặt vào đúng trang Release đó:
   - 🪟 **Máy Windows**: `.exe` (NSIS) và `.msi`.
   - 🐧 **Máy Linux**: `.deb`, `.rpm` và `.AppImage`.
3. Sau khoảng 6–15 phút, trang Release có đủ file cài đặt.

> 💡 Commit message nên theo dạng `feat: …` / `fix: …` (Conventional Commits): ghi chú phát hành lấy nguyên dòng tiêu đề commit, và nhìn vào đó là biết nên tăng MINOR hay PATCH.

---

## 4. Cách Phát Hành Thủ Công Trên Giao Diện Web

Nếu bạn không muốn gõ lệnh tạo tag trên terminal:

1. Truy cập vào mục **Actions** của repo:  
   👉 [https://github.com/VanMinh-HNIMNAVGNAD/crwl-data/actions](https://github.com/VanMinh-HNIMNAVGNAD/crwl-data/actions)
2. Ở cột bên trái, bấm vào workflow **Release App**.
3. Ở góc trên bên phải, bấm vào nút **Run workflow**:
   - Nhập một tag **đã có sẵn** trên GitHub (ví dụ: `v1.10.4`) để build lại bản đó.
   - Bấm nút xanh **Run workflow**.

> Chạy thủ công chỉ dùng để build lại một tag đã có (ví dụ lần build trước lỗi giữa chừng). Muốn ra bản mới thì làm theo mục 3.

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
| **Đổi số phiên bản ở mọi file** | `pnpm bump X.Y.Z` |
| **Phát hành phiên bản mới (kích hoạt CI/CD)** | `git tag -a vX.Y.Z -m "Release vX.Y.Z" && git push origin vX.Y.Z` |
| **Xóa một tag bị lỗi trên local & GitHub** | `git tag -d vX.Y.Z && git push origin --delete vX.Y.Z` |
| **Đồng bộ tag sau khi tag trên GitHub bị đổi** | `git fetch --prune --prune-tags origin` |

---

## 8. Lịch Sử Đánh Lại Số Phiên Bản (11/2026)

Trước 20/11/2026 số phiên bản được chọn tuỳ ý (ví dụ `1.5.0 → 2.7.1`, `3.0.1 → 3.3.1 → 3.5.7`). Toàn bộ tag và release cũ đã được đánh lại theo quy tắc ở mục 3: bản phát hành chính thức đầu tiên là `1.0.0`, bản có `feat` tăng MINOR, còn lại tăng PATCH. Mỗi tag mới trỏ vào **đúng commit** của tag cũ tương ứng, và release giữ nguyên ngày phát hành, file cài đặt và lượt tải.

Lưu ý: file cài đặt của các bản **trước `v1.10.3`** được build từ trước khi đánh số lại. Tên tệp đã được đổi theo số mới, nhưng phần mềm bên trong vẫn ghi số cũ (ví dụ "About" của bản `v1.3.0` vẫn hiện `0.1.9`). Từ `v1.10.3` trở đi, số trong app khớp với tag.

| Tag cũ | Tag mới |
| :--- | :--- |
| `v0.1.1` | `v1.0.0` |
| `v0.1.2` | `v1.1.0` |
| `v0.1.3` | `v1.1.1` |
| `v0.1.4` | `v1.1.2` |
| `v0.1.5` | `v1.1.3` |
| `v0.1.6` | `v1.1.4` |
| `v0.1.7` | `v1.1.5` |
| `v0.1.8` | `v1.2.0` |
| `v0.1.9` | `v1.2.1` |
| `v1.1.0` | `v1.3.0` |
| `v1.2.1` | `v1.3.1` |
| `v1.3.1` | `v1.3.2` |
| `v1.4.2` | `v1.3.3` |
| `v1.5.0` | `v1.4.0` |
| `v2.7.1` | `v1.5.0` |
| `v2.8.1` | `v1.5.1` |
| `v2.9.2` | `v1.5.2` |
| `v3.0.1` | `v1.6.0` |
| `v3.3.1` | `v1.6.1` |
| `v3.5.7` | `v1.7.0` |
| `v3.6.9` | `v1.8.0` |
| `v3.7.1` | `v1.8.1` |
| `v3.7.2` | `v1.9.0` |
| `v3.7.3` | `v1.9.1` |
| `v3.7.4` | `v1.10.0` |
| `v3.7.5` | `v1.10.1` |
| `v3.7.6` | `v1.10.2` |
| `v3.7.7` | `v1.10.3` |
