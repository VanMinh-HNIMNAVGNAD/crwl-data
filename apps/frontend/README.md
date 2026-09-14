# Desktop Frontend (React 19 + Vite + Tauri 2)

This directory contains the user interface for the Social Media Crawler Linux Desktop application.

## Development & Build Commands

- `pnpm dev`: Runs the Vite web preview server locally at `http://localhost:5173`.
- `pnpm tauri dev` (or `pnpm dev:tauri` from repo root): Launches the full Tauri 2 native desktop application with live reload.
- `pnpm build`: Builds the production web frontend distribution files into `dist/`.
- `pnpm tauri build` (or `pnpm build:tauri` from repo root): Packages the native Linux desktop binary (`.deb`, `.AppImage`).
- `pnpm lint`: Runs ESLint validation.

