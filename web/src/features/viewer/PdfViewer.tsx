import { useEffect, useMemo, useRef, useState } from "react";
import { getDocument, type PDFDocumentProxy, type PDFPageProxy } from "pdfjs-dist";
import "../../lib/pdfWorker";
import { toViewportRect } from "../../lib/geometry";
import { pagesToRender } from "../../lib/viewerVirtualization";
import { HighlightOverlay } from "./HighlightOverlay";
import type { ViewerSelection } from "./types";

const RENDER_SCALE = 1.5;

export function PdfViewer({
  sourceUrl,
  selection,
}: {
  sourceUrl: string;
  selection: ViewerSelection | null;
}) {
  const [doc, setDoc] = useState<PDFDocumentProxy | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pages, setPages] = useState<PDFPageProxy[]>([]);
  const [currentPage, setCurrentPage] = useState(1);
  const containerRef = useRef<HTMLDivElement>(null);
  const pageRefs = useRef(new Map<number, HTMLDivElement>());

  // docs/06: "fetched with the presigned source-url, with withCredentials: false" — no cookies
  // or Authorization header belong on a presigned S3 URL; its signature is the credential.
  // State only changes from the async callbacks below, never synchronously in the effect body
  // (react-hooks/set-state-in-effect) — callers should `key` this component by document id so
  // switching documents remounts it outright rather than relying on this effect to reset state.
  useEffect(() => {
    let cancelled = false;
    // `isImageDecoderSupported: false` is explicit, not a default left alone: pdf.js's own
    // default (`!isNodeJS && (isFirefox || !globalThis.chrome)`) turns this *on* for any
    // non-Chromium browser, including WebKit, routing JPEG-filtered (`DCTDecode`) images through
    // the browser's native `ImageDecoder` (WebCodecs) API instead of pdf.js's own pure-JS
    // decoder. Chromium was never affected (its branch of pdf.js's default expression is always
    // `false`, since `globalThis.chrome` exists there) — this is a WebKit-only path, but any real
    // Safari user opening a scanned/OCR'd document would hit the same crash without this.
    const loadingTask = getDocument({
      url: sourceUrl,
      withCredentials: false,
      isImageDecoderSupported: false,
    });
    loadingTask.promise
      .then(async (loaded) => {
        if (cancelled) return;
        const pageNumbers = Array.from({ length: loaded.numPages }, (_, i) => i + 1);
        // Page *objects* (not renders) are fetched for every page up front, not just the
        // virtualised window — this is what lets the container reserve the correct scroll
        // height for every page without rendering any of them, matching real pdf.js viewers'
        // own approach. `getPage` parses that page's dictionary, not its content stream.
        const loadedPages = await Promise.all(pageNumbers.map((n) => loaded.getPage(n)));
        if (cancelled) return;
        setDoc(loaded);
        setPages(loadedPages);
      })
      .catch((caught: unknown) => {
        if (!cancelled) {
          setError(caught instanceof Error ? caught.message : "Failed to load the document.");
        }
      });
    return () => {
      cancelled = true;
      void loadingTask.destroy();
    };
  }, [sourceUrl]);

  const viewports = useMemo(
    () =>
      new Map(pages.map((page) => [page.pageNumber, page.getViewport({ scale: RENDER_SCALE })])),
    [pages],
  );

  const renderSet = useMemo(
    () => new Set(pagesToRender(currentPage, pages.length)),
    [currentPage, pages.length],
  );

  // docs/06: "Only the visible page ± 1 is rendered" — the intersection observer tracks which
  // single page is most in view; `renderSet` above derives the +/-1 window from it.
  useEffect(() => {
    const root = containerRef.current;
    if (root === null || pages.length === 0) return;
    const observer = new IntersectionObserver(
      (entries) => {
        let best: { page: number; ratio: number } | null = null;
        for (const entry of entries) {
          const pageAttr = (entry.target as HTMLElement).dataset.pageNumber;
          if (pageAttr === undefined) continue;
          const ratio = entry.intersectionRatio;
          if (best === null || ratio > best.ratio) {
            best = { page: Number(pageAttr), ratio };
          }
        }
        if (best !== null && best.ratio > 0) {
          setCurrentPage(best.page);
        }
      },
      { root, threshold: [0, 0.25, 0.5, 0.75, 1] },
    );
    for (const el of pageRefs.current.values()) observer.observe(el);
    return () => observer.disconnect();
  }, [pages.length]);

  // Citation click -> scroll to page (docs/06-frontend.md#rendering-citations, step 2).
  useEffect(() => {
    if (selection === null) return;
    const target = pageRefs.current.get(selection.pageNumber);
    target?.scrollIntoView({ block: "start", behavior: "smooth" });
  }, [selection]);

  if (error !== null) {
    return <p className="p-4 text-sm text-red-600">{error}</p>;
  }

  if (doc === null) {
    return <p className="p-4 text-sm text-slate-500">Loading document…</p>;
  }

  return (
    <div ref={containerRef} className="flex-1 overflow-y-auto">
      <div className="flex flex-col items-center gap-4 p-4">
        {pages.map((page) => {
          const viewport = viewports.get(page.pageNumber);
          if (viewport === undefined) return null;
          const highlightRects =
            selection !== null && selection.pageNumber === page.pageNumber
              ? selection.rects.map((rect) => toViewportRect(rect, viewport))
              : [];
          return (
            <div
              key={page.pageNumber}
              ref={(el) => {
                if (el !== null) pageRefs.current.set(page.pageNumber, el);
                else pageRefs.current.delete(page.pageNumber);
              }}
              data-page-number={page.pageNumber}
              className="relative bg-white shadow"
              style={{ width: viewport.width, height: viewport.height }}
            >
              {renderSet.has(page.pageNumber) ? (
                <PageCanvas page={page} viewport={viewport} />
              ) : null}
              {highlightRects.length > 0 && (
                <HighlightOverlay rects={highlightRects} pulseKey={selection!.nonce} />
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function PageCanvas({
  page,
  viewport,
}: {
  page: PDFPageProxy;
  viewport: ReturnType<PDFPageProxy["getViewport"]>;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (canvas === null) return;
    const renderTask = page.render({ canvas, viewport });
    renderTask.promise.catch(() => {
      // RenderingCancelledException on unmount/re-render is expected, not an error to surface.
    });
    return () => {
      renderTask.cancel();
    };
  }, [page, viewport]);

  return <canvas ref={canvasRef} width={viewport.width} height={viewport.height} />;
}
