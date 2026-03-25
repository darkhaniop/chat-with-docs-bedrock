# 10 — Implementation roadmap

This file is the first thing to read at the start of a coding session.

## How phases work

- Each phase starts on a new branch: `phase-NN-short-name`.
- A phase is **complete when the full applicable test suite is green** — see
  [08-testing.md](08-testing.md#strategy) for which tiers apply.
- Items marked 🧑 require a human. Stop for human action, **DO NOT** attempting them.

---

## Human prerequisites

These must be done by a person before Phase 0 can complete. Claude Code runs as an
unprivileged user and cannot do any of them.

| # | Task | Needed by |
| --- | --- | --- |
| 🧑 1 | Install toolchain locally: **Python 3.12**, **uv**, **Node 22 + npm**, **Docker** (running), **AWS CLI v2**, **git** | Phase 0 |
| 🧑 2 | Provide an **AWS sandbox account** and credentials (a named profile, e.g. `cwd-dev`) with permission to create the resources in [09-operations.md](09-operations.md#stacks) | Phase 0 |
| 🧑 3 | **Choose the region.** It must have: Bedrock access to Claude Sonnet 4.6, Claude Haiku 4.5, and Nova Multimodal Embeddings; **S3 Vectors**; AppSync Events; Textract. | Phase 0 |
| 🧑 4 | **Enable Bedrock model access** for `anthropic.claude-sonnet-4-6`, `anthropic.claude-haiku-4-5`, and `amazon.nova-2-multimodal-embeddings-v1:0` in that region (console → Bedrock → Model access) | Phase 0 |
| 🧑 5 | Confirm whether Bedrock in that region requires an **inference-profile prefix** (e.g. `us.anthropic.claude-sonnet-4-6`) and record the exact model id strings to use | Phase 0 |
| 🧑 6 | `npx cdk bootstrap` the account/region | Phase 1 |
| 🧑 7 | Create a **seeded test user** in the Cognito user pool after Phase 1, with a permanent password, and store the credentials where the e2e suite reads them | Phase 2 |
| 🧑 8 | Set an **AWS Budget** with alerts at 50/80/100% of the agreed monthly envelope | Phase 1 |
| 🧑 9 | Review and approve every `cdk diff` before every `cdk deploy` | ongoing |
| 🧑 10 | Push branches to the remote and open/merge PRs | ongoing |

Where a phase needs a human step mid-flight, the phase's task list says so explicitly.

---

## Phase 0 — Scaffolding and toolchain

**Branch:** `phase-00-scaffolding`

**Goal:** a repository that installs, lints, tests, and — critically — a set of *live contract
smoke tests* that pin down the three AWS APIs whose exact shapes this design depends on.
Everything after this phase assumes those shapes; finding out in Phase 5 that Citations
behaves differently than expected would be expensive.

### Tasks

1. uv workspace at the root: `pyproject.toml` with `requires-python = ">=3.12"`,
   workspace members `services/common`, `services/api`, `services/ingestion`,
   `services/answering`.
2. `infra/` and `web/` npm projects with `package-lock.json` committed.
3. Tooling: ruff (lint + format), mypy (strict on `services/common`), pytest + pytest-cov,
   eslint + prettier, tsconfig strict for both TS projects.
4. `scripts/` with `cwd-check` (offline suite runner) wired as a uv script entry point.
5. `services/Dockerfile` — one arm64 base image, uv-installed deps, handler selected by `CMD`.
6. `services/common/config.py` — a single typed settings object: region, model ids,
   `EMBED_DIM`, limits, thresholds. Everything tunable in the docs lives here.
7. Adapter skeletons with the interfaces the rest of the system will code against:
   `services/common/bedrock/{messages,embeddings,guardrail}.py`,
   `services/common/vectors.py`, `services/common/ocr.py`.
8. **Live contract smoke tests** (`pytest -m contract`, requires credentials — 🧑 must run at
   least once):
   - `smoke_citations.py` — send two custom-content documents of two sentences each, ask a
     question answered by the second sentence of the first document. **Record**: the exact
     `content_block_location` payload, whether `end_block_index` is exclusive, and the
     streamed citation delta's event/type name.
   - `smoke_nova.py` — embed a string and an image. **Record**: request/response body shape,
     the output dimension, whether dimension is configurable, batch limits, and image size
     constraints. Set `EMBED_DIM` from this.
   - `smoke_s3vectors.py` — create an index, put 3 vectors with metadata, query with and
     without a filter, delete. **Record**: exact client/method/argument names and the
     metadata-filter syntax.
   - `smoke_guardrail.py` — `apply_guardrail` on a benign and a blocked input.
   - `smoke_models.py` — confirm the model id strings from prerequisite 5 actually invoke.
9. Fixture corpus in `e2e/fixtures/`: a born-digital PDF, a two-column PDF, a rotated page, a
   scanned PDF, a slide export, and a photograph. Small files, committed.

### Exit criteria

- `uv sync --frozen` and `npm ci` (both projects) succeed offline.
- `uv run cwd-check` green.
- All five contract smoke tests have been run against real AWS by a human, and encoded as
  constants/comments in the adapters.

---

## Phase 1 — Core infrastructure

**Branch:** `phase-01-core-infra`

**Goal:** the stacks exist, a `/health` route answers, and the SPA shell is served from
CloudFront behind Cognito login.

### Tasks

1. CDK app with the five stacks from [09-operations.md](09-operations.md#stacks), all
   parameterised by `-c env=`.
2. `CwdDevDataStack`: DynamoDB table (`pk`/`sk`, on-demand, PITR, TTL on `expiresAt`,
   `RETAIN`), documents bucket, S3 vector bucket.
3. `CwdDevAuthStack`: user pool (self sign-up off, 12-char passwords), app client (public,
   PKCE, callback URLs from the web stack), Managed Login domain.
4. `CwdDevComputeStack`: the `api` Lambda from the shared image, HTTP API with the JWT
   authorizer, `/health` route (unauthenticated) and one authenticated echo route.
5. `CwdDevWebStack`: private site bucket, CloudFront with OAC, SPA fallback for 403/404,
   `BucketDeployment`.
6. Vite + React 19 + TS 6.0 + Tailwind + shadcn/ui skeleton with the OIDC login flow and a
   single authenticated page that calls the echo route.
7. `scripts/build-web.sh` — read CDK outputs, write `web/.env.{env}`, run `vite build`.
8. CloudWatch dashboard and alarm stubs; log retention set to 30 days.
9. Infra assertion tests + template snapshots.

### Exit criteria

- 🧑 `cdk bootstrap` done, `cdk deploy --all -c env=dev` succeeds.
- `curl $API_BASE/health` returns `{"status":"ok"}`.
- A human can sign in through Managed Login and the SPA calls the echo route with a valid JWT.
- `uv run cwd-check` green; infra snapshots committed.

---

## Phase 2 — Projects, documents, and upload

**Branch:** `phase-02-projects-documents`

**Goal:** full control-plane CRUD and the upload path, with the SPA able to create a project
and get bytes into S3. No ingestion yet.

### Tasks

1. `services/common/repo.py` — DynamoDB access for Project, Document, Page, Conversation,
   Message; transactions for canonical + list-view pairs; cursor pagination.
2. `services/common/authz.py` — `require_project` / `require_document` /
   `require_conversation`, 404-for-forbidden, chain validation. Plus the lint test that fails
   a handler reading an id without a `require_*`.
3. `api` Lambda routing and the project + document endpoints from
   [05-api-contracts.md](05-api-contracts.md), including presigned `PUT`, `source-url`, and
   `render-url`.
4. Content-type/size validation, limit enforcement, the documented error envelope.
5. Documents-bucket CORS and lifecycle rules; the orphan-upload sweeper Lambda on a daily
   schedule.
6. SPA: project list/create/delete, document list, dropzone with client-side validation,
   presigned upload with progress, TanStack Query hooks, error surfaces.
7. Unit tests for repo, authz, routing, validation; vitest for the upload hook and API client.

### Exit criteria

- A human can create a project, upload a PDF, and see it listed as `PENDING`.
- The object is in S3 under `raw/{p}/{d}/`.
- A second user's token gets 404 on the first user's project (integration test).
- `uv run cwd-check` green; integration authz test green.

---

## Phase 3 — Ingestion pipeline

**Branch:** `phase-03-ingestion`

**Goal:** an uploaded document becomes pages, geometry-bearing lines, sentences, and chunks in
DynamoDB. No embeddings yet — this phase is about getting the *citation map* right, which is
the hardest correctness problem in the project.

### Tasks

1. Step Functions Standard state machine per [03-ingestion.md](03-ingestion.md), with the
   Distributed Map reading its item list from `probe.json`.
2. `ingest-probe`: magic-byte validation, page classification by text density, limits, Page
   items, `probe.json`.
3. `ingest-page`: dual render (display + `.embed.jpg` ≤1568 px), PyMuPDF line extraction with
   rotation normalisation, Textract `DetectDocumentText` on the embed render for OCR pages,
   geometry conversion to canonical points, per-page block JSON.
4. `ingest-chunk`: column-aware reading order, hyphenation healing, sentence segmentation,
   proportional rect clipping, chunk assembly with the documented caps, Chunk items.
5. `ingest-finalize` and `MarkFailed` with human-readable `statusDetail`.
6. Progress events on the project channel (every 10 pages) — publishing only; the SPA
   consumes them in Phase 6. Until then, verify with `aws appsync` or the console.
7. Wire `:ingest` to `StartExecution`; re-ingest deletes prior artifacts.
8. **Geometry test suite** per [08-testing.md](08-testing.md#geometry-tests), including the
   hand-checked fixtures. Do this *first*, before the extraction code, and treat a failure as
   a blocker rather than a tolerance to widen.
9. Integration test: ingest the 5-page fixture, assert chunk count and one hand-checked
   sentence rect.

### Exit criteria

- All six fixture documents reach `READY` with plausible chunk counts.
- The scanned fixture produces chunks with Textract-derived geometry.
- The rotated-page fixture's rects land inside the page box.
- `uv run cwd-check` green; `test_ingest_pipeline` and `test_textract_geometry` green.

---

## Phase 4 — Embeddings and retrieval

**Branch:** `phase-04-retrieval`

**Goal:** chunks and pages are embedded and indexed; a query returns a fused, ranked set of
chunks. Still no generation.

### Tasks

1. Finalise `services/common/vectors.py` against the Phase 0 smoke findings: create index,
   put, query, delete by key, delete index; `preview` as a non-filterable key.
2. `EnsureIndex` and `EmbedAndIndex` states; batching, bounded concurrency, throttle retry,
   deterministic keys, delete-before-write on re-ingest.
3. Nova adapter for text and image modes at the pinned dimension.
4. `services/answering/retrieve.py`: embed query → `query_vectors(topK=40)` → RRF by page →
   page selection → chunk selection → DynamoDB hydration, with the documented caps.
5. Document and project deletion paths: delete vectors by key, delete index, sweep S3 and
   DynamoDB in the documented order.
6. `uv run cwd-reindex --project` maintenance command.
7. A retrieval-only harness (`uv run cwd-eval --retrieval-only`) reporting Recall@10 against
   the eval question set.
8. `FakeVectorIndex` and `FakeNova` finalised; fusion and selection unit-tested against them.

### Exit criteria

- All fixtures indexed; vector counts match `chunkCount + pageCount`.
- Recall@10 ≥ 0.90 on the eval set, **including** at least one chart question and one scanned
  question. Record the number in [Evaluation history](#evaluation-history).
- Deleting a document removes exactly its vectors and no others (integration test).
- `uv run cwd-check` green; `test_s3_vectors_roundtrip` and `test_nova_embeddings_contract`
  green.

---

## Phase 5 — Answering with citations

**Branch:** `phase-05-answering`

**Goal:** a question produces a grounded answer with citations mapped to pages and rects.
**Synchronous** — no SQS worker, no streaming yet. Keeping this phase request/response makes
the citation mapping testable without a real-time layer in the way.

### Tasks

1. Conversation and message endpoints; the conversation lock as a conditional update.
2. `services/answering/rewrite.py` — Haiku 4.5, the documented prompt, fallback on failure.
3. `services/answering/prompt.py` — custom-content document blocks, one text block per
   sentence, `title`/`context`, page images for text-thin pages, reading-order sort, and the
   `documentIndex → chunkId` map built in the same loop.
4. `services/answering/generate.py` — Sonnet 4.6 with adaptive thinking, `effort: medium`,
   citations enabled, non-streaming for now.
5. `services/answering/citations.py` — mapping with every defensive rule from
   [04](04-retrieval-and-citations.md#8-citation-mapping), including `suspect` detection and
   its metric.
6. Guardrail on input and on the completed output.
7. Persist the assistant message with `citations`, `retrieved`, `usage`, `latencyMs`.
8. SPA: chat pane, message list, composer, citation markers rendered inline with popovers.
   Clicking does nothing yet (viewer is Phase 7).
9. `test_citations.py` golden files; `test_answer_turn` integration test.

### Exit criteria

- A question against the fixture corpus returns an answer with ≥ 1 citation whose
  `documentId`/`pageNumber`/sentence range are correct by hand inspection.
- The "no answer in the documents" case says so and cites nothing.
- Suspect-citation rate is 0 on the eval set.
- `uv run cwd-check` green; `test_answer_turn` and `test_bedrock_citations_contract` green.
- Record the first full eval run in [Evaluation history](#evaluation-history).

---

## Phase 6 — Real-time streaming

**Branch:** `phase-06-realtime`

**Goal:** answers stream token by token, and ingestion progress appears live.

### Tasks

1. `CwdDevRealtimeStack`: AppSync Events API, `/projects` and `/conversations` namespaces,
   Cognito auth for subscribe, IAM for publish, `onSubscribe` ownership handler.
2. SQS answer queue + DLQ (2 receives), reserved concurrency 10 on the worker.
3. Move the Phase 5 answering logic into the `answering` Lambda triggered by SQS; `POST
   .../messages` becomes enqueue-and-202.
4. Switch generation to streaming; the delta/citation republisher with ~80 ms batching and
   monotonic `seq`; final reconciliation from `get_final_message()`.
5. `:cancel` and the cancellation check between chunks.
6. Stuck-message sweeper (`STREAMING` > 10 minutes → `FAILED`, lock released) on a schedule.
7. SPA: channel client with reconnect/backoff, the streaming reducer, gap detection,
   reconciliation on `message.completed`, live ingest progress on the document list.
8. `test_channel_authz`, `test_conversation_lock`, and the `resilience.spec.ts` e2e.

### Exit criteria

- Text appears progressively; citations render as they arrive.
- Killing the WebSocket mid-answer still produces a correct final message (e2e proves it).
- A second concurrent message in one conversation returns 409 and the UI shows it.
- A non-owner cannot subscribe; no user token can publish (integration test).
- `uv run cwd-check` green; integration + `resilience.spec.ts` green.

---

## Phase 7 — Viewer and highlighting

**Branch:** `phase-07-viewer`

**Goal:** clicking a citation lands on the right page with the right span highlighted.

### Tasks

1. pdf.js integration with a self-hosted, version-pinned worker; page virtualisation.
2. `lib/geometry.ts` `toViewportRect` — the single conversion, mirrored by the Python property
   tests.
3. Highlight overlay component (absolutely positioned rects, `mix-blend-mode: multiply`,
   one-shot pulse honouring `prefers-reduced-motion`).
4. Citation click → select document → scroll to page → draw and pulse.
5. Image-document viewer using `render-url` and the same overlay.
6. Resizable three-pane layout; viewer collapses when nothing is selected.
7. Keyboard navigation, `aria` wiring, dark mode.
8. `chat-citations.spec.ts` and `scanned-doc.spec.ts` e2e on chromium and webkit.

### Exit criteria

- Clicking a citation in the fixture corpus visibly highlights the correct text on the correct
  page, verified by a human on all six fixtures.
- Citation page accuracy ≥ 0.95 and sentence-span accuracy ≥ 0.90 on the eval set.
- Both Playwright projects green.

---

## Phase 8 — Hardening, guardrails, and tuning

**Branch:** `phase-08-hardening`

**Goal:** the thing is safe, observable, affordable, and tuned.

### Tasks

1. Bedrock Guardrail defined in CDK with the settings from
   [07-security.md](07-security.md#bedrock-guardrails); fail-open behaviour with a metric.
2. Full metric set, dashboard, and alarms from [09-operations.md](09-operations.md#alarms).
3. Rate limits: per-user message and document caps; API Gateway usage plan.
4. Reserved concurrency everywhere; DLQ handler marking messages `FAILED`.
5. The logging whitelist helper and its no-content test.
6. **Tuning sweep** on the eval set: `effort` ∈ {low, medium, high}; chunk cap ∈ {6, 8, 10};
   topK ∈ {20, 40, 60}; RRF `k` ∈ {30, 60}; text-density threshold ∈ {0.10, 0.15, 0.25}.
   Record results and pick defaults with the numbers written down.
7. Measure explicit prompt caching now that the system prompt is final; adopt only if it
   clears the 1024-token minimum and shows a measurable win.
8. Cost review against actual Cost Explorer figures; update the cost model in
   [09-operations.md](09-operations.md#cost-model) with real numbers.

### Exit criteria

- All alarms exist and at least one has been deliberately triggered and observed.
- Guardrail blocks a known-bad input and the UI shows it correctly.
- Every eval target in [08-testing.md](08-testing.md#citation-fidelity-evaluation) met, with
  the tuning sweep recorded.
- `uv run cwd-check` + full integration + full e2e green.

---

## Deferred

Deliberately out of the phased plan. Revisit after the core functionality is implemented.

| Item | Why deferred |
| --- | --- |
| Document deduplication by sha256 | Optimisation, not correctness |
| Reranking model between retrieval and generation | Measure first; RRF may be sufficient |
| Hybrid keyword + vector retrieval | Same — measure the failure cases before adding a second index |
| Prompt caching | Phase 8 measures whether it is worth it at all |
| Empty states, error recovery affordances | Polish, not core functionality |
| Generate conversation titles, switch conversations | Extra features |
| Document pinning in a conversation (`pinnedDocumentIds`) | Extra features |
| Accessibility pass: contrast, `aria-live` behaviour, keyboard-only walkthrough | Out of scope for an internal tool |
| Eval runs and cost figures | Extra features and improvements |
