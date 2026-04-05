# 02 — Data model

Three stores: DynamoDB for entities and the citation map, S3 for bytes, S3 Vectors for
embeddings. DynamoDB is authoritative; S3 and S3 Vectors are derivable from it plus the
original uploads.

## DynamoDB single table

Table `cwd-{env}`, on-demand, point-in-time recovery on, `deletedAt`-driven TTL attribute
`expiresAt`. Keys are `pk` (partition) and `sk` (sort), both strings.

### Access patterns

| # | Pattern | Index | Key condition |
| --- | --- | --- | --- |
| 1 | List a user's projects | main | `pk = USER#{sub}`, `sk begins_with PROJECT#` |
| 2 | Get project metadata (incl. owner, for authz) | main | `pk = PROJECT#{p}`, `sk = META` |
| 3 | List documents in a project | main | `pk = PROJECT#{p}`, `sk begins_with DOC#` |
| 4 | Get one document | main | `pk = DOC#{d}`, `sk = META` |
| 5 | List a document's pages | main | `pk = DOC#{d}`, `sk begins_with PAGE#` |
| 6 | Batch-get chunks by id (citation mapping) | main | `pk = DOC#{d}`, `sk = CHUNK#{c}` |
| 7 | List conversations in a project | main | `pk = PROJECT#{p}`, `sk begins_with CONV#` |
| 8 | Get conversation metadata + lock | main | `pk = CONV#{c}`, `sk = META` |
| 9 | Page through a conversation's messages | main | `pk = CONV#{c}`, `sk begins_with MSG#` |
| 10 | Resolve conversation → project → owner (authz) | main | pattern 8 then 2, or `ownerSub` denormalised on the item |

`ownerSub` is denormalised onto every `META` item so authorization needs exactly one
`GetItem`. No GSI is required for the access patterns above; do not add one speculatively.

### Item shapes

#### Project

```
pk = USER#{ownerSub}          sk = PROJECT#{projectId}       # list view (sparse copy)
pk = PROJECT#{projectId}      sk = META                      # canonical
{
  entity: "Project",
  projectId, ownerSub,
  name, description,
  documentCount: 3, chunkCount: 1420,
  createdAt, updatedAt
}
```

The `USER#` copy carries only `{projectId, name, documentCount, updatedAt}` and is written in
the same `TransactWriteItems` as the canonical item. Two items rather than a GSI because the
write rate is trivial and it keeps the list query a single-partition read.

#### Document

```
pk = PROJECT#{projectId}      sk = DOC#{documentId}          # list view (sparse copy)
pk = DOC#{documentId}         sk = META                      # canonical
{
  entity: "Document",
  documentId, projectId, ownerSub,
  filename, contentType, byteSize, sha256,
  kind: "pdf" | "image",
  pageCount: 42,
  status: "PENDING" | "UPLOADED" | "PROCESSING" | "READY" | "FAILED",
  statusDetail: "textract: throttled after 3 retries",
  ingestion: { executionArn, startedAt, finishedAt, ocrPages: 7, chunkCount: 310 },
  s3: { source: "raw/{p}/{d}/source.pdf" },
  createdAt, updatedAt
}
```

#### Page

```
pk = DOC#{documentId}         sk = PAGE#{pageNumber:04d}
{
  entity: "Page",
  documentId, pageNumber,
  width, height,               # PDF user-space points; the coordinate system for all bboxes
  rotation: 0,
  textSource: "pdf" | "textract" | "none",
  textDensity: 0.83,           # heuristic that drove the textSource decision
  s3: { display: "pages/{p}/{d}/0007.png", embed: "pages/{p}/{d}/0007.embed.jpg" }
}
```

#### Chunk — the citation map

This is the most important item type in the system. It is what turns a model citation into a
rectangle on screen.

```
pk = DOC#{documentId}         sk = CHUNK#{chunkId}
{
  entity: "Chunk",
  chunkId, documentId, projectId, pageNumber,
  ordinal: 12,                 # position of the chunk within the page
  text: "…full chunk text, sentences joined by a space…",
  sentences: [
    { i: 0, text: "The facility achieved 94% uptime in Q3.",
      rects: [[72.0, 640.2, 511.4, 655.8]] },
    { i: 1, text: "This was attributable to the new cooling loop.",
      rects: [[72.0, 622.0, 388.7, 637.6], [72.0, 604.0, 210.3, 619.6]] }
  ],
  tokenEstimate: 412,
  createdAt
}
```

- `rects` are `[x0, y0, x1, y1]` in **PDF user-space points, origin top-left**, matching the
  page's `width`/`height`. A sentence spanning a line break has more than one rect.
- `sentences[].i` is the index of the sentence's text block inside the Citations-API
  `document` we send. A `content_block_location` with `start_block_index: 0,
  end_block_index: 2` covers sentences 0 and 1 (the API's end index is exclusive).
- Chunk text is stored in full so the answering Lambda can build the prompt from DynamoDB
  without a second S3 read. Chunks are capped so items stay well under the 400 KB limit;
  see [03-ingestion.md](03-ingestion.md#chunking).

#### Conversation

```
pk = PROJECT#{projectId}      sk = CONV#{conversationId}     # list view (sparse copy)
pk = CONV#{conversationId}    sk = META                      # canonical
{
  entity: "Conversation",
  conversationId, projectId, ownerSub,
  title,                        # generated from the first user message
  pinnedDocumentIds: [],        # empty = search the whole project
  activeMessageId: null,        # the lock: non-null while an answer is in flight
  lockExpiresAt: 1780000000,    # epoch seconds; a stale lock is reclaimable
  messageCount, createdAt, updatedAt
}
```

#### Message

```
pk = CONV#{conversationId}    sk = MSG#{ulid}
{
  entity: "Message",
  messageId,                   # the ULID; sorts chronologically
  conversationId, projectId, ownerSub,
  role: "user" | "assistant",
  status: "COMPLETE" | "STREAMING" | "FAILED" | "BLOCKED",
  text: "…",
  # assistant only:
  rewrittenQuery: "What was Q3 uptime at the Ashford facility?",
  retrieved: [ { chunkId, documentId, pageNumber, score, kind } ],
  citations: [
    { citationId: "c0",
      documentId, pageNumber,
      chunkId, startSentence: 0, endSentence: 2,
      citedText: "The facility achieved 94% uptime in Q3.",
      rects: [[72.0, 640.2, 511.4, 655.8]],
      spanStart: 118, spanEnd: 186     # char offsets into `text`
    }
  ],
  usage: { inputTokens, outputTokens, cacheReadInputTokens },
  latencyMs: { rewrite, embed, retrieve, firstToken, total },
  createdAt
}
```

`citations[].rects` is denormalised from the chunk at answer time. That is redundant with the
chunk item, but it means rendering a historical message never depends on chunks that may have
been re-indexed or deleted since. A message is self-contained.

### Write patterns worth calling out

- **Project and document creation** use `TransactWriteItems` to keep the canonical item and
  its list-view copy consistent.
- **The conversation lock** is a conditional update: set `activeMessageId` only if it is null
  or `lockExpiresAt < now`. A second concurrent message in the same conversation gets `409`.
- **Chunk writes** use `BatchWriteItem` in batches of 25 with retry on unprocessed items.
- **Document deletion** is a two-phase job: mark `DELETING`, delete vectors, delete S3
  prefixes, delete chunk/page items via query-and-batch-delete, then delete the metadata.
  Doing it in that order means a crash leaves orphan storage (cheap, sweepable) rather than
  dangling references (breaks citations).

## S3 layout

One bucket, `cwd-documents-{env}-{account}`, versioning off, SSE-S3, all public access
blocked, CORS allowing `PUT` and `GET` from the CloudFront origin only.

```
raw/{projectId}/{documentId}/source.pdf            # or source.png / source.jpg / source.webp
pages/{projectId}/{documentId}/{page:04d}.png      # display render, ~200 DPI
pages/{projectId}/{documentId}/{page:04d}.embed.jpg # ≤1568px long edge, for Nova + Textract
artifacts/{projectId}/{documentId}/probe.json      # page classification
artifacts/{projectId}/{documentId}/blocks.jsonl    # one JSON object per page: blocks + geometry
artifacts/{projectId}/{documentId}/chunks.jsonl    # one JSON object per chunk (mirror of DDB)
uploads/{projectId}/{documentId}/                  # nothing lives here; reserved
```

Lifecycle rules:

| Prefix | Rule |
| --- | --- |
| `raw/` | none — originals are kept for the life of the document |
| `pages/` | none — needed for the viewer and for re-indexing |
| `artifacts/` | transition to Infrequent Access after 30 days |
| all | abort incomplete multipart uploads after 1 day |

Objects for a `PENDING` document that never gets a `/ingest` call are swept by a daily
scheduled Lambda rather than a lifecycle rule, because the rule cannot see DynamoDB state.

## S3 Vectors

One **vector bucket** per environment: `cwd-vectors-{env}-{account}`.
One **index per project**: `proj-{projectId}`.

| Property | Value |
| --- | --- |
| Dimension | Whatever Nova Multimodal Embeddings emits for the configured output size. Pinned as `EMBED_DIM` in `services/common/config.py`, confirmed by the Phase 0 smoke test, and asserted at index creation. |
| Distance metric | Cosine |
| Vector key | `{documentId}:{chunkId}` for text, `{documentId}:p{page:04d}` for page images |

### Metadata

```jsonc
{
  "documentId": "01JQ…",     // filterable — used when the user pins documents
  "pageNumber": 7,            // filterable
  "kind": "text",             // filterable: "text" | "page"
  "chunkId": "01JQ…",         // filterable (identity lookups)
  "ordinal": 12,
  "preview": "The facility achieved 94% uptime…"   // NON-filterable
}
```

`preview` is registered as a non-filterable metadata key at index creation so it does not
count against the filterable-metadata budget. It exists only for debugging and for rendering
the "sources" strip before chunk hydration completes.

### Lifecycle

- The index is created lazily on the first document ingest for a project, not at project
  creation — an empty project costs nothing.
- Deleting a document deletes its vectors by key (keys are deterministic, so no query is
  needed: page vectors from `pageCount`, chunk vectors from the chunk items).
- Deleting a project deletes the whole index.
- Re-ingesting a document deletes its vectors first, then writes new ones. There is no
  in-place update path.

> **Verify before Phase 4.** The exact `s3vectors` API surface (`create_index`,
> `put_vectors`, `query_vectors` argument names, metadata-filter syntax, batch limits) is not
> assumed anywhere in these docs. All of it sits behind `services/common/vectors.py`, and the
> Phase 4 first task is a live smoke test that pins the real shapes.

## Coordinate systems

Getting this wrong is the most likely source of "the highlight is 40 pixels off" bugs, so it
is stated once, here, and referenced everywhere else.

- **Canonical space:** PDF user-space points, origin **top-left**, y increasing downward.
  Page `width`/`height` on the Page item are in this space.
- **PyMuPDF** returns rects in exactly this space for an unrotated page. If `page.rotation`
  is non-zero, normalise with the page's transformation matrix at extraction time and store
  the normalised rect — never at render time.
- **Textract** returns `Geometry.BoundingBox` normalised to `[0,1]` of the *image we sent it*.
  Multiply by the embed render's pixel dimensions, then scale by
  `page.width / embedWidthPx` to get canonical points.
- **pdf.js** in the browser applies a viewport scale. The overlay converts canonical points
  to CSS pixels with `viewport.convertToViewportRectangle()`; it must never do its own
  arithmetic on the scale factor.

Every conversion has a unit test with a hand-checked fixture. See
[08-testing.md](08-testing.md#geometry-tests).
