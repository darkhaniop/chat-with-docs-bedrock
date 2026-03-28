import { Button } from "../../components/ui/button";
import { useDeleteDocument, useDocuments } from "../../api/hooks/documents";
import { Dropzone } from "./Dropzone";

const STATUS_LABEL: Record<string, string> = {
  PENDING: "Pending",
  UPLOADED: "Uploaded",
  PROCESSING: "Processing…",
  READY: "Ready",
  FAILED: "Failed",
  DELETING: "Deleting…",
};

export function DocumentList({ projectId }: { projectId: string }) {
  const { data, isLoading, error } = useDocuments(projectId);
  const deleteDocument = useDeleteDocument(projectId);

  return (
    <div className="flex flex-1 flex-col gap-3 p-3">
      <Dropzone projectId={projectId} />

      {isLoading && <p className="text-sm text-slate-500">Loading documents…</p>}
      {error !== null && <p className="text-sm text-red-600">Failed to load documents.</p>}
      {data !== undefined && data.items.length === 0 && (
        <p className="text-sm text-slate-500">No documents yet — upload one above.</p>
      )}

      <ul className="flex flex-col gap-1">
        {data?.items.map((document) => (
          <li
            key={document.documentId}
            className="flex items-center gap-2 rounded-md border border-slate-200 px-3 py-2 text-sm"
          >
            <span className="flex-1 truncate">{document.filename}</span>
            <span className="text-xs text-slate-500">
              {STATUS_LABEL[document.status] ?? document.status}
            </span>
            <Button
              variant="outline"
              aria-label={`Delete ${document.filename}`}
              onClick={() => deleteDocument.mutate(document.documentId)}
            >
              Delete
            </Button>
          </li>
        ))}
      </ul>
    </div>
  );
}
