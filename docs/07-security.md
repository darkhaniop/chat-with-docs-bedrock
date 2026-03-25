# 07 — Security

Threat model appropriate to an **internal tool**: authenticated colleagues, their own
documents, one AWS account per environment. The controls below aim at accident prevention,
tenant isolation, and blast-radius limits — not at defending against a determined attacker
with an insider account.

## Identity

- **Amazon Cognito user pool** with Managed Login (hosted UI). Email + password, MFA optional
  (TOTP), self sign-up **disabled** — users are created by an administrator or federated later.
- Password policy: 12+ characters, no forced rotation, no composition rules beyond length.
- The SPA is a **public OIDC client** using authorization code flow with PKCE. No client
  secret exists to leak.
- **No Cognito identity pool.** The browser never holds AWS credentials. This is the single
  most important structural decision in the security design: there is no path from a
  compromised browser session to arbitrary AWS API calls, only to our own HTTP API with its
  own authorization checks.

### Token handling

| Token | Lifetime | Storage |
| --- | --- | --- |
| ID / access | 1 hour | JavaScript memory only |
| Refresh | 30 days | `sessionStorage`, managed by `oidc-client-ts` |

`sessionStorage` over `localStorage` so closing the tab ends the session. A BFF with
httpOnly cookies would be stronger and is the documented upgrade path if this tool ever leaves
the internal network; it is not worth adding a server for today.

## Authorization

Every project, document, conversation and message belongs to exactly one Cognito `sub`.
Enforcement is **explicit in application code**, in one place:

```python
# services/common/authz.py
def require_project(claims: Claims, project_id: str) -> Project:
    project = repo.get_project(project_id)
    if project is None or project.owner_sub != claims.sub:
        raise NotFound()          # deliberately not Forbidden
    return project
```

Rules:

- Every handler that touches a project-scoped resource calls one of `require_project`,
  `require_document`, or `require_conversation` **before** any other work. There is a lint
  test that fails if a route handler reads a path parameter named `*Id` without a preceding
  `require_*` call.
- `ownerSub` is denormalised onto every canonical item so the check is a single `GetItem`.
- Nested resources verify the *whole* chain: a document check confirms the document's
  `projectId` matches the path's project and that the project's owner is the caller. Ids
  from the path are never trusted to be consistent with each other.
- Cross-tenant access returns **404**, not 403 — there is no reason to confirm existence.

### Channel authorization

AppSync Events channels carry the same risk as an API route: `/conversations/{id}` must not be
subscribable by a non-owner. The Events API is configured with a Cognito authorizer plus an
`onSubscribe` handler on each namespace that:

1. Parses the channel path for the resource id.
2. Reads the resource's `ownerSub` from DynamoDB.
3. Compares to the JWT `sub`; rejects otherwise.

Publishing is IAM-only: no Cognito principal has publish permission on either namespace, so a
browser can subscribe but can never inject events. This is enforced by the Events API's
auth-mode configuration, and there is an integration test that attempts a publish with a user
token and asserts failure.

## IAM

Every Lambda gets its own role. No shared "app role", no managed policies beyond
`AWSLambdaBasicExecutionRole`.

| Function | Notable permissions |
| --- | --- |
| `api` | DynamoDB CRUD on the table; `s3:PutObject`/`GetObject` on `raw/*` and `pages/*` (for presigning); `states:StartExecution` on the one state machine; `sqs:SendMessage` on the one queue; `appsync:EventPublish` on the project + conversation namespaces |
| `ingest-*` | S3 read/write on the documents bucket; DynamoDB write; `textract:DetectDocumentText`; `bedrock:InvokeModel` on the **Nova embeddings model ARN only**; `s3vectors:*` on the environment's vector bucket; `appsync:EventPublish` |
| `answering` | DynamoDB read/write; `s3vectors:QueryVectors`; `bedrock:InvokeModelWithResponseStream` + `InvokeModel` on the **Sonnet 4.6 and Haiku 4.5 ARNs only**; `bedrock:ApplyGuardrail` on the one guardrail; `appsync:EventPublish` |
| `cleanup` | DynamoDB delete; S3 delete on the documents bucket; `s3vectors:DeleteVectors`/`DeleteIndex` |

Principles applied:

- **Model ARNs are enumerated, never wildcarded.** `bedrock:InvokeModel` on `*` would let a
  compromised function call any model in the account, including expensive ones.
- The `api` function has **no** Bedrock permissions at all. It cannot be made to spend money
  on inference no matter what a request contains.
- Presigned URLs inherit the signing role's permissions, so the `api` role's S3 access is
  scoped by prefix condition to `raw/*` and `pages/*` — never `artifacts/*`, which contains
  the extraction internals.
- No role has `iam:*`, `s3:DeleteBucket`, or `dynamodb:DeleteTable`.

## Data protection

| Concern | Control |
| --- | --- |
| S3 encryption | SSE-S3 (AES-256) on all buckets. KMS CMKs add cost and key-policy complexity for no benefit in a single-account internal tool. |
| S3 access | All public access blocked; site bucket reachable only via CloudFront OAC; documents bucket reachable only via presigned URLs |
| DynamoDB | Encryption at rest (AWS-owned key), point-in-time recovery on |
| Transit | TLS everywhere; CloudFront minimum TLS 1.2; API Gateway and AppSync are HTTPS/WSS only |
| Presigned URLs | 15-minute expiry, method- and content-type-constrained, single object |
| CORS | Documents bucket allows `PUT`/`GET` from the CloudFront distribution domain only |
| Secrets | There are none. All service access is IAM. No API keys, no Secrets Manager entries. |

**Data retention:** documents live until the user deletes them. Deletion is real — S3 objects,
DynamoDB items, and vectors are all removed (see
[02-data-model.md](02-data-model.md#write-patterns-worth-calling-out)). CloudWatch log
retention is 30 days.

**What is logged:** request ids, user `sub`, resource ids, timings, token counts, error
messages. **Never logged:** document text, chunk text, message text, rewritten queries, or
model output. Debugging content problems is done via the evaluation harness against fixture
documents, not by reading production logs. This is enforced by a structured-logging helper
that takes only whitelisted fields, plus a test asserting no handler passes a `text` field to
the logger.

## Bedrock Guardrails

One guardrail, applied via the standalone `ApplyGuardrail` API (the Messages API path has no
inline guardrail parameter).

| Setting | Value |
| --- | --- |
| Content filters | Hate, Insults, Sexual, Violence, Misconduct, Prompt Attack — all at MEDIUM |
| Denied topics | none |
| Word filters | profanity filter off |
| Sensitive information | PII detection off, no masking |
| Contextual grounding | off — our grounding guarantee comes from the Citations API, not from a second scoring pass |

Applied to:

- **User input**, before any model call. A block short-circuits the turn with
  `status: BLOCKED` and costs nothing.
- **Model output**, in ~500-character segments as it streams. A block truncates and marks the
  message.

**Not** applied to retrieved document content. Users' own documents legitimately contain
material a filter would flag — incident reports, legal discovery, medical records — and
blocking on them would make the tool useless. The documents are the user's own data; the
guardrail exists to catch misuse of the *model*, not to censor the corpus.

`ApplyGuardrail` failures fail **open** with a WARN log and a metric, on the reasoning that an
internal tool being unavailable is a worse outcome than an unfiltered turn between colleagues.
This is a deliberate posture and would be inverted for an external product.

## Prompt injection

Retrieved document content is untrusted input rendered into a model prompt. A hostile PDF can
contain "ignore previous instructions and…". Our exposure is bounded by design rather than by
detection:

- **The model has no tools.** No web access, no code execution, no file access, no API calls.
  The worst outcome of a successful injection is a wrong or rude answer to the user who
  uploaded the malicious document themselves.
- **No cross-tenant reach.** Retrieval is scoped to one project owned by one user, so a
  poisoned document cannot influence anyone else's answers.
- Document content is placed in `document` content blocks with `context` metadata, not
  concatenated into the system prompt, which keeps the instruction/data boundary explicit.
- The system prompt states the grounding rules positively rather than as prohibitions, which
  survives injection attempts better than a list of "do not"s.

No injection *detection* is attempted. Detection is unreliable and the residual risk here is
"the user gets a bad answer from their own file".

## Abuse and cost controls

The realistic threat is not a breach, it is a colleague accidentally uploading a 4000-page
scan corpus and spending the quarter's Bedrock budget.

- Per-user rate limits on message posting and document creation
  ([05-api-contracts.md](05-api-contracts.md#rate-limits)).
- Hard document limits: 200 MB, 1000 pages.
- Bedrock model-invocation permissions scoped to three specific models.
- AWS Budgets alarm on the account at 50/80/100% of the monthly envelope, and a CloudWatch
  alarm on Bedrock invocation count with a 3× week-over-week trigger.
- Lambda reserved concurrency on `answering` (10) and on the ingestion functions (25) so a
  runaway cannot consume the account's whole concurrency pool.

## Known accepted risks

| Risk | Why accepted |
| --- | --- |
| Refresh token in `sessionStorage` is XSS-reachable | Internal tool, restricted renderer, no third-party scripts, no user-generated HTML. A BFF is the documented upgrade. |
| Guardrail fails open | Availability preferred over filtering for an internal audience |
| No detection of prompt injection | Model has no tools; blast radius is the injecting user's own answer |
| Single AWS account per environment | Cost and operational simplicity; `dev` holds no real data |
| No WAF on CloudFront or API Gateway | Authenticated-only surface, low request volume, internal audience |
| 404-for-forbidden hides genuine bugs | Accepted; the logs record the real reason at WARN |
