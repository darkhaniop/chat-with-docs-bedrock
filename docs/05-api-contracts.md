# 05 — API contracts

Two interfaces: a REST-ish HTTP API for control-plane operations, and an AppSync Events
channel set for server-pushed updates. Bulk bytes never travel through either — they move via
presigned S3 URLs.

## Conventions

- Base path: `https://{apiId}.execute-api.{region}.amazonaws.com` (a custom domain is not set
  up in `dev`).
- Auth: `Authorization: Bearer <Cognito ID token>` on every route. API Gateway's JWT
  authorizer validates issuer, audience, and expiry; the Lambda re-reads `sub` from the
  validated claims and never trusts a body-supplied identity.
- Ids are ULIDs (lexicographically sortable, timestamp-prefixed).
- Timestamps are RFC 3339 UTC strings.
- All request and response bodies are `application/json`.
- Pagination is cursor-based: `?limit=&cursor=`, response `{items: [...], nextCursor: null}`.
  The cursor is an opaque base64 of the DynamoDB `LastEvaluatedKey`.

### Error shape

```jsonc
{
  "error": {
    "code": "DOCUMENT_TOO_LARGE",
    "message": "The document is 1420 pages; the limit is 1000.",
    "details": { "pageCount": 1420, "limit": 1000 }
  }
}
```

| Status | When |
| --- | --- |
| 400 | Malformed body, unsupported content type, limit exceeded |
| 401 | Missing/invalid/expired token (emitted by the authorizer) |
| 403 | Valid token, but the resource belongs to another user |
| 404 | Resource does not exist **or** belongs to another user and we chose not to disclose |
| 409 | Conflict — an answer is already in flight for this conversation; document not `READY` |
| 413 | Upload declared over the byte limit |
| 429 | Per-user rate limit |
| 500 | Unexpected; body carries a `requestId` for log correlation |

403 vs 404: we return **404** for a resource owned by someone else. There is no legitimate
reason for a user to distinguish "does not exist" from "not yours" in this product.

## HTTP API

### Projects

```
POST   /projects                          {name, description?} → 201 Project
GET    /projects                          → {items: [ProjectSummary], nextCursor}
GET    /projects/{projectId}              → Project
PATCH  /projects/{projectId}              {name?, description?} → Project
DELETE /projects/{projectId}              → 202 {status: "DELETING"}
```

`DELETE` is asynchronous: it flips status, enqueues a cleanup job (vectors, S3 prefixes,
DynamoDB items), and returns immediately. The project disappears from `GET /projects` at once.

```jsonc
// Project
{
  "projectId": "01JQ…", "name": "Ashford due diligence", "description": "",
  "documentCount": 12, "chunkCount": 4180,
  "createdAt": "0000-12-31T23:59:59Z", "updatedAt": "0000-12-31T23:59:59Z"
}
```

### Documents

```
POST   /projects/{projectId}/documents            → 201 {document, upload}
POST   /projects/{projectId}/documents/{documentId}/ingest   → 202 {executionArn}
GET    /projects/{projectId}/documents            → {items: [Document], nextCursor}
GET    /projects/{projectId}/documents/{documentId}          → Document
DELETE /projects/{projectId}/documents/{documentId}          → 202 {status: "DELETING"}
GET    /projects/{projectId}/documents/{documentId}/source-url → {url, expiresAt}
GET    /projects/{projectId}/documents/{documentId}/pages/{page}/render-url → {url, expiresAt}
```

**Create** takes the client's declared metadata and returns a presigned `PUT`:

```jsonc
// request
{ "filename": "report.pdf", "contentType": "application/pdf", "byteSize": 8123456 }

// response 201
{
  "document": { "documentId": "01JQ…", "status": "PENDING", … },
  "upload": {
    "url": "https://cwd-documents-dev-….s3.amazonaws.com/raw/…?X-Amz-…",
    "method": "PUT",
    "headers": { "Content-Type": "application/pdf" },
    "expiresAt": "0000-12-31T23:59:59Z"
  }
}
```

The presigned URL is generated with a `Content-Type` condition and a `Content-Length` range,
so a client cannot upload something other than what it declared. Accepted content types:
`application/pdf`, `image/png`, `image/jpeg`, `image/webp`. Limits: 200 MB, 1000 pages
(page count is only knowable after upload, so it is enforced at `Probe`).

**Ingest** is idempotent and also serves as retry/re-index. It returns `409` if an execution
is already running for that document.

**Source and render URLs** are presigned `GET`s with a 15-minute expiry, used by the pdf.js
viewer. The render URL exists so the viewer can fall back to a page image when a PDF fails to
render client-side, and it is the only way to display an ingested image document at a
sensible size.

```jsonc
// Document
{
  "documentId": "01JQ…", "projectId": "01JQ…",
  "filename": "report.pdf", "contentType": "application/pdf",
  "kind": "pdf", "byteSize": 8123456, "pageCount": 42,
  "status": "READY", "statusDetail": null,
  "ingestion": { "ocrPages": 7, "chunkCount": 310,
                 "startedAt": "…", "finishedAt": "…" },
  "createdAt": "…", "updatedAt": "…"
}
```

### Conversations and messages

```
POST   /projects/{projectId}/conversations       {title?, pinnedDocumentIds?} → 201 Conversation
GET    /projects/{projectId}/conversations       → {items, nextCursor}
GET    /conversations/{conversationId}           → Conversation
PATCH  /conversations/{conversationId}           {title?, pinnedDocumentIds?} → Conversation
DELETE /conversations/{conversationId}           → 204
GET    /conversations/{conversationId}/messages  ?limit=&cursor= → {items: [Message], nextCursor}
POST   /conversations/{conversationId}/messages  {text} → 202 {userMessage, assistantMessageId, channel}
POST   /conversations/{conversationId}/messages/{messageId}/cancel → 202
```

`POST .../messages` is the only interesting one:

1. Validates the text (1–8000 characters).
2. Claims the conversation lock conditionally. If already held and unexpired → **409
   `ANSWER_IN_FLIGHT`**.
3. Writes the user message and a placeholder assistant message (`status: "STREAMING"`).
4. Enqueues to SQS.
5. Returns `202` with the ids and the AppSync channel to subscribe to.

```jsonc
// response 202
{
  "userMessage": { "messageId": "01JQ…", "role": "user", "text": "…", "createdAt": "…" },
  "assistantMessageId": "01JQ…",
  "channel": "/conversations/01JQ…"
}
```

The client should subscribe to the channel **before** posting where possible; if it
subscribes after, it reconciles by `GET /conversations/{c}/messages` — see
[06-frontend.md](06-frontend.md#reconnection-and-reconciliation).

`/cancel` sets a cancellation flag on the message item. The worker checks it between stream
chunks and stops. It is best-effort: an already-completed turn returns `202` and does nothing.

```jsonc
// Message (assistant, complete)
{
  "messageId": "01JQ…", "role": "assistant", "status": "COMPLETE",
  "text": "Uptime was 94% in Q3, up from 88% in Q2.",
  "rewrittenQuery": "What was Q3 uptime at the Ashford facility?",
  "retrieved": [ {"chunkId":"…","documentId":"…","pageNumber":7,"score":0.71,"kind":"text"} ],
  "citations": [
    { "citationId": "c0", "documentId": "01JQ…", "pageNumber": 7,
      "chunkId": "01JQ…", "startSentence": 0, "endSentence": 1,
      "citedText": "The facility achieved 94% uptime in Q3.",
      "rects": [[72.0, 640.2, 511.4, 655.8]],
      "spanStart": 0, "spanEnd": 26 }
  ],
  "usage": { "inputTokens": 12840, "outputTokens": 210 },
  "createdAt": "…"
}
```

### Health

```
GET /health → {status: "ok", version, commit}
```

Unauthenticated. Used by the deploy smoke check.

## AppSync Events

An Events API with two channel namespaces, both authorized by the Cognito user pool. Clients
subscribe over WebSocket with their ID token; backend Lambdas publish over HTTP with IAM
SigV4.

| Channel | Subscriber | Publisher |
| --- | --- | --- |
| `/projects/{projectId}` | the project owner | ingestion Step Functions tasks |
| `/conversations/{conversationId}` | the conversation owner | the answering Lambda |

Channel authorization is enforced by an `onSubscribe` handler on the namespace that resolves
the id to its owner and compares against the token's `sub`; see
[07-security.md](07-security.md#channel-authorization).

Every event carries an envelope:

```jsonc
{ "type": "message.delta", "at": "0000-12-31T23:59:59.990Z", "seq": 17, "data": { … } }
```

`seq` is monotonic per channel per logical operation (per document ingest, per assistant
message). The client uses it to detect gaps and trigger reconciliation.

### Project channel events

| `type` | `data` |
| --- | --- |
| `document.status` | `{documentId, status, statusDetail?}` |
| `document.progress` | `{documentId, stage, pagesDone, pagesTotal}` — `stage` ∈ `probe`, `pages`, `chunk`, `embed` |
| `document.ready` | `{documentId, pageCount, chunkCount, ocrPages}` |
| `document.failed` | `{documentId, statusDetail}` |
| `project.deleted` | `{projectId}` |

### Conversation channel events

| `type` | `data` |
| --- | --- |
| `message.started` | `{messageId}` |
| `message.rewritten` | `{messageId, rewrittenQuery}` |
| `message.retrieval` | `{messageId, sources: [{documentId, filename, pageNumber, score}]}` |
| `message.thinking` | `{messageId}` — emitted once when a thinking block opens |
| `message.delta` | `{messageId, text}` — appended in order; batched ~80 ms |
| `message.citation` | `{messageId, citation}` — the mapped citation object |
| `message.completed` | `{messageId, usage, latencyMs}` |
| `message.blocked` | `{messageId, reason}` |
| `message.failed` | `{messageId, code, message}` |

The client must treat `message.delta` as append-only and idempotent by `seq`; it must not
assume it received every delta. `message.completed` is the signal to `GET` the message and
replace the streamed reconstruction with the authoritative record.

## Rate limits

| Scope | Limit |
| --- | --- |
| HTTP API, per user | 20 req/s burst 40 (API Gateway usage plan keyed on `sub` via the authorizer context) |
| `POST .../messages`, per user | 10/minute, enforced in the Lambda with a DynamoDB counter |
| Document creation, per user | 100/hour |
| Concurrent ingests, per project | 5 (Step Functions execution count check before start) |

These exist to bound cost, not to defend against attack; this is an authenticated internal
tool.

## Versioning

There is no `/v1` prefix and no versioning story. This is an internal tool with exactly one
client, deployed from the same repository. Breaking the API and the SPA together in one deploy
is the intended workflow.
