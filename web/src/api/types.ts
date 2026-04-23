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

export interface PresignedGet {
  url: string;
  expiresAt: string;
}

/** docs/05-api-contracts.md#documents: the viewer's coordinate-space endpoint
 * (`GET .../pages/{page}`) — `width`/`height` are canonical PDF-user-space points
 * (docs/02-data-model.md#coordinate-systems), origin top-left. */
export interface DocumentPage {
  pageNumber: number;
  width: number;
  height: number;
  rotation: number;
  textSource: "pdf" | "textract" | "none";
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

export interface Conversation {
  conversationId: string;
  projectId: string;
  title: string;
  pinnedDocumentIds: string[];
  messageCount: number;
  createdAt: string;
  updatedAt: string;
}

export type Rect = [number, number, number, number];

export interface Citation {
  citationId: string;
  documentId: string;
  pageNumber: number;
  chunkId: string;
  startSentence: number;
  endSentence: number;
  citedText: string;
  rects: Rect[];
  spanStart: number;
  spanEnd: number;
  suspect: boolean;
}

export interface RetrievedRef {
  chunkId: string | null;
  documentId: string;
  pageNumber: number;
  score: number | null;
  kind: "text" | "page";
}

export interface Usage {
  inputTokens: number;
  outputTokens: number;
  cacheReadInputTokens: number;
}

export type MessageRole = "user" | "assistant";
export type MessageStatus = "COMPLETE" | "STREAMING" | "FAILED" | "BLOCKED" | "CANCELLED";

export interface Message {
  messageId: string;
  role: MessageRole;
  status: MessageStatus;
  text: string;
  rewrittenQuery?: string | null;
  retrieved?: RetrievedRef[];
  citations?: Citation[];
  usage?: Usage | null;
  createdAt: string;
}

/** docs/05-api-contracts.md#conversations-and-messages: `POST .../messages`'s `202` response —
 * a placeholder to subscribe against, not a resolved message (Phase 6's documented steady
 * state; Phase 5's synchronous `{userMessage, assistantMessage}` stand-in is gone). */
export interface PostMessageAccepted {
  userMessage: Message;
  assistantMessageId: string;
  channel: string;
}
