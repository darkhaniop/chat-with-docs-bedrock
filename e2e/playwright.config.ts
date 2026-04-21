import { defineConfig, devices } from "@playwright/test";

/**
 * docs/08-testing.md#end-to-end-tests: chromium and webkit only, no mobile, no firefox — kept
 * deliberately small. Runs against a real deployed `dev` stack (`CWD_E2E_BASE_URL`, the
 * CloudFront domain) with a seeded Cognito test user
 * (`CWD_E2E_TEST_USER_EMAIL`/`CWD_E2E_TEST_USER_PASSWORD`, read from `fixtures/.env` by
 * `global-setup.ts`) — there is no local/offline mode, by design (ADR-004: the four
 * un-emulated services are exactly what this suite exists to exercise for real).
 */
const baseURL = process.env.CWD_E2E_BASE_URL ?? "";

export default defineConfig({
  testDir: "./tests",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  // One worker: multiple concurrent signed-in sessions during development would race the same
  // seeded user's Cognito rate limits and the same DynamoDB items pointlessly.
  workers: 1,
  reporter: [["list"], ["html", { open: "never" }]],
  // resilience.spec.ts deliberately keeps a turn in flight through the ~3s polling fallback
  // (docs/06-frontend.md), which needs more headroom than Playwright's 30s default.
  timeout: 180_000,
  globalSetup: "./global-setup.ts",
  use: {
    baseURL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "webkit", use: { ...devices["Desktop Safari"] } },
  ],
});
