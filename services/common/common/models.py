"""Pydantic item shapes for the DynamoDB single table (docs/02-data-model.md).

Project, Document, Page, and Chunk are modelled here (Phase 3 adds Page/Chunk). Conversation and
Message get their models in Phase 5, the first phase that writes those items — modelling an item
shape nothing produces yet is untested dead code.

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
