import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/e2e",
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || "http://localhost:3000",
    headless: true,
  },
  webServer: {
    command: "npm run dev -- --port 3000",
    port: 3000,
    timeout: 120000,
    reuseExistingServer: true,
  },
});
