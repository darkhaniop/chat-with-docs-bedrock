# 08 — Testing

## Strategy

Four tiers, with a deliberate split between what runs offline on every change and what needs
real AWS.

| Tier | Runner | Dependencies | When |
| --- | --- | --- | --- |
| **Unit** | pytest, vitest | none — mocks and `moto` | every change; must be fast (< 60 s total) |
| **Infra** | jest + `aws-cdk-lib/assertions` | none — synth only | every change |
| **Integration** | pytest, marked `integration` | **real `dev` stack** | before closing a phase; on demand |
| **E2E** | Playwright (chromium, webkit) | **real `dev` stack** | before closing a phase |

S3 Vectors, AppSync Events, Bedrock, and Textract are the four services this product is actually
made of, and none of them is faithfully emulated. Testing against LocalStack for all
four would validate the parts we are least likely to get wrong.

**A phase is complete when the full suite is green**, which means: unit + infra always, and
integration + e2e for phases that touch the relevant surface. See
[10-roadmap.md](10-roadmap.md).

## Unit tests — Python

`pytest`, `pytest-asyncio`, `moto` for DynamoDB/S3/SQS/Step Functions, hand-written fakes for
Bedrock, Textract, S3 Vectors, and AppSync.

```
services/
├── common/tests/
│   ├── test_authz.py            # ownership checks, 404-not-403, chain validation
│   ├── test_geometry.py         # coordinate conversions (see below)
│   └── test_repo.py             # DynamoDB item shapes, transactions, pagination
├── ingestion/tests/
│   ├── test_probe.py            # text-density classification over fixture PDFs
│   ├── test_extract.py          # PyMuPDF line extraction, rotation normalisation
│   ├── test_ocr.py              # Textract geometry conversion from fake responses
│   ├── test_chunk.py            # reading order, sentence segmentation, rect clipping
│   └── test_embed.py            # batching, retry, key derivation
└── answering/tests/
    ├── test_rewrite.py          # prompt construction, fallback on failure
    ├── test_fusion.py           # RRF, page grouping, selection caps
    ├── test_prompt.py           # document block assembly, index↔chunk map
    ├── test_citations.py        # mapping, defensive clamping, suspect detection
    └── test_stream.py           # delta batching, unknown event types, reconciliation
```

### Fakes for the four un-emulated services

Each lives in `services/common/testing/` and is deliberately dumb — a recorded-response
player, not a simulator:

- `FakeBedrockMessages` — replays captured Sonnet/Haiku responses, including a streaming mode
  that yields recorded event dicts. Captures are refreshed by an opt-in script that hits real
  Bedrock (`uv run cwd-capture`), and the captures are committed.
- `FakeNova` — returns deterministic pseudo-embeddings derived from a hash of the input, with
  the configured dimension. Cosine similarity between two fake embeddings is meaningless, so
  retrieval *ranking* is never unit-tested — only the plumbing.
- `FakeVectorIndex` — in-memory dict with real metadata filtering and exact cosine search.
  Good enough to test fusion and filtering logic.
- `FakeTextract` — replays captured `DetectDocumentText` responses for the fixture scans.

### Geometry tests

The highest-value unit tests in the repository, because an off-by-one here produces a
plausible-looking but wrong highlight, which is worse than a crash.

- Hand-checked fixtures: for three real PDF pages (single column, two column, rotated 90°) a
  human has recorded the expected rect for a specific sentence.
- Round-trip properties: canonical → Textract-normalised → canonical is identity within
  0.5 pt; canonical → viewport → canonical is identity within 0.5 px at scales 0.5, 1.0, 2.0.
- Rotation: a 90°-rotated page's extracted rects must land inside the page box.
- Rect clipping: a sentence starting mid-line produces a rect narrower than the full line, on
  the correct side, and the clipped rects of two adjacent sentences do not overlap by more
  than 2 pt.

## Unit tests — TypeScript

`vitest` + `@testing-library/react` for `web/`, `jest` for `infra/`.

Frontend coverage focuses on the parts that are logic rather than layout:

- The streaming reducer: ordering, gap detection, citation-before-text, completion replacing
  the reconstruction, blocked and failed terminal states.
- Citation run-splitting: text with zero, one, adjacent, and overlapping citation spans;
  markdown interaction; offsets at string boundaries.
- `toViewportRect`, mirroring the Python geometry property tests so both sides of the wire
  agree.
- The API client: 401 → single silent renew → retry → redirect; error envelope mapping.
- The channel client: reconnect backoff, resubscribe, seq gap emission.

Rendering snapshots are avoided — they break on every Tailwind tweak and catch nothing.

## Infrastructure tests

`aws-cdk-lib/assertions` against a synthesised template, per stack:

- Every Lambda has a distinct role; no role has a wildcard `bedrock:InvokeModel` resource.
- The documents bucket blocks public access, has encryption, and has the expected lifecycle
  rules.
- The HTTP API has a JWT authorizer on every route except `/health`.
- The AppSync Events API grants no publish permission to the Cognito auth mode.
- Reserved concurrency is set on `answering` and the ingestion functions.
- A full-template snapshot per stack, reviewed on change — this catches accidental resource
  replacement (which for a stateful resource means data loss) better than any assertion.

## Integration tests

`pytest -m integration`, run against a deployed `dev` stack with credentials from the
environment. They are skipped, not failed, when `CWD_INTEGRATION=1` is unset, so the default
`uv run pytest` stays offline and fast.

| Test | Asserts |
| --- | --- |
| `test_bedrock_citations_contract` | A two-sentence custom-content document produces a `content_block_location`; `end_block_index` semantics are as documented; streamed citation delta shape matches the parser |
| `test_nova_embeddings_contract` | Text and image inputs return vectors of the same configured dimension; a text query is more similar to a related passage than to an unrelated one |
| `test_s3_vectors_roundtrip` | Create index, put, query with and without metadata filter, delete by key, delete index |
| `test_textract_geometry` | A fixture scan's OCR line boxes, converted to canonical points, land within the page and match the recorded fixture within tolerance |
| `test_guardrail_blocks` | A known-blocked input returns a block; a benign input passes |
| `test_ingest_pipeline` | Upload a 5-page fixture PDF, run the state machine, assert `READY`, chunk count, vector count, and one hand-checked sentence's rect |
| `test_answer_turn` | Post a question with a known answer; assert the citation resolves to the expected page and sentence range |
| `test_channel_authz` | A second user's token cannot subscribe to the first user's conversation channel, and no user token can publish |
| `test_conversation_lock` | Two concurrent posts to one conversation: one 202, one 409 |

The first five are **contract tests**: their job is to fail loudly if AWS changes a shape we
depend on. They are the reason the fakes are trustworthy.

## End-to-end tests

Playwright, targets **chromium** and **webkit** only, no mobile projects, no firefox — a
deliberate trade to keep the suite under five minutes.

Authentication is handled by a global setup that signs in a seeded test user against the real
Cognito hosted UI once and reuses the storage state.

| Spec | Flow |
| --- | --- |
| `auth.spec.ts` | Sign in, land on projects, sign out, protected route redirects |
| `project-lifecycle.spec.ts` | Create project, rename, delete, gone from list |
| `upload-ingest.spec.ts` | Upload a fixture PDF, watch progress events, reach READY |
| `chat-citations.spec.ts` | Ask a question, see streamed text, click a citation, assert the viewer scrolled to the right page and a highlight overlay exists with plausible geometry |
| `scanned-doc.spec.ts` | Upload a scanned fixture, ask a question, assert a citation resolves (proves the OCR path end to end) |
| `resilience.spec.ts` | Kill the WebSocket mid-answer; assert the message still completes correctly via reconciliation |
| `errors.spec.ts` | Oversized upload rejected client-side; second concurrent message shows the in-flight state |

`resilience.spec.ts` is the one that justifies the whole reconciliation design; it is not
optional.

### The docker-compose harness

`e2e/docker-compose.yml` runs the **frontend dev server and Playwright** in containers with a
pinned browser image, pointed at the deployed `dev` API. It exists to make the e2e run
reproducible across machines, **not** to emulate AWS. No LocalStack.

```bash
cd e2e && docker compose run --rm playwright npx playwright test --project=chromium
```

## Citation fidelity evaluation

**Corpus** (`e2e/fixtures/eval/`): 6 documents chosen to cover the failure surface —
a born-digital report, a two-column paper, a scanned letter, a slide export, a chart-heavy
deck, and a standalone photograph of a whiteboard.

**Questions**: ~40 triples of `{question, expectedPages[], expectedSentences[]}`, hand-labelled
once, committed as JSON.

**Metrics** reported by `uv run cwd-eval`:

| Metric | Target |
| --- | --- |
| Recall@10 (expected page in retrieved set) | ≥ 0.90 |
| Citation page accuracy | ≥ 0.95 |
| Citation sentence-span accuracy (IoU ≥ 0.5 with expected) | ≥ 0.90 |
| Suspect-citation rate | ≤ 0.02 |
| Uncited-claim rate (sampled, hand-labelled) | ≤ 0.10 |
| p50 / p95 time to first token | ≤ 5 s / 10 s |
| Mean cost per turn | ≤ $0.06 |

## Coverage

Coverage is measured (`pytest --cov`, `vitest --coverage`) and reported, with a floor of **80%
on `services/`** enforced in the suite. `web/` has no floor — coverage numbers on UI code
mostly measure how much of the render tree a test happened to walk.

## Running everything

```bash
# fast, offline — the default loop
uv run pytest                    # Python units
cd infra && npm test             # CDK assertions
cd web && npm test               # vitest

# needs the dev stack
CWD_INTEGRATION=1 uv run pytest -m integration
cd e2e && npx playwright test

# measurement
uv run cwd-eval --env dev
```

`uv run cwd-check` runs the offline three in sequence and is what the autonomous loop uses to
decide a phase is green.
