"""Ingestion orchestration for `kogwistar-llm-wiki`.

This pipeline wires parsing, source/base/curated write paths, review artifact
creation, and the app-level projection/query helpers into one place.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Literal

from kg_doc_parser.workflow_ingest.page_index import parse_page_index_document
from kg_doc_parser.workflow_ingest.providers import (
    EmbeddingProviderConfig,
    build_embedding_function,
)
from kogwistar.engine_core import GraphKnowledgeEngine
from kogwistar.engine_core.models import (  # noqa: F401 - compatibility model seam
    Node,
    Span,
)
from kogwistar.typing_interfaces import EmbeddingFunctionLike

from .configuration.workspace import (  # noqa: F401 - compatibility enum seam
    GraphSpace,
    WorkspaceNamespaces,
)
from .diagnostics.debug_helpers import (
    LiveTracePrinter,
    ParseStatisticsStore,
    configure_debug_logging,
    env_flag_enabled,
)
from .embeddings.embedding_config_resolver import (
    embedding_profile as _embedding_profile,
)
from .embeddings.embedding_config_resolver import (
    resolve_embedding_function as _resolve_embedding_function_impl,
)
from .embeddings.embedding_config_resolver import (
    resolve_embedding_functions as _resolve_embedding_functions_impl,
)
from .embeddings.multimodal_projection import (
    MultimodalEncoder,
    MultimodalProjectionStore,
    build_configured_multimodal_encoder,
)
from .ingest.artifacts import IngestArtifactSupportMixin
from .ingest.base_kg_projection import BaseKgProjectionMixin
from .ingest.engine_builders import (
    _build_postgres_engine as _build_postgres_engine_impl,
)
from .ingest.engine_builders import (
    build_in_memory_namespace_engines as _build_in_memory_namespace_engines,
)
from .ingest.engine_builders import (
    build_persistent_namespace_engines as _build_persistent_namespace_engines,
)
from .ingest.engine_builders import (
    build_postgres_namespace_engines as _build_postgres_namespace_engines,
)
from .ingest.graph_persistence import GraphPersistenceMixin
from .ingest.maintenance_requests import MaintenanceRequestMixin
from .ingest.multimodal import MultimodalIngestMixin
from .ingest.pipeline_run import (
    IngestRunMixin,
    ParserFn,
    ParseSourceResult,  # noqa: F401 - compatibility type seam
    SemanticTreeLike,  # noqa: F401 - compatibility type seam
)
from .ingest.projection_access import ProjectionAccessMixin
from .ingest.source_lifecycle import SourceLifecycleMixin
from .ingest.source_parsing import SourceParsingMixin
from .ingest.workbench_access import WorkbenchAccessMixin
from .longrun_parser_worker import (
    run_workflow_layered_parse,  # noqa: F401 - public monkeypatch seam
)
from .maintenance.maintenance_guards import (
    SourceRevision,
)
from .models import (
    IngestPipelineArtifacts,  # noqa: F401 - compatibility model seam
    IngestPipelineRequest,  # noqa: F401 - compatibility model seam
    NamespaceEngines,
    ObsidianBuildResult,  # noqa: F401 - compatibility model seam
)
from .otel import LlmWikiTelemetry
from .policies.rules import LlmWikiPolicies, build_default_policies
from .projection import ProjectionManager
from .usage.projection_engine import UsageProjection
from .usage.usage_models import UsageProjectionSnapshot
from .utils import _temporary_namespace  # noqa: F401 - compatibility helper seam
from .workbench.investigation_history import InvestigationHistoryService
from .workbench.query import GraphSpaceQueryService
from .workbench.review_query import ReviewQueryService
from .workbench.semantic_lens import SemanticLensService


def _metadata_list_value(items: list[str] | None) -> list[str] | None:
    if not items:
        return None
    return list(items)


def _resolve_embedding_function(
    *args: object,
    **kwargs: object,
) -> tuple[EmbeddingFunctionLike, EmbeddingProviderConfig]:
    """Preserve the historical monkeypatch seam for provider-free tests."""

    return _resolve_embedding_function_impl(
        *args,
        embedding_factory=build_embedding_function,
        **kwargs,
    )


def _resolve_embedding_functions(
    **kwargs: object,
) -> tuple[dict[str, EmbeddingFunctionLike], dict[str, EmbeddingProviderConfig]]:
    """Preserve provider-factory injection for all graph-space embeddings."""

    return _resolve_embedding_functions_impl(
        embedding_factory=build_embedding_function,
        **kwargs,
    )


def _build_postgres_engine(
    persist_directory: Path,
    *,
    kg_graph_type: str,
    embedding_function: EmbeddingFunctionLike,
    dsn: str,
    embedding_dim: int,
    schema: str,
    embedding_profile: object | None = None,
    embedding_profile_mode: Literal["enforce", "inspect", "adopt"] = "enforce",
    persistence_mode: Literal["single_stage", "two_stage"] = "single_stage",
) -> GraphKnowledgeEngine:
    """Preserve the historical private seam used by backend tests and wrappers."""
    return _build_postgres_engine_impl(
        persist_directory,
        kg_graph_type=kg_graph_type,
        embedding_function=embedding_function,
        dsn=dsn,
        embedding_dim=embedding_dim,
        schema=schema,
        embedding_profile=embedding_profile,
        embedding_profile_mode=embedding_profile_mode,
        persistence_mode=persistence_mode,
        graph_engine_factory=GraphKnowledgeEngine,
    )


def build_in_memory_namespace_engines(
    base_dir: str | Path | None = None,
    *,
    split_derived_knowledge: bool = False,
    conversation_persistence_mode: Literal["single_stage", "two_stage"] = "single_stage",
    embedding_function: EmbeddingFunctionLike | None = None,
    embedding_config: EmbeddingProviderConfig | None = None,
    embedding_provider: str | None = None,
    embedding_model: str | None = None,
    embedding_dimension: int | None = None,
    embedding_base_url: str | None = None,
    embedding_api_key_env: str | None = None,
    embedding_functions: Mapping[str, EmbeddingFunctionLike] | None = None,
    embedding_configs: Mapping[str, EmbeddingProviderConfig] | None = None,
    embedding_profile_mode: Literal["enforce", "inspect", "adopt"] = "enforce",
) -> NamespaceEngines:
    """Build an in-memory namespace bundle through the shared builder module."""
    return _build_in_memory_namespace_engines(
        base_dir=base_dir,
        split_derived_knowledge=split_derived_knowledge,
        conversation_persistence_mode=conversation_persistence_mode,
        embedding_function=embedding_function,
        embedding_config=embedding_config,
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
        embedding_dimension=embedding_dimension,
        embedding_base_url=embedding_base_url,
        embedding_api_key_env=embedding_api_key_env,
        embedding_functions=embedding_functions,
        embedding_configs=embedding_configs,
        embedding_profile_mode=embedding_profile_mode,
        embedding_resolver=_resolve_embedding_functions,
        profile_resolver=_embedding_profile,
    )


def build_persistent_namespace_engines(
    base_dir: str | Path,
    *,
    split_derived_knowledge: bool = False,
    conversation_persistence_mode: Literal["single_stage", "two_stage"] = "single_stage",
    embedding_function: EmbeddingFunctionLike | None = None,
    embedding_config: EmbeddingProviderConfig | None = None,
    embedding_provider: str | None = None,
    embedding_model: str | None = None,
    embedding_dimension: int | None = None,
    embedding_base_url: str | None = None,
    embedding_api_key_env: str | None = None,
    embedding_functions: Mapping[str, EmbeddingFunctionLike] | None = None,
    embedding_configs: Mapping[str, EmbeddingProviderConfig] | None = None,
    embedding_profile_mode: Literal["enforce", "inspect", "adopt"] = "enforce",
) -> NamespaceEngines:
    """Build a persistent namespace bundle through the shared builder module."""
    return _build_persistent_namespace_engines(
        base_dir=base_dir,
        split_derived_knowledge=split_derived_knowledge,
        conversation_persistence_mode=conversation_persistence_mode,
        embedding_function=embedding_function,
        embedding_config=embedding_config,
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
        embedding_dimension=embedding_dimension,
        embedding_base_url=embedding_base_url,
        embedding_api_key_env=embedding_api_key_env,
        embedding_functions=embedding_functions,
        embedding_configs=embedding_configs,
        embedding_profile_mode=embedding_profile_mode,
        embedding_resolver=_resolve_embedding_functions,
        profile_resolver=_embedding_profile,
    )


def build_postgres_namespace_engines(
    *,
    base_dir: str | Path,
    dsn: str,
    embedding_dim: int | None = None,
    schema: str = "public",
    split_derived_knowledge: bool = False,
    conversation_persistence_mode: Literal["single_stage", "two_stage"] = "single_stage",
    embedding_function: EmbeddingFunctionLike | None = None,
    embedding_config: EmbeddingProviderConfig | None = None,
    embedding_provider: str | None = None,
    embedding_model: str | None = None,
    embedding_dimension: int | None = None,
    embedding_base_url: str | None = None,
    embedding_api_key_env: str | None = None,
    embedding_functions: Mapping[str, EmbeddingFunctionLike] | None = None,
    embedding_configs: Mapping[str, EmbeddingProviderConfig] | None = None,
    embedding_profile_mode: Literal["enforce", "inspect", "adopt"] = "enforce",
) -> NamespaceEngines:
    """Build a PostgreSQL namespace bundle through the shared builder module."""
    return _build_postgres_namespace_engines(
        base_dir=base_dir,
        dsn=dsn,
        embedding_dim=embedding_dim,
        schema=schema,
        split_derived_knowledge=split_derived_knowledge,
        conversation_persistence_mode=conversation_persistence_mode,
        embedding_function=embedding_function,
        embedding_config=embedding_config,
        embedding_provider=embedding_provider,
        embedding_model=embedding_model,
        embedding_dimension=embedding_dimension,
        embedding_base_url=embedding_base_url,
        embedding_api_key_env=embedding_api_key_env,
        embedding_functions=embedding_functions,
        embedding_configs=embedding_configs,
        embedding_profile_mode=embedding_profile_mode,
        embedding_resolver=_resolve_embedding_functions,
        profile_resolver=_embedding_profile,
        engine_builder=_build_postgres_engine,
    )


class IngestPipeline(
    IngestRunMixin,
    ProjectionAccessMixin,
    IngestArtifactSupportMixin,
    MultimodalIngestMixin,
    MaintenanceRequestMixin,
    SourceParsingMixin,
    BaseKgProjectionMixin,
    GraphPersistenceMixin,
    SourceLifecycleMixin,
    WorkbenchAccessMixin,
):
    def __init__(
        self,
        engines: NamespaceEngines,
        *,
        parser: ParserFn = parse_page_index_document,
        policies: LlmWikiPolicies | None = None,
        debug_run_dir: str | Path | None = None,
        live_trace: bool | None = None,
        conversation_persistence_mode: Literal["single_stage", "two_stage"] = "single_stage",
        multimodal_projection_store: MultimodalProjectionStore | None = None,
        multimodal_encoder: MultimodalEncoder | None = None,
    ) -> None:
        self.engines = engines
        self.parser = parser
        self.policies = policies or build_default_policies()
        self.projection = ProjectionManager(engines, policies=self.policies)
        self.query_service = GraphSpaceQueryService(engines)
        self.semantic_lens_service = SemanticLensService(engines, query_service=self.query_service)
        self.investigation_history_service = InvestigationHistoryService(engines)
        self.review_query_service = ReviewQueryService(engines)
        self.debug_run_dir = configure_debug_logging(debug_run_dir)
        self.debug_trace_path = self.debug_run_dir / "run_trace.jsonl" if self.debug_run_dir else None
        self.live_trace = (
            env_flag_enabled("KOGWISTAR_LLM_WIKI_LIVE_TRACE", "KOGWISTAR_LIVE_TRACE")
            if live_trace is None
            else bool(live_trace)
        )
        self.live_trace_printer = LiveTracePrinter(prefix="llm-wiki.ingest") if self.live_trace else None
        self.telemetry = LlmWikiTelemetry.from_environment()
        self.conversation_persistence_mode = conversation_persistence_mode
        self.multimodal_projection_store = multimodal_projection_store
        self.multimodal_encoder = multimodal_encoder
        if self.multimodal_encoder is None and multimodal_projection_store is not None:
            # A configured service client is lightweight; model inference stays
            # outside this process and is only attempted during Stage 2.
            from .embeddings.multimodal_runtime import (
                configured_embedding_service_url,
                configured_multimodal_backend,
                configured_vllm_url,
            )

            if configured_embedding_service_url() or (
                configured_multimodal_backend() == "vllm" and configured_vllm_url()
            ):
                self.multimodal_encoder = build_configured_multimodal_encoder()
        self._source_revisions: dict[tuple[str, str], SourceRevision] = {}
        self.stats_store = (
            ParseStatisticsStore(self.debug_run_dir / "llm_wiki_stats.sqlite3")
            if self.debug_run_dir is not None
            else None
        )


    def namespaces_for(self, workspace_id: str) -> WorkspaceNamespaces:
        return WorkspaceNamespaces(workspace_id)

    def usage_projection(self, workspace_id: str) -> UsageProjection:
        namespaces = self.namespaces_for(workspace_id)
        return UsageProjection(
            self.engines.conversation.meta_sqlite,
            workspace_id=workspace_id,
            source_namespace=namespaces.usage_events,
            projection_namespace=namespaces.usage_projection,
        )

    def refresh_usage_projection(
        self,
        workspace_id: str,
        *,
        rebuild_from_scratch: bool = False,
    ) -> UsageProjectionSnapshot:
        return self.usage_projection(workspace_id).refresh(
            rebuild_from_scratch=rebuild_from_scratch,
        )
