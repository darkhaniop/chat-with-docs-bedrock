import { Util } from "pdfjs-dist";
import { describe, expect, it } from "vitest";
import { toImageViewportRect, toViewportRect, type ViewportRect } from "./geometry";
import type { Rect } from "../api/types";

/**
 * `toViewportRect` itself only needs `{ scale }` (see geometry.ts's own comment for why: a
 * canonical rect is already fully rotation-normalised by the time it reaches the frontend, so
 * the conversion is a plain per-axis scale, nothing rotation-aware). The oracle below exists
 * only to *cross-check* that claim against pdf.js's own rotation-aware transform.
 *
 * `PageViewport` (the class real code gets from `page.getViewport(...)`) is not part of
 * `pdfjs-dist`'s runtime export surface — only its type is (confirmed against the installed
 * 5.5.207 package: `pdf.mjs`'s own `export { ... }` statement has no `PageViewport` binding,
 * only `Util`, `getXfaPageViewport`, etc.), and actually loading a real PDF through
 * `getDocument(...)` in this project's jsdom-based vitest environment hits a chain of
 * Node-vs-browser gaps in pdf.js's non-worker fallback path deep enough that "just load a real
 * PDF" stopped being a fast, reliable unit test (see the pdf.js finding in CLAUDE.md). Instead,
 * this builds the transform matrix by hand, copied verbatim from `PageViewport`'s own
 * constructor in the installed `pdfjs-dist@5.5.207` (`node_modules/pdfjs-dist/build/pdf.mjs`,
 * `class PageViewport`, the `rotateA/B/C/D` switch on `rotation % 360`), and applies it via
 * pdf.js's real, runtime-exported `Util.applyTransform` — so the only thing not "real pdf.js"
 * here is the one-time act of constructing the matrix, not the math applied to it. Re-sync if a
 * future `pdfjs-dist` upgrade changes `PageViewport`'s transform formula (unlikely — it hasn't
 * changed across major versions, and this exact formula was independently confirmed live against
 * a real browser's real `page.getViewport({scale})` for `rotated.pdf` before this file was
 * written this way).
 */
function oracleTransform(
  pageWidth: number,
  pageHeight: number,
  scale: number,
  rotation: 0 | 90 | 180 | 270,
): number[] {
  const viewBox = [0, 0, pageWidth, pageHeight];
  const centerX = (viewBox[2]! + viewBox[0]!) / 2;
  const centerY = (viewBox[3]! + viewBox[1]!) / 2;
  let rotateA: number, rotateB: number, rotateC: number, rotateD: number;
  switch (rotation) {
    case 180:
      [rotateA, rotateB, rotateC, rotateD] = [-1, 0, 0, 1];
      break;
    case 90:
      [rotateA, rotateB, rotateC, rotateD] = [0, 1, 1, 0];
      break;
    case 270:
      [rotateA, rotateB, rotateC, rotateD] = [0, -1, -1, 0];
      break;
    default:
      [rotateA, rotateB, rotateC, rotateD] = [1, 0, 0, -1];
  }
  let offsetCanvasX: number, offsetCanvasY: number;
  if (rotateA === 0) {
    offsetCanvasX = Math.abs(centerY - viewBox[1]!) * scale;
    offsetCanvasY = Math.abs(centerX - viewBox[0]!) * scale;
  } else {
    offsetCanvasX = Math.abs(centerX - viewBox[0]!) * scale;
    offsetCanvasY = Math.abs(centerY - viewBox[1]!) * scale;
  }
  return [
    rotateA * scale,
    rotateB * scale,
    rotateC * scale,
    rotateD * scale,
    offsetCanvasX - rotateA * scale * centerX - rotateC * scale * centerY,
    offsetCanvasY - rotateB * scale * centerX - rotateD * scale * centerY,
  ];
}

function oracleConvertToViewportRectangle(rect: number[], transform: number[]): number[] {
  const topLeft = [rect[0]!, rect[1]!];
  Util.applyTransform(topLeft, transform);
  const bottomRight = [rect[2]!, rect[3]!];
  Util.applyTransform(bottomRight, transform);
  return [topLeft[0]!, topLeft[1]!, bottomRight[0]!, bottomRight[1]!];
}

describe("toViewportRect", () => {
  const pageWidth = 612.0;
  const pageHeight = 792.0;
  const rect: Rect = [72.0, 640.2, 511.4, 655.8];

  it.each([0.5, 1.0, 2.0])("is a pure per-axis scale of the canonical rect (scale %s)", (scale) => {
    const vrect = toViewportRect(rect, { scale });
    expect(vrect).toEqual({
      left: 72.0 * scale,
      top: 640.2 * scale,
      width: (511.4 - 72.0) * scale,
      height: (655.8 - 640.2) * scale,
    });
  });

  it("scales viewport pixel dimensions linearly with the viewport scale", () => {
    const at1x = toViewportRect(rect, { scale: 1.0 });
    const at2x = toViewportRect(rect, { scale: 2.0 });
    expect(at2x.width).toBeCloseTo(at1x.width * 2, 5);
    expect(at2x.height).toBeCloseTo(at1x.height * 2, 5);
  });

  it("keeps a rect near the top of the page near viewport y=0 at scale 1", () => {
    const topRect: Rect = [72.0, 10.0, 200.0, 30.0]; // near the canonical top edge
    const vrect = toViewportRect(topRect, { scale: 1.0 });
    expect(vrect.top).toBeCloseTo(10.0, 5);
  });

  it("matches a hand-computed example at scale 1", () => {
    const vrect = toViewportRect(rect, { scale: 1.0 });
    expect(vrect.left).toBeCloseTo(72.0, 5);
    expect(vrect.top).toBeCloseTo(640.2, 5);
    expect(vrect.width).toBeCloseTo(511.4 - 72.0, 5);
    expect(vrect.height).toBeCloseTo(655.8 - 640.2, 5);
  });

  describe("rotation", () => {
    it.each([90, 180, 270] as const)(
      "matches pdf.js's real rotation-aware transform applied to the raw pre-normalisation rect (rotation %s)",
      (rotation) => {
        // A synthetic raw (pre-rotation-normalisation) rect in the *original*, unrotated page's
        // own top-left/y-down content-stream space.
        const rawRect: Rect = [72.0, 82.8, 223.18, 104.78];

        function rotateCorner(x: number, y: number): [number, number] {
          switch (rotation) {
            case 90:
              return [pageHeight - y, x];
            case 180:
              return [pageWidth - x, pageHeight - y];
            default:
              return [y, pageWidth - x];
          }
        }
        const [rx0, ry0] = rotateCorner(rawRect[0], rawRect[1]);
        const [rx1, ry1] = rotateCorner(rawRect[2], rawRect[3]);
        const canonicalRect: Rect = [
          Math.min(rx0, rx1),
          Math.min(ry0, ry1),
          Math.max(rx0, rx1),
          Math.max(ry0, ry1),
        ];

        const scale = 1.5;
        const rotatedPageWidth = rotation === 180 ? pageWidth : pageHeight;
        const rotatedPageHeight = rotation === 180 ? pageHeight : pageWidth;
        const transform = oracleTransform(pageWidth, pageHeight, scale, rotation);
        // The oracle's `convertToViewportRectangle` expects genuine PDF-native (bottom-left,
        // y-up) input, within the *raw*, unrotated page's own frame.
        const nativeRect = [
          rawRect[0],
          pageHeight - rawRect[3],
          rawRect[2],
          pageHeight - rawRect[1],
        ];
        const [a, b, c, d] = oracleConvertToViewportRectangle(nativeRect, transform);
        const expected: ViewportRect = {
          left: Math.min(a!, c!),
          top: Math.min(b!, d!),
          width: Math.abs(c! - a!),
          height: Math.abs(d! - b!),
        };

        const actual = toViewportRect(canonicalRect, { scale });

        expect(actual.left).toBeCloseTo(expected.left, 5);
        expect(actual.top).toBeCloseTo(expected.top, 5);
        expect(actual.width).toBeCloseTo(expected.width, 5);
        expect(actual.height).toBeCloseTo(expected.height, 5);
        expect(actual.top + actual.height).toBeLessThanOrEqual(rotatedPageHeight * scale + 0.01);
        expect(actual.left + actual.width).toBeLessThanOrEqual(rotatedPageWidth * scale + 0.01);
      },
    );

    it("matches services/ingestion/tests/test_extract.py's hand-checked rotated.pdf title rect", () => {
      const canonicalRect: Rect = [687.22, 72.0, 709.2, 223.18];
      const vrect = toViewportRect(canonicalRect, { scale: 1 });
      expect(vrect.left).toBeCloseTo(687.22, 1);
      expect(vrect.top).toBeCloseTo(72.0, 1);
      expect(vrect.width).toBeCloseTo(21.98, 1);
      expect(vrect.height).toBeCloseTo(151.18, 1);
      // And critically: inside the canonical 792×612 page box, not off the bottom of it.
      expect(vrect.top + vrect.height).toBeLessThanOrEqual(612);
      expect(vrect.left + vrect.width).toBeLessThanOrEqual(792);
    });
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
