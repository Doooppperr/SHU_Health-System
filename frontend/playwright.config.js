import path from "node:path";
import { fileURLToPath } from "node:url";

import { defineConfig } from "@playwright/test";

const frontendDirectory = path.dirname(fileURLToPath(import.meta.url));
const backendDirectory = path.resolve(frontendDirectory, "../backend");
const externalBaseUrl = String(process.env.PLAYWRIGHT_BASE_URL || "").trim();
const pythonCommand = process.platform === "win32"
  ? ".\\.venv\\Scripts\\python.exe scripts\\run_e2e_server.py"
  : ".venv/bin/python scripts/run_e2e_server.py";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 1,
  timeout: 45_000,
  expect: { timeout: 10_000 },
  reporter: [["list"], ["html", { open: "never", outputFolder: "playwright-report" }]],
  use: {
    baseURL: externalBaseUrl || "http://127.0.0.1:5173",
    channel: process.env.PLAYWRIGHT_CHANNEL || (process.platform === "win32" ? "chrome" : undefined),
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: externalBaseUrl
    ? undefined
    : [
        {
          command: pythonCommand,
          cwd: backendDirectory,
          url: "http://127.0.0.1:5050/api/health",
          timeout: 180_000,
          reuseExistingServer: true,
        },
        {
          command: "npm run dev -- --host 127.0.0.1 --port 5173",
          cwd: frontendDirectory,
          url: "http://127.0.0.1:5173",
          timeout: 120_000,
          reuseExistingServer: true,
        },
      ],
});
