import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests/offline",
  timeout: 45_000,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: "http://127.0.0.1:5193",
    channel: process.env.CHROME_PATH ? undefined : "chrome",
    launchOptions: { executablePath: process.env.CHROME_PATH },
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: {
    command: process.env.OFFLINE_TEST_PYTHON
      ? `"${process.env.OFFLINE_TEST_PYTHON}" -m tests.browser_server`
      : '".venv/Scripts/python.exe" -m tests.browser_server',
    cwd: "../src",
    url: "http://127.0.0.1:5193/api/health",
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
