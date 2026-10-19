import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  testIgnore: "**/offline/**",
  timeout: 40_000,
  fullyParallel: false,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: process.env.TEST_BASE_URL ?? "http://127.0.0.1:5182",
    viewport: { width: 1440, height: 1000 },
    launchOptions: { executablePath: process.env.CHROME_PATH },
    channel: process.env.CHROME_PATH ? undefined : "chrome",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
});
