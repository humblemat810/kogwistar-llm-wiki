"""Ingestion orchestration for `kogwistar-llm-wiki`.

This pipeline wires parsing, source/base/curated write paths, review artifact
creation, and the app-level projection/query helpers into one place.
"""

from __future__ import annotations

import inspect
import json
import os
import hashlib
import time
from dataclasses import replace
from pathlib import Path
import tempfile
from types import SimpleNamespace
from typing import Callable, Mapping, Protocol

from .utils import _temporary_namespace
from kogwistar.engine_core import GraphKnowledgeEngine
from kogwistar.engine_core.in_memory_backend import build_in_memory_backend
from kogwistar.engine_core.models import Document, GraphExtractionWithIDs, Grounding, Node, Span
from kogwistar.id_provider import stable_id
from kogwistar.logical_refs import (
    LogicalRef,
    build_reference_edge_payload,
    build_reference_node_payload,
    logical_ref_id,
)
from kogwistar.policy import PromotionDecision
from kogwistar.runtime.budget import BudgetAttribution, budget_event_from_dict
from kogwistar.provenance import EvidencePackDigest, evidence_pack_digest_hash
from kg_doc_parser.workflow_ingest.page_index import parse_page_index_document
from kg_doc_parser.workflow_ingest.semantics import semantic_tree_to_kge_payload
from kogwistar.typing_interfaces import EmbeddingFunctionLike
from .provider_config import normalize_provider_name, resolve_parser_provider_settings
from .debug_run import (
    LiveTracePrinter,
    ParseStatisticsStore,
    append_jsonl,
    build_parse_statistics_record,
    configure_debug_logging,
    env_flag_enabled,
    now_ms,
)
from .longrun_parser_worker import run_workflow_layered_parse
from .models import (
    IngestPipelineArtifacts,
    IngestPipelineRequest,
    ObsidianBuildResult,
    NamespaceEngines,
    ProjectionSnapshot,
)
from .query import GraphSpaceQueryResult, GraphSpaceQueryService
from .policies import LlmWikiPolicies, build_default_policies
from .namespaces import GraphSpace, WorkspaceNamespaces
from .projection import ProjectionManager
from .usage_projection import UsageProjection, UsageProjectionSnapshot, append_usage_event
from .review_query import ReviewQueryService


def _metadata_digest_value(digest: dict[str, object] | None) -> str | None:
    if digest is None:
        return None
    return json.dumps(digest, sort_keys=True, separators=(",", ":"))


def _metadata_list_value(items: list[str] | None) -> list[str] | None:
    if not items:
        return None
    return list(items)


class _TinyEmbeddingFunction:
    _name = "kogwistar-llm-wiki-embedding-v1"

    def name(self) -> str:
        return self._name

    def __call__(self, input: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for value in input:
            text = str(value or "")
            checksum = float((sum(ord(ch) for ch in text) % 97) + 1)
            vectors.append([float(len(text) + 1), checksum])
        return vectors





class SemanticTreeLike(Protocol):
    title: str


class ParseSourceResult(Protocol):
    semantic_tree: SemanticTreeLike


ParserFn = Callable[..., ParseSourceResult]


def build_in_memory_namespace_engines(
    base_dir: str | Path | None = None,
    *,
    split_derived_knowledge: bool = False,
) -> NamespaceEngines:
    root = Path(base_dir) if base_dir is not None else Path(tempfile.mkdtemp(prefix="kogwistar-llm-wiki-"))
    embedding = _TinyEmbeddingFunction()
    
    # Shared conversation engine (fg/bg lanes)
    conversation = _build_engine(root / "conversation", kg_graph_type="conversation", embedding_function=embedding)
    
    derived_engine = _build_engine(root / "derived_knowledge", kg_graph_type="derived_knowledge", embedding_function=embedding) if split_derived_knowledge else None
    return NamespaceEngines(
        conversation=conversation,
        workflow=_build_engine(root / "workflow", kg_graph_type="workflow", embedding_function=embedding),
        kg=_build_engine(root / "kg", kg_graph_type="knowledge", embedding_function=embedding),
        wisdom=_build_engine(root / "wisdom", kg_graph_type="wisdom", embedding_function=embedding),
        derived_knowledge=derived_engine,
    )


def build_persistent_namespace_engines(
    base_dir: str | Path,
    *,
    split_derived_knowledge: bool = False,
) -> NamespaceEngines:
    root = Path(base_dir)
    root.mkdir(parents=True, exist_ok=True)
    embedding = _TinyEmbeddingFunction()
    derived_engine = _build_persistent_engine(root / "derived_knowledge", kg_graph_type="derived_knowledge", embedding_function=embedding) if split_derived_knowledge else None
    return NamespaceEngines(
        conversation=_build_persistent_engine(root / "conversation", kg_graph_type="conversation", embedding_function=embedding),
        workflow=_build_persistent_engine(root / "workflow", kg_graph_type="workflow", embedding_function=embedding),
        kg=_build_persistent_engine(root / "kg", kg_graph_type="knowledge", embedding_function=embedding),
        wisdom=_build_persistent_engine(root / "wisdom", kg_graph_type="wisdom", embedding_function=embedding),
        derived_knowledge=derived_engine,
    )


def build_postgres_namespace_engines(
    *,
    base_dir: str | Path,
    dsn: str,
    embedding_dim: int = 2,
    schema: str = "public",
    split_derived_knowledge: bool = False,
) -> NamespaceEngines:
    root = Path(base_dir)
    root.mkdir(parents=True, exist_ok=True)
    embedding = _TinyEmbeddingFunction()
    derived_engine = _build_postgres_engine(
        root / "derived_knowledge",
        kg_graph_type="derived_knowledge",
        embedding_function=embedding,
        dsn=dsn,
        embedding_dim=embedding_dim,
        schema=schema,
    ) if split_derived_knowledge else None
    return NamespaceEngines(
        conversation=_build_postgres_engine(
            root / "conversation",
            kg_graph_type="conversation",
            embedding_function=embedding,
            dsn=dsn,
            embedding_dim=embedding_dim,
            schema=schema,
        ),
        workflow=_build_postgres_engine(
            root / "workflow",
            kg_graph_type="workflow",
            embedding_function=embedding,
            dsn=dsn,
            embedding_dim=embedding_dim,
            schema=schema,
        ),
        kg=_build_postgres_engine(
            root / "kg",
            kg_graph_type="knowledge",
            embedding_function=embedding,
            dsn=dsn,
            embedding_dim=embedding_dim,
            schema=schema,
        ),
        wisdom=_build_postgres_engine(
            root / "wisdom",
            kg_graph_type="wisdom",
            embedding_function=embedding,
            dsn=dsn,
            embedding_dim=embedding_dim,
            schema=schema,
        ),
        derived_knowledge=derived_engine,
    )


def _build_engine(
    persist_directory: Path,
    *,
    kg_graph_type: str,
    embedding_function: EmbeddingFunctionLike,
) -> GraphKnowledgeEngine:
    persist_directory.mkdir(parents=True, exist_ok=True)
    return GraphKnowledgeEngine(
        persist_directory=str(persist_directory),
        kg_graph_type=kg_graph_type,
        embedding_function=embedding_function,
        backend_factory=build_in_memory_backend,
        namespace=kg_graph_type,
    )


def _build_persistent_engine(
    persist_directory: Path,
    *,
    kg_graph_type: str,
    embedding_function: EmbeddingFunctionLike,
) -> GraphKnowledgeEngine:
    persist_directory.mkdir(parents=True, exist_ok=True)
    return GraphKnowledgeEngine(
        persist_directory=str(persist_directory),
        kg_graph_type=kg_graph_type,
        embedding_function=embedding_function,
        namespace=kg_graph_type,
    )


def _build_postgres_engine(
    persist_directory: Path,
    *,
    kg_graph_type: str,
    embedding_function: EmbeddingFunctionLike,
    dsn: str,
    embedding_dim: int,
    schema: str,
) -> GraphKnowledgeEngine:
    from kogwistar.engine_core.engine_postgres import EnginePostgresConfig, build_postgres_backend

    persist_directory.mkdir(parents=True, exist_ok=True)
    backend, _ = build_postgres_backend(
        EnginePostgresConfig(
            dsn=dsn,
            embedding_dim=embedding_dim,
            schema=schema,
        )
    )
    return GraphKnowledgeEngine(
        persist_directory=str(persist_directory),
        kg_graph_type=kg_graph_type,
        embedding_function=embedding_function,
        backend=backend,
        namespace=kg_graph_type,
    )


class IngestPipeline:
    def __init__(
        self,
        engines: NamespaceEngines,
        *,
        parser: ParserFn = parse_page_index_document,
        policies: LlmWikiPolicies | None = None,
        debug_run_dir: str | Path | None = None,
        live_trace: bool | None = None,
    ) -> None:
        self.engines = engines
        self.parser = parser
        self.policies = policies or build_default_policies()
        self.projection = ProjectionManager(engines, policies=self.policies)
        self.query_service = GraphSpaceQueryService(engines)
        self.review_query_service = ReviewQueryService(engines)
        self.debug_run_dir = configure_debug_logging(debug_run_dir)
        self.debug_trace_path = self.debug_run_dir / "run_trace.jsonl" if self.debug_run_dir else None
        self.live_trace = (
            env_flag_enabled("KOGWISTAR_LLM_WIKI_LIVE_TRACE", "KOGWISTAR_LIVE_TRACE")
            if live_trace is None
            else bool(live_trace)
        )
        self.live_trace_printer = LiveTracePrinter(prefix="llm-wiki.ingest") if self.live_trace else None
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

    def run(self, request: IngestPipelineRequest) -> IngestPipelineArtifacts:
        ns = self.namespaces_for(request.workspace_id)
        source_document_id = self._source_document_id(request)
        operation_mode = self._operation_mode(request)
        self._trace_event(
            "ingest_run_start",
            workspace_id=request.workspace_id,
            source_document_id=source_document_id,
            source_uri=request.source_uri,
            parser_lane=request.parser_lane,
            parser_mode=request.parser_mode,
            operation_mode=operation_mode,
        )

        self.register_source(
            request=request,
            source_document_id=source_document_id,
            namespace=ns.conv_fg,
        )
        if operation_mode == "maintenance_first":
            self.seed_source_map(
                request=request,
                source_document_id=source_document_id,
                namespace=ns.conv_bg,
            )
            maintenance_job_id = self.create_maintenance_request(
                request=request,
                source_document_id=source_document_id,
                namespace=ns.conv_bg,
                maintenance_kind="document_seed_graph",
            )
            self._trace_event(
                "ingest_run_complete",
                workspace_id=request.workspace_id,
                source_document_id=source_document_id,
                operation_mode=operation_mode,
                graph_status="seeded",
            )
            return IngestPipelineArtifacts(
                source_document_id=source_document_id,
                maintenance_job_id=maintenance_job_id,
                candidate_link_id="",
                promotion_candidate_id="",
                promoted_entity_id=None,
                operation_mode=operation_mode,
                graph_status="seeded",
            )
        parse_started_at = time.perf_counter()
        parse_result = self.parse_source(
            request=request,
            source_document_id=source_document_id,
        )
        parse_runtime_ms = max(0, int((time.perf_counter() - parse_started_at) * 1000))
        self.create_parse_retry_history(
            request=request,
            source_document_id=source_document_id,
            parse_result=parse_result,
            namespace=ns.conv_bg,
        )
        graph_extraction = self.translate_parse_result(
            parse_result=parse_result,
            source_document_id=source_document_id,
        )
        self.ingest_parse_result(
            request=request,
            source_document_id=source_document_id,
            graph_extraction=graph_extraction,
            namespace=ns.conv_fg,
        )
        self._record_parse_statistics(
            request=request,
            source_document_id=source_document_id,
            parse_result=parse_result,
            graph_extraction=graph_extraction,
            parse_runtime_ms=parse_runtime_ms,
            status="ok",
        )
        maintenance_job_id = self.create_maintenance_request(
            request=request,
            source_document_id=source_document_id,
            namespace=ns.conv_bg,
            maintenance_kind=self._maintenance_kind_for_operation_mode(operation_mode),
        )
        candidate_link_id = self.create_candidate_link(
            request=request,
            source_document_id=source_document_id,
            parse_result=parse_result,
            namespace=ns.conv_bg,
        )
        promotion_evidence_pack_id, promotion_evidence_pack_digest = self.create_promotion_evidence_pack(
            request=request,
            source_document_id=source_document_id,
            candidate_link_id=candidate_link_id,
            graph_extraction=graph_extraction,
            namespace=ns.conv_bg,
        )
        promotion_candidate_id = self.create_promotion_candidate(
            request=request,
            source_document_id=source_document_id,
            candidate_link_id=candidate_link_id,
            promotion_evidence_pack_id=promotion_evidence_pack_id,
            promotion_evidence_pack_digest=promotion_evidence_pack_digest,
            lineage_node_ids=[source_document_id, candidate_link_id],
            lineage_edge_ids=[],
            namespace=ns.conv_bg,
        )


        promoted_entity_id: str | None = None
        promotion_decision = self.policies.promotion.decide(
            promotion_mode=request.promotion_mode,
            auto_accept_threshold=request.auto_accept_threshold,
            metadata={
                "workspace_id": request.workspace_id,
                "source_document_id": source_document_id,
                "promotion_candidate_id": promotion_candidate_id,
                "promotion_evidence_pack_id": promotion_evidence_pack_id,
            },
        )
        if promotion_decision.should_promote:
            promoted_entity_id = self.promote_to_knowledge(
                request=request,
                source_document_id=source_document_id,
                promotion_candidate_id=promotion_candidate_id,
                promotion_evidence_pack_id=promotion_evidence_pack_id,
                promotion_evidence_pack_digest=promotion_evidence_pack_digest,
                promotion_decision=promotion_decision,
                namespace=ns.curated_kg_space,
            )

        self._trace_event(
            "ingest_run_complete",
            workspace_id=request.workspace_id,
            source_document_id=source_document_id,
            operation_mode=operation_mode,
            graph_status="expanding" if operation_mode == "hybrid" else "stable",
            parse_runtime_ms=parse_runtime_ms,
            promoted_entity_id=promoted_entity_id,
        )
        return IngestPipelineArtifacts(
            source_document_id=source_document_id,
            maintenance_job_id=maintenance_job_id,
            candidate_link_id=candidate_link_id,
            promotion_candidate_id=promotion_candidate_id,
            promoted_entity_id=promoted_entity_id,
            operation_mode=operation_mode,
            graph_status="expanding" if operation_mode == "hybrid" else "stable",
        )

    def build_obsidian_vault(
        self,
        vault_root: str | Path,
        *,
        workspace_id: str,
        graph_spaces: list[GraphSpace | str] | None = None,
        projection_filter: str | None = None,
        version: int | None = None,
        event_seq: int | None = None,
    ) -> ObsidianBuildResult:
        return self.projection.build_obsidian_vault(
            vault_root,
            workspace_id=workspace_id,
            graph_spaces=graph_spaces,
            projection_filter=projection_filter,
            version=version,
            event_seq=event_seq,
        )

    def sync_obsidian_vault(
        self,
        vault_root: str | Path,
        *,
        workspace_id: str,
        changed_ids: set[str] | None = None,
        deleted_ids: set[str] | None = None,
        affected_titles: set[str] | None = None,
        version: int | None = None,
        event_seq: int | None = None,
    ) -> ObsidianBuildResult:
        return self.projection.sync_obsidian_vault(
            vault_root,
            workspace_id=workspace_id,
            changed_ids=changed_ids,
            deleted_ids=deleted_ids,
            affected_titles=affected_titles,
            version=version,
            event_seq=event_seq,
        )

    def _source_document_id(self, request: IngestPipelineRequest) -> str:
        return str(
            stable_id(
                "kogwistar_llm_wiki.source_document",
                request.workspace_id,
                request.source_uri,
            )
        )

    @staticmethod
    def _operation_mode(request: IngestPipelineRequest) -> str:
        operation_mode = str(getattr(request, "operation_mode", "parse_first") or "parse_first").strip().lower()
        if operation_mode not in {"parse_first", "maintenance_first", "hybrid"}:
            raise ValueError("operation_mode must be one of: parse_first, maintenance_first, hybrid")
        return operation_mode

    @staticmethod
    def _maintenance_kind_for_operation_mode(operation_mode: str) -> str:
        if operation_mode == "maintenance_first":
            return "document_seed_graph"
        if operation_mode == "hybrid":
            return "document_expand_parse_children"
        return "distill"

    def register_source(self, *, request: IngestPipelineRequest, source_document_id: str, namespace: str) -> None:
        source_namespace = self.namespaces_for(request.workspace_id).source_space
        source_metadata = {
            "workspace_id": request.workspace_id,
            "graph_space": "source",
            "source_uri": request.source_uri,
            "title": request.title,
            "source_format": request.source_format,
            "operation_mode": self._operation_mode(request),
            "parser_mode": request.parser_mode,
        }
        source_document = Document(
            id=source_document_id,
            content=request.raw_text,
            type="text",
            metadata=dict(source_metadata),
        )
        compatibility_metadata = dict(source_metadata)
        compatibility_metadata["legacy_namespace"] = namespace
        compatibility_document = Document(
            id=source_document_id,
            content=request.raw_text,
            type="text",
            metadata=compatibility_metadata,
        )

        with _temporary_namespace(self.engines.kg, source_namespace):
            self.engines.kg.write.add_document(source_document)
        with _temporary_namespace(self.engines.conversation, namespace):
            self.engines.conversation.write.add_document(compatibility_document)

    def seed_source_map(self, *, request: IngestPipelineRequest, source_document_id: str, namespace: str) -> str:
        source_namespace = self.namespaces_for(request.workspace_id).source_space
        source_map_digest = hashlib.sha256((request.raw_text or "").encode("utf-8")).hexdigest()
        node_id = str(
            stable_id(
                "kogwistar_llm_wiki.source_map_seed",
                request.workspace_id,
                source_document_id,
                source_map_digest,
            )
        )
        seed_metadata = {
            "graph_space": "source",
            "operation_mode": self._operation_mode(request),
            "graph_status": "seeded",
            "source_map_digest": source_map_digest,
            "source_map_kind": "single_text_span",
            "source_span_count": 1 if request.raw_text else 0,
        }
        source_seed = self._artifact_node(
            request=request,
            source_document_id=source_document_id,
            namespace=source_namespace,
            node_id=node_id,
            artifact_kind="source_map_seed",
            lane="background",
            visibility="internal",
            label=f"Source Map Seed: {request.title}",
            summary=f"Seed source map for {request.title}",
            extra_metadata=seed_metadata,
        )
        compatibility_seed = self._artifact_node(
            request=request,
            source_document_id=source_document_id,
            namespace=namespace,
            node_id=node_id,
            artifact_kind="source_map_seed",
            lane="background",
            visibility="internal",
            label=f"Source Map Seed: {request.title}",
            summary=f"Seed source map for {request.title}",
            extra_metadata={**seed_metadata, "legacy_namespace": namespace},
        )
        with _temporary_namespace(self.engines.kg, source_namespace):
            if not self.engines.kg.read.node_exists(ids=[node_id]):
                self.engines.kg.write.add_node(source_seed)
        with _temporary_namespace(self.engines.conversation, namespace):
            if not self.engines.conversation.read.node_exists(ids=[node_id]):
                self.engines.conversation.write.add_node(compatibility_seed)
        return node_id

    def parse_source(self, *, request: IngestPipelineRequest, source_document_id: str) -> ParseSourceResult:
        self._trace_event(
            "parse_source_start",
            workspace_id=request.workspace_id,
            source_document_id=source_document_id,
            parser_lane=request.parser_lane,
            parser_mode=request.parser_mode,
        )
        if request.parser_lane == "workflow_layered":
            result = self._parse_workflow_layered_source(
                request=request,
                source_document_id=source_document_id,
            )
            self._trace_event(
                "parse_source_complete",
                workspace_id=request.workspace_id,
                source_document_id=source_document_id,
                parser_lane=request.parser_lane,
                parser_mode=request.parser_mode,
                parse_backend="workflow_layered",
            )
            return result
        parser_kwargs = self._build_parser_kwargs(
            request=request,
            source_document_id=source_document_id,
            trace_log=self._trace_text if self.debug_trace_path is not None else None,
        )
        self._trace_event(
            "parse_source_dispatch",
            workspace_id=request.workspace_id,
            source_document_id=source_document_id,
            parser_lane=request.parser_lane,
            parser_mode=request.parser_mode,
            parse_backend="direct",
        )
        result = self.parser(**parser_kwargs)
        self._trace_event(
            "parse_source_complete",
            workspace_id=request.workspace_id,
            source_document_id=source_document_id,
            parser_lane=request.parser_lane,
            parser_mode=request.parser_mode,
            parse_backend="direct",
        )
        return result

    def _parse_workflow_layered_source(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
    ) -> SimpleNamespace:
        provider = request.llm_provider or self._provider_from_mode(request.parser_mode)
        model = request.llm_model or self._model_from_env(provider)
        if provider is None:
            raise ValueError(f"missing llm provider for parser_mode={request.parser_mode!r}")

        provider_settings = resolve_parser_provider_settings(
            provider=provider,
            model=model,
        )
        engine_dir = Path(tempfile.mkdtemp(prefix="kogwistar-workflow-layered-"))
        self._trace_event(
            "workflow_layered_parse_start",
            workspace_id=request.workspace_id,
            source_document_id=source_document_id,
            provider=provider,
            model=model,
            engine_dir=str(engine_dir),
        )
        result = run_workflow_layered_parse(
            source_document_id=source_document_id,
            title=request.title,
            raw_text=request.raw_text,
            provider_settings=provider_settings,
            engine_dir=engine_dir,
            trace=self._trace_text if self.debug_trace_path is not None else None,
        )
        self._persist_parser_usage_events(
            request=request,
            source_document_id=source_document_id,
            provider=provider,
            model=model,
            usage_events=list(getattr(result, "usage_events", []) or []),
        )
        self._trace_event(
            "workflow_layered_parse_complete",
            workspace_id=request.workspace_id,
            source_document_id=source_document_id,
            provider=provider,
            model=model,
            proposal_mode=getattr(provider_settings, "proposal_mode", None),
            parse_session_mode=getattr(result, "parse_session", {}).get("mode") if getattr(result, "parse_session", None) else None,
        )
        return SimpleNamespace(
            semantic_tree=result.semantic_tree,
            graph_payload=result.graph_payload,
            evaluation=result.evaluation,
            diagnostics=result.diagnostics,
            usage_summary=result.usage_summary,
            usage_events=list(getattr(result, "usage_events", []) or []),
            layer_log=result.layer_log,
            parse_session=getattr(result, "parse_session", None),
        )

    def _persist_parser_usage_events(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        provider: str,
        model: str,
        usage_events: list[object],
    ) -> None:
        if not usage_events:
            return
        namespaces = self.namespaces_for(request.workspace_id)
        for index, raw_event in enumerate(usage_events):
            if not isinstance(raw_event, dict):
                continue
            event = budget_event_from_dict(raw_event)
            operation_id = str(
                stable_id(
                    "kogwistar_llm_wiki.parser_usage_operation",
                    request.workspace_id,
                    source_document_id,
                    index,
                    event.meta.get("reason"),
                )
            )
            attribution = event.attribution or BudgetAttribution()
            attribution = replace(
                attribution,
                workspace_id=attribution.workspace_id or request.workspace_id,
                source_document_id=attribution.source_document_id or source_document_id,
                operation_id=attribution.operation_id or operation_id,
                operation_kind=attribution.operation_kind or "parser",
                provider=attribution.provider or provider,
                model=attribution.model or model,
            )
            enriched = replace(
                event,
                event_id=str(
                    stable_id(
                        "kogwistar_llm_wiki.parser_usage_event",
                        request.workspace_id,
                        source_document_id,
                        index,
                        event.event_id,
                    )
                ),
                attribution=attribution,
            )
            append_usage_event(
                self.engines.conversation.meta_sqlite,
                namespace=namespaces.usage_events,
                event=enriched,
            )

    def _build_parser_kwargs(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        trace_log: Callable[[str], None] | None = None,
    ) -> dict[str, object]:
        parser_kwargs: dict[str, object] = {
            "document_id": source_document_id,
            "title": request.title,
            "raw_text": request.raw_text,
            "source_format": request.source_format,
            "mode": request.parser_mode,
        }

        if request.parser_mode == "heuristic":
            return parser_kwargs

        provider = request.llm_provider or self._provider_from_mode(request.parser_mode)
        model = request.llm_model or self._model_from_env(provider)
        if provider is None:
            raise ValueError(f"missing llm provider for parser_mode={request.parser_mode!r}")
        sig = inspect.signature(self.parser)
        params = sig.parameters
        supports_kwargs = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values())

        if "llm_provider" in params or supports_kwargs:
            parser_kwargs["llm_provider"] = provider
        if "model" in params or supports_kwargs:
            parser_kwargs["model"] = model
        if "provider_settings" in params or supports_kwargs:
            parser_kwargs["provider_settings"] = resolve_parser_provider_settings(
                provider=provider,
                model=model,
            )
        if trace_log is not None and ("trace_log" in params or supports_kwargs):
            parser_kwargs["trace_log"] = trace_log

        if not supports_kwargs and not any(
            key in parser_kwargs for key in ("llm_provider", "model", "provider_settings")
        ):
            raise ValueError(
                "Configured parser does not expose llm_provider/model or provider_settings; "
                "upgrade kg-doc-parser or provide a compatible parser callable."
            )

        return parser_kwargs

    def _trace_text(self, message: str) -> None:
        self._trace_event("trace", message=message)

    def _trace_event(self, stage: str, **fields: object) -> None:
        payload = {
            "timestamp_ms": now_ms(),
            "stage": stage,
            **fields,
        }
        if self.debug_trace_path is not None:
            append_jsonl(self.debug_trace_path, payload)
        if self.live_trace_printer is not None:
            self.live_trace_printer.emit(payload)

    def _record_parse_statistics(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        parse_result: ParseSourceResult,
        graph_extraction: GraphExtractionWithIDs,
        parse_runtime_ms: int,
        status: str,
    ) -> None:
        if self.stats_store is None:
            return
        diagnostics = dict(getattr(parse_result, "diagnostics", {}) or {})
        evaluation = dict(getattr(parse_result, "evaluation", {}) or {})
        usage_summary = dict(getattr(parse_result, "usage_summary", {}) or {})
        proposal_summary = dict(diagnostics.get("proposal_summary") or {})
        graph_payload = graph_extraction.model_dump(dump_format="python")
        provider = normalize_provider_name(request.llm_provider or self._provider_from_mode(request.parser_mode))
        record = build_parse_statistics_record(
            workspace_id=request.workspace_id,
            source_document_id=source_document_id,
            source_uri=request.source_uri,
            title=request.title,
            raw_text=request.raw_text,
            parser_lane=request.parser_lane,
            parser_mode=request.parser_mode,
            proposal_mode=str(proposal_summary.get("proposal_mode")) if proposal_summary.get("proposal_mode") is not None else None,
            provider=provider,
            model=request.llm_model or self._model_from_env(provider),
            parse_runtime_ms=parse_runtime_ms,
            semantic_tree=getattr(parse_result, "semantic_tree", request.title),
            graph_payload=graph_payload,
            diagnostics={
                **diagnostics,
                "usage_summary": usage_summary,
                "proposal_summary": proposal_summary,
            },
            evaluation=evaluation,
            status=status,
        )
        self.stats_store.record_parse_run(record)
        self._trace_event(
            "parse_statistics_recorded",
            workspace_id=request.workspace_id,
            source_document_id=source_document_id,
            status=status,
            parse_runtime_ms=parse_runtime_ms,
            node_count=record.node_count,
            edge_count=record.edge_count,
            tree_depth=record.tree_depth,
        )

    def _trace_step(self, stage: str, *, request: IngestPipelineRequest, source_document_id: str, **fields: object) -> None:
        self._trace_event(
            stage,
            workspace_id=request.workspace_id,
            source_document_id=source_document_id,
            **fields,
        )

    @staticmethod
    def _provider_from_mode(mode: str) -> str | None:
        if mode in {"ollama", "gemini", "openai", "azure_openai", "azure"}:
            if mode == "azure_openai":
                return "azure"
            return mode
        return None

    @staticmethod
    def _model_from_env(provider: str | None) -> str | None:
        if provider == "ollama":
            return (
                os.getenv("KOGWISTAR_PARSER_MODEL")
                or os.getenv("OLLAMA_MODEL")
                or os.getenv("KG_DOC_PARSER_MODEL")
            )
        if provider == "gemini":
            return (
                os.getenv("KOGWISTAR_PARSER_MODEL")
                or os.getenv("GEMINI_MODEL")
                or os.getenv("KG_DOC_PARSER_MODEL")
            )
        if provider in {"openai", "azure"}:
            return (
                os.getenv("KOGWISTAR_PARSER_MODEL")
                or os.getenv("OPENAI_MODEL")
                or os.getenv("KG_DOC_PARSER_MODEL")
            )
        return os.getenv("KOGWISTAR_PARSER_MODEL") or os.getenv("KG_DOC_PARSER_MODEL")

    def translate_parse_result(
        self,
        *,
        parse_result: ParseSourceResult,
        source_document_id: str,
    ) -> GraphExtractionWithIDs:
        self._trace_event(
            "translate_parse_result_start",
            source_document_id=source_document_id,
            semantic_title=getattr(parse_result.semantic_tree, "title", None),
        )
        graph_payload = getattr(parse_result, "graph_payload", None)
        if graph_payload is not None:
            payload = dict(graph_payload)
            payload["doc_id"] = source_document_id
            result = GraphExtractionWithIDs.model_validate(payload)
        else:
            payload = semantic_tree_to_kge_payload(parse_result.semantic_tree, doc_id=source_document_id)
            result = GraphExtractionWithIDs.model_validate(payload)
        self._trace_event(
            "translate_parse_result_complete",
            source_document_id=source_document_id,
            node_count=len(getattr(result, "nodes", []) or []),
            edge_count=len(getattr(result, "edges", []) or []),
        )
        return result

    def ingest_parse_result(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        graph_extraction: GraphExtractionWithIDs,
        namespace: str,
    ) -> None:
        source_namespace = self.namespaces_for(request.workspace_id).source_space
        self._trace_step(
            "ingest_parse_result_start",
            request=request,
            source_document_id=source_document_id,
            namespace=namespace,
            node_count=len(getattr(graph_extraction, "nodes", []) or []),
            edge_count=len(getattr(graph_extraction, "edges", []) or []),
        )
        source_parsed = self._source_graph_extraction(
            request=request,
            source_document_id=source_document_id,
            graph_extraction=graph_extraction,
        )
        compatibility_parsed = self._source_graph_extraction(
            request=request,
            source_document_id=source_document_id,
            graph_extraction=graph_extraction,
            legacy_namespace=namespace,
        )

        with _temporary_namespace(self.engines.kg, source_namespace):
            self.engines.kg.persist_document_graph_extraction(
                doc_id=source_document_id,
                parsed=source_parsed,
                mode="append",
            )
        self._trace_step(
            "ingest_parse_result_persisted_source",
            request=request,
            source_document_id=source_document_id,
            namespace=str(source_namespace),
        )
        with _temporary_namespace(self.engines.conversation, namespace):
            self.engines.conversation.persist_document_graph_extraction(
                doc_id=source_document_id,
                parsed=compatibility_parsed,
                mode="append",
            )
        self._trace_step(
            "ingest_parse_result_persisted_compatibility",
            request=request,
            source_document_id=source_document_id,
            namespace=namespace,
        )
        self._project_base_kg_references(
            request=request,
            source_document_id=source_document_id,
            source_namespace=source_namespace,
            graph_extraction=source_parsed,
        )
        self._trace_step(
            "ingest_parse_result_complete",
            request=request,
            source_document_id=source_document_id,
            namespace=namespace,
        )

    def create_maintenance_request(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        namespace: str,
        maintenance_kind: str | None = None,
    ) -> str:
        maintenance_kind = str(maintenance_kind or self._maintenance_kind_for_operation_mode(self._operation_mode(request)))
        self._trace_step(
            "create_maintenance_request_start",
            request=request,
            source_document_id=source_document_id,
            namespace=namespace,
            maintenance_kind=maintenance_kind,
        )
        node_id = str(
            stable_id(
                "kogwistar_llm_wiki.maintenance_request",
                request.workspace_id,
                source_document_id,
                maintenance_kind,
            )
        )
        if not self._node_exists(self.engines.conversation, namespace=namespace, node_id=node_id):
            node = self._artifact_node(
                request=request,
                source_document_id=source_document_id,
                namespace=namespace,
                node_id=node_id,
                artifact_kind="maintenance_job_request",
                lane="background",
                visibility="internal",
                label="Maintenance Job Request",
                summary=f"Maintenance requested for {request.title}",
                extra_metadata={
                    "job_type": "maintenance",
                    "trigger_type": "ingest",
                    "status": "pending",
                    "operation_mode": self._operation_mode(request),
                    "maintenance_kind": maintenance_kind,
                },
            )
            with _temporary_namespace(self.engines.conversation, namespace):
                self.engines.conversation.write.add_node(node)
            request_node_id = str(node.id)
        else:
            request_node_id = node_id
        lane_idempotency_key = str(
            stable_id(
                "kogwistar_llm_wiki.maintenance_request_lane",
                request.workspace_id,
                source_document_id,
                maintenance_kind,
            )
        )
        with _temporary_namespace(self.engines.conversation, namespace):
            existing_messages = self.engines.conversation.read.get_nodes(
                where={
                    "$and": [
                        {"artifact_kind": "lane_message"},
                        {"idempotency_key": lane_idempotency_key},
                    ],
                },
                limit=1,
            )
        existing_lane_message_id = str(existing_messages[0].id) if existing_messages else None
        with _temporary_namespace(self.engines.conversation, namespace):
            lane_message = self.engines.conversation.send_lane_message(
                conversation_id=f"maintenance:{source_document_id}",
                inbox_id="inbox:worker:maintenance",
                sender_id="lane:foreground",
                recipient_id="lane:worker:maintenance",
                msg_type="request.maintenance",
                payload={
                    "workspace_id": request.workspace_id,
                    "request_node_id": request_node_id,
                    "source_document_id": source_document_id,
                    "maintenance_kind": maintenance_kind,
                },
                idempotency_key=lane_idempotency_key,
            )
        lane_message_id = existing_lane_message_id or lane_message.message_id
        if not self._job_exists(
            namespace=self.namespaces_for(request.workspace_id).maintenance_jobs,
            entity_kind="maintenance_job",
            entity_id=source_document_id,
            job_kind=f"maintenance_job:{maintenance_kind}",
            payload_matches={"maintenance_kind": maintenance_kind},
        ):
            self._enqueue_maintenance_job(
                request=request,
                request_node_id=request_node_id,
                source_document_id=source_document_id,
                namespace=self.namespaces_for(request.workspace_id).maintenance_jobs,
                lane_message_id=lane_message_id,
                maintenance_kind=maintenance_kind,
            )
        self._trace_step(
            "create_maintenance_request_complete",
            request=request,
            source_document_id=source_document_id,
            namespace=namespace,
            maintenance_kind=maintenance_kind,
            request_node_id=request_node_id,
            lane_message_id=lane_message_id,
        )
        return request_node_id

    def create_parse_retry_history(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        parse_result: ParseSourceResult,
        namespace: str,
    ) -> str | None:
        """Persist compact parse retry history into the background conversation graph."""
        diagnostics = dict(getattr(parse_result, "diagnostics", {}) or {})
        page_index_diag = dict(diagnostics.get("page_index") or {})
        parser_lane = str(diagnostics.get("parser_lane") or page_index_diag.get("parser_lane") or "page_index")
        assignment_mode = str(page_index_diag.get("assignment_mode") or diagnostics.get("assignment_mode") or "")
        final_outcome = str(page_index_diag.get("final_outcome") or diagnostics.get("final_outcome") or "")
        assignment_attempt_count = int(page_index_diag.get("assignment_attempt_count") or diagnostics.get("assignment_attempt_count") or 0)
        assignment_retry_used = bool(page_index_diag.get("assignment_retry_used") or diagnostics.get("assignment_retry_used"))
        assignment_retry_succeeded = bool(
            page_index_diag.get("assignment_retry_succeeded") or diagnostics.get("assignment_retry_succeeded")
        )
        structure_retry_used = bool(page_index_diag.get("structure_retry_used") or diagnostics.get("structure_retry_used"))
        structure_retry_succeeded = bool(
            page_index_diag.get("structure_retry_succeeded") or diagnostics.get("structure_retry_succeeded")
        )
        retry_used = bool(page_index_diag.get("retry_used") or diagnostics.get("retry_used"))
        retry_succeeded = bool(page_index_diag.get("retry_succeeded") or diagnostics.get("retry_succeeded"))
        fallback_reason = str(page_index_diag.get("fallback_reason") or diagnostics.get("fallback_reason") or "")
        self._trace_step(
            "create_parse_retry_history_check",
            request=request,
            source_document_id=source_document_id,
            namespace=namespace,
            parser_lane=parser_lane,
            assignment_mode=assignment_mode,
            retry_used=retry_used,
            fallback_reason=fallback_reason or None,
        )
        if not (retry_used or fallback_reason or assignment_attempt_count > 1):
            return None

        first_validation_errors = list(page_index_diag.get("first_validation_errors") or diagnostics.get("first_validation_errors") or [])
        retry_validation_errors = list(page_index_diag.get("retry_validation_errors") or diagnostics.get("retry_validation_errors") or [])
        assignment_validation_errors = list(
            page_index_diag.get("assignment_validation_errors") or diagnostics.get("assignment_validation_errors") or []
        )
        structure_validation_errors = list(
            page_index_diag.get("structure_validation_errors") or diagnostics.get("structure_validation_errors") or []
        )
        validation_errors = list(page_index_diag.get("validation_errors") or diagnostics.get("validation_errors") or [])
        history_payload = {
            "workspace_id": request.workspace_id,
            "source_document_id": source_document_id,
            "source_uri": request.source_uri,
            "parser_lane": parser_lane,
            "assignment_mode": assignment_mode,
            "final_outcome": final_outcome or None,
            "assignment_attempt_count": assignment_attempt_count,
            "assignment_retry_used": assignment_retry_used,
            "assignment_retry_succeeded": assignment_retry_succeeded,
            "structure_retry_used": structure_retry_used,
            "structure_retry_succeeded": structure_retry_succeeded,
            "retry_used": retry_used,
            "retry_succeeded": retry_succeeded,
            "fallback_reason": fallback_reason or None,
            "assignment_validation_errors": assignment_validation_errors,
            "structure_validation_errors": structure_validation_errors,
            "first_validation_errors": first_validation_errors,
            "retry_validation_errors": retry_validation_errors,
            "validation_errors": validation_errors,
            "workflow_run_id": diagnostics.get("workflow_run_id") or page_index_diag.get("workflow_run_id"),
            "workflow_status": diagnostics.get("workflow_status") or page_index_diag.get("workflow_status"),
            "retry_prompt_summary": page_index_diag.get("retry_prompt_summary"),
            "structure_retry_prompt_summary": page_index_diag.get("structure_retry_prompt_summary")
            or diagnostics.get("structure_retry_prompt_summary"),
        }
        node_id = str(
            stable_id(
                "kogwistar_llm_wiki.parse_retry_history",
                request.workspace_id,
                source_document_id,
                assignment_mode or "unknown",
                final_outcome or "unknown",
                str(retry_used),
                str(retry_succeeded),
                fallback_reason or "none",
            )
        )
        if self._node_exists(self.engines.conversation, namespace=namespace, node_id=node_id):
            return node_id

        summary = (
            f"Parse retry history for {request.title}: attempts={assignment_attempt_count} "
            f"retry_used={retry_used} retry_succeeded={retry_succeeded} mode={assignment_mode or 'unknown'}"
        )
        node = self._artifact_node(
            request=request,
            source_document_id=source_document_id,
            namespace=namespace,
            node_id=node_id,
            artifact_kind="parse_retry_history",
            lane="background",
            visibility="internal",
            label=f"Parse retry history: {request.title}",
            summary=summary,
            extra_metadata={
                "retry_history_json": json.dumps(history_payload, sort_keys=True, separators=(",", ":")),
                "parser_lane": parser_lane,
                "assignment_mode": assignment_mode,
                "final_outcome": final_outcome or None,
                "assignment_attempt_count": assignment_attempt_count,
                "assignment_retry_used": assignment_retry_used,
                "assignment_retry_succeeded": assignment_retry_succeeded,
                "structure_retry_used": structure_retry_used,
                "structure_retry_succeeded": structure_retry_succeeded,
                "retry_used": retry_used,
                "retry_succeeded": retry_succeeded,
                "fallback_reason": fallback_reason or None,
                "assignment_validation_errors": assignment_validation_errors,
                "structure_validation_errors": structure_validation_errors,
                "first_validation_errors": first_validation_errors,
                "retry_validation_errors": retry_validation_errors,
                "validation_errors": validation_errors,
                "workflow_run_id": history_payload["workflow_run_id"],
                "workflow_status": history_payload["workflow_status"],
            },
        )
        with _temporary_namespace(self.engines.conversation, namespace):
            self.engines.conversation.write.add_node(node)
        self._trace_step(
            "create_parse_retry_history_complete",
            request=request,
            source_document_id=source_document_id,
            namespace=namespace,
            parser_lane=parser_lane,
            assignment_mode=assignment_mode,
            retry_used=retry_used,
            retry_succeeded=retry_succeeded,
        )
        return str(node.id)

    def create_candidate_link(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        parse_result: ParseSourceResult,
        namespace: str,
    ) -> str:
        node_id = str(
            stable_id(
                "kogwistar_llm_wiki.candidate_link",
                request.workspace_id,
                source_document_id,
            )
        )
        self._trace_step(
            "create_candidate_link_start",
            request=request,
            source_document_id=source_document_id,
            namespace=namespace,
            semantic_title=getattr(parse_result.semantic_tree, "title", None),
        )
        if self._node_exists(self.engines.conversation, namespace=namespace, node_id=node_id):
            self._trace_step(
                "create_candidate_link_complete",
                request=request,
                source_document_id=source_document_id,
                namespace=namespace,
                candidate_link_id=node_id,
                existing=True,
            )
            return node_id
        node = self._artifact_node(
            request=request,
            source_document_id=source_document_id,
            namespace=namespace,
            node_id=node_id,
            artifact_kind="candidate_link",
            lane="background",
            visibility="review",
            label=f"Candidate link: {request.title}",
            summary=f"Candidate link derived from {parse_result.semantic_tree.title}",
        )
        with _temporary_namespace(self.engines.conversation, namespace):
            self.engines.conversation.write.add_node(node)
        self._trace_step(
            "create_candidate_link_complete",
            request=request,
            source_document_id=source_document_id,
            namespace=namespace,
            candidate_link_id=str(node.id),
            existing=False,
        )
        return str(node.id)

    def create_promotion_candidate(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        candidate_link_id: str,
        promotion_evidence_pack_id: str | None = None,
        promotion_evidence_pack_digest: dict[str, object] | None = None,
        lineage_node_ids: list[str] | None = None,
        lineage_edge_ids: list[str] | None = None,
        namespace: str,
    ) -> str:
        node_id = str(
            stable_id(
                "kogwistar_llm_wiki.promotion_candidate",
                request.workspace_id,
                source_document_id,
            )
        )
        self._trace_step(
            "create_promotion_candidate_start",
            request=request,
            source_document_id=source_document_id,
            namespace=namespace,
            candidate_link_id=candidate_link_id,
        )
        if self._node_exists(self.engines.conversation, namespace=namespace, node_id=node_id):
            self._trace_step(
                "create_promotion_candidate_complete",
                request=request,
                source_document_id=source_document_id,
                namespace=namespace,
                promotion_candidate_id=node_id,
                existing=True,
            )
            return node_id
        node = self._artifact_node(
            request=request,
            source_document_id=source_document_id,
            namespace=namespace,
            node_id=node_id,
            artifact_kind="promotion_candidate",
            lane="background",
            visibility="review",
            label=f"Promotion candidate: {request.title}",
            summary=f"Promotion candidate linked from {candidate_link_id}",
            extra_metadata={
                "candidate_link_id": candidate_link_id,
                "promotion_evidence_pack_id": promotion_evidence_pack_id,
                "promotion_evidence_pack_digest": _metadata_digest_value(promotion_evidence_pack_digest),
                "promotion_mode": request.promotion_mode,
                "queue_state": "pending",
                "queue_previous_id": None,
                "queue_next_id": None,
                "lineage_source_ids": [source_document_id, candidate_link_id],
                "lineage_node_ids": _metadata_list_value(
                    list(lineage_node_ids or [source_document_id, candidate_link_id])
                ),
                "lineage_edge_ids": _metadata_list_value(list(lineage_edge_ids or [])),
                "review_namespace": self.namespaces_for(request.workspace_id).review,
            },
        )
        with _temporary_namespace(self.engines.conversation, namespace):
            self.engines.conversation.write.add_node(node)
        self._trace_step(
            "create_promotion_candidate_complete",
            request=request,
            source_document_id=source_document_id,
            namespace=namespace,
            promotion_candidate_id=str(node.id),
            existing=False,
        )
        return str(node.id)

    def create_promotion_evidence_pack(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        candidate_link_id: str,
        graph_extraction: GraphExtractionWithIDs,
        namespace: str,
    ) -> tuple[str, dict[str, object]]:
        node_ids = sorted(
            str(node.id) for node in (graph_extraction.nodes or []) if str(getattr(node, "id", "") or "")
        )
        edge_ids = sorted(
            str(edge.id) for edge in (graph_extraction.edges or []) if str(getattr(edge, "id", "") or "")
        )
        digest = EvidencePackDigest(
            node_ids=list(node_ids),
            edge_ids=list(edge_ids),
            depth="parsed_graph_extraction",
            max_chars_per_item=0,
            max_total_chars=0,
        )
        digest.evidence_pack_hash = evidence_pack_digest_hash(digest)
        node_id = str(
            stable_id(
                "kogwistar_llm_wiki.promotion_evidence_pack",
                request.workspace_id,
                source_document_id,
                *node_ids,
                *edge_ids,
            )
        )
        self._trace_step(
            "create_promotion_evidence_pack_start",
            request=request,
            source_document_id=source_document_id,
            namespace=namespace,
            candidate_link_id=candidate_link_id,
            node_count=len(node_ids),
            edge_count=len(edge_ids),
        )
        if self._node_exists(self.engines.conversation, namespace=namespace, node_id=node_id):
            self._trace_step(
                "create_promotion_evidence_pack_complete",
                request=request,
                source_document_id=source_document_id,
                namespace=namespace,
                promotion_evidence_pack_id=node_id,
                existing=True,
            )
            return node_id, digest.model_dump(mode="python")
        node = self._artifact_node(
            request=request,
            source_document_id=source_document_id,
            namespace=namespace,
            node_id=node_id,
            artifact_kind="promotion_evidence_pack",
            lane="background",
            visibility="internal",
            label=f"Promotion evidence pack: {request.title}",
            summary=f"Promotion evidence pack derived from parsed graph for {request.title}",
            extra_metadata={
                "candidate_link_id": candidate_link_id,
                "evidence_role": "promotion",
                "created_from": "parsed_graph_extraction",
                "node_ids": list(node_ids),
                "edge_ids": list(edge_ids),
                "evidence_pack_hash": digest.evidence_pack_hash,
                "promotion_evidence_pack_digest": _metadata_digest_value(digest.model_dump(mode="python")),
            },
        )
        with _temporary_namespace(self.engines.conversation, namespace):
            self.engines.conversation.write.add_node(node)
        self._trace_step(
            "create_promotion_evidence_pack_complete",
            request=request,
            source_document_id=source_document_id,
            namespace=namespace,
            promotion_evidence_pack_id=str(node.id),
            existing=False,
        )
        return str(node.id), digest.model_dump(mode="python")

    def promote_to_knowledge(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        promotion_candidate_id: str,
        promotion_evidence_pack_id: str | None = None,
        promotion_evidence_pack_digest: dict[str, object] | None = None,
        promotion_decision: PromotionDecision | None = None,
        namespace: str,
    ) -> str:
        curated_namespace = self.namespaces_for(request.workspace_id).curated_kg_space
        node_id = str(
            stable_id(
                "kogwistar_llm_wiki.promoted_knowledge",
                request.workspace_id,
                source_document_id,
            )
        )
        self._trace_step(
            "promote_to_knowledge_start",
            request=request,
            source_document_id=source_document_id,
            namespace=namespace,
            promotion_candidate_id=promotion_candidate_id,
            promotion_mode=request.promotion_mode,
        )
        curated_exists = self._node_exists(self.engines.kg, namespace=curated_namespace, node_id=node_id)

        projection_namespace = self.namespaces_for(request.workspace_id).projection_jobs
        promoted_common_metadata = {
            "graph_space": "curated_kg",
            "projection_visible": True,
            "promotion_candidate_id": promotion_candidate_id,
            "promotion_evidence_pack_id": promotion_evidence_pack_id,
            "promotion_evidence_pack_digest": _metadata_digest_value(promotion_evidence_pack_digest),
            "promotion_decision_reason": promotion_decision.reason if promotion_decision else None,
            "promotion_decision_metadata": json.dumps(
                dict(promotion_decision.metadata or {}) if promotion_decision else {},
                sort_keys=True,
                separators=(",", ":"),
            ),
        }

        if not curated_exists:
            node = self._artifact_node(
                request=request,
                source_document_id=source_document_id,
                namespace=curated_namespace,
                node_id=node_id,
                artifact_kind="promoted_knowledge",
                lane="knowledge",
                visibility="projection",
                label=request.title,
                summary=f"Promoted knowledge derived from {request.title}",
                extra_metadata=dict(promoted_common_metadata),
            )
            with _temporary_namespace(self.engines.kg, curated_namespace):
                self.engines.kg.write.add_node(node)
            self._trace_step(
                "promote_to_knowledge_node_written",
                request=request,
                source_document_id=source_document_id,
                namespace=curated_namespace,
                promoted_entity_id=node_id,
            )

        if not self._job_exists(
            namespace=projection_namespace,
            entity_kind="projection_request",
            entity_id=node_id,
            job_kind="projection_request",
        ):
            self._enqueue_projection_job(
                request=request,
                promoted_id=node_id,
                namespace=projection_namespace,
            )
        self._trace_step(
            "promote_to_knowledge_complete",
            request=request,
            source_document_id=source_document_id,
            namespace=namespace,
            promoted_entity_id=node_id,
            curated_exists=curated_exists,
        )
        return node_id

    def _enqueue_maintenance_job(
        self,
        *,
        request: IngestPipelineRequest,
        request_node_id: str,
        source_document_id: str,
        namespace: str,
        lane_message_id: str | None = None,
        maintenance_kind: str = "distill",
    ) -> str:
        payload = {
            "workspace_id": request.workspace_id,
            "request_node_id": request_node_id,
            "source_document_id": source_document_id,
            "maintenance_kind": maintenance_kind,
            "lane_message_id": lane_message_id,
        }
        job_id = request_node_id
        self.engines.conversation.jobs.require_available(enqueue=True)
        self.engines.conversation.jobs.enqueue(
            job_id=job_id,
            namespace=namespace,
            entity_kind="maintenance_job",
            entity_id=source_document_id,
            job_kind=f"maintenance_job:{maintenance_kind}",
            op="UPSERT",
            payload=payload,
        )
        return job_id

    def _enqueue_projection_job(
        self,
        *,
        request: IngestPipelineRequest,
        promoted_id: str,
        namespace: str,
    ) -> str:
        job_id = str(
            stable_id(
                "kogwistar_llm_wiki.projection_request",
                request.workspace_id,
                promoted_id,
            )
        )
        payload = {
            "workspace_id": request.workspace_id,
            "promoted_entity_id": promoted_id,
            "promotion_mode": request.promotion_mode,
        }
        self.engines.conversation.jobs.require_available(enqueue=True)
        self.engines.conversation.jobs.enqueue(
            job_id=job_id,
            namespace=namespace,
            entity_kind="projection_request",
            entity_id=promoted_id,
            job_kind="projection_request",
            op="UPSERT",
            payload=payload,
        )
        return job_id

    def build_projection_snapshot(
        self,
        workspace_id: str,
        *,
        graph_spaces: list[GraphSpace | str] | None = None,
        projection_filter: str | None = None,
    ) -> ProjectionSnapshot:
        return self.projection.build_projection_snapshot(
            workspace_id,
            graph_spaces=graph_spaces,
            projection_filter=projection_filter,
        )

    def query_nodes(
        self,
        *,
        workspace_id: str,
        graph_spaces: list[GraphSpace | str],
        where: Mapping[str, object] | None = None,
        resolve_mode: str = "pointer_only",
    ) -> list[GraphSpaceQueryResult]:
        return self.query_service.get_nodes(
            workspace_id=workspace_id,
            graph_spaces=graph_spaces,
            where=where,
            resolve_mode=resolve_mode,
        )

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
        extra_metadata: dict[str, object] | None = None,
    ) -> Node:
        span = self._leading_span(source_document_id, request.raw_text, insertion_method=artifact_kind)
        extra_meta = dict(extra_metadata or {})
        normalized_visibility = self.policies.visibility.visibility_for(
            {
                "artifact_kind": artifact_kind,
                "visibility": visibility,
                "projection_visible": extra_meta.get("projection_visible"),
            }
        )
        metadata = {
            "workspace_id": request.workspace_id,
            "source_document_id": source_document_id,
            "source_uri": request.source_uri,
            "artifact_kind": artifact_kind,
            "namespace": namespace,
            "conversation_lane": lane,
            "visibility": normalized_visibility,
            "title": request.title,
            "parser_mode": request.parser_mode,
            "requires_provenance": self.policies.lifecycle.requires_provenance(artifact_kind),
        }
        if normalized_visibility == "projection":
            metadata["projection_visible"] = True
        if extra_meta:
            metadata.update(extra_meta)
        return Node(
            id=node_id,
            label=label,
            type="entity",
            summary=summary,
            doc_id=source_document_id,
            mentions=[{
                "spans": [span.model_dump(field_mode="backend")]
            }],
            metadata=metadata,
        )

    def _node_exists(self, engine: GraphKnowledgeEngine, *, namespace: str, node_id: str) -> bool:
        with _temporary_namespace(engine, namespace):
            return bool(engine.read.node_exists(ids=[str(node_id)]))

    def _job_exists(
        self,
        *,
        namespace: str,
        entity_kind: str,
        entity_id: str,
        job_kind: str,
        payload_matches: Mapping[str, object] | None = None,
    ) -> bool:
        jobs = self.engines.conversation.jobs.list(namespace=namespace, limit=10_000)
        for job in jobs:
            if (
                str(job.entity_kind) == str(entity_kind)
                and str(job.entity_id) == str(entity_id)
                and str(job.job_kind) == str(job_kind)
            ):
                if payload_matches:
                    payload = dict(job.payload)
                    if any(payload.get(key) != expected for key, expected in payload_matches.items()):
                        continue
                return True
        return False

    def _leading_span(self, source_document_id: str, raw_text: str, *, insertion_method: str) -> Span:
        excerpt = (raw_text or " ")[:1]
        return Span.model_validate(
            {
                "collection_page_url": f"document_collection/{source_document_id}",
                "document_page_url": f"document/{source_document_id}",
                "doc_id": source_document_id,
                "insertion_method": insertion_method,
                "page_number": 1,
                "start_char": 0,
                "end_char": 1,
                "excerpt": excerpt,
                "context_before": "",
                "context_after": raw_text[1:81] if len(raw_text) > 1 else "",
                "chunk_id": None,
                "source_cluster_id": None,
            }
        )

    def _source_graph_extraction(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        graph_extraction: GraphExtractionWithIDs,
        legacy_namespace: str | None = None,
    ) -> GraphExtractionWithIDs:
        enriched = graph_extraction.model_copy(deep=True)
        metadata = {
            "workspace_id": request.workspace_id,
            "graph_space": "source",
            "source_document_id": source_document_id,
            "source_uri": request.source_uri,
        }
        for node in enriched.nodes:
            node_metadata = dict(getattr(node, "metadata", {}) or {})
            node_metadata.update(metadata)
            if legacy_namespace is not None:
                node_metadata["legacy_namespace"] = legacy_namespace
            node.metadata = node_metadata
        for edge in enriched.edges:
            edge_metadata = dict(getattr(edge, "metadata", {}) or {})
            edge_metadata.update(metadata)
            if legacy_namespace is not None:
                edge_metadata["legacy_namespace"] = legacy_namespace
            edge.metadata = edge_metadata
        return enriched

    def _base_kg_reference_span(self, *, request: IngestPipelineRequest, source_document_id: str, insertion_method: str) -> Span:
        return Span.model_validate(
            {
                "collection_page_url": f"document_collection/{source_document_id}",
                "document_page_url": f"document/{source_document_id}",
                "doc_id": source_document_id,
                "insertion_method": insertion_method,
                "page_number": 1,
                "start_char": 0,
                "end_char": 1,
                "excerpt": "",
                "context_before": "",
                "context_after": "",
                "chunk_id": None,
                "source_cluster_id": None,
            }
        )

    def _project_base_kg_references(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        source_namespace: str,
        graph_extraction: GraphExtractionWithIDs,
    ) -> None:
        base_namespace = self.namespaces_for(request.workspace_id).base_kg_space
        node_id_map: dict[str, str] = {}

        for node in graph_extraction.nodes or []:
            source_id = str(getattr(node, "id", "") or "")
            if not source_id:
                continue
            ref_id = logical_ref_id(
                scope=request.workspace_id,
                pointer_kind="base_kg_node",
                logical_ref=LogicalRef(
                    target_namespace=source_namespace,
                    target_kind="node",
                    target_id=source_id,
                ),
            )
            node_id_map[source_id] = ref_id
            payload = build_reference_node_payload(
                logical_ref=LogicalRef(
                    target_namespace=source_namespace,
                    target_kind="node",
                    target_id=source_id,
                ),
                pointer_kind="base_kg_node",
                pointer_id=ref_id,
                label=str(getattr(node, "label", "") or ""),
                summary=str(getattr(node, "summary", "") or ""),
                graph_space="base_kg",
                extra_properties={
                    "artifact_kind": "base_kg_reference",
                    "workspace_id": request.workspace_id,
                    "source_document_id": source_document_id,
                    "source_namespace": source_namespace,
                    "source_uri": request.source_uri,
                    "knowledge_layer": "base_kg",
                    "extraction_status": "machine_extracted",
                    "verification_status": "unverified",
                },
                extra_metadata={
                    "artifact_kind": "base_kg_reference",
                    "workspace_id": request.workspace_id,
                    "source_document_id": source_document_id,
                    "source_namespace": source_namespace,
                    "source_uri": request.source_uri,
                    "knowledge_layer": "base_kg",
                    "extraction_status": "machine_extracted",
                    "verification_status": "unverified",
                },
            )
            base_node = Node(
                **payload,
                doc_id=source_document_id,
                mentions=[
                    Grounding(
                        spans=[
                            self._base_kg_reference_span(
                                request=request,
                                source_document_id=source_document_id,
                                insertion_method="base_kg_projection",
                            )
                        ]
                    )
                ],
                domain_id=None,
                canonical_entity_id=None,
                embedding=None,
            )
            with _temporary_namespace(self.engines.kg, base_namespace):
                self.engines.kg.write.add_node(base_node)

        for edge in graph_extraction.edges or []:
            source_edge_id = str(getattr(edge, "id", "") or "")
            if not source_edge_id:
                continue
            ref_id = logical_ref_id(
                scope=request.workspace_id,
                pointer_kind="base_kg_edge",
                logical_ref=LogicalRef(
                    target_namespace=source_namespace,
                    target_kind="edge",
                    target_id=source_edge_id,
                ),
            )
            source_ids = [
                node_id_map.get(str(source_id), str(source_id))
                for source_id in list(getattr(edge, "source_ids", []) or [])
            ]
            target_ids = [
                node_id_map.get(str(target_id), str(target_id))
                for target_id in list(getattr(edge, "target_ids", []) or [])
            ]
            payload = build_reference_edge_payload(
                logical_ref=LogicalRef(
                    target_namespace=source_namespace,
                    target_kind="edge",
                    target_id=source_edge_id,
                ),
                pointer_kind="base_kg_edge",
                pointer_id=ref_id,
                source_ids=source_ids,
                target_ids=target_ids,
                relation=str(getattr(edge, "relation", "") or getattr(edge, "type", "") or "base_kg_edge"),
                label=str(getattr(edge, "label", "") or "base_kg_edge"),
                summary=str(getattr(edge, "summary", "") or ""),
                graph_space="base_kg",
                extra_properties={
                    "artifact_kind": "base_kg_reference",
                    "workspace_id": request.workspace_id,
                    "source_document_id": source_document_id,
                    "source_namespace": source_namespace,
                    "source_uri": request.source_uri,
                    "knowledge_layer": "base_kg",
                    "extraction_status": "machine_extracted",
                    "verification_status": "unverified",
                },
                extra_metadata={
                    "artifact_kind": "base_kg_reference",
                    "workspace_id": request.workspace_id,
                    "source_document_id": source_document_id,
                    "source_namespace": source_namespace,
                    "source_uri": request.source_uri,
                    "knowledge_layer": "base_kg",
                    "extraction_status": "machine_extracted",
                    "verification_status": "unverified",
                },
            )
            base_edge = type(edge)(
                **payload,
                doc_id=source_document_id,
                mentions=[
                    Grounding(
                        spans=[
                            self._base_kg_reference_span(
                                request=request,
                                source_document_id=source_document_id,
                                insertion_method="base_kg_projection",
                            )
                        ]
                    )
                ],
                domain_id=None,
                canonical_entity_id=None,
                embedding=None,
                source_edge_ids=[],
                target_edge_ids=[],
            )
            with _temporary_namespace(self.engines.kg, base_namespace):
                self.engines.kg.write.add_edge(base_edge)
