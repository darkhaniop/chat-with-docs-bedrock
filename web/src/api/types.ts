/** docs/05-api-contracts.md response shapes — camelCase on the wire, matched here field for
 * field rather than redeclared loosely. */

export interface ProjectSummary {
  projectId: string;
  name: string;
  documentCount: number;
  updatedAt: string;
}

export interface Project {
  projectId: string;
  name: string;
  description: string;
  documentCount: number;
  chunkCount: number;
  createdAt: string;
  updatedAt: string;
}

export type DocumentKind = "pdf" | "image";
export type DocumentStatus =
  "PENDING" | "UPLOADED" | "PROCESSING" | "READY" | "FAILED" | "DELETING";

export interface Document {
  documentId: string;
  projectId: string;
  filename: string;
  contentType: string;
  byteSize: number;
  kind: DocumentKind;
  pageCount: number | null;
  status: DocumentStatus;
  statusDetail: string | null;
  createdAt: string;
  updatedAt: string;
}

export interface PresignedUpload {
  url: string;
  method: string;
  headers: Record<string, string>;
  expiresAt: string;
}

export interface CreateDocumentResponse {
  document: Document;
  upload: PresignedUpload;
}

export interface Page<T> {
  items: T[];
  nextCursor: string | null;
}

export interface ApiErrorBody {
  error: { code: string; message: string; details?: Record<string, unknown> };
}
