import { GlobalWorkerOptions } from "pdfjs-dist";

/**
 * docs/06-frontend.md#pdf-viewer-and-highlighting: "the worker self-hosted from public/ and
 * pinned to the exact package version — a CDN worker version drift is a classic source of
 * silent rendering breakage." `public/pdf.worker.min.mjs` is copied byte-for-byte from
 * `node_modules/pdfjs-dist/build/pdf.worker.min.mjs` at the exact `pdfjs-dist` version pinned in
 * `package.json` (no caret range there, unlike this project's other dependencies) — re-copy it
 * any time that version changes.
 */
GlobalWorkerOptions.workerSrc = "/pdf.worker.min.mjs";
