import { useEffect, useRef, useState } from "react";
import { toImageViewportRect } from "../../lib/geometry";
import { HighlightOverlay } from "./HighlightOverlay";
import type { DocumentPage } from "../../api/types";
import type { ViewerSelection } from "./types";

/**
 * docs/06-frontend.md#pdf-viewer-and-highlighting: "Image documents bypass pdf.js entirely: the
 * viewer renders the render-url image with the same overlay component, using the Page item's
 * width/height as the coordinate space." `page` is this document's page-1 metadata (image
 * documents always have exactly one page, docs/02-data-model.md#page).
 */
export function ImageViewer({
  renderUrl,
  page,
  selection,
}: {
  renderUrl: string;
  page: DocumentPage;
  selection: ViewerSelection | null;
}) {
  const imgRef = useRef<HTMLImageElement>(null);
  const [rendered, setRendered] = useState<{ width: number; height: number } | null>(null);

  useEffect(() => {
    const el = imgRef.current;
    if (el === null) return;
    // Tracks the displayed (post-CSS-layout) size, which can change on window resize or on a
    // resizable-pane drag (docs/06's three-pane layout) — neither fires a `load` event again.
    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (entry !== undefined) {
        setRendered({ width: entry.contentRect.width, height: entry.contentRect.height });
      }
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const handleLoad = () => {
    const el = imgRef.current;
    if (el !== null) setRendered({ width: el.clientWidth, height: el.clientHeight });
  };

  const highlightRects =
    selection !== null && selection.pageNumber === page.pageNumber && rendered !== null
      ? selection.rects.map((rect) => toImageViewportRect(rect, page, rendered))
      : [];

  return (
    <div className="flex flex-1 items-center justify-center overflow-auto p-4">
      <div className="relative">
        <img
          ref={imgRef}
          src={renderUrl}
          alt=""
          onLoad={handleLoad}
          className="block max-h-full max-w-full"
        />
        {highlightRects.length > 0 && (
          <HighlightOverlay rects={highlightRects} pulseKey={selection!.nonce} />
        )}
      </div>
    </div>
  );
}
