import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests', workers: 1, retries: 0, timeout: 30000,
  reporter: [['list']],
  outputDir: process.env.BA_UI_OUTPUT || 'test-results',
  use: {channel: 'chrome', baseURL: process.env.BA_UI_URL || 'https://localhost:8443',
    viewport: {width: 1600, height: 1000}, ignoreHTTPSErrors: false, trace: 'off', video: 'off'},
});
