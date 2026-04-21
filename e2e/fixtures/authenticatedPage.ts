import * as fs from "node:fs";
import { test as base } from "@playwright/test";
import { SESSION_STORAGE_FILE } from "../global-setup";

/**
 * Every spec should import `test`/`expect` from here, not `@playwright/test` directly — the
 * `page` fixture arrives already signed in (see `global-setup.ts` for why this restores
 * `sessionStorage` via `addInitScript` instead of Playwright's built-in `storageState()`).
 */
export const test = base.extend({
  page: async ({ page }, use) => {
    const sessionStorageJson = fs.readFileSync(SESSION_STORAGE_FILE, "utf-8");
    await page.addInitScript((json: string) => {
      const data = JSON.parse(json) as Record<string, string>;
      for (const [key, value] of Object.entries(data)) {
        window.sessionStorage.setItem(key, value);
      }
    }, sessionStorageJson);
    await use(page);
  },
});

export { expect } from "@playwright/test";
