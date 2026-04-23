import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// `vite.config.ts` doesn't set `test.globals: true`, so `@testing-library/react`'s own
// auto-cleanup (which only registers itself when it finds a *global* `afterEach`) never
// triggers — every previous test file happened not to need it (single render per file, or
// content distinct enough not to collide), until `MessageText.test.tsx` rendered the same
// accessible name ("[0]") across multiple `it` blocks in one file and got a "multiple elements
// found" error from a *previous* test's DOM still being there. Registering this explicitly
// fixes it for every test file, not just that one.
afterEach(cleanup);

// jsdom (unlike a real browser) has no canvas backend and doesn't implement `DOMMatrix`.
// `pdfjs-dist`'s single bundled module touches `new DOMMatrix()` at top-level scope (its canvas
// glue, unrelated to the pure `PageViewport` math this codebase actually uses in tests) so
// merely importing anything from "pdfjs-dist" throws `ReferenceError: DOMMatrix is not defined`
// without this. The stub only needs to be constructible — nothing under test exercises it.
if (typeof globalThis.DOMMatrix === "undefined") {
  // @ts-expect-error -- minimal test-only stub, not a real DOMMatrix implementation
  globalThis.DOMMatrix = class DOMMatrix {};
}

// `pdfjs-dist`'s worker-message plumbing calls `Promise.try`, a method this project's pinned
// Node 22 (matching `.gitlab-ci.yml`'s `node:22.23.2`) doesn't have. Real browsers running the
// actual worker are unaffected; this only matters for merely importing "pdfjs-dist" under
// vitest/jsdom (`lib/geometry.test.ts`, `features/viewer/*`).
const promiseCtor = Promise as unknown as { try?: (fn: () => unknown) => Promise<unknown> };
if (promiseCtor.try === undefined) {
  promiseCtor.try = (fn: () => unknown) => new Promise((resolve) => resolve(fn()));
}

// jsdom has no layout engine, so it never implements `ResizeObserver` (there's nothing real for
// it to observe). `ImageViewer` uses it to track the displayed image's CSS size for the
// highlight overlay's scale conversion; tests drive that same state update directly (via a fake
// `load` event with a mocked `clientWidth`/`clientHeight`, see `ImageViewer.test.tsx`) rather
// than through a real resize, so this stub only needs to exist, not actually observe anything.
if (typeof globalThis.ResizeObserver === "undefined") {
  globalThis.ResizeObserver = class ResizeObserver {
    observe(): void {}
    unobserve(): void {}
    disconnect(): void {}
  };
}

// jsdom doesn't implement `window.matchMedia` at all. `app/providers.tsx`'s `useSystemDarkMode`
// calls it to follow `prefers-color-scheme`; no current test wraps a component in
// `AppProviders` (App.test.tsx renders `<App>` directly under `AuthProvider`), so nothing
// exercises this today, but a bare stub here means a future test that does won't need to
// rediscover this gap.
if (typeof globalThis.matchMedia === "undefined") {
  globalThis.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addEventListener: () => {},
      removeEventListener: () => {},
      addListener: () => {},
      removeListener: () => {},
      dispatchEvent: () => false,
    }) as unknown as MediaQueryList;
}
