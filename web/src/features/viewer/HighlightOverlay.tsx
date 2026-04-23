import type { ViewportRect } from "../../lib/geometry";

/**
 * docs/06-frontend.md#pdf-viewer-and-highlighting: "absolutely-positioned divs in an overlay
 * layer above the canvas, one per rect, with mix-blend-mode: multiply so text stays legible."
 * Shared by both `PdfViewer` and `ImageViewer` — this component only knows about CSS-pixel
 * rects already converted by `lib/geometry.ts`, never canonical points.
 *
 * `pulseKey` changes on every citation click (even re-clicking the same one) so the one-shot
 * pulse animation restarts — a `key` on each highlight `div` keyed by it, remounting the
 * element, is what actually restarts a CSS animation in React. Decorative and `aria-hidden`
 * (docs/06's accessibility section: "Highlights are decorative ... the citation popover carries
 * the text").
 */
export function HighlightOverlay({ rects, pulseKey }: { rects: ViewportRect[]; pulseKey: number }) {
  return (
    <div className="pointer-events-none absolute inset-0" aria-hidden="true">
      {rects.map((rect, index) => (
        <div
          key={`${pulseKey}-${index}`}
          className="absolute bg-yellow-400/40 motion-safe:animate-cwd-pulse"
          style={{
            left: rect.left,
            top: rect.top,
            width: rect.width,
            height: rect.height,
            mixBlendMode: "multiply",
          }}
        />
      ))}
    </div>
  );
}
