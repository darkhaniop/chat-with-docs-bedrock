import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiJson } from "../client";
import type { CreateDocumentResponse, Document, DocumentPage, Page, PresignedGet } from "../types";

export function documentsKey(projectId: string) {
  return ["projects", projectId, "documents"] as const;
}

export function useDocuments(projectId: string | null) {
  return useQuery({
    queryKey: documentsKey(projectId ?? ""),
    queryFn: () => apiJson<Page<Document>>(`/projects/${projectId}/documents`),
    enabled: projectId !== null,
  });
}

export function useDocument(projectId: string | null, documentId: string | null) {
  return useQuery({
    queryKey: ["projects", projectId, "documents", documentId, "meta"],
    queryFn: () => apiJson<Document>(`/projects/${projectId}/documents/${documentId}`),
    enabled: projectId !== null && documentId !== null,
  });
}

/**
 * docs/06-frontend.md#upload-flow: create -> presigned PUT -> `POST .../ingest` -> invalidate.
 *
 * The `/ingest` call was missing entirely until Phase 7 — a real gap, not a deliberate
 * simplification: the doc comment this replaced said "there is no ingestion pipeline until
 * Phase 3," which stopped being true as of Phase 3 itself, but nothing added the call once the
 * pipeline existed, so every document uploaded through the SPA in Phases 3-6 stayed `UPLOADED`
 * forever unless ingested some other way (the integration tests all call `/ingest` directly).
 * Found while wiring `chat-citations.spec.ts`/`scanned-doc.spec.ts` (Phase 7) — those are the
 * first e2e specs that need a document to actually reach `READY` through the real UI.
 */
export function useCreateDocument(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: async ({
      file,
      onProgress,
    }: {
      file: File;
      onProgress?: (fraction: number) => void;
    }) => {
      const created = await apiJson<CreateDocumentResponse>(`/projects/${projectId}/documents`, {
        method: "POST",
        body: JSON.stringify({
          filename: file.name,
          contentType: file.type,
          byteSize: file.size,
        }),
      });
      await uploadWithProgress(created.upload, file, onProgress);
      await apiJson(`/projects/${projectId}/documents/${created.document.documentId}/ingest`, {
        method: "POST",
      });
      return created.document;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: documentsKey(projectId) });
    },
  });
}

const PRESIGNED_URL_STALE_MS = 10 * 60 * 1000; // docs/05-api-contracts.md: 15-minute expiry

/** docs/06-frontend.md#pdf-viewer-and-highlighting: "the document is fetched with the
 * presigned source-url". */
export function useDocumentSourceUrl(projectId: string | null, documentId: string | null) {
  return useQuery({
    queryKey: ["projects", projectId, "documents", documentId, "source-url"],
    queryFn: () =>
      apiJson<PresignedGet>(`/projects/${projectId}/documents/${documentId}/source-url`),
    enabled: projectId !== null && documentId !== null,
    staleTime: PRESIGNED_URL_STALE_MS,
  });
}

/** docs/05-api-contracts.md#documents: the viewer's coordinate-space metadata for one page —
 * a PDF page also has this from pdf.js's own `page.getViewport()`, but an image document has no
 * other source for it (see the route's own docstring, `api/documents.py`'s `page()`). */
export function useDocumentPage(
  projectId: string | null,
  documentId: string | null,
  pageNumber: number | null,
) {
  return useQuery({
    queryKey: ["projects", projectId, "documents", documentId, "pages", pageNumber],
    queryFn: () =>
      apiJson<DocumentPage>(`/projects/${projectId}/documents/${documentId}/pages/${pageNumber}`),
    enabled: projectId !== null && documentId !== null && pageNumber !== null,
  });
}

/** docs/06-frontend.md#pdf-viewer-and-highlighting: "Image documents ... the viewer renders the
 * render-url image". */
export function useDocumentRenderUrl(
  projectId: string | null,
  documentId: string | null,
  pageNumber: number | null,
) {
  return useQuery({
    queryKey: ["projects", projectId, "documents", documentId, "pages", pageNumber, "render-url"],
    queryFn: () =>
      apiJson<PresignedGet>(
        `/projects/${projectId}/documents/${documentId}/pages/${pageNumber}/render-url`,
      ),
    enabled: projectId !== null && documentId !== null && pageNumber !== null,
    staleTime: PRESIGNED_URL_STALE_MS,
  });
}

export function useDeleteDocument(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (documentId: string) =>
      apiJson<{ status: string }>(`/projects/${projectId}/documents/${documentId}`, {
        method: "DELETE",
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: documentsKey(projectId) });
    },
  });
}

/** `fetch` has no upload-progress event, so the presigned PUT goes through `XMLHttpRequest`
 * (docs/06-frontend.md#upload-flow) — the one place in the codebase that isn't `fetch`. Exported
 * for its own vitest coverage — this is the fiddliest logic in the upload path. */
export function uploadWithProgress(
  upload: CreateDocumentResponse["upload"],
  file: File,
  onProgress?: (fraction: number) => void,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open(upload.method, upload.url);
    for (const [name, value] of Object.entries(upload.headers)) {
      xhr.setRequestHeader(name, value);
    }
    xhr.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable && onProgress) {
        onProgress(event.loaded / event.total);
      }
    });
    xhr.addEventListener("load", () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve();
      } else {
        reject(new Error(`upload failed: ${xhr.status}`));
      }
    });
    xhr.addEventListener("error", () => reject(new Error("upload failed: network error")));
    xhr.send(file);
  });
}
