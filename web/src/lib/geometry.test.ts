import { Util } from "pdfjs-dist";
import { describe, expect, it } from "vitest";
import {
  toImageViewportRect,
  toViewportRect,
  type ViewportLike,
  type ViewportRect,
} from "./geometry";
import type { Rect } from "../api/types";

/**
 * Mirrors services/common/tests/test_geometry.py's round-trip properties
 * (docs/08-testing.md#geometry-tests) on the TypeScript side of the same conversion.
 *
 * `PageViewport` (the class real code gets from `page.getViewport(...)`) is not part of
 * `pdfjs-dist`'s runtime export surface — only its type is (confirmed against the installed
 * 5.5.207 package: `pdf.mjs`'s own `export { ... }` statement has no `PageViewport` binding,
 * only `Util`, `getXfaPageViewport`, etc.), and actually loading a real PDF through
 * `getDocument(...)` in this project's jsdom-based vitest environment hits a chain of
 * Node-vs-browser gaps in pdf.js's non-worker fallback path (`Promise.try`, a `DOMMatrix`
 * dependency at module scope, and an internal hashing helper) deep enough that "just load a
 * real PDF" stopped being a fast, reliable unit test. Instead, this builds a `ViewportLike`
 * test double whose transform matrix is copied verbatim from `PageViewport`'s own constructor
 * in the installed `pdfjs-dist@5.5.207` (`node_modules/pdfjs-dist/build/pdf.mjs`, `class
 * PageViewport`) and whose point transformation calls pdf.js's real, runtime-exported
 * `Util.applyTransform`/`applyInverseTransform` — so the only thing not "real pdf.js" here is
 * the one-time act of constructing the matrix, not the math applied to it. Re-sync
 * `makeViewport` below if a future `pdfjs-dist` upgrade changes `PageViewport`'s transform
 * formula (unlikely — it hasn't changed across major versions).
 */
function makeViewport(pageWidth: number, pageHeight: number, scale: number): ViewportLike {
  const viewBox = [0, 0, pageWidth, pageHeight];
  const centerX = (viewBox[2]! + viewBox[0]!) / 2;
  const centerY = (viewBox[3]! + viewBox[1]!) / 2;
  // rotation 0, dontFlip false — the only case this app's viewer ever asks pdf.js for (rotation
  // is normalised at ingest time, never at render time, per docs/02-data-model.md).
  const rotateA = 1;
  const rotateB = 0;
  const rotateC = 0;
  const rotateD = -1;
  // rotateA !== 0 (rotation 0 or 180) branch of PageViewport's constructor — see the module
  // comment above for exactly which source lines this mirrors.
  const offsetCanvasX = Math.abs(centerX - viewBox[0]!) * scale;
  const offsetCanvasY = Math.abs(centerY - viewBox[1]!) * scale;
  const transform = [
    rotateA * scale,
    rotateB * scale,
    rotateC * scale,
    rotateD * scale,
    offsetCanvasX - rotateA * scale * centerX - rotateC * scale * centerY,
    offsetCanvasY - rotateB * scale * centerX - rotateD * scale * centerY,
  ];
  return {
    viewBox,
    convertToViewportRectangle(rect: number[]): number[] {
      const topLeft = [rect[0]!, rect[1]!];
      Util.applyTransform(topLeft, transform);
      const bottomRight = [rect[2]!, rect[3]!];
      Util.applyTransform(bottomRight, transform);
      return [topLeft[0]!, topLeft[1]!, bottomRight[0]!, bottomRight[1]!];
    },
    convertToPdfPoint(x: number, y: number): number[] {
      const p = [x, y];
      Util.applyInverseTransform(p, transform);
      return p;
    },
  } as ViewportLike & { convertToPdfPoint(x: number, y: number): number[] };
}

// Inverse of toViewportRect, used only by this round-trip test.
function fromViewportRect(
  vrect: ViewportRect,
  viewport: ReturnType<typeof makeViewport>,
  pageHeight: number,
): Rect {
  const withInverse = viewport as unknown as { convertToPdfPoint(x: number, y: number): number[] };
  const [px0, py0] = withInverse.convertToPdfPoint(vrect.left, vrect.top) as [number, number];
  const [px1, py1] = withInverse.convertToPdfPoint(
    vrect.left + vrect.width,
    vrect.top + vrect.height,
  ) as [number, number];
  return [
    Math.min(px0, px1),
    pageHeight - Math.max(py0, py1),
    Math.max(px0, px1),
    pageHeight - Math.min(py0, py1),
  ];
}

describe("toViewportRect", () => {
  const pageWidth = 612.0;
  const pageHeight = 792.0;
  const rect: Rect = [72.0, 640.2, 511.4, 655.8];

  it.each([0.5, 1.0, 2.0])(
    "round-trips canonical -> viewport -> canonical at scale %s",
    (scale) => {
      const viewport = makeViewport(pageWidth, pageHeight, scale);
      const vrect = toViewportRect(rect, viewport);
      const back = fromViewportRect(vrect, viewport, pageHeight);
      for (let i = 0; i < 4; i++) {
        // within 0.5 px, matching the Python property test's tolerance for the equivalent
        // round-trip (services/common/tests/test_geometry.py)
        expect(back[i]).toBeCloseTo(rect[i]!, 0);
      }
    },
  );

  it("scales viewport pixel dimensions linearly with the viewport scale", () => {
    const at1x = toViewportRect(rect, makeViewport(pageWidth, pageHeight, 1.0));
    const at2x = toViewportRect(rect, makeViewport(pageWidth, pageHeight, 2.0));
    expect(at2x.width).toBeCloseTo(at1x.width * 2, 1);
    expect(at2x.height).toBeCloseTo(at1x.height * 2, 1);
  });

  it("keeps a rect near the top of the page near viewport y=0 at scale 1", () => {
    const viewport = makeViewport(pageWidth, pageHeight, 1.0);
    const topRect: Rect = [72.0, 10.0, 200.0, 30.0]; // near the canonical top edge
    const vrect = toViewportRect(topRect, viewport);
    expect(vrect.top).toBeCloseTo(10.0, 0);
  });

  it("matches a hand-computed example at scale 1", () => {
    // At scale 1, offsets 0, rotation 0: toViewportRect's own y-flip (canonical top-left ->
    // pdf.js's native bottom-left space) composes with convertToViewportRectangle's internal
    // flip (bottom-left -> screen top-left) into the identity, since both flips mirror around
    // the same page height. So a canonical rect maps to numerically the same left/top/width/
    // height at scale 1 — both conventions are top-left, y-down, just different units that
    // happen to coincide here.
    const viewport = makeViewport(pageWidth, pageHeight, 1.0);
    const vrect = toViewportRect(rect, viewport);
    expect(vrect.left).toBeCloseTo(72.0, 1);
    expect(vrect.top).toBeCloseTo(640.2, 1);
    expect(vrect.width).toBeCloseTo(511.4 - 72.0, 1);
    expect(vrect.height).toBeCloseTo(655.8 - 640.2, 1);
  });
});

describe("toImageViewportRect", () => {
  const page = { width: 1000, height: 2000 };
  const rect: Rect = [100, 200, 300, 250];

  it("is the identity when rendered at native size", () => {
    const vrect = toImageViewportRect(rect, page, { width: 1000, height: 2000 });
    expect(vrect).toEqual({ left: 100, top: 200, width: 200, height: 50 });
  });

  it("scales down uniformly when the display render is smaller than canonical size", () => {
    // A downscaled display render (docs/03-ingestion.md#2a-render's display_max_long_edge_px
    // cap) at exactly half size on both axes.
    const vrect = toImageViewportRect(rect, page, { width: 500, height: 1000 });
    expect(vrect).toEqual({ left: 50, top: 100, width: 100, height: 25 });
  });

  it("does not flip y, unlike toViewportRect", () => {
    // A rect near the canonical top of the page stays near CSS y=0 regardless of scale —
    // image documents share CSS's top-left, y-down convention, so there's nothing to flip.
    const topRect: Rect = [10, 5, 50, 15];
    const vrect = toImageViewportRect(topRect, page, { width: 2000, height: 4000 });
    expect(vrect.top).toBeCloseTo(10, 5); // 5 * (4000/2000)
  });
});
