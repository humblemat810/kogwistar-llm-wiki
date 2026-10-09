"""Shared structural contracts for ingestion and parser results."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from kogwistar.json_types import JsonValue

if TYPE_CHECKING:
    from kg_doc_parser.workflow_ingest.providers import WorkflowProviderSettings
    from kogwistar.engine_core.engine import GraphKnowledgeEngine
    from kogwistar.engine_core.models import GraphExtractionWithIDs, Node, Span
    from kogwistar.policy import PromotionDecision

    from ..configuration.workspace import GraphSpace, WorkspaceNamespaces
    from ..diagnostics.debug_helpers import LiveTracePrinter, ParseStatisticsStore
    from ..embeddings.multimodal_projection import (
        AssetResolver,
        MultimodalEncoder,
        MultimodalProjectionStore,
        MultimodalSearchHit,
        MultimodalSourceUnit,
    )
    from ..embeddings.multimodal_sources import MultimodalSourceBundle
    from ..ingest.maintenance_requests import ParseTarget
    from ..maintenance.maintenance_guards import SourceRevision
    from ..models import IngestPipelineRequest, NamespaceEngines, ProjectionSnapshot
    from ..otel import LlmWikiTelemetry
    from ..parsing.parse_views import ParseSessionState, SourceRegion
    from ..policies.rules import LlmWikiPolicies
    from ..projection import ProjectionManager
    from ..usage.usage_models import UsageProjectionSnapshot
    from ..workbench.investigation_history import (
        InvestigationHistoryRecord,
        InvestigationHistoryService,
    )
    from ..workbench.query import GraphSpaceQueryResult, GraphSpaceQueryService
    from ..workbench.review_query import ReviewQueryService
    from ..workbench.semantic_lens import (
        InvestigationOutcome,
        SemanticLensRequest,
        SemanticLensService,
        SemanticLensSnapshot,
    )


class SemanticTreeLike(Protocol):
    """Minimal semantic-tree surface required by ingestion orchestration."""

    @property
    def title(self) -> str: ...


class ParseSourceResult(Protocol):
    """Parser result contract shared by direct and maintenance ingestion."""

    @property
    def semantic_tree(self) -> SemanticTreeLike: ...


ParserCallable = Callable[..., ParseSourceResult]


class TraceLog(Protocol):
    """Receive one bounded parser trace message."""

    def __call__(self, message: str, /) -> None: ...


class IngestPipelineHost(Protocol):
    """Shared host surface consumed by the ingestion mixins.

    ``IngestPipeline`` is intentionally composed from focused mixins.  This
    protocol records their collaboration surface without turning the mixins
    into a second inheritance implementation or hiding missing members behind
    ``Any``.
    """

    engines: NamespaceEngines
    parser: ParserCallable
    policies: LlmWikiPolicies
    projection: ProjectionManager
    query_service: GraphSpaceQueryService
    semantic_lens_service: SemanticLensService
    investigation_history_service: InvestigationHistoryService
    review_query_service: ReviewQueryService
    debug_trace_path: Path | None
    live_trace_printer: LiveTracePrinter | None
    telemetry: LlmWikiTelemetry
    conversation_persistence_mode: str
    parser_provider_settings: WorkflowProviderSettings | None
    multimodal_projection_store: MultimodalProjectionStore | None
    multimodal_encoder: MultimodalEncoder | None
    stats_store: ParseStatisticsStore | None
    _source_revisions: dict[tuple[str, str], SourceRevision]

    def namespaces_for(self, workspace_id: str) -> WorkspaceNamespaces: ...
    def parse_source(self, *, request: IngestPipelineRequest, source_document_id: str) -> ParseSourceResult: ...
    def _trace_event(self, stage: str, **fields: object) -> None: ...
    def _trace_step(
        self,
        stage: str,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        **fields: object,
    ) -> None: ...
    def _trace_text(self, message: str) -> None: ...
    def _source_document_id(self, request: IngestPipelineRequest) -> str: ...
    def _operation_mode(self, request: IngestPipelineRequest) -> str: ...
    def _maintenance_kind_for_operation_mode(self, operation_mode: str) -> str: ...
    def _durable_parse_limits(self, request: IngestPipelineRequest) -> dict[str, int | float | str | None]: ...
    def _durable_parse_profile(self, request: IngestPipelineRequest) -> str: ...
    def _provider_from_mode(self, mode: str) -> str | None: ...
    def _model_from_env(self, provider: str | None) -> str | None: ...
    def _parse_document_id_for_request(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        revision: SourceRevision,
    ) -> str: ...
    def source_revision(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
    ) -> SourceRevision: ...
    def begin_source_revision(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
    ) -> SourceRevision: ...
    def record_source_readiness(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        stage: str,
    ) -> str: ...
    def initialize_durable_parse_session(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        revision_document_id: str,
        revision: SourceRevision | None = None,
        max_depth: int = 10,
        max_frontier_items: int = 1,
        max_parser_calls: int = 1000,
        max_region_chars: int = 16_384,
        token_budget: int | None = None,
        wall_time_seconds: float | None = None,
        parser_profile: str | None = None,
        parser_version: str | None = None,
        model_version: str | None = None,
        prompt_version: str | None = None,
        initial_region: SourceRegion | None = None,
        session_id_override: str | None = None,
    ) -> ParseSessionState: ...
    def register_source(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        namespace: str,
    ) -> None: ...
    def seed_source_map(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        namespace: str,
    ) -> str: ...
    def _artifact_node(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        namespace: str,
        node_id: str | None = None,
        artifact_kind: str,
        lane: str,
        visibility: str,
        label: str,
        summary: str,
        extra_metadata: dict[str, JsonValue] | None = None,
    ) -> Node: ...
    def _node_exists(
        self,
        engine: GraphKnowledgeEngine,
        *,
        namespace: str,
        node_id: str,
    ) -> bool: ...
    def _document_exists(self, engine: GraphKnowledgeEngine, document_id: str) -> bool: ...
    def _job_exists(
        self,
        *,
        namespace: str,
        entity_kind: str,
        entity_id: str,
        job_kind: str,
        payload_matches: Mapping[str, JsonValue] | None = None,
    ) -> bool: ...
    def _leading_span(self, source_document_id: str, raw_text: str, *, insertion_method: str) -> Span: ...
    def _base_kg_reference_span(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        insertion_method: str,
    ) -> Span: ...
    def translate_parse_result(
        self,
        *,
        parse_result: ParseSourceResult,
        source_document_id: str,
    ) -> GraphExtractionWithIDs: ...
    def ingest_parse_result(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        graph_extraction: GraphExtractionWithIDs,
        namespace: str,
        parse_generation_id: str | None = None,
        parse_generation_member_id: str | None = None,
        parse_region: SourceRegion | None = None,
    ) -> None: ...
    def _project_base_kg_references(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        source_namespace: str,
        graph_extraction: GraphExtractionWithIDs,
    ) -> None: ...
    def _source_graph_extraction(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        graph_extraction: GraphExtractionWithIDs,
        legacy_namespace: str | None = None,
        parse_generation_id: str | None = None,
        parse_generation_member_id: str | None = None,
        parse_region: SourceRegion | None = None,
    ) -> GraphExtractionWithIDs: ...
    def _scope_generation_extraction_ids(
        self,
        graph_extraction: GraphExtractionWithIDs,
        *,
        parse_generation_member_id: str,
    ) -> GraphExtractionWithIDs: ...
    def _repair_graph_extraction_spans(
        self,
        *,
        graph_extraction: GraphExtractionWithIDs,
        source_document_id: str,
        request: IngestPipelineRequest,
    ) -> dict[str, int]: ...
    def _validate_parse_region_spans(
        self,
        *,
        graph_extraction: GraphExtractionWithIDs,
        source_document_id: str,
        parse_region: SourceRegion,
    ) -> None: ...
    def create_maintenance_request(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        namespace: str,
        maintenance_kind: str | None = None,
        topic: str | None = None,
        objective: str | None = None,
        budgets: Mapping[str, JsonValue] | None = None,
        seed_node_ids: Sequence[str] | None = None,
        maintenance_context: Mapping[str, JsonValue] | None = None,
        max_rounds: int | None = None,
        parse_target: ParseTarget | Mapping[str, JsonValue] | None = None,
    ) -> str: ...
    def create_parse_retry_history(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        parse_result: ParseSourceResult,
        namespace: str,
    ) -> str | None: ...
    def create_candidate_link(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        parse_result: ParseSourceResult,
        namespace: str,
    ) -> str: ...
    def create_promotion_candidate(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        candidate_link_id: str,
        promotion_evidence_pack_id: str | None = None,
        promotion_evidence_pack_digest: dict[str, JsonValue] | None = None,
        lineage_node_ids: list[str] | None = None,
        lineage_edge_ids: list[str] | None = None,
        namespace: str,
    ) -> str: ...
    def create_promotion_evidence_pack(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        candidate_link_id: str,
        graph_extraction: GraphExtractionWithIDs,
        namespace: str,
    ) -> tuple[str, dict[str, JsonValue]]: ...
    def promote_to_knowledge(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        promotion_candidate_id: str,
        promotion_evidence_pack_id: str | None = None,
        promotion_evidence_pack_digest: dict[str, JsonValue] | None = None,
        promotion_decision: PromotionDecision | None = None,
        namespace: str,
    ) -> str: ...
    def _enqueue_maintenance_job(
        self,
        *,
        request: IngestPipelineRequest,
        request_node_id: str,
        source_document_id: str,
        namespace: str,
        lane_message_id: str | None = None,
        maintenance_kind: str = "distill",
        source_revision_id: str = "",
        source_digest: str = "",
        revision_document_id: str = "",
        required_stage: str = "parsed_graph_persisted",
        objective: str | None = None,
        budgets: Mapping[str, JsonValue] | None = None,
        topic: str | None = None,
        seed_node_ids: Sequence[str] | None = None,
        maintenance_context: Mapping[str, JsonValue] | None = None,
        max_rounds: int | None = None,
        parse_target: ParseTarget | None = None,
        parse_session_id_override: str | None = None,
    ) -> str: ...
    def _enqueue_projection_job(
        self,
        *,
        request: IngestPipelineRequest,
        promoted_id: str,
        namespace: str,
    ) -> str: ...
    def build_projection_snapshot(
        self,
        workspace_id: str,
        *,
        graph_spaces: list[GraphSpace | str] | None = None,
        projection_filter: str | None = None,
    ) -> ProjectionSnapshot: ...
    def query_nodes(
        self,
        *,
        workspace_id: str,
        graph_spaces: list[GraphSpace | str],
        where: Mapping[str, JsonValue] | None = None,
        resolve_mode: str = "pointer_only",
    ) -> list[GraphSpaceQueryResult]: ...
    def resolve_semantic_lens(self, request: SemanticLensRequest) -> SemanticLensSnapshot: ...
    def record_investigation(
        self,
        *,
        workspace_id: str,
        session_id: str,
        question: str,
        action_kind: str,
        snapshot: SemanticLensSnapshot,
        outcome: InvestigationOutcome,
        created_at_ms: int | None = None,
    ) -> InvestigationHistoryRecord: ...
    def query_investigation_history(
        self,
        *,
        workspace_id: str,
        session_id: str | None = None,
        limit: int = 100,
    ) -> list[InvestigationHistoryRecord]: ...
    def capture_multimodal_units(self, units: Sequence[MultimodalSourceUnit]) -> int: ...
    def resolve_multimodal_source_map(
        self, unit: MultimodalSourceUnit
    ) -> Mapping[str, JsonValue] | None: ...
    def capture_multimodal_source(
        self,
        *,
        workspace_id: str,
        source_id: str,
        source_revision_id: str,
        source_format: str,
        source_uri: str | None = None,
        raw_text: str | None = None,
        content_ref: str | None = None,
        manifest: Mapping[str, JsonValue] | None = None,
        max_chars: int = 4000,
    ) -> MultimodalSourceBundle: ...
    def embed_multimodal_pending(
        self,
        *,
        batch_size: int | None = None,
        max_units: int | None = None,
        resolver: AssetResolver | None = None,
        workspace_id: str | None = None,
    ) -> int: ...
    def search_multimodal(self, query: str, *, limit: int = 10) -> list[MultimodalSearchHit]: ...
    def search_multimodal_image(
        self,
        images: Sequence[object],
        *,
        limit: int = 10,
        batch_size: int | None = None,
    ) -> list[MultimodalSearchHit]: ...
    def search_multimodal_mixed(
        self,
        *,
        text_queries: Sequence[str] = (),
        images: Sequence[object] = (),
        limit: int = 10,
        text_weight: float = 1.0,
        image_weight: float = 1.0,
    ) -> list[MultimodalSearchHit]: ...
    def refresh_usage_projection(self, workspace_id: str) -> UsageProjectionSnapshot: ...
    def _record_parse_statistics(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        parse_result: ParseSourceResult,
        graph_extraction: GraphExtractionWithIDs,
        parse_runtime_ms: int,
        status: str,
    ) -> None: ...
    def _parse_workflow_layered_source(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
    ) -> ParseSourceResult: ...
    def _build_parser_kwargs(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        trace_log: TraceLog | None = None,
    ) -> dict[str, object]: ...
    def _persist_parser_usage_events(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        provider: str,
        model: str,
        attempt_id: str,
        usage_events: list[object],
    ) -> None: ...
