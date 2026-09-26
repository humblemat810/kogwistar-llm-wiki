"""Maintenance request, promotion, and queue orchestration for ingestion."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Protocol

from kogwistar.engine_core.models import (
    GraphExtractionWithIDs,
)
from kogwistar.id_provider import stable_id
from kogwistar.policy import PromotionDecision
from kogwistar.provenance import EvidencePackDigest, evidence_pack_digest_hash

from ..maintenance.maintenance_context import (
    bound_maintenance_context,
    maintenance_execution_active,
)
from ..maintenance.maintenance_guards import (
    required_stage_for_maintenance,
)
from ..maintenance.maintenance_planner import DEFAULT_DOCUMENT_MAINTENANCE_PLAN
from ..configuration.identity import durable_claims_snapshot
from ..models import (
    IngestPipelineRequest,
)
from ..parsing.parse_views import (
    ParseTarget,
    ParseViewStore,
    parse_session_id,
    reparse_session_id,
)
from ..utils import _temporary_namespace


class ParseSourceResult(Protocol):
    """Minimal parser result contract needed by maintenance evidence methods."""

    semantic_tree: object


def _metadata_digest_value(digest: dict[str, object] | None) -> str | None:
    if digest is None:
        return None
    return json.dumps(digest, sort_keys=True, separators=(",", ":"))


def _metadata_list_value(items: list[str] | None) -> list[str] | None:
    if not items:
        return None
    return list(items)


class MaintenanceRequestMixin:
    """Methods that turn ingestion outcomes into durable maintenance work."""

    def create_maintenance_request(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        namespace: str,
        maintenance_kind: str | None = None,
        topic: str | None = None,
        objective: str | None = None,
        budgets: Mapping[str, object] | None = None,
        seed_node_ids: Sequence[str] | None = None,
        maintenance_context: Mapping[str, object] | None = None,
        max_rounds: int | None = None,
        parse_target: Mapping[str, object] | ParseTarget | None = None,
    ) -> str:
        if maintenance_execution_active():
            raise RuntimeError(
                "maintenance execution cannot create a new maintenance request; "
                "follow-up phases must reuse the current leased job"
            )
        maintenance_kind = str(maintenance_kind or self._maintenance_kind_for_operation_mode(self._operation_mode(request)))
        seed_node_ids = [str(value) for value in (seed_node_ids or [source_document_id]) if str(value).strip()]
        topic = str(topic or "").strip() or None
        revision = self.source_revision(request=request, source_document_id=source_document_id)
        target = (
            parse_target
            if isinstance(parse_target, ParseTarget)
            else ParseTarget.model_validate(parse_target)
            if parse_target is not None
            else None
        )
        if target is not None:
            if maintenance_kind != "document_reparse_region":
                raise ValueError("parse_target requires maintenance_kind='document_reparse_region'")
            if (
                target.source_document_id != source_document_id
                or target.source_revision_id != revision.revision_id
                or target.revision_document_id != (revision.revision_document_id or source_document_id)
            ):
                raise ValueError("parse_target must match the current immutable source revision")
            if target.generation_member_id:
                active_view = ParseViewStore(
                    self.engines.conversation.meta_sqlite,
                    workspace_id=request.workspace_id,
                ).get(source_document_id)
                if active_view is None:
                    raise ValueError(
                        "legacy_evidence_unavailable: generation_member_id requires an active ParseView"
                    )
                selected_member = next(
                    (
                        selection
                        for selection in active_view.selections
                        if selection.member_id == target.generation_member_id
                    ),
                    None,
                )
                if selected_member is None or selected_member.region != target.region:
                    raise ValueError(
                        "generation_member_id must identify the active member with the requested region"
                    )
        if target is None:
            layered_session_id = parse_session_id(
                workspace_id=request.workspace_id,
                source_document_id=source_document_id,
                source_revision_id=revision.revision_id,
                parser_profile=request.parser_lane,
            )
        else:
            layered_session_id = reparse_session_id(
                workspace_id=request.workspace_id,
                source_document_id=source_document_id,
                source_revision_id=revision.revision_id,
                parser_profile=target.parser_profile,
                region=target.region,
                llm_provider=target.llm_provider,
                llm_model=target.llm_model,
                model_version=target.model_version,
                prompt_version=target.prompt_version,
                parser_version=target.parser_version,
            )
            # A targeted reparse may be requested for a legacy parse-first
            # source. Seed grounding first so the normal revision guard is
            # satisfied without rewriting source evidence.
            self.seed_source_map(
                request=request,
                source_document_id=source_document_id,
                namespace=namespace,
            )
            target_request = request.model_copy(
                update={
                    "llm_provider": target.llm_provider or request.llm_provider,
                    "llm_model": target.llm_model or request.llm_model,
                }
            )
            self.initialize_durable_parse_session(
                request=target_request,
                source_document_id=source_document_id,
                revision_document_id=target.revision_document_id,
                revision=revision,
                parser_profile=(
                    f"reparse:{target.parser_profile}:{target.region.start_char}:{target.region.end_char}"
                ),
                parser_version=target.parser_version,
                model_version=target.model_version,
                prompt_version=target.prompt_version,
                initial_region=target.region,
                session_id_override=layered_session_id,
            )
        required_stage = required_stage_for_maintenance(maintenance_kind)
        request_fingerprint = str(
            stable_id(
                "kogwistar_llm_wiki.maintenance_request_parameters",
                objective or "",
                json.dumps(dict(budgets or {}), sort_keys=True, default=str),
                topic or "",
                json.dumps(sorted(seed_node_ids), separators=(",", ":")),
                json.dumps(bound_maintenance_context(maintenance_context), sort_keys=True),
                target.model_dump_json() if target is not None else "",
            )
        )
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
                revision.revision_id,
                request_fingerprint,
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
                    "source_revision_id": revision.revision_id,
                    "source_digest": revision.source_digest,
                    "revision_document_id": revision.revision_document_id or source_document_id,
                    "parse_session_id": layered_session_id,
                    "required_stage": required_stage,
                    "objective": objective,
                    "topic": topic,
                    "mode": "request",
                    "maintenance_origin": "request",
                    "selection_strategy": "connected_semantic_evidence_history",
                    "seed_node_ids": seed_node_ids,
                    "maintenance_context": _metadata_digest_value(bound_maintenance_context(maintenance_context)),
                    "maintenance_max_rounds": max(0, int(max_rounds or 0)),
                    "request_fingerprint": request_fingerprint,
                    "budgets": _metadata_digest_value(dict(budgets or {})),
                    "parse_target": target.model_dump(mode="json") if target is not None else None,
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
                revision.revision_id,
                request_fingerprint,
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
                    "source_revision_id": revision.revision_id,
                    "source_digest": revision.source_digest,
                    "revision_document_id": revision.revision_document_id or source_document_id,
                    "parse_session_id": layered_session_id,
                    "required_stage": required_stage,
                    "objective": objective,
                    "topic": topic,
                    "mode": "request",
                    "maintenance_origin": "request",
                    "selection_strategy": "connected_semantic_evidence_history",
                    "seed_node_ids": seed_node_ids,
                    "maintenance_context": bound_maintenance_context(maintenance_context),
                    "maintenance_max_rounds": max(0, int(max_rounds or 0)),
                    "request_fingerprint": request_fingerprint,
                    "budgets": dict(budgets or {}),
                    "parse_target": target.model_dump(mode="json") if target is not None else None,
                },
                idempotency_key=lane_idempotency_key,
            )
        lane_message_id = existing_lane_message_id or lane_message.message_id
        self._supersede_stale_maintenance_jobs(
            namespace=self.namespaces_for(request.workspace_id).maintenance_jobs,
            source_document_id=source_document_id,
            maintenance_kind=maintenance_kind,
            current_revision_id=revision.revision_id,
        )
        if not self._job_exists(
            namespace=self.namespaces_for(request.workspace_id).maintenance_jobs,
            entity_kind="maintenance_job",
            entity_id=source_document_id,
            job_kind=f"maintenance_job:{maintenance_kind}",
            payload_matches={
                "maintenance_kind": maintenance_kind,
                "source_revision_id": revision.revision_id,
                "request_fingerprint": request_fingerprint,
            },
        ):
            self._enqueue_maintenance_job(
                request=request,
                request_node_id=request_node_id,
                source_document_id=source_document_id,
                namespace=self.namespaces_for(request.workspace_id).maintenance_jobs,
                lane_message_id=lane_message_id,
                maintenance_kind=maintenance_kind,
                source_revision_id=revision.revision_id,
                source_digest=revision.source_digest,
                revision_document_id=revision.revision_document_id or source_document_id,
                required_stage=required_stage,
                objective=objective,
                budgets=budgets,
                topic=topic,
                seed_node_ids=seed_node_ids,
                maintenance_context=maintenance_context,
                max_rounds=max_rounds,
                parse_target=target,
                parse_session_id_override=layered_session_id,
            )
        self._trace_step(
            "create_maintenance_request_complete",
            request=request,
            source_document_id=source_document_id,
            namespace=namespace,
            maintenance_kind=maintenance_kind,
            request_node_id=request_node_id,
            lane_message_id=lane_message_id,
            source_revision_id=revision.revision_id,
            required_stage=required_stage,
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
        source_revision_id: str = "",
        source_digest: str = "",
        revision_document_id: str = "",
        required_stage: str = "parsed_graph_persisted",
        objective: str | None = None,
        budgets: Mapping[str, object] | None = None,
        topic: str | None = None,
        seed_node_ids: Sequence[str] | None = None,
        maintenance_context: Mapping[str, object] | None = None,
        max_rounds: int | None = None,
        parse_target: ParseTarget | None = None,
        parse_session_id_override: str | None = None,
    ) -> str:
        if maintenance_execution_active():
            raise RuntimeError(
                "maintenance execution cannot enqueue a new maintenance job; "
                "follow-up phases must reuse the current leased job"
            )
        payload = {
            "workspace_id": request.workspace_id,
            "request_node_id": request_node_id,
            "source_document_id": source_document_id,
            "maintenance_kind": maintenance_kind,
            "topic": str(topic or "").strip() or None,
            "mode": "request",
            "selection_strategy": "connected_semantic_evidence_history",
            "seed_node_ids": [str(value) for value in (seed_node_ids or [source_document_id]) if str(value).strip()],
            "maintenance_context": bound_maintenance_context(maintenance_context),
            "maintenance_round": 0,
            "maintenance_max_rounds": max(0, int(max_rounds or 0)),
            "lane_message_id": lane_message_id,
            "source_revision_id": source_revision_id,
            "source_digest": source_digest,
            "revision_document_id": revision_document_id or source_document_id,
            "parse_session_id": parse_session_id_override or parse_session_id(
                workspace_id=request.workspace_id,
                source_document_id=source_document_id,
                source_revision_id=source_revision_id,
                parser_profile=request.parser_lane,
            ),
            "required_stage": required_stage,
            "objective": objective,
            "budgets": dict(budgets or {}),
            "parse_target": parse_target.model_dump(mode="json") if parse_target is not None else None,
            "durable_layered_parse": bool(
                request.parser_lane == "workflow_layered" or parse_target is not None
            ),
            "authority_claims": durable_claims_snapshot(),
        }
        if request.operation_mode == "maintenance_first" and maintenance_kind == "document_seed_graph":
            payload.update(
                {
                    "maintenance_plan": list(DEFAULT_DOCUMENT_MAINTENANCE_PLAN),
                    "maintenance_phase_index": 0,
                    "maintenance_round": 0,
                    "maintenance_max_rounds": max(0, int(max_rounds or 0)),
                }
            )
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

    def _supersede_stale_maintenance_jobs(
        self,
        *,
        namespace: str,
        source_document_id: str,
        maintenance_kind: str,
        current_revision_id: str,
    ) -> None:
        """Fence queued work from an older ingestion attempt.

        The queue remains append-only/auditable: an old job is terminally
        failed as superseded rather than deleted or reused for a new source
        revision.
        """
        for job in self.engines.conversation.jobs.list(
            namespace=namespace,
            status="PENDING",
            limit=10_000,
        ):
            if (
                str(job.entity_id) != source_document_id
                or str(job.job_kind) != f"maintenance_job:{maintenance_kind}"
                or str(job.payload.get("source_revision_id") or "") == current_revision_id
            ):
                continue
            self.engines.conversation.jobs.mark_failed(
                job.job_id,
                f"superseded_by_source_revision:{current_revision_id}",
                final=True,
            )

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
