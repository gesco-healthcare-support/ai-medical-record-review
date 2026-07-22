import { defineConfig, devices } from "@playwright/test";

// End-to-end specs run against a LIVE app stack (the docker-compose proxy on :8080), started
// externally - locally via `docker compose up`, in CI by the e2e job. No webServer here on
// purpose: the app is more than the Next server (nginx + FastAPI + Postgres + Redis), so the
// compose stack is the faithful target. Override the origin with E2E_BASE_URL.
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:8080",
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
