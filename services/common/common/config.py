"""Single typed settings object. Everything tunable elsewhere in the docs is a field here.

Model ids, the Nova embedding request shape, and the S3 Vectors distance metric are pinned by
the Phase 0 live contract smoke tests (``services/common/tests/contract/``) against the
``us-east-1`` sandbox account — see docs/10-roadmap.md's Phase 0 progress log entry for the
raw findings.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CWD_", frozen=True)

    env: str = "dev"
    aws_region: str = "us-east-1"

    # Bedrock model ids. Sonnet and Haiku require the cross-region inference-profile prefix in
    # us-east-1 — invoking the bare model id fails with "on-demand throughput isn't supported"
    # (confirmed live). Both `us.` and `global.` profiles work; `us.` is the default because it
    # keeps inference in-region. Nova has no inference profile of its own; the base model id is
    # invoked directly.
    sonnet_model_id: str = "us.anthropic.claude-sonnet-4-6"
    haiku_model_id: str = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    nova_model_id: str = "amazon.nova-2-multimodal-embeddings-v1:0"

    # Nova Multimodal Embeddings. `embeddingDimension` is a fixed enum — {384, 1024, 3072} were
    # confirmed valid live; 10000 was rejected. 1024 is chosen as the default balance of recall
    # vs. S3 Vectors storage/query cost; revisit in the Phase 8 tuning sweep.
    embed_dim: Literal[384, 1024, 3072] = 1024
    # `embeddingPurpose` is also a fixed enum. GENERIC_INDEX/GENERIC_RETRIEVAL were confirmed
    # valid live; DOCUMENT/QUERY were rejected. Use the index purpose when embedding chunks and
    # pages at ingest time, and the retrieval purpose when embedding the query at answer time.
    nova_embed_purpose_index: Literal["GENERIC_INDEX"] = "GENERIC_INDEX"
    nova_embed_purpose_query: Literal["GENERIC_RETRIEVAL"] = "GENERIC_RETRIEVAL"

    # S3 Vectors. Confirmed live: `create_vector_bucket`/`create_index`/`put_vectors`/
    # `query_vectors`/`delete_vectors`/`delete_index`/`delete_vector_bucket` on boto3's
    # `s3vectors` client (added to botocore after the workspace's initial resolution — pin a
    # recent boto3, do not cap it, see the Phase 0 finding). `filter` accepts both plain
    # equality (`{"documentId": "doc2"}`) and Mongo-style operators (`{"documentId": {"$in":
    # [...]}}`. `preview` metadata is returned by `query_vectors` regardless of whether it is
    # registered as non-filterable — non-filterable only affects the filterable-metadata quota.
    vector_distance_metric: Literal["cosine"] = "cosine"
    vector_data_type: Literal["float32"] = "float32"
    vector_query_top_k: int = 40
    rrf_k: int = 60

    # Ingestion limits (docs/07-security.md#abuse-and-cost-controls, docs/03-ingestion.md #Probe)
    max_document_pages: int = 1000
    max_document_bytes: int = 200 * 1024 * 1024
    text_density_threshold: float = 0.15
    text_density_chars_per_sq_inch: int = 250

    # Chunking (docs/03-ingestion.md#step-3-chunk)
    chunk_target_min_tokens: int = 400
    chunk_target_max_tokens: int = 600
    chunk_hard_cap_tokens: int = 900
    chunk_item_max_bytes: int = 300 * 1024
    sentence_max_chars: int = 1000
    chars_per_token_estimate: int = 4

    # Rendering (docs/03-ingestion.md#2a-render)
    display_render_dpi: int = 200
    display_render_max_long_edge_px: int = 3000
    embed_render_max_long_edge_px: int = 1568

    # Retrieval and fusion (docs/04-retrieval-and-citations.md#4-fusion-and-selection)
    max_selected_pages: int = 6
    max_chunks_per_page: int = 2
    max_total_chunks: int = 10
    max_context_tokens: int = 14_000
    thin_text_char_threshold: int = 200

    # Query rewriting (docs/04-retrieval-and-citations.md#1-query-rewriting)
    rewrite_max_tokens: int = 200
    rewrite_effort: Literal["low", "medium", "high"] = "low"
    rewrite_history_turns: int = 6
    rewrite_history_max_chars: int = 4000

    # Generation (docs/04-retrieval-and-citations.md#5-prompt-assembly)
    generation_max_tokens: int = 8000
    generation_effort: Literal["low", "medium", "high"] = "medium"

    # Streaming (docs/04-retrieval-and-citations.md#streaming-to-the-client)
    stream_batch_tick_ms: int = 80

    # Presigned URLs (docs/07-security.md#data-protection)
    presigned_url_ttl_seconds: int = 900

    # Orphan-upload sweeper (docs/02-data-model.md#s3-layout): a PENDING document whose client
    # never called `:ingest` is swept after this many hours, not by a bucket lifecycle rule,
    # because the rule can't see DynamoDB state.
    orphan_upload_staleness_hours: int = 24

    # Concurrency (docs/07-security.md#abuse-and-cost-controls)
    answering_reserved_concurrency: int = 10
    ingestion_reserved_concurrency: int = 25
    distributed_map_max_concurrency: int = 20

    # Guardrail (docs/07-security.md#bedrock-guardrails). The guardrail resource is created by
    # CDK in Phase 8; unset until then. `apply_guardrail` fails open when this is None.
    guardrail_id: str | None = None
    guardrail_version: str = "DRAFT"

    # AppSync Events (docs/05-api-contracts.md#appsync-events). `CwdDevRealtimeStack` — the API
    # this domain points at — is built in Phase 6; `common.events.AppSyncEventsPublisher`
    # no-ops until then. The bare domain (`{api-id}.appsync-api.{region}.amazonaws.com`), not a
    # full URL — `publish` builds the `/event` path itself.
    events_http_domain: str | None = None

    # Observability (docs/09-operations.md#observability)
    log_retention_days: int = 30

    @property
    def table_name(self) -> str:
        return f"cwd-{self.env}"

    def documents_bucket_name(self, account_id: str) -> str:
        return f"cwd-documents-{self.env}-{account_id}"

    def vector_bucket_name(self, account_id: str) -> str:
        return f"cwd-vectors-{self.env}-{account_id}"

    @staticmethod
    def vector_index_name(project_id: str) -> str:
        return f"proj-{project_id}"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
