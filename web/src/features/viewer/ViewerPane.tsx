import { useEffect } from "react";
import {
  useDocument,
  useDocumentPage,
  useDocumentRenderUrl,
  useDocumentSourceUrl,
} from "../../api/hooks/documents";
import { ImageViewer } from "./ImageViewer";
import { PdfViewer } from "./PdfViewer";
import type { ViewerSelection } from "./types";

/**
 * docs/06-frontend.md's screen structure: the third pane, picking pdf.js vs the image viewer by
 * `Document.kind`. `App.tsx`'s `Workspace` unmounts this pane entirely when nothing is selected
 * ("the viewer pane collapses when nothing is selected") rather than rendering it empty.
 */
export function ViewerPane({
  projectId,
  selection,
  onClose,
}: {
  projectId: string;
  selection: ViewerSelection;
  onClose: () => void;
}) {
  const { documentId, pageNumber } = selection;
  const { data: document } = useDocument(projectId, documentId);
  const isPdf = document?.kind === "pdf";
  const isImage = document?.kind === "image";

  const { data: sourceUrl } = useDocumentSourceUrl(projectId, isPdf ? documentId : null);
  const { data: page } = useDocumentPage(projectId, isImage ? documentId : null, pageNumber);
  const { data: renderUrl } = useDocumentRenderUrl(
    projectId,
    isImage ? documentId : null,
    pageNumber,
  );

  // docs/06-frontend.md#accessibility-and-polish: "Esc closes the viewer."
  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  return (
    <div className="flex flex-1 flex-col overflow-hidden border-l border-slate-200">
      <div className="flex items-center justify-between border-b border-slate-200 p-2">
        <span className="truncate text-sm font-medium" title={document?.filename}>
          {document?.filename ?? "Loading…"} · page {pageNumber}
        </span>
        <button
          type="button"
          aria-label="Close viewer"
          onClick={onClose}
          className="rounded px-2 text-slate-500 hover:bg-slate-100"
        >
          ×
        </button>
      </div>
      <div className="flex flex-1 overflow-hidden">
        {isPdf && sourceUrl !== undefined && (
          <PdfViewer key={documentId} sourceUrl={sourceUrl.url} selection={selection} />
        )}
        {isImage && renderUrl !== undefined && page !== undefined && (
          <ImageViewer renderUrl={renderUrl.url} page={page} selection={selection} />
        )}
        {(document === undefined ||
          (isPdf && sourceUrl === undefined) ||
          (isImage && (renderUrl === undefined || page === undefined))) && (
          <p className="p-4 text-sm text-slate-500">Loading…</p>
        )}
      </div>
    </div>
  );
}
