import type { Rect } from "../api/types";

/**
 * docs/02-data-model.md#coordinate-systems / docs/06-frontend.md#pdf-viewer-and-highlighting:
 * the one place canonical PDF-user-space points (origin top-left, y increasing downward) get
 * converted to viewport CSS pixels. pdf.js viewports use a bottom-left origin, so y is flipped
 * using the page height before handing off to `convertToViewportRectangle` — nothing else in
 * this codebase multiplies by `viewport.scale`.
 */
export interface ViewportRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

/**
 * The only two `pdfjs-dist` `PageViewport` members this conversion needs. Deliberately narrower
 * than importing the `PageViewport` class type: `PageViewport` itself isn't part of pdfjs-dist's
 * runtime export surface (only obtainable via a real `page.getViewport(...)` call, never
 * constructed directly — see `geometry.test.ts`'s comment), and a real instance satisfies this
 * interface structurally, so callers pass it straight through with no cast.
 */
export interface ViewportLike {
  viewBox: number[];
  convertToViewportRectangle(rect: number[]): number[];
}

export function toViewportRect(rect: Rect, viewport: ViewportLike): ViewportRect {
  const [x0, y0, x1, y1] = rect;
  // `viewBox`/`convertToViewportRectangle` are plain `number[]` (pdf.js's own typings, not
  // tuples) but always length 4 in practice — a page's bounding box and a converted rectangle
  // both have exactly two corners' worth of coordinates.
  const h = viewport.viewBox[3]! - viewport.viewBox[1]!;
  const [a, b, c, d] = viewport.convertToViewportRectangle([x0, h - y1, x1, h - y0]) as [
    number,
    number,
    number,
    number,
  ];
  return {
    left: Math.min(a, c),
    top: Math.min(b, d),
    width: Math.abs(c - a),
    height: Math.abs(d - b),
  };
}

/**
 * docs/06-frontend.md#pdf-viewer-and-highlighting: "Image documents bypass pdf.js entirely: the
 * viewer renders the render-url image with the same overlay component, using the Page item's
 * width/height as the coordinate space." An image document's canonical space is already
 * top-left, y-down (docs/02-data-model.md#page: "native pixel dimensions ... 1 px == 1 pt") —
 * the same convention CSS pixels use — so, unlike `toViewportRect`, this is a plain per-axis
 * scale with no y-flip and no pdf.js viewport involved. `rendered` is the `<img>` element's own
 * displayed (post-CSS-layout) size, not its natural pixel size — the two can differ both because
 * the display render is capped at `display_max_long_edge_px` (services/ingestion/ingestion/
 * render.py) and because the browser may lay the element out at any CSS size.
 */
export function toImageViewportRect(
  rect: Rect,
  page: { width: number; height: number },
  rendered: { width: number; height: number },
): ViewportRect {
  const scaleX = rendered.width / page.width;
  const scaleY = rendered.height / page.height;
  const [x0, y0, x1, y1] = rect;
  return {
    left: x0 * scaleX,
    top: y0 * scaleY,
    width: (x1 - x0) * scaleX,
    height: (y1 - y0) * scaleY,
  };
}
