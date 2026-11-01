import { defineConfig, devices } from "@playwright/test";

const port = process.env.TEAM_SYNC_WEB_TEST_PORT || "8941";
const baseURL = `http://127.0.0.1:${port}`;
const python =
  process.platform === "win32"
    ? ".venv/Scripts/python.exe"
    : ".venv/bin/python";

export default defineConfig({
  testDir: "./web-tests/e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 30000,
  use: {
    baseURL,
    ...devices["Desktop Chrome"],
    viewport: { width: 1440, height: 1000 },
    screenshot: "only-on-failure",
    trace: "off",
  },
  webServer: {
    command: `${python} -m tests.serve_web`,
    url: `${baseURL}/health`,
    reuseExistingServer: false,
    timeout: 30000,
    gracefulShutdown: { signal: "SIGTERM", timeout: 5000 },
  },
});
