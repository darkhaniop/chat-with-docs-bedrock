"""Pydantic item shapes for the DynamoDB single table (docs/02-data-model.md).

Project, Document, Page, and Chunk were modelled starting Phase 3. Conversation and Message are
added in Phase 5, the first phase that writes those items.

Field names are snake_case in Python, camelCase on the wire (DynamoDB attributes and API JSON
both use camelCase per docs/02-data-model.md and docs/05-api-contracts.md).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

DocumentStatus = Literal["PENDING", "UPLOADED", "PROCESSING", "READY", "FAILED", "DELETING"]
DocumentKind = Literal["pdf", "image"]
TextSource = Literal["pdf", "textract", "none"]

# A rect is (x0, y0, x1, y1) in canonical PDF user-space points, origin top-left
# (docs/02-data-model.md#coordinate-systems, common/geometry.py).
Rect = tuple[float, float, float, float]


class _CamelModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")


class Project(_CamelModel):
    """Canonical `PROJECT#{id} / META` item."""

    project_id: str
    owner_sub: str
    name: str
    description: str = ""
    document_count: int = 0
    chunk_count: int = 0
    created_at: str
    updated_at: str

    def to_api(self) -> dict[str, object]:
        """docs/05-api-contracts.md#projects `Project` shape — `ownerSub` is internal only."""
        return self.model_dump(by_alias=True, exclude={"owner_sub"})


class ProjectSummary(_CamelModel):
    """The `USER#{sub} / PROJECT#{id}` list-view copy — docs/02-data-model.md's documented
    sparse fields, not the full `Project` shape."""

    project_id: str
    name: str
    document_count: int = 0
    updated_at: str

    def to_api(self) -> dict[str, object]:
        return self.model_dump(by_alias=True)


class DocumentIngestion(_CamelModel):
    """docs/02-data-model.md's `ingestion` sub-object on the Document item. Populated as the
    state machine progresses; every field defaults to "not started yet" so a freshly created
    `Document` doesn't need a separate optional wrapper."""

    execution_arn: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    ocr_pages: int = 0
    chunk_count: int = 0


class Document(_CamelModel):
    """Canonical `DOC#{id} / META` item. The `PROJECT#{p} / DOC#{d}` list-view copy is a full
    mirror of this shape (docs/05-api-contracts.md's `GET .../documents` returns `[Document]`,
    not a reduced summary type), so one model serves both."""

    document_id: str
    project_id: str
    owner_sub: str
    filename: str
    content_type: str
    byte_size: int
    kind: DocumentKind
    page_count: int | None = None
    status: DocumentStatus
    status_detail: str | None = None
    ingestion: DocumentIngestion = Field(default_factory=DocumentIngestion)
    created_at: str
    updated_at: str

    def to_api(self) -> dict[str, object]:
        return self.model_dump(by_alias=True, exclude={"owner_sub"})


class PageRenders(_CamelModel):
    """docs/02-data-model.md's Page `s3` sub-object — keys, not URLs; the API layer presigns."""

    display: str
    embed: str


class Page(_CamelModel):
    """`DOC#{id} / PAGE#{pageNumber:04d}` item — docs/02-data-model.md#page.

    Written in two passes: `ingest-probe` creates it with `width`/`height`/`rotation`/
    `textDensity`/`textSource` (all knowable from PyMuPDF metadata and the text-density formula
    before any rendering happens), and `ingest-page` fills in `s3` once the renders exist, and
    may downgrade `textSource` to `"none"` if extraction failed for that specific page
    (docs/03-ingestion.md's "known failure modes" table).
    """

    document_id: str
    page_number: int
    width: float
    height: float
    rotation: int = 0
    text_source: TextSource
    text_density: float
    s3: PageRenders | None = None


class Sentence(_CamelModel):
    """One text block in the Citations-API `document` content block — `i` is the block index
    (docs/04-retrieval-and-citations.md#5-prompt-assembly). This correspondence is the citation
    map: nothing may reorder, filter, or merge this list between here and the request."""

    i: int
    text: str
    rects: list[Rect]


class Chunk(_CamelModel):
    """`DOC#{id} / CHUNK#{chunkId}` item — docs/02-data-model.md#chunk--the-citation-map, the
    single most important item type in the system."""

    chunk_id: str
    document_id: str
    project_id: str
    page_number: int
    ordinal: int
    text: str
    sentences: list[Sentence]
    token_estimate: int
    created_at: str


MessageRole = Literal["user", "assistant"]
MessageStatus = Literal["COMPLETE", "STREAMING", "FAILED", "BLOCKED"]
RetrievedKind = Literal["text", "page"]


class Conversation(_CamelModel):
    """`CONV#{id} / META` canonical item, mirrored (docs/02-data-model.md#conversation) at
    `PROJECT#{p} / CONV#{c}` for the list route — same full-mirror convention `Document` uses,
    since no reduced `ConversationSummary` is documented. `active_message_id`/`lock_expires_at`
    are the conversation lock (docs/02-data-model.md's "write patterns worth calling out": "set
    `activeMessageId` only if it is null or `lockExpiresAt < now`") and are excluded from
    `to_api()` — a client never needs to read the lock directly, only the 409 it produces."""

    conversation_id: str
    project_id: str
    owner_sub: str
    title: str = ""
    pinned_document_ids: list[str] = Field(default_factory=list)
    active_message_id: str | None = None
    lock_expires_at: int = 0
    message_count: int = 0
    created_at: str
    updated_at: str

    def to_api(self) -> dict[str, object]:
        return self.model_dump(
            by_alias=True, exclude={"owner_sub", "active_message_id", "lock_expires_at"}
        )


class RetrievedRef(_CamelModel):
    """One entry of `Message.retrieved` — docs/02-data-model.md#message: every hit
    `query_vectors` returned, pre-fusion (mirrors `answering.retrieve.RetrievedHit`, denormalised
    onto the persisted message so a historical turn is self-contained)."""

    chunk_id: str | None = None
    document_id: str
    page_number: int
    score: float | None = None
    kind: RetrievedKind


class CitationRecord(_CamelModel):
    """One entry of `Message.citations` — docs/04-retrieval-and-citations.md#8-citation-mapping.
    `suspect` is the canary described in that section's defensive rules: a citation whose
    `cited_text` shares no 8-character substring with the sentences it claims to cite is kept
    (never dropped) but flagged, so the eval harness can compute a suspect rate."""

    citation_id: str
    document_id: str
    page_number: int
    chunk_id: str
    start_sentence: int
    end_sentence: int
    cited_text: str
    rects: list[Rect]
    span_start: int
    span_end: int
    suspect: bool = False


class Usage(_CamelModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0


class LatencyMs(_CamelModel):
    rewrite: int | None = None
    embed: int | None = None
    retrieve: int | None = None
    first_token: int | None = None
    total: int = 0


class Message(_CamelModel):
    """`CONV#{c} / MSG#{ulid}` item — docs/02-data-model.md#message. `messageId` is the ULID
    itself (sorts chronologically), so no separate sort key field is needed beyond `sk`.
    User messages leave every assistant-only field at its default."""

    message_id: str
    conversation_id: str
    project_id: str
    owner_sub: str
    role: MessageRole
    status: MessageStatus
    text: str
    rewritten_query: str | None = None
    retrieved: list[RetrievedRef] = Field(default_factory=list)
    citations: list[CitationRecord] = Field(default_factory=list)
    usage: Usage | None = None
    latency_ms: LatencyMs | None = None
    created_at: str

    def to_api(self) -> dict[str, object]:
        return self.model_dump(
            by_alias=True, exclude={"owner_sub", "project_id", "conversation_id"}
        )
