import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "../../components/ui/button";
import { documentsKey, useDeleteDocument, useDocuments } from "../../api/hooks/documents";
import { useChannelSubscription } from "../../realtime/useChannel";
import { Dropzone } from "./Dropzone";

const STATUS_LABEL: Record<string, string> = {
  PENDING: "Pending",
  UPLOADED: "Uploaded",
  PROCESSING: "Processing…",
  READY: "Ready",
  FAILED: "Failed",
  DELETING: "Deleting…",
};

interface Progress {
  stage: string;
  pagesDone: number;
  pagesTotal: number;
}

/** docs/05-api-contracts.md#appsync-events' project-channel events, docs/10-roadmap.md's Phase
 * 6 goal ("live ingest progress on the document list"). `document.progress` only updates local
 * state for an inline indicator; every other event type invalidates the documents query so the
 * canonical status (from `GET .../documents`) is what's actually shown — this component never
 * trusts the channel for anything beyond "a refetch is worth doing now". */
export function DocumentList({ projectId }: { projectId: string }) {
  const { data, isLoading, error } = useDocuments(projectId);
  const deleteDocument = useDeleteDocument(projectId);
  const queryClient = useQueryClient();
  const [progress, setProgress] = useState<Record<string, Progress>>({});

  useChannelSubscription(`/projects/${projectId}`, (envelope) => {
    const eventData = envelope.data as Record<string, unknown>;
    const documentId = typeof eventData.documentId === "string" ? eventData.documentId : null;

    if (envelope.type === "document.progress" && documentId !== null) {
      setProgress((prev) => ({
        ...prev,
        [documentId]: {
          stage: typeof eventData.stage === "string" ? eventData.stage : "",
          pagesDone: typeof eventData.pagesDone === "number" ? eventData.pagesDone : 0,
          pagesTotal: typeof eventData.pagesTotal === "number" ? eventData.pagesTotal : 0,
        },
      }));
      return;
    }

    if (
      envelope.type === "document.status" ||
      envelope.type === "document.ready" ||
      envelope.type === "document.failed"
    ) {
      if (documentId !== null) {
        setProgress((prev) => {
          if (!(documentId in prev)) return prev;
          const rest = { ...prev };
          delete rest[documentId];
          return rest;
        });
      }
      void queryClient.invalidateQueries({ queryKey: documentsKey(projectId) });
    }
  });

  return (
    <div className="flex h-full flex-col gap-3 overflow-y-auto p-3">
      <Dropzone projectId={projectId} />

      {isLoading && <p className="text-sm text-slate-500">Loading documents…</p>}
      {error !== null && <p className="text-sm text-red-600">Failed to load documents.</p>}
      {data !== undefined && data.items.length === 0 && (
        <p className="text-sm text-slate-500">No documents yet — upload one above.</p>
      )}

      <ul className="flex flex-col gap-1">
        {data?.items.map((document) => {
          const live = progress[document.documentId];
          return (
            <li
              key={document.documentId}
              className="flex items-center gap-2 rounded-md border border-slate-200 px-3 py-2 text-sm"
            >
              <span className="flex-1 truncate">{document.filename}</span>
              <span className="text-xs text-slate-500">
                {live !== undefined && document.status === "PROCESSING"
                  ? `${live.stage} ${live.pagesDone}/${live.pagesTotal}`
                  : (STATUS_LABEL[document.status] ?? document.status)}
              </span>
              <Button
                variant="outline"
                aria-label={`Delete ${document.filename}`}
                onClick={() => deleteDocument.mutate(document.documentId)}
              >
                Delete
              </Button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
