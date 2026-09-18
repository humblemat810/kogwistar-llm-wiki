"""Ingestion-run transaction, tracing, and parse statistics."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Protocol

from kg_doc_parser.semantic_document_splitting_layerwise_edits import (
    parser_llm_cache_transaction,
)
from kogwistar.engine_core.models import GraphExtractionWithIDs

from ..debug_run import append_jsonl, build_parse_statistics_record, now_ms
from ..models import IngestPipelineArtifacts, IngestPipelineRequest
from ..provider_config import normalize_provider_name


class SemanticTreeLike(Protocol):
    title: str


class ParseSourceResult(Protocol):
    semantic_tree: SemanticTreeLike


ParserFn = Callable[..., ParseSourceResult]


class IngestRunMixin:
    """Run one ingestion transaction and expose its diagnostics."""

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
        revision = self.source_revision(request=request, source_document_id=source_document_id)
        parse_document_id = self._parse_document_id_for_request(
            request=request,
            source_document_id=source_document_id,
            revision=revision,
        )
        if operation_mode in {"maintenance_first", "hybrid"} or request.parser_lane == "workflow_layered":
            parse_limits = self._durable_parse_limits(request)
            self.initialize_durable_parse_session(
                request=request,
                source_document_id=source_document_id,
                revision_document_id=parse_document_id,
                revision=revision,
                **parse_limits,
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

        # Parser LLM outputs stay transaction-local until the canonical graph
        # write succeeds. A rejected tree or failed persistence must be retried.
        with parser_llm_cache_transaction() as parser_cache_transaction:
            parse_started_at = time.perf_counter()
            parse_result = self.parse_source(
                request=request,
                source_document_id=parse_document_id,
            )
            parse_runtime_ms = max(0, int((time.perf_counter() - parse_started_at) * 1000))
            self.create_parse_retry_history(
                request=request,
                source_document_id=parse_document_id,
                parse_result=parse_result,
                namespace=ns.conv_bg,
            )
            graph_extraction = self.translate_parse_result(
                parse_result=parse_result,
                source_document_id=parse_document_id,
            )
            self.ingest_parse_result(
                request=request,
                source_document_id=parse_document_id,
                graph_extraction=graph_extraction,
                namespace=ns.conv_fg,
            )
            promoted_cache_entries = parser_cache_transaction.promote()
        self._trace_event(
            "parser_llm_cache_promoted",
            workspace_id=request.workspace_id,
            source_document_id=source_document_id,
            entry_count=promoted_cache_entries,
        )
        self.record_source_readiness(
            request=request,
            source_document_id=source_document_id,
            stage="parsed_graph_persisted",
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
        self.telemetry.instrument_event(payload)

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

    def _trace_step(
        self,
        stage: str,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        **fields: object,
    ) -> None:
        self._trace_event(
            stage,
            workspace_id=request.workspace_id,
            source_document_id=source_document_id,
            **fields,
        )
