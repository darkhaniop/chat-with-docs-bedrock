# 09 — Operations

## Environments

One environment: **`dev`**. It is deployed manually and holds no production data.

The CDK app is parameterised from day one:

```bash
npx cdk deploy --all --context env=dev
```

Every resource name carries the env suffix (`cwd-documents-dev-{account}`, `cwd-dev`,
`proj-…` inside `cwd-vectors-dev-{account}`), every stack is `Cwd{Env}{Purpose}Stack`, and
nothing reads account or region from an implicit default. Adding `prod` later is a second
context value and a second deploy — not a refactor.

## Stacks

Split by lifecycle, not by layer — things that must never be accidentally replaced live apart
from things that change constantly.

| Stack | Contents | Change frequency |
| --- | --- | --- |
| `CwdDevDataStack` | DynamoDB table, documents bucket, S3 vector bucket | rarely; `RETAIN` removal policy |
| `CwdDevAuthStack` | Cognito user pool, client, Managed Login domain | rarely; `RETAIN` |
| `CwdDevComputeStack` | Lambda images, HTTP API, Step Functions, SQS, guardrail | constantly |
| `CwdDevRealtimeStack` | AppSync Events API, namespaces, authorizers | occasionally |
| `CwdDevWebStack` | Site bucket, CloudFront, OAC, deployment | every frontend change |

Cross-stack references go through CDK's native mechanism (exports), not SSM lookups — one
`cdk deploy --all` keeps them consistent, and there is no second source of truth.

## Deployment

Run by a human, from the repository root:

```bash
# 0. Once per account/region
cd infra && npx cdk bootstrap aws://{account}/{region}

# 1. Verify the diff before every deploy — this is not optional
cd infra && npx cdk diff --all --context env=dev

# 2. Deploy
cd infra && npx cdk deploy --all --context env=dev --require-approval broadening

# 3. Build and publish the frontend with the fresh outputs
cd .. && ./scripts/build-web.sh dev      # reads CDK outputs → web/.env.dev, runs vite build
cd infra && npx cdk deploy CwdDevWebStack --context env=dev

# 4. Smoke
curl -sf "$API_BASE/health" | jq .
```

`--require-approval broadening` means CDK stops and asks whenever a change widens IAM or
security-group scope. Keep it.

Docker must be running: all Python Lambdas are container images and `cdk synth` builds them.

### Rollback

`cdk deploy` of the previous commit. There is no blue/green and no canary — the environment
is `dev` and the user population is small enough to tell.

The dangerous case is a **stateful** resource replacement. `CwdDevDataStack` and
`CwdDevAuthStack` use `RemovalPolicy.RETAIN`, and the infra snapshot tests
([08-testing.md](08-testing.md#infrastructure-tests)) exist specifically to make a logical-id
change visible in review before it reaches `cdk deploy`. If `cdk diff` shows a replacement of
the table, the bucket, or the user pool: **stop and ask a human**.

## Observability

### Logging

AWS Lambda Powertools for Python, structured JSON, one line per event.

- `service`, `env`, `functionName`, `requestId`, `correlationId`, `userSub`, `projectId`,
  `documentId`, `conversationId`, `messageId`, `stage`, `durationMs`, and error fields.
- **Never** `text`, `chunkText`, `query`, `rewrittenQuery`, or model output. The logging
  helper accepts only whitelisted keys, and a test asserts it.
- `correlationId` is the ULID of the originating request; it flows through SQS message
  attributes and Step Functions state so one answer turn or one ingest is greppable end to
  end.
- Retention: 30 days.

### Tracing

AWS X-Ray active tracing on all Lambdas and the HTTP API, with Powertools subsegments around
each external call (`bedrock.rewrite`, `bedrock.embed`, `vectors.query`, `ddb.hydrate`,
`bedrock.generate`, `guardrail.input`, `guardrail.output`). The answering trace should read as
a legible waterfall — that is the single most useful artifact when someone says "it feels
slow".

### Metrics

EMF custom metrics in namespace `Cwd/{env}`:

| Metric | Unit | Why |
| --- | --- | --- |
| `IngestDuration` | ms, by `kind` | Ingest performance regression |
| `IngestFailures` | count, by `reason` | The reason dimension is what makes this actionable |
| `OcrPages` | count | Textract cost driver |
| `AnswerLatency` | ms, by `stage` | Where the seconds go |
| `TimeToFirstToken` | ms | The number users actually feel |
| `RetrievedChunks` | count | Detects a retrieval collapse |
| `CitationsPerAnswer` | count | A sudden drop means grounding broke |
| `SuspectCitations` | count | The off-by-one canary from [04](04-retrieval-and-citations.md#8-citation-mapping) |
| `GuardrailBlocks` | count, by `source` | |
| `TokensIn` / `TokensOut` | count, by `model` | Cost attribution |

### Alarms

| Alarm | Condition | Action |
| --- | --- | --- |
| Answer DLQ depth | `> 0` for 5 min | Notify |
| Ingest failure rate | `> 20%` over 15 min | Notify |
| API 5xx rate | `> 2%` over 5 min | Notify |
| Answer p95 latency | `> 30 s` over 15 min | Notify |
| `SuspectCitations` | `> 5` in an hour | Notify — grounding is broken |
| Bedrock invocations | `> 3×` the trailing week | Notify — runaway cost |
| Lambda throttles | `> 0` | Notify |

All alarms publish to one SNS topic with an email subscription. No paging, no on-call rota —
this is an internal tool.

### Dashboard

One CloudWatch dashboard, `cwd-dev`, defined in CDK: ingest throughput and failures, answer
latency percentiles by stage, time to first token, token spend by model, error rates, DLQ
depth, and the citation-health metrics. If a question cannot be answered from this dashboard
in thirty seconds, the dashboard is wrong.

## Cost model

Rough monthly envelope for the expected internal load — **10 users, 500 documents ingested,
2000 answer turns**. Figures are order-of-magnitude planning numbers, not quotes; validate
against Cost Explorer after the first month.

| Component | Driver | Estimate |
| --- | --- | --- |
| Bedrock — Sonnet 4.6 generation | ~14k in / ~400 out tokens per turn × 2000 | dominant line item |
| Bedrock — Haiku 4.5 rewrite | ~1k in / 50 out × 2000 | negligible |
| Bedrock — Nova embeddings | ~200 vectors/doc × 500 docs + 2000 queries | small |
| Textract | OCR pages only; ~15% of pages | small |
| Lambda | x86_64, ingest-dominated | small |
| Step Functions Standard | ~5 + pageCount transitions per document | small |
| DynamoDB on-demand | chunk writes dominate | small |
| S3 storage | originals + two renders per page | small, grows monotonically |
| S3 Vectors | per-vector storage + queries | small |
| CloudFront + API Gateway | trivial volume | negligible |

Cost control levers, in the order to reach for them:

1. `effort: "low"` on generation for a measured quality cost.
2. Fewer context chunks (10 → 6) — directly proportional to the dominant line item.
3. Skip the page-image vector for pages with dense text (they rarely win fusion anyway).
4. Explicit prompt caching once the system prompt exceeds 1024 tokens.
5. Lower the display render DPI (storage only, no quality impact on answers).

Guardrails against surprises: AWS Budgets at 50/80/100% of the envelope, the Bedrock
invocation alarm above, per-user rate limits, and Lambda reserved concurrency.

## Runbooks

### An answer is stuck in `STREAMING`

1. Find the message: `pk = CONV#{c}`, `sk = MSG#{id}`.
2. Grep logs for its `correlationId`. A Lambda timeout shows as a `Task timed out` line.
3. Check the answer DLQ. If the message is there, the turn failed twice.
4. Resolution: the stuck-message sweeper marks anything `STREAMING` for > 10 minutes as
   `FAILED` and releases the conversation lock. If it has not run, invoke it manually.
5. The user retries by resending. No data is corrupted; the turn is idempotent.

### Ingestion failed

1. `GET /projects/{p}/documents/{d}` — `statusDetail` is written for humans and usually says
   exactly what happened.
2. Open the Step Functions execution (`ingestion.executionArn` on the document item); the
   failed state and its input are right there.
3. For Textract throttling: lower Distributed Map `MaxConcurrency`, or request a quota
   increase.
4. Re-run with `POST .../:ingest` — idempotent, deletes partial vectors first.

### Citations are landing on the wrong text

This is the highest-severity class of bug in the product, because it is *silently* wrong.

1. Check the `SuspectCitations` metric — a spike dates the regression.
2. Run `uv run cwd-eval --env dev` and compare against the last entry in
   [Evaluation history](10-roadmap.md#evaluation-history).
3. Bisect the layer: `test_citations.py` (mapping), `test_chunk.py` (sentence→rect),
   `test_geometry.py` (coordinate conversion), then the browser overlay.
4. The usual suspects, in order of historical likelihood: an off-by-one in
   `end_block_index` handling; a change that reorders or filters sentences between DynamoDB
   and the request (this breaks the index correspondence and must never happen); a rotated
   page normalised at render time instead of extraction time.

### Costs spiked

1. Cost Explorer grouped by service, then the `TokensIn`/`TokensOut` metrics by model.
2. If ingest-driven: someone uploaded a large corpus. Check `OcrPages` — scans are the
   expensive path.
3. If answer-driven: check `RetrievedChunks` for a fusion bug inflating context, then per-user
   turn counts.
4. Immediate lever: lower `effort` and the chunk cap; both are configuration, not code.

### A user cannot sign in

1. Cognito console → the user's status (`FORCE_CHANGE_PASSWORD` is the usual answer).
2. Check the app client's callback URLs match the CloudFront domain exactly, including the
   trailing path.
3. Browser console for an OIDC error — a clock skew over 5 minutes fails token validation.

## Backup and recovery

- **DynamoDB**: point-in-time recovery, 35 days. This is the only store whose loss is
  unrecoverable, because it holds the citation map.
- **S3 documents**: no versioning, no cross-region replication. Originals are re-uploadable
  by users; renders and artifacts are regenerable by re-ingest.
- **S3 Vectors**: no backup. Fully regenerable from DynamoDB chunks + S3 renders. Recovery is
  a re-ingest loop over every document, and there is a maintenance command for it
  (`uv run cwd-reindex --project …`).
- **Cognito**: no export. Users would be recreated. Acceptable for `dev`; a real concern to
  revisit before `prod`.

Recovery order after a total data loss: user pool → table (PITR restore) → re-index vectors
from restored chunks → re-render pages from S3 originals if needed.
