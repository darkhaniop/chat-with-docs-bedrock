/**
 * docs/06-frontend.md#pdf-viewer-and-highlighting: "Only the visible page ± 1 is rendered;
 * pages virtualise on scroll." Pure function, no DOM/pdf.js involved — the one piece of
 * `PdfViewer`'s virtualization worth unit-testing directly, since jsdom has no real canvas 2D
 * context for `page.render(...)` itself to run against.
 */
export function pagesToRender(currentPage: number, pageCount: number): number[] {
  return [currentPage - 1, currentPage, currentPage + 1].filter(
    (page) => page >= 1 && page <= pageCount,
  );
}
