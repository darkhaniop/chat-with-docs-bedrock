# 04 — Retrieval and citations

This is the core of the product. Everything else exists to make this step correct.

## The answer turn, end to end

```
user message
  → 1. rewrite      Haiku 4.5, history → standalone query
  → 2. embed        Nova Multimodal, text mode
  → 3. search       S3 Vectors, project index, topK=40
  → 4. fuse         RRF over text-hits and page-hits → page set → chunk set
  → 5. hydrate      DynamoDB BatchGetItem on chunks
  → 6. guard        Bedrock ApplyGuardrail (INPUT)
  → 7. generate     Sonnet 4.6, streaming, citations enabled
  → 8. map          content_block_location → {documentId, page, rects}
  → 9. guard        Bedrock ApplyGuardrail (OUTPUT)
  → 10. persist     assistant message with citations
```

Steps 1–5 publish `retrieval.done` before generation begins, so the UI can show the source
strip while the model is still thinking.

## 1. Query rewriting

Follow-ups like "what about the second one?" or "why?" embed terribly. A cheap Haiku 4.5 call
turns them into standalone queries.

```python
REWRITE_SYSTEM = """\
You rewrite the latest user message into a single standalone search query for a document \
retrieval system.

Rules:
- Resolve every pronoun and elliptical reference using the conversation history.
- Preserve the user's domain terms, names, numbers, and units verbatim.
- Output only the query. No preamble, no quotes, no explanation.
- If the latest message is already standalone, output it unchanged.
"""
```

- Input: the last **6** turns (3 exchanges), truncated to 4000 characters, plus the new
  message.
- `max_tokens=200`, `thinking={"type": "disabled"}`, `output_config={"effort": "low"}`.
- **Not** structured outputs — a bare string is the whole payload, and a JSON envelope would
  only add failure modes.
- On any failure (timeout, guardrail, empty output) fall back to the raw user message. A
  degraded query beats a failed turn.
- The rewritten query is stored on the assistant message as `rewrittenQuery` and shown in the
  UI behind a disclosure, because "why did it retrieve that?" is the first debugging question
  anyone asks.

The first message in a conversation skips the rewrite entirely.

## 2. Query embedding

The rewritten query is embedded with `amazon.nova-2-multimodal-embeddings-v1:0` in text mode,
at the same output dimension used for indexing. One query vector is compared against both
text-chunk vectors and page-image vectors — which is only sound because both were produced by
the same multimodal model into the same space.

## 3. Vector search

```
query_vectors(
    index      = "proj-{projectId}",
    vector     = queryEmbedding,
    topK       = 40,
    filter     = {"documentId": {"$in": pinnedDocumentIds}} if pinned else None,
    returnMetadata = True,
)
```

`topK=40` is deliberately generous because fusion and page-grouping collapse the list hard.
When the user has pinned documents in the conversation, the metadata filter scopes the search;
otherwise the whole project index is searched.

## 4. Fusion and selection

Results arrive as a mixed list of `kind: "text"` and `kind: "page"` hits. They are not
directly comparable by score — a page vector summarises a whole page and will systematically
score differently from a 400-token passage. So they are fused by rank, not by score.

**Reciprocal Rank Fusion**, computed per *page* (`documentId`, `pageNumber`):

```
score(page) = Σ over lists  1 / (k + rank_in_list(page))       k = 60
```

with two input lists: text hits ranked by score, page hits ranked by score. A page that a
text chunk hit *and* whose image hit ranks well floats to the top — exactly the behaviour we
want for a page where the prose and the figure both matter.

Then:

1. Take the top **6 pages** by fused score.
2. For each selected page, include its text chunks that appeared in the text-hit list, best
   first, up to 2 chunks per page.
3. If a selected page contributed **no** text chunk (a pure figure, a scan whose OCR was
   sparse), include its highest-ordinal text chunk if one exists; if none exists, the page
   contributes a page image (see below) and no citable text.
4. Cap the total at **10 chunks** and **~14 000 tokens** of context, whichever binds first.

### Page images in the prompt

For any selected page where the text is thin (`< 200` characters of chunk text) the page's
`.embed.jpg` render is attached to the user turn as an `image` content block, immediately
before that page's document block, with a text block naming it:

```
[image: page render]
"The image above is page 7 of report.pdf, provided for visual context. Cite the
 text document that follows it, not the image."
```

**Images are not citable.** The Citations API produces locations only for `document` blocks.
This is why OCR is not optional: for a scanned page, the Textract text *is* the citable
surface and the image is corroboration. A page with an image and no extractable text can
inform the answer but cannot be cited, and the system prompt tells the model to say so rather
than invent a citation.

### Ordering

Context documents are ordered by `(documentId, pageNumber, ordinal)` — reading order, not
relevance order. Relevance ordering encourages the model to lean on position; reading order
makes multi-page synthesis read naturally and makes the "sources" strip in the UI sensible.

## 5. Prompt assembly

### Context document shape

Each chunk becomes one `document` content block whose source is **custom content**, with one
text block per sentence:

```python
{
    "type": "document",
    "source": {
        "type": "content",
        "content": [
            {"type": "text", "text": "The facility achieved 94% uptime in Q3."},
            {"type": "text", "text": "This was attributable to the new cooling loop."},
        ],
    },
    "title": "report.pdf — page 7",
    "context": "documentId=01JQ… chunkId=01JQ… page=7",
    "citations": {"enabled": True},
}
```

- **`citations` must be enabled on all documents or none.** Mixed settings are a 400.
- `title` is what the model echoes back as `document_title`; making it human-readable means
  the raw citation payload is already debuggable.
- `context` is metadata the model can see but never cites. It carries the ids so that a
  mis-mapped citation is diagnosable from a transcript alone.
- The sentence list is exactly `chunk.sentences[*].text`, in order, so block index `i` in the
  request corresponds to `sentences[i]` in DynamoDB. **This correspondence is the citation
  map.** Nothing may reorder, filter, or merge sentences between DynamoDB and the request.

The Lambda keeps an in-memory `documentIndex → chunkId` list built in the same loop that
appends the document blocks, so `document_index` from the response resolves in O(1).

### Message shape

```python
messages = [
    *history_pairs,                      # prior turns, text only, no documents, no citations
    {"role": "user", "content": [
        *context_blocks,                 # images + document blocks, reading order
        {"type": "text", "text": user_question},
    ]},
]
```

Prior turns are included as plain text without their documents. Re-sending every previous
turn's context would blow the token budget and, worse, would let the model cite a document
block from turn 1 whose `document_index` no longer means what it did. **Only the current
turn's documents are ever in the request**, so `document_index` is unambiguous.

### System prompt

```
You answer questions strictly from the provided documents.

- Ground every factual claim in the documents. Cite as you write.
- If the documents do not contain the answer, say so plainly and stop. Do not answer
  from general knowledge, and do not speculate.
- Page images are provided for visual context only and cannot be cited. If a fact comes
  only from an image with no corresponding text document, say that you can see it but
  cannot cite it.
- Prefer quoting numbers, names and dates exactly as they appear.
- Be direct. No preamble, no restating the question, no "based on the documents".
- When sources disagree, say so and cite both.
```

Response shaping is done entirely here: assistant prefill returns a 400 on Sonnet 4.6, and
`output_config.format` is incompatible with citations.

### Request parameters

```python
client.messages.create(
    model="anthropic.claude-sonnet-4-6",
    max_tokens=8000,
    system=SYSTEM_PROMPT,
    messages=messages,
    thinking={"type": "adaptive"},
    output_config={"effort": "medium"},
    stream=True,
)
```

- **Adaptive thinking**, never `budget_tokens` (deprecated on 4.6). Thinking materially helps
  multi-document synthesis and citation discipline.
- `effort: "medium"` — Sonnet 4.6 defaults to `high`, which is more deliberation than a
  grounded-QA turn needs. This is a tuned value; the evaluation harness sweeps
  `low/medium/high` in Phase 8.
- `display` is left at its default (`omitted`); we do not surface reasoning to users, we
  surface a `thinking` lifecycle event.
- Streaming is mandatory at this `max_tokens` — non-streaming requests risk HTTP timeouts.
- **Prompt caching is not applied initially.** Bedrock has no automatic caching, Sonnet 4.6's
  minimum cacheable prefix is 1024 tokens, our system prompt is smaller than that, and the
  document set changes every turn. Phase 8 measures whether an explicit `cache_control`
  breakpoint on the system block is worth it once the system prompt has grown.

## 6 & 9. Guardrails

Bedrock Guardrails is used via the standalone `apply_guardrail` API, not via Converse — the
Messages API path does not carry a guardrail parameter.

- **Input:** the raw user message (not the rewritten query, not the retrieved documents) with
  `source="INPUT"`. A block sets the assistant message to `status: "BLOCKED"` with the
  guardrail's message and skips generation.
- **Output:** buffered and checked in ~500-character segments as the stream progresses, with
  `source="OUTPUT"`. A block mid-stream truncates the message, marks it `BLOCKED`, and
  publishes `message.blocked`.
- Configuration is ordinary: content filters at MEDIUM across the standard categories, no
  denied topics, no PII masking, no word policy. This is an internal tool and the documents
  are the user's own.
- Retrieved document text is **not** run through the guardrail. Users' own documents
  legitimately contain material a filter would flag, and blocking on document content would
  make the tool unusable.

## 8. Citation mapping

The response's text blocks carry a `citations` array. For custom content documents each entry
is a `content_block_location`:

```jsonc
{
  "type": "content_block_location",
  "cited_text": "The facility achieved 94% uptime in Q3.",
  "document_index": 3,
  "document_title": "report.pdf — page 7",
  "start_block_index": 0,
  "end_block_index": 1
}
```

Mapping:

```python
chunk = chunks_by_request_index[c.document_index]
sentences = chunk.sentences[c.start_block_index : c.end_block_index]   # end exclusive
citation = {
    "citationId": f"c{n}",
    "documentId": chunk.documentId,
    "pageNumber": chunk.pageNumber,
    "chunkId": chunk.chunkId,
    "startSentence": c.start_block_index,
    "endSentence": c.end_block_index,
    "citedText": c.cited_text,
    "rects": [r for s in sentences for r in s.rects],
    "spanStart": running_text_offset,
    "spanEnd": running_text_offset + len(block.text),
}
```

> **`end_block_index` is treated as exclusive.** This is asserted by a live smoke test in
> Phase 0 (send a two-sentence document, ask a question answered by the first sentence, check
> the returned indices) and by a golden-file test thereafter. If it turns out to be
> inclusive, exactly one line changes.

`spanStart`/`spanEnd` are character offsets into the assembled answer text, accumulated as
text blocks are appended. They are what lets the UI render an inline marker at the right
place without re-parsing prose.

Defensive rules, because a bad mapping is worse than a missing citation:

- `document_index` out of range → drop the citation, log at WARN, increment a metric. Never
  guess.
- `end_block_index <= start_block_index` or out of range → clamp to the chunk's sentence count;
  if that yields an empty range, drop it.
- A citation whose `cited_text` shares no 8-character substring with the joined sentence text
  → keep it but flag `suspect: true` and count it. This is the canary for an off-by-one in the
  index convention.

## Streaming to the client

The worker consumes the Bedrock stream and republishes to AppSync Events. Deltas are batched
on a ~80 ms tick so a fast generation does not produce hundreds of tiny publishes.

| Bedrock stream event | Published as |
| --- | --- |
| `message_start` | `message.started` |
| `content_block_start` (thinking) | `message.thinking` (once) |
| `content_block_delta` / `text_delta` | `message.delta` `{text}` (batched) |
| `content_block_delta` / `citations_delta` | `message.citation` `{citation}` after mapping |
| `content_block_stop` | — |
| `message_delta` / `message_stop` | `message.completed` `{usage, latencyMs}` |

Citations arrive as their own delta type while the text block is streaming, so a citation can
be published before the text block it belongs to has finished. The client attaches citations
by `spanStart` once the block closes; until then it renders them in the sources strip only.

> The exact delta type name for streamed citations is confirmed by the Phase 0 smoke test and
> pinned in `services/answering/stream.py`. Consuming the stream defensively (ignore unknown
> delta types, reconcile against the final message) means an unexpected shape degrades to
> "citations appear at the end" rather than to a crash.

At the end, the worker calls `stream.get_final_message()` and reconciles: the persisted
message is built from the final message, not from the accumulated deltas. Deltas are for the
UI; the final message is the record.

## Failure behaviour

| Failure | Result |
| --- | --- |
| Rewrite fails | Fall back to the raw message; turn proceeds |
| Embedding fails | Retry twice, then fail the turn with a retryable error |
| Vector search returns nothing | Generate anyway with zero documents and a system note; the model says it has no sources. Better than an error page for a project that is still ingesting. |
| Guardrail blocks input | `status: BLOCKED`, no model call, no charge |
| Bedrock throttles | Exponential backoff inside the Lambda; SQS redrive gives one more whole-turn retry |
| Lambda times out (300 s) | Message left `STREAMING`; a sweeper marks messages stuck > 10 minutes as `FAILED` |
| SQS message fails twice | DLQ; alarm; message marked `FAILED` by the DLQ handler |

## Evaluation

A fixed evaluation set lives in `e2e/fixtures/eval/` — a small document corpus plus ~40
question/expected-page/expected-sentence triples covering prose, tables, charts, and scanned
pages. The harness (`services/answering/eval.py`, run with `uv run cwd-eval`) reports:

- Recall@10 over expected pages
- Citation page accuracy and sentence-span accuracy
- Rate of `suspect: true` citations
- Uncited-claim rate (assistant sentences with no citation, sampled and hand-labelled)
- p50/p95 latency and per-turn token cost

It is run before and after any change to chunking, fusion, prompt, or model parameters. See
[08-testing.md](08-testing.md#citation-fidelity-evaluation).
