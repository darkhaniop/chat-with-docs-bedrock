import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiJson } from "../client";
import type { CreateDocumentResponse, Document, Page } from "../types";

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

/** docs/06-frontend.md#upload-flow: create → presigned PUT → invalidate. `/ingest` is not
 * called — there is no ingestion pipeline until Phase 3, so a freshly uploaded document stays
 * `PENDING` (the documented Phase 2 exit criterion). */
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
      return created.document;
    },
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: documentsKey(projectId) });
    },
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
