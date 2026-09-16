import { defineConfig } from '@playwright/test';
import { loadEnvFile } from 'node:process';
try { loadEnvFile('.env'); } catch { /* credentials may be supplied by CI */ }
export default defineConfig({
  testDir: './test/browser', fullyParallel: false, workers: 1, timeout: 60000,
  use: { baseURL: 'http://127.0.0.1:3001', headless: true, viewport: { width: 1440, height: 1000 }, trace: 'off', actionTimeout: 10000,
    launchOptions: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {} },
  // A separate port tests current source without replacing the Docker portal.
  webServer: { command: 'uv run --no-sync uvicorn server.app:create_app --factory --host 127.0.0.1 --port 3001', port: 3001, reuseExistingServer: false,
    env: { PORT: '3001', HOST: '127.0.0.1' } },
});
