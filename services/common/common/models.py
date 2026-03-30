"""Pydantic item shapes for the DynamoDB single table (docs/02-data-model.md).

Only Project and Document are modelled here. Page, Conversation, and Message get their models
in the phases that first write those items (Phase 3 ingestion, Phase 5 answering) — modelling an
item shape nothing produces yet is untested dead code.

Field names are snake_case in Python, camelCase on the wire (DynamoDB attributes and API JSON
both use camelCase per docs/02-data-model.md and docs/05-api-contracts.md).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

DocumentStatus = Literal["PENDING", "UPLOADED", "PROCESSING", "READY", "FAILED", "DELETING"]
DocumentKind = Literal["pdf", "image"]


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
    created_at: str
    updated_at: str

    def to_api(self) -> dict[str, object]:
        return self.model_dump(by_alias=True, exclude={"owner_sub"})
