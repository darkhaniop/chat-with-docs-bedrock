# 00 — Overview

## What this is

An internal tool that lets a person upload a set of documents (PDFs and images) into a
**project**, then hold a conversation about them. Every substantive claim in an answer carries
a citation that resolves to specific sentences on a specific page, and clicking the citation
scrolls the embedded PDF viewer to that spot and highlights it.

## Who it is for

Internal staff doing document-heavy analysis: reading contracts, reports, specs, scanned
correspondence, screenshots, and slide exports. A single user creates projects for themselves;
there is no sharing, no team workspace, and no permissions model beyond "your projects are
yours".

## Product scope

### In scope

- Cognito-authenticated sign-in via Managed Login (hosted UI).
- Projects as isolated document spaces. One user owns each project.
- Upload of PDFs (text-layer or scanned) and images (PNG, JPEG, WebP).
- Automatic ingestion: page rendering, text extraction with geometry, OCR where needed,
  chunking, multimodal embedding, and vector indexing.
- Conversational Q&A scoped to a project, with multi-turn follow-ups.
- Model-produced citations rendered inline in the answer and as highlights in the source.
- Live streaming of the answer as it is generated.
- Deleting a document (with de-indexing) and deleting a project.

### Out of scope (for now)

- Sharing, collaboration, comments, or any multi-user surface on a project.
- Document editing, annotation, or redaction.
- Office formats (`.docx`, `.pptx`, `.xlsx`) — PDF export is the supported path.
- Audio, video, or spreadsheet-native understanding.
- Agentic behaviour: no tool use, no web search, no code execution during answering.
- Fine-tuning, custom model hosting, or self-hosted inference.
- Mobile-specific layouts (desktop browsers only; testing targets chromium and webkit).

## Non-goals

- **Not a search engine.** Retrieval exists to feed generation. There is no standalone
  keyword search UI.
- **Not a knowledge base.** Nothing crosses project boundaries; there is no global corpus.
- **Not multi-tenant SaaS.** It is an internal tool in a single AWS account per environment.
  Isolation is per user, enforced in application code, not by account or VPC boundaries.

## Success criteria

The system is doing its job when:

1. **Citations are trustworthy.** A citation's highlighted span in the PDF genuinely contains
   the claim it is attached to. Measured by a fixed evaluation set (see
   [08-testing.md](08-testing.md#citation-fidelity-evaluation)); target ≥ 95% of citations
   land on the correct page and ≥ 90% on the correct sentence span.
2. **Retrieval finds the right pages.** Recall@10 ≥ 0.9 on the evaluation set, including
   questions whose answers live in charts, diagrams and scanned pages.
3. **It feels responsive.** First token within 5s p50 / 10s p95 of sending a message.
   Ingestion of a 50-page text-layer PDF completes within 2 minutes.
4. **It is affordable.** Per-project ingestion and per-conversation costs stay within the
   budget envelope in [09-operations.md](09-operations.md#cost-model).

## Key constraints

- **Serverless only.** No EC2, no ECS/Fargate services, no NAT gateways, no VPC-attached
  Lambdas. Cost at idle should be close to storage-only.
- **Single AWS region.** Whichever region has Bedrock access to Claude Sonnet 4.6, Claude
  Haiku 4.5, and Nova Multimodal Embeddings *and* has S3 Vectors available. This must be
  confirmed before Phase 1 — see [10-roadmap.md](10-roadmap.md#human-prerequisites).
- **Internal tool posture.** Guardrails are configured, but at ordinary strength.

## Glossary

| Term | Meaning |
| --- | --- |
| **Project** | A single-user container for documents, its own vector index, and its conversations. |
| **Document** | One uploaded file (a PDF or an image) belonging to a project. |
| **Page** | One page of a PDF, or the single page of an uploaded image. |
| **Block** | A text region extracted from a page, with a bounding box. PyMuPDF blocks or Textract lines. |
| **Sentence** | The atomic citable unit. Each sentence carries the page and the union of the boxes of the words it covers. |
| **Chunk** | An ordered run of sentences from one page, the unit of embedding and retrieval. |
| **Context document** | One chunk, serialised as a Citations-API `document` content block whose content is one text block per sentence. |
| **Citation** | A `content_block_location` returned by Claude, mapped back to `{documentId, page, rects[]}`. |
| **Answer turn** | One user message plus the assistant message it produces, including retrieval and citations. |
