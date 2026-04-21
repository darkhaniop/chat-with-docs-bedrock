import * as fs from "node:fs";
import * as path from "node:path";
import { chromium, type FullConfig } from "@playwright/test";

/**
 * Signs the seeded test user in once, through the real Cognito Managed Login hosted UI, and
 * saves the result for every test file to reuse.
 *
 * This does **not** use Playwright's built-in `storageState()` mechanism — that API captures
 * cookies and `localStorage` only, never `sessionStorage`
 * (https://playwright.dev/docs/auth#core-concepts), and this app deliberately keeps its OIDC
 * session in `sessionStorage`, not `localStorage` (docs/07-security.md#token-handling: "closing
 * the tab ends the session" is the whole point). Instead, the signed-in page's `sessionStorage`
 * is serialised to a file, and `fixtures/authenticatedPage.ts` restores it into every test's
 * browser context via `addInitScript` before any app code runs — a documented workaround for
 * exactly this Playwright limitation, not a guess.
 */

function loadEnvFile(filePath: string): Record<string, string> {
  if (!fs.existsSync(filePath)) return {};
  const values: Record<string, string> = {};
  for (const line of fs.readFileSync(filePath, "utf-8").split("\n")) {
    const trimmed = line.trim();
    if (trimmed.length === 0 || trimmed.startsWith("#") || !trimmed.includes("=")) continue;
    const [key, ...rest] = trimmed.split("=");
    if (key === undefined) continue;
    values[key.trim()] = rest.join("=").trim();
  }
  return values;
}

const AUTH_DIR = path.join(__dirname, ".auth");
export const SESSION_STORAGE_FILE = path.join(AUTH_DIR, "session-storage.json");

export default async function globalSetup(config: FullConfig): Promise<void> {
  const env = loadEnvFile(path.join(__dirname, "fixtures", ".env"));
  const email = process.env.CWD_E2E_TEST_USER_EMAIL ?? env.CWD_E2E_TEST_USER_EMAIL;
  const password = process.env.CWD_E2E_TEST_USER_PASSWORD ?? env.CWD_E2E_TEST_USER_PASSWORD;
  const baseURL = config.projects[0]?.use.baseURL as string | undefined;

  if (!baseURL) {
    throw new Error("CWD_E2E_BASE_URL is not set (see e2e/playwright.config.ts)");
  }
  if (!email || !password) {
    throw new Error(
      "CWD_E2E_TEST_USER_EMAIL/CWD_E2E_TEST_USER_PASSWORD not set (see e2e/fixtures/.env)",
    );
  }

  const browser = await chromium.launch();
  const page = await browser.newPage();

  await page.goto(baseURL);
  await page.getByRole("button", { name: "Sign in" }).click();

  // Cognito Managed Login hosted UI — a real redirect to *.auth.*.amazoncognito.com.
  await page.waitForURL(/\.amazoncognito\.com\//, { timeout: 30_000 });
  await page.locator('input[name="username"]').fill(email);
  await page.locator('input[name="password"]').fill(password);
  await page.getByRole("button", { name: /sign in/i }).click();

  // Back on our own origin after the /callback redirect completes.
  await page.waitForURL((url) => url.origin === new URL(baseURL).origin, { timeout: 30_000 });
  await page.waitForLoadState("networkidle");

  const sessionStorageJson = await page.evaluate(() => JSON.stringify(window.sessionStorage));
  fs.mkdirSync(AUTH_DIR, { recursive: true });
  fs.writeFileSync(SESSION_STORAGE_FILE, sessionStorageJson);

  await browser.close();
}
