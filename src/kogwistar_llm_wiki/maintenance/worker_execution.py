"""Maintenance planning, guards, patches, and runtime execution."""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import time
from collections.abc import Mapping
from dataclasses import replace

from kogwistar.engine_core.models import Grounding, Node, Span
from kogwistar.id_provider import stable_id
from kogwistar.runtime.budget import (
    BudgetEvent,
    BudgetExhaustedError,
    StateBackedBudgetLedger,
)
from kogwistar.runtime.models import RunSuccess
from kogwistar.server.auth_middleware import can_access_security_scope
from kogwistar.utils import source_pointer_has_character_span, validate_source_pointer

from ..configuration.identity import runtime_authority_context
from ..configuration.workspace import WorkspaceNamespaces
from ..maintenance import (
    MaintenanceJobExecutionContext,
    workflow_id_for_maintenance_kind,
)
from ..maintenance.crosslink_proposals import (
    CrosslinkCriticResponse,
    CrosslinkEvidence,
    CrosslinkProposalResponse,
)
from ..maintenance.maintenance_context import (
    append_maintenance_round,
    bound_maintenance_context,
)
from ..maintenance.maintenance_designs import (
    materialize_maintenance_designs as _default_materialize_maintenance_designs,
)
from ..maintenance.maintenance_guards import (
    MaintenanceGuardDecision,
    SourceRevision,
    evaluate_maintenance_guard,
    evaluate_parse_session_guard,
    required_stage_for_maintenance,
    source_digest,
)
from ..maintenance.maintenance_patch_apply import (
    apply_maintenance_patch_for_scope as _default_apply_maintenance_patch_for_scope,
)
from ..maintenance.maintenance_patches import (
    MaintenanceIntent,
    MaintenanceOperationKind,
    MaintenancePatch,
    MaintenancePatchOperation,
    MaintenanceProvenance,
    MaintenanceScope,
)
from ..maintenance.maintenance_planner import decide_next_maintenance_phase
from ..usage.provider import ProviderUsageCallback, resolve_token_pricing
from ..utils import _background_namespace, _temporary_namespace
from .dependency_planning import (
    DependencyInvalidationPlan,
    plan_dependency_invalidation,
)
from .state import (
    and_where as _and_where,
)
from .state import (
    durable_maintenance_usage as _durable_maintenance_usage,
)
from .state import (
    maintenance_budget_state as _maintenance_budget_state,
)
from .state import metadata_mapping
from .state import (
    persisted_budget_state as _persisted_budget_state,
)

_CROSSLINK_STAGE_FIELDS = frozenset({
    "group_id",
    "artifact_id",
    "operation_count",
    "group_count",
    "approval_mode",
    "critic_verdict",
    "pending_groups",
    "automatic_groups",
    "rejected_groups",
})

logger = logging.getLogger(__name__)


def materialize_maintenance_designs(workflow_engine: object) -> object:
    """Resolve the legacy worker hook so existing monkeypatches remain effective."""
    from .. import worker as worker_module

    callback = getattr(worker_module, "materialize_maintenance_designs", _default_materialize_maintenance_designs)
    return callback(workflow_engine)


def apply_maintenance_patch_for_scope(*args: object, **kwargs: object) -> object:
    """Resolve the legacy worker hook so existing monkeypatches remain effective."""
    from .. import worker as worker_module

    callback = getattr(worker_module, "apply_maintenance_patch_for_scope", _default_apply_maintenance_patch_for_scope)
    return callback(*args, **kwargs)


class MaintenanceExecutionWorkerMixin:
    """Methods for bounded maintenance planning and runtime execution."""

    def _maintenance_run_identity(self, ctx: MaintenanceJobExecutionContext) -> dict[str, object]:
        """Return the stable identity carried by every cross-link run record."""

        payload = ctx.payload
        attempt = int(
            payload.get("maintenance_attempt")
            or payload.get("attempt_count")
            or getattr(ctx.job, "claim_attempts", 0)
            or 1
        )
        run_id = str(payload.get("maintenance_run_id") or "").strip()
        if not run_id:
            run_id = str(
                stable_id(
                    "maintenance_run",
                    ctx.workspace_id,
                    ctx.request_node_id,
                    ctx.job_id,
                    attempt,
                )
            )
        return {
            "maintenance_run_id": run_id,
            "workspace_id": ctx.workspace_id,
            "request_node_id": ctx.request_node_id,
            "job_id": ctx.job_id,
            "worker_id": str(getattr(self, "worker_id", "maintenance-worker")),
            "attempt": attempt,
            "maintenance_kind": ctx.maintenance_kind,
            "workflow_version": "crosslink-groups.v1",
        }

    @staticmethod
    def _crosslink_resource_keys(patch: MaintenancePatch) -> tuple[str, ...]:
        keys: set[str] = set()
        for operation in patch.operations:
            for prefix, value in (
                ("node", operation.from_node_id),
                ("node", operation.to_node_id),
                ("node", operation.node_id),
                ("edge", operation.edge_id),
                ("edge", operation.tombstone_target_id),
                ("edge", operation.properties.get("supersedes_edge_id")),
            ):
                if value:
                    keys.add(f"{prefix}:{value}")
            if operation.provenance:
                for pointer in operation.provenance.source_pointers:
                    document_id = str(pointer.get("doc_id") or pointer.get("source_document_id") or "").strip()
                    revision_id = str(pointer.get("source_revision_id") or pointer.get("revision_id") or "").strip()
                    if document_id and revision_id:
                        keys.add(f"source:{document_id}:{revision_id}")
        return tuple(sorted(keys))

    def _acquire_crosslink_resource_locks(
        self, ctx: MaintenanceJobExecutionContext, patch: MaintenancePatch
    ) -> tuple[str, ...] | None:
        """Claim conflicting cross-link resources using durable CAS projections."""

        keys = self._crosslink_resource_keys(patch)
        if not keys:
            return ()
        store = self.engines.conversation.meta_sqlite
        namespace = f"{WorkspaceNamespaces(ctx.workspace_id).maintenance_jobs}:resource_locks"
        owner = str(self._maintenance_run_identity(ctx)["maintenance_run_id"])
        now = int(time.time() * 1000)
        expires = now + 180_000
        updates: list[dict[str, object]] = []
        for key in keys:
            current = store.get_named_projection(namespace, key)
            if current is not None:
                current_payload = current.get("payload") or {}
                current_owner = str(current_payload.get("owner_run_id") or "")
                current_expires = int(current_payload.get("expires_at_ms") or 0)
                if current_owner and current_owner != owner and current_expires > now:
                    return None
                expected_authoritative = int(current.get("last_authoritative_seq") or 0)
                expected_materialized = int(current.get("last_materialized_seq") or 0)
            else:
                expected_authoritative = expected_materialized = None
            updates.append({
                "namespace": namespace,
                "key": key,
                "payload": {
                    "owner_run_id": owner,
                    "workspace_id": ctx.workspace_id,
                    "resource_key": key,
                    "expires_at_ms": expires,
                },
                "expected_last_authoritative_seq": expected_authoritative,
                "expected_last_materialized_seq": expected_materialized,
                "last_authoritative_seq": now,
                "last_materialized_seq": now,
                "projection_schema_version": 1,
                "materialization_status": "ready",
            })
        if not store.compare_and_swap_named_projections(updates):
            return None
        return keys

    def _release_crosslink_resource_locks(
        self, ctx: MaintenanceJobExecutionContext, keys: tuple[str, ...]
    ) -> None:
        if not keys:
            return
        store = self.engines.conversation.meta_sqlite
        namespace = f"{WorkspaceNamespaces(ctx.workspace_id).maintenance_jobs}:resource_locks"
        owner = str(self._maintenance_run_identity(ctx)["maintenance_run_id"])
        now = int(time.time() * 1000)
        updates: list[dict[str, object]] = []
        for key in keys:
            current = store.get_named_projection(namespace, key)
            if current is None or str((current.get("payload") or {}).get("owner_run_id") or "") != owner:
                continue
            current_seq = int(current.get("last_authoritative_seq") or 0)
            current_materialized = int(current.get("last_materialized_seq") or 0)
            updates.append({
                "namespace": namespace,
                "key": key,
                "payload": {
                    "owner_run_id": "",
                    "workspace_id": ctx.workspace_id,
                    "resource_key": key,
                    "expires_at_ms": 0,
                },
                "expected_last_authoritative_seq": current_seq,
                "expected_last_materialized_seq": current_materialized,
                "last_authoritative_seq": now,
                "last_materialized_seq": now,
                "projection_schema_version": 1,
                "materialization_status": "released",
            })
        if updates:
            store.compare_and_swap_named_projections(updates)

    @staticmethod
    def _derived_authority_payload(ctx: MaintenanceJobExecutionContext) -> dict[str, object]:
        """Carry the parent job's bounded authority into generated work."""

        claims = ctx.payload.get("authority_claims")
        return {
            "authority_claims": dict(claims) if isinstance(claims, Mapping) else None,
            "authority_required": bool(ctx.payload.get("authority_required")),
        }

    def _advance_maintenance_plan(
        self,
        ctx: MaintenanceJobExecutionContext,
        *,
        budget_state: Mapping[str, object] | None = None,
    ) -> bool:
        """Requeue one planned phase, returning whether work remains."""
        decision = decide_next_maintenance_phase(
            ctx.payload,
            completed_kind=ctx.maintenance_kind,
        )
        if not decision.should_continue or decision.next_kind == "document_parse_graph" and self.document_parser is None:
            self._emit_trace(
                "maintenance_plan_complete",
                workspace_id=ctx.workspace_id,
                source_document_id=str(ctx.payload.get("source_document_id") or ""),
                request_node_id=ctx.request_node_id,
                job_id=ctx.job_id,
                completed_kind=ctx.maintenance_kind,
                reason=("parse_callback_unavailable" if decision.next_kind == "document_parse_graph" else decision.reason),
            )
            return False
        next_payload = dict(ctx.payload)
        if budget_state is None:
            budgets = ctx.payload.get("budgets")
            budgets = budgets if isinstance(budgets, Mapping) else {}
            previous_state = ctx.payload.get("maintenance_budget_state")
            previous_state = previous_state if isinstance(previous_state, Mapping) else {}
            step_used = int(previous_state.get("step_used") or 0) + 1
            max_steps = int(budgets.get("max_steps") or 0)
            next_payload["maintenance_budget_state"] = {
                **dict(previous_state),
                "step_used": step_used,
                "step_budget": max_steps,
            }
            if max_steps > 0 and step_used >= max_steps:
                self._emit_trace(
                    "maintenance_plan_complete",
                    workspace_id=ctx.workspace_id,
                    request_node_id=ctx.request_node_id,
                    job_id=ctx.job_id,
                    completed_kind=ctx.maintenance_kind,
                    reason="step_budget_exhausted",
                )
                return False
        next_payload["maintenance_context"] = append_maintenance_round(
            ctx.payload.get("maintenance_context")
            if isinstance(ctx.payload.get("maintenance_context"), Mapping)
            else None,
            round_number=int(ctx.payload.get("maintenance_round") or 0),
            summary=f"Completed maintenance phase: {ctx.maintenance_kind}",
            touched_node_ids=[
                str(item.get("candidate_id"))
                for item in (ctx.payload.get("maintenance_candidates") or [])
                if isinstance(item, Mapping) and str(item.get("candidate_id") or "").strip()
            ],
            next_seed_node_ids=[
                str(item.get("candidate_id"))
                for item in (ctx.payload.get("maintenance_candidates") or [])
                if isinstance(item, Mapping) and str(item.get("candidate_id") or "").strip()
            ],
            selection_reasons=[
                item
                for item in (ctx.payload.get("maintenance_candidates") or [])
                if isinstance(item, Mapping)
            ],
        )
        if budget_state is not None:
            next_payload["maintenance_budget_state"] = _persisted_budget_state(budget_state)
        next_payload.update(
            {
                "maintenance_kind": decision.next_kind,
                "maintenance_phase_index": decision.next_index,
                "maintenance_previous_kind": ctx.maintenance_kind,
                "maintenance_round": int(ctx.payload.get("maintenance_round") or 0) + 1,
            }
        )
        self.engines.conversation.jobs.requeue_at_tail(ctx.job, payload=next_payload)
        self._emit_trace(
            "maintenance_plan_advanced",
            workspace_id=ctx.workspace_id,
            source_document_id=str(ctx.payload.get("source_document_id") or ""),
            request_node_id=ctx.request_node_id,
            job_id=ctx.job_id,
            completed_kind=ctx.maintenance_kind,
            next_kind=decision.next_kind,
            phase_index=decision.next_index,
            round=next_payload["maintenance_round"],
        )
        return True

    def _evaluate_maintenance_guard(
        self,
        ctx: MaintenanceJobExecutionContext,
    ) -> MaintenanceGuardDecision:
        """Verify that a claimed job still targets the current ready source attempt."""
        source_document_id = str(ctx.payload.get("source_document_id") or "")
        requested_revision_id = str(ctx.payload.get("source_revision_id") or "")
        requested_digest = str(ctx.payload.get("source_digest") or "")
        requested_revision_document_id = str(
            ctx.payload.get("revision_document_id") or ""
        )
        required_stage = str(
            ctx.payload.get("required_stage")
            or required_stage_for_maintenance(ctx.maintenance_kind)
        )
        if (
            not source_document_id
            or not requested_revision_id
            or not requested_digest
            or not requested_revision_document_id
        ):
            return MaintenanceGuardDecision(
                status="blocked",
                reason="job_revision_metadata_missing",
                source_document_id=source_document_id,
                source_revision_id=requested_revision_id,
                source_digest=requested_digest,
                required_stage=required_stage,
            )

        ns = WorkspaceNamespaces(ctx.workspace_id)
        with _temporary_namespace(self.engines.kg, ns.source_space):
            revision_nodes: list[Node] = self.engines.kg.read.get_nodes(
                where=_and_where(
                    {"artifact_kind": "source_revision"},
                    {"source_document_id": source_document_id},
                ),
                limit=10_000,
            )
            readiness_nodes: list[Node] = self.engines.kg.read.get_nodes(
                where=_and_where(
                    {"artifact_kind": "source_readiness"},
                    {"source_document_id": source_document_id},
                    {"source_revision_id": requested_revision_id},
                    {"readiness_stage": required_stage},
                ),
                limit=10_000,
            )

        def metadata(node: Node) -> dict[str, object]:
            raw = getattr(node, "metadata", {})
            return dict(raw) if isinstance(raw, dict) else {}

        revision_nodes = [
            node
            for node in revision_nodes
            if str(metadata(node).get("workspace_id") or "") == ctx.workspace_id
        ]
        current_node = max(
            revision_nodes,
            key=lambda node: int(metadata(node).get("created_at_ms") or 0),
            default=None,
        )
        current_revision = (
            SourceRevision(
                source_document_id=source_document_id,
                revision_id=str(metadata(current_node).get("source_revision_id") or current_node.id),
                source_digest=str(metadata(current_node).get("source_digest") or ""),
                revision_document_id=str(metadata(current_node).get("revision_document_id") or ""),
            )
            if current_node is not None
            else None
        )
        ready_revision_ids = {
            str(metadata(node).get("source_revision_id") or "")
            for node in readiness_nodes
        }
        decision = evaluate_maintenance_guard(
            source_revision=current_revision,
            requested_revision_id=requested_revision_id,
            requested_digest=requested_digest,
            requested_revision_document_id=requested_revision_document_id,
            required_stage=required_stage,
            ready_revision_ids=ready_revision_ids,
        )
        if ctx.maintenance_kind in {
            "document_seed_graph",
            "document_parse_graph",
            "document_expand_parse_children",
            "document_reparse_region",
        }:
            requested_session_id = str(ctx.payload.get("parse_session_id") or "")
            if requested_session_id:
                from ..parsing.parse_session_store import ParseSessionStore

                active_session_id = ParseSessionStore(
                    self.engines.conversation.meta_sqlite,
                    workspace_id=ctx.workspace_id,
                ).active_session_id(
                    source_document_id,
                    scope_id=str(ctx.payload.get("parse_session_scope") or "full"),
                )
                decision = evaluate_parse_session_guard(
                    decision,
                    requested_session_id=requested_session_id,
                    active_session_id=active_session_id,
                )
        return decision

    def _block_guarded_job(
        self,
        ctx: MaintenanceJobExecutionContext,
        decision: MaintenanceGuardDecision,
    ) -> None:
        """Persist and surface a non-destructive source-fence refusal."""
        ns = WorkspaceNamespaces(ctx.workspace_id)
        artifact_id = str(
            stable_id(
                "kogwistar_llm_wiki.maintenance_guard_decision",
                ctx.job_id,
                decision.status,
                decision.reason,
            )
        )
        node = Node(
            id=artifact_id,
            label="Maintenance Guard Decision",
            type="entity",
            summary=f"Maintenance job blocked: {decision.reason}",
            doc_id=decision.source_document_id or None,
            mentions=[Grounding(spans=[Span(
                collection_page_url=f"conversation/{ns.conv_bg}",
                document_page_url=f"conversation/{ns.conv_bg}",
                doc_id=f"conv:{ns.conv_bg}",
                insertion_method="maintenance_guard",
                page_number=1,
                start_char=0,
                end_char=1,
                excerpt="maintenance_guard",
                context_before="",
                context_after="",
                chunk_id=None,
                source_cluster_id=None,
            )])],
            metadata={
                "workspace_id": ctx.workspace_id,
                "source_document_id": decision.source_document_id,
                "artifact_kind": "maintenance_guard_decision",
                "maintenance_guard_status": decision.status,
                "reason": decision.reason,
                "source_revision_id": decision.source_revision_id,
                "source_digest": decision.source_digest,
                "required_stage": decision.required_stage,
                "job_id": ctx.job_id,
                "maintenance_kind": ctx.maintenance_kind,
                "selection_strategy": ctx.payload.get("selection_strategy"),
                "maintenance_candidates": list(ctx.payload.get("maintenance_candidates") or [])[:24],
                "created_at_ms": int(time.time() * 1000),
            },
        )
        with _background_namespace(self.engines.conversation, ns.conv_bg):
            if not self.engines.conversation.read.node_exists(ids=[artifact_id]):
                self.engines.conversation.write.add_node(node)
        self._emit_lane_reply(
            workspace_id=ctx.workspace_id,
            source_document_id=decision.source_document_id,
            request_node_id=ctx.request_node_id,
            reply_to_message_id=ctx.lane_message_id or None,
            status="failed",
            payload={
                "maintenance_kind": ctx.maintenance_kind,
                "maintenance_guard_status": decision.status,
                "maintenance_guard_reason": decision.reason,
                **self._selection_result_payload(ctx),
                "guard_artifact_id": artifact_id,
            },
        )
        self.engines.conversation.jobs.mark_failed(
            ctx.job_id,
            f"maintenance_guard_{decision.status}:{decision.reason}",
            final=True,
        )

    def _handle_execution_wisdom_strategy(self, ctx: MaintenanceJobExecutionContext) -> None:
        workflow_id: str = workflow_id_for_maintenance_kind(ctx.maintenance_kind)
        try:
            if self._claim_lost.is_set():
                self._emit_stale_claim_discarded(ctx, reason="claim_lost_before_wisdom")
                return
            emitted: list[str] = self._emit_execution_wisdom_from_history(
                ctx.workspace_id,
                self.engines,
                before_write=lambda _pattern: self._assert_claim_owned(
                    ctx,
                    reason="claim_lost_before_wisdom_artifact",
                ),
            )
            if self._claim_lost.is_set():
                self._emit_stale_claim_discarded(ctx, reason="claim_lost_after_wisdom")
                return
            logger.info(
                "Maintenance job %s execution finished: finished (%s emitted=%s)",
                ctx.request_node_id,
                workflow_id,
                len(emitted),
            )
            self._emit_lane_reply(
                workspace_id=ctx.workspace_id,
                source_document_id=str(ctx.payload.get("source_document_id") or ""),
                request_node_id=ctx.request_node_id,
                reply_to_message_id=ctx.lane_message_id or None,
                status="completed",
                            payload={
                                "maintenance_kind": ctx.maintenance_kind,
                                **self._selection_result_payload(ctx),
                                "execution_wisdom_emitted": emitted,
                },
            )
            if ctx.job_id:
                self._acknowledge_job(ctx)
        except Exception as e:
            if self._claim_lost.is_set():
                self._emit_stale_claim_discarded(ctx, reason="claim_lost_during_wisdom")
                return
            logger.exception("Maintenance job %s encountered runtime error", ctx.request_node_id)
            self._emit_lane_reply(
                workspace_id=ctx.workspace_id,
                source_document_id=str(ctx.payload.get("source_document_id") or ""),
                request_node_id=ctx.request_node_id,
                reply_to_message_id=ctx.lane_message_id or None,
                status="failed",
                        payload={
                            "maintenance_kind": ctx.maintenance_kind,
                            **self._selection_result_payload(ctx),
                            "error": str(e),
                },
            )
            if ctx.job_id:
                self.engines.conversation.jobs.retry_or_fail(ctx.job, e)

    def _handle_graph_patch_apply_strategy(self, ctx: MaintenanceJobExecutionContext) -> None:
        decision = self._evaluate_maintenance_guard(ctx)
        if decision.status != "ready":
            self._block_guarded_job(ctx, decision)
            return
        patch_payload = ctx.payload.get("patch")
        if not isinstance(patch_payload, dict):
            self._handle_runtime_workflow_strategy(ctx)
            return
        accepted_candidate = self.engines.conversation.jobs.accepted_candidate(ctx.job)
        if isinstance(accepted_candidate, dict) and accepted_candidate.get("kind") == "graph_patch":
            patch_payload = accepted_candidate.get("patch")
            self._emit_trace(
                "maintenance_accepted_candidate_reused",
                workspace_id=ctx.workspace_id,
                source_document_id=str(ctx.payload.get("source_document_id") or ""),
                job_id=ctx.job_id,
                candidate_kind="graph_patch",
            )
        if self._claim_lost.is_set():
            self._emit_trace(
                "maintenance_repeated_work_discarded",
                workspace_id=ctx.workspace_id,
                source_document_id=str(ctx.payload.get("source_document_id") or ""),
                request_node_id=ctx.request_node_id,
                job_id=ctx.job_id,
                reason="claim_lost_before_graph_patch",
                comparison_result={"patch_present": True},
            )
            return
        try:
            patch: MaintenancePatch = MaintenancePatch.model_validate(patch_payload)
            if patch.scope.workspace_id != ctx.workspace_id:
                raise ValueError(
                    "maintenance patch scope workspace does not match the claimed job workspace"
                )
            decision = self.engines.conversation.jobs.accept_candidate(
                ctx.job,
                {"kind": "graph_patch", "patch": patch.model_dump(mode="json")},
            )
            if decision.get("status") == "rejected":
                self._emit_trace(
                    "maintenance_repeated_work_discarded",
                    workspace_id=ctx.workspace_id,
                    source_document_id=str(ctx.payload.get("source_document_id") or ""),
                    job_id=ctx.job_id,
                    reason=str(decision.get("reason") or "claim_not_valid"),
                )
                return
            if decision.get("status") == "existing":
                winner = self.engines.conversation.jobs.accepted_candidate(ctx.job)
                if isinstance(winner, dict) and isinstance(winner.get("patch"), dict):
                    patch = MaintenancePatch.model_validate(winner["patch"])
                    self._emit_trace(
                        "maintenance_repeated_work_discarded",
                        workspace_id=ctx.workspace_id,
                        source_document_id=str(ctx.payload.get("source_document_id") or ""),
                        job_id=ctx.job_id,
                        reason="candidate_already_accepted",
                    )
            if self._claim_lost.is_set():
                self._emit_stale_claim_discarded(ctx, reason="claim_lost_before_graph_patch")
                return
            lock_keys: tuple[str, ...] = ()
            if patch.intent in {
                MaintenanceIntent.DERIVE_CROSSLINK_CANDIDATE,
                MaintenanceIntent.ADD_CROSSLINK,
                MaintenanceIntent.RETRACT_CROSSLINK,
            }:
                acquired = self._acquire_crosslink_resource_locks(ctx, patch)
                if acquired is None:
                    self._emit_trace(
                        "maintenance_crosslink_resource_conflict",
                        workspace_id=ctx.workspace_id,
                        request_node_id=ctx.request_node_id,
                        job_id=ctx.job_id,
                        reason="conflicting resource lease",
                    )
                    self.engines.conversation.jobs.requeue_at_tail(
                        ctx.job,
                        payload={**ctx.payload, "resource_conflict_requeued": True},
                    )
                    return
                lock_keys = acquired
            try:
                result = apply_maintenance_patch_for_scope(
                    self.engines,
                    patch,
                    # The job workspace, not mutable payload data, selects the
                    # destination namespace.
                    namespace_prefix=f"ws:{ctx.workspace_id}:",
                )
            finally:
                self._release_crosslink_resource_locks(ctx, lock_keys)
            if self._claim_lost.is_set():
                self._emit_stale_claim_discarded(ctx, reason="claim_lost_after_graph_patch")
                return
            invalidation = None
            if result.status.value == "applied":
                invalidation = self._plan_and_enqueue_dependency_invalidation(ctx, patch)
            if patch.intent in {
                MaintenanceIntent.DERIVE_CROSSLINK_CANDIDATE,
                MaintenanceIntent.ADD_CROSSLINK,
                MaintenanceIntent.RETRACT_CROSSLINK,
            }:
                self._persist_crosslink_group_apply_outcome(
                    ctx,
                    patch,
                    status=result.status.value,
                    applied_count=result.applied_count,
                    failed_count=result.failed_count,
                    skipped_count=result.skipped_count,
                )
            self._emit_lane_reply(
                workspace_id=ctx.workspace_id,
                source_document_id=str(ctx.payload.get("source_document_id") or ""),
                request_node_id=ctx.request_node_id,
                reply_to_message_id=ctx.lane_message_id or None,
                status="completed" if result.status.value == "applied" else "failed",
                payload={
                    "maintenance_kind": ctx.maintenance_kind,
                    "patch_id": result.patch_id,
                    "patch_status": result.status.value,
                    "applied_count": result.applied_count,
                    "skipped_count": result.skipped_count,
                    "failed_count": result.failed_count,
                    "artifact_id": result.artifact_id,
                    "dependency_invalidation": (
                        self._dependency_invalidation_payload(invalidation)
                        if invalidation is not None
                        else None
                    ),
                },
            )
            if result.status.value == "applied":
                if ctx.job_id:
                    self._acknowledge_job(ctx)
            else:
                raise RuntimeError(f"graph patch apply did not complete: {result.status.value}")
        except Exception as e:
            if self._claim_lost.is_set():
                self._emit_stale_claim_discarded(ctx, reason="claim_lost_during_graph_patch")
                return
            logger.exception("Maintenance job %s encountered graph patch apply error", ctx.request_node_id)
            self._emit_lane_reply(
                workspace_id=ctx.workspace_id,
                source_document_id=str(ctx.payload.get("source_document_id") or ""),
                request_node_id=ctx.request_node_id,
                reply_to_message_id=ctx.lane_message_id or None,
                status="failed",
                payload={
                    "maintenance_kind": ctx.maintenance_kind,
                    "error": str(e),
                },
            )
            if ctx.job_id:
                self.engines.conversation.jobs.retry_or_fail(ctx.job, e)

    def _persist_crosslink_group_apply_outcome(
        self,
        ctx: MaintenanceJobExecutionContext,
        patch: MaintenancePatch,
        *,
        status: str,
        applied_count: int,
        failed_count: int,
        skipped_count: int,
    ) -> str:
        """Record the mutation result separately from proposal/review artifacts."""

        group_id = str(
            ctx.payload.get("crosslink_candidate_group_id")
            or patch.operations[0].properties.get("crosslink_group_id")
            if patch.operations
            else ""
        )
        outcome_id = str(
            stable_id(
                "crosslink_group_outcome",
                ctx.workspace_id,
                group_id,
                patch.patch_id,
                status,
            )
        )
        metadata = {
            "artifact_kind": "crosslink_group_outcome",
            "workspace_id": ctx.workspace_id,
            "conversation_lane": "background",
            "group_id": group_id,
            "patch_id": patch.patch_id,
            "outcome_status": status,
            "applied_count": applied_count,
            "failed_count": failed_count,
            "skipped_count": skipped_count,
            "graph_mutation": status == "applied" and applied_count > 0,
            "created_at_ms": int(time.time() * 1000),
            **self._maintenance_run_identity(ctx),
        }
        node = Node(
            id=outcome_id,
            label=f"Cross-link group outcome {group_id or patch.patch_id}",
            type="entity",
            summary=f"Cross-link group apply outcome: {status}",
            doc_id=group_id or patch.patch_id,
            mentions=[Grounding(spans=[Span.from_dummy_for_workflow(outcome_id)])],
            metadata=metadata,
        )
        with _background_namespace(
            self.engines.conversation, WorkspaceNamespaces(ctx.workspace_id).conv_bg
        ):
            self.engines.conversation.write.add_node(node)
        return outcome_id

    def _handle_crosslink_maintenance_strategy(self, ctx: MaintenanceJobExecutionContext) -> None:
        """Create or route a guarded derived-link patch; never mutate in place."""

        try:
            patch_payload = ctx.payload.get("patch")
            if isinstance(patch_payload, Mapping):
                patch = MaintenancePatch.model_validate(patch_payload)
            elif ctx.maintenance_kind in {
                "document_propose_crosslinks",
                "document_validate_crosslinks",
                "document_revalidate_crosslinks",
            }:
                if not isinstance(ctx.payload.get("crosslink_candidate"), Mapping):
                    if ctx.maintenance_kind == "document_propose_crosslinks":
                        self._propose_background_crosslink_groups(ctx)
                        return
                    if ctx.maintenance_kind == "document_revalidate_crosslinks":
                        edge_id = str(
                            ctx.payload.get("crosslink_edge_id")
                            or ctx.payload.get("supersedes_edge_id")
                            or ""
                        ).strip()
                        if not edge_id:
                            raise ValueError("crosslink revalidation requires crosslink_edge_id")
                        patch = MaintenancePatch(
                            patch_id=str(
                                stable_id(
                                    "maintenance_crosslink_stale_review",
                                    ctx.workspace_id,
                                    edge_id,
                                    str(ctx.payload.get("active_view_version") or ""),
                                )
                            ),
                            intent=MaintenanceIntent.REQUEST_REVIEW,
                            scope=MaintenanceScope(workspace_id=ctx.workspace_id),
                            rationale="active ParseView changed; derived crosslink requires revalidation",
                            operations=[
                                MaintenancePatchOperation(
                                    operation_id=f"review:{edge_id}",
                                    kind=MaintenanceOperationKind.REQUEST_REVIEW,
                                    reason="derived crosslink is stale until source evidence is revalidated",
                                    properties={
                                        "crosslink_status": "needs_revalidation",
                                        "crosslink_edge_id": edge_id,
                                        "active_view_version": int(
                                            ctx.payload.get("active_view_version") or 0
                                        ),
                                    },
                                )
                            ],
                        )
                        next_payload = dict(ctx.payload)
                        next_payload.update(
                            {
                                "maintenance_kind": "graph_patch_apply",
                                "patch": patch.model_dump(mode="json"),
                                "crosslink_lifecycle": "needs_revalidation",
                                "maintenance_previous_kind": ctx.maintenance_kind,
                            }
                        )
                        if self._claim_lost.is_set():
                            self._emit_stale_claim_discarded(ctx, reason="claim_lost_before_crosslink_requeue")
                            return
                        self.engines.conversation.jobs.requeue_at_tail(ctx.job, payload=next_payload)
                        return
                    self._emit_trace(
                        "maintenance_crosslink_no_candidate",
                        workspace_id=ctx.workspace_id,
                        job_id=ctx.job_id,
                        request_node_id=ctx.request_node_id,
                        reason="bounded proposal found no eligible candidate",
                    )
                    if self._claim_lost.is_set():
                        self._emit_stale_claim_discarded(ctx, reason="claim_lost_before_crosslink_reply")
                        return
                    self._emit_lane_reply(
                        workspace_id=ctx.workspace_id,
                        source_document_id=str(ctx.payload.get("source_document_id") or ""),
                        request_node_id=ctx.request_node_id,
                        reply_to_message_id=ctx.lane_message_id or None,
                        status="completed",
                        payload={
                            "maintenance_kind": ctx.maintenance_kind,
                            "crosslink_lifecycle": "no_candidate",
                            "graph_mutation": False,
                        },
                    )
                    if self._claim_lost.is_set():
                        self._emit_stale_claim_discarded(ctx, reason="claim_lost_before_crosslink_advance")
                        return
                    if self._advance_maintenance_plan(ctx):
                        return
                    if ctx.job_id:
                        self._acknowledge_job(ctx)
                    return
                patch = self._build_crosslink_candidate_patch(ctx)
            else:
                raise ValueError(
                    f"{ctx.maintenance_kind} requires a previously persisted crosslink patch"
                )
            self._validate_crosslink_authority(ctx, patch)
            if ctx.maintenance_kind == "document_validate_crosslinks":
                patch = self._promote_crosslink_candidate(
                    ctx, patch, authority_validated=True
                )
            elif (
                ctx.maintenance_kind == "document_retract_crosslinks"
                and patch.intent != MaintenanceIntent.RETRACT_CROSSLINK
            ):
                raise ValueError("crosslink retraction requires retract_crosslink intent")
            next_payload = dict(ctx.payload)
            next_payload.update(
                {
                    "maintenance_kind": "graph_patch_apply",
                    "patch": patch.model_dump(mode="json"),
                    "crosslink_lifecycle": (
                        "candidate"
                        if ctx.maintenance_kind == "document_propose_crosslinks"
                        else "revalidation_candidate"
                        if ctx.maintenance_kind == "document_revalidate_crosslinks"
                        else "validated"
                        if ctx.maintenance_kind == "document_validate_crosslinks"
                        else "retracted"
                    ),
                    "maintenance_previous_kind": ctx.maintenance_kind,
                }
            )
            if self._claim_lost.is_set():
                self._emit_stale_claim_discarded(ctx, reason="claim_lost_before_crosslink_requeue")
                return
            self.engines.conversation.jobs.requeue_at_tail(ctx.job, payload=next_payload)
            self._emit_trace(
                "maintenance_crosslink_patch_prepared",
                workspace_id=ctx.workspace_id,
                job_id=ctx.job_id,
                maintenance_kind=ctx.maintenance_kind,
                patch_id=patch.patch_id,
                lifecycle=next_payload["crosslink_lifecycle"],
            )
        except Exception as exc:  # noqa: BLE001 - durable job boundary must record provider/backend failures
            if self._claim_lost.is_set():
                self._emit_stale_claim_discarded(ctx, reason="claim_lost_during_crosslink")
                return
            self._emit_trace(
                "maintenance_crosslink_patch_rejected",
                workspace_id=ctx.workspace_id,
                job_id=ctx.job_id,
                maintenance_kind=ctx.maintenance_kind,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            self._emit_lane_reply(
                workspace_id=ctx.workspace_id,
                source_document_id=str(ctx.payload.get("source_document_id") or ""),
                request_node_id=ctx.request_node_id,
                reply_to_message_id=ctx.lane_message_id or None,
                status="failed",
                payload={"maintenance_kind": ctx.maintenance_kind, "error": str(exc)},
            )
            if ctx.job_id:
                self.engines.conversation.jobs.retry_or_fail(ctx.job, exc)

    def _build_crosslink_candidate_patch(self, ctx: MaintenanceJobExecutionContext) -> MaintenancePatch:
        candidate = ctx.payload.get("crosslink_candidate")
        if not isinstance(candidate, Mapping):
            raise TypeError("crosslink proposal requires crosslink_candidate evidence")
        left_id = str(candidate.get("left_node_id") or "").strip()
        right_id = str(candidate.get("right_node_id") or "").strip()
        relation = str(candidate.get("relation") or "related_to").strip()
        left_doc = str(candidate.get("left_source_document_id") or "").strip()
        right_doc = str(candidate.get("right_source_document_id") or "").strip()
        if not left_id or not right_id or not relation or not left_doc or not right_doc:
            raise ValueError("crosslink candidate requires both nodes, relation, and two source documents")
        namespace_prefix = f"ws:{ctx.workspace_id}:"
        if not left_id.startswith(namespace_prefix) or not right_id.startswith(namespace_prefix):
            raise ValueError("crosslink candidate nodes must remain in the workspace namespace")
        pointers = candidate.get("source_pointers")
        normalized_pointers = [dict(item) for item in pointers if isinstance(item, Mapping)] if isinstance(pointers, list) else []
        if len({left_doc, right_doc}) < 2:
            raise ValueError("crosslink candidate requires evidence from two source documents")
        if not normalized_pointers:
            raise ValueError("crosslink candidate requires authoritative source pointers")
        pointer_documents = {
            str(pointer.get("doc_id") or pointer.get("source_document_id") or "").strip()
            for pointer in normalized_pointers
        }
        if {left_doc, right_doc} - pointer_documents:
            raise ValueError("crosslink candidate pointers must cover both source documents")
        for pointer in normalized_pointers:
            pointer_workspace = str(pointer.get("workspace_id") or "").strip()
            if pointer_workspace and pointer_workspace != ctx.workspace_id:
                raise ValueError("crosslink candidate evidence crosses workspace boundaries")
            pointer_namespace = str(pointer.get("namespace") or "").strip()
            if pointer_namespace and not pointer_namespace.startswith(namespace_prefix):
                raise ValueError("crosslink candidate evidence crosses namespace boundaries")
        confidence = float(candidate.get("confidence") or 0.0)
        supersedes_edge_id = str(candidate.get("supersedes_edge_id") or "").strip()
        if ctx.maintenance_kind == "document_revalidate_crosslinks":
            if not supersedes_edge_id:
                raise ValueError("crosslink revalidation requires supersedes_edge_id")
            if candidate.get("superseded_edge_source_native") is True:
                raise ValueError("crosslink revalidation cannot replace a source-native edge")
            if str(candidate.get("superseded_edge_status") or "").strip().lower() not in {
                "accepted",
                "stale",
                "needs_revalidation",
            }:
                raise ValueError("crosslink revalidation requires an accepted or stale derived edge")
        provenance = MaintenanceProvenance(
            source_document_id=left_doc,
            source_pointers=normalized_pointers,
            maintenance_run_id=str(
                ctx.payload.get("maintenance_run_id")
                or stable_id(
                    "maintenance_run",
                    ctx.workspace_id,
                    ctx.request_node_id,
                    ctx.job_id,
                    int(ctx.payload.get("maintenance_attempt") or 1),
                )
            ),
            confidence=confidence,
        )
        edge_id = str(
            stable_id(
                "kogwistar_llm_wiki.derived_crosslink",
                ctx.workspace_id,
                left_id,
                right_id,
                relation,
                left_doc,
                right_doc,
                str(candidate.get("supersedes_edge_id") or ""),
            )
        )
        return MaintenancePatch(
            patch_id=str(stable_id("maintenance_crosslink_patch", edge_id, "candidate")),
            intent=MaintenanceIntent.DERIVE_CROSSLINK_CANDIDATE,
            scope=MaintenanceScope(workspace_id=ctx.workspace_id),
            rationale=str(candidate.get("rationale") or "bounded cross-document relation candidate"),
            operations=[
                MaintenancePatchOperation(
                    operation_id=f"candidate:{edge_id}",
                    kind=MaintenanceOperationKind.ADD_EDGE,
                    edge_id=edge_id,
                    from_node_id=left_id,
                    to_node_id=right_id,
                    relation=relation,
                    properties={
                        "crosslink_status": "candidate",
                        "left_source_document_id": left_doc,
                        "right_source_document_id": right_doc,
                        **(
                            {
                                "supersedes_edge_id": supersedes_edge_id,
                                "crosslink_lifecycle": "revalidation_candidate",
                            }
                            if supersedes_edge_id
                            else {}
                        ),
                    },
                    provenance=provenance,
                )
            ],
        )

    def _propose_background_crosslink_groups(self, ctx: MaintenanceJobExecutionContext) -> None:
        """Generate, ground, critique, and route bounded background groups."""
        self._trace_crosslink_workflow_stage(ctx, "select", "started")
        blocked_parse_statuses = {
            "expanding",
            "quality_unknown",
            "review_required",
            "stale",
            "inactive",
            "historical",
            "failed",
            "unknown",
        }
        for field_name in (
            "crosslink_parse_quality",
            "parse_quality_status",
            "source_region_status",
            "source_view_status",
        ):
            if str(ctx.payload.get(field_name) or "").strip().lower() in blocked_parse_statuses:
                self._emit_trace(
                    "maintenance_crosslink_proposal_blocked",
                    workspace_id=ctx.workspace_id,
                    job_id=ctx.job_id,
                    reason="parse_quality_not_ready",
                    status_field=field_name,
                )
                self._finish_crosslink_proposal(
                    ctx, groups=0, status="blocked_parse_quality"
                )
                self._trace_crosslink_workflow_stage(ctx, "continue", "blocked_parse_quality")
                return
        evidence = self._collect_crosslink_evidence(ctx)
        self._trace_crosslink_workflow_stage(
            ctx, "select", "completed", evidence_count=len(evidence)
        )
        self._trace_crosslink_workflow_stage(
            ctx, "evidence", "ready" if len(evidence) >= 2 else "insufficient",
            evidence_count=len(evidence),
        )
        if len(evidence) < 2:
            self._finish_crosslink_proposal(ctx, groups=0, status="no_candidate")
            self._trace_crosslink_workflow_stage(ctx, "propose", "no_candidate")
            self._trace_crosslink_workflow_stage(ctx, "continue", "no_candidate")
            return
        self._trace_crosslink_workflow_stage(ctx, "propose", "started")
        try:
            raw = self._invoke_crosslink_proposer(evidence, ctx)
        except BudgetExhaustedError:
            self._finish_crosslink_proposal(ctx, groups=0, status="budget_exhausted")
            self._trace_crosslink_workflow_stage(ctx, "propose", "budget_exhausted")
            self._trace_crosslink_workflow_stage(ctx, "continue", "budget_exhausted")
            return
        if not isinstance(raw, Mapping):
            raise TypeError("crosslink provider response must be an object")
        if set(raw) - {"groups"}:
            raise ValueError("crosslink provider response contains unsupported fields")
        raw_groups = raw.get("groups", [])
        if not isinstance(raw_groups, list) or len(raw_groups) > 12:
            raise ValueError("crosslink provider groups must be a list with at most 12 items")
        groups: list[object] = []
        malformed_groups: list[tuple[object, str]] = []
        group_ids: set[str] = set()
        for raw_group in raw_groups:
            try:
                group = CrosslinkProposalResponse.model_validate({"groups": [raw_group]}).groups[0]
                if group.group_id in group_ids:
                    raise ValueError("group_id values must be unique within a proposal")
                group_ids.add(group.group_id)
                groups.append(group)
            except Exception as exc:  # noqa: BLE001 - isolate one provider group
                malformed_groups.append((raw_group, str(exc)))
        self._trace_crosslink_workflow_stage(
            ctx, "propose", "groups_proposed" if raw_groups else "no_candidate",
            group_count=len(raw_groups),
        )
        evidence_by_id = {item.evidence_id: item for item in evidence}
        seen_operations: set[tuple[str, str, str]] = set()
        pending = automatic = rejected = 0
        for malformed_group, error in malformed_groups:
            group_json = json.dumps(malformed_group, sort_keys=True, separators=(",", ":"), default=str)
            malformed_group_id = str(
                stable_id(
                    "crosslink_review_group",
                    ctx.workspace_id,
                    str(ctx.job_id or ctx.request_node_id),
                    "malformed",
                    hashlib.sha256(group_json.encode("utf-8")).hexdigest(),
                )
            )
            artifact_id = self._persist_crosslink_group_rejection(
                ctx, malformed_group_id, malformed_group, error
            )
            self._trace_crosslink_workflow_stage(
                ctx, "validate", "rejected", group_id=malformed_group_id,
                artifact_id=artifact_id,
            )
            self._trace_crosslink_workflow_stage(
                ctx, "outcome", "rejected", group_id=malformed_group_id,
                artifact_id=artifact_id,
            )
            rejected += 1
        for group in groups:
            group_json = json.dumps(
                group.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
            )
            stable_group_id = str(
                stable_id(
                    "crosslink_review_group",
                    ctx.workspace_id,
                    str(ctx.job_id or ctx.request_node_id),
                    group.group_id,
                    hashlib.sha256(group_json.encode("utf-8")).hexdigest(),
                )
            )
            patches: list[MaintenancePatchOperation] = []
            group_evidence: dict[str, CrosslinkEvidence] = {}
            group_operation_keys: set[tuple[str, str, str]] = set()
            group_error: str | None = None
            for operation in group.operations:
                left = evidence_by_id.get(operation.left_evidence_id)
                right = evidence_by_id.get(operation.right_evidence_id)
                if left is None or right is None:
                    group_error = "crosslink provider cited an unknown evidence ID"
                    break
                if left.node_id == right.node_id:
                    group_error = "crosslink operation endpoints must be different nodes"
                    break
                if left.source_document_id == right.source_document_id:
                    group_error = "crosslink operation requires evidence from two source documents"
                    break
                key = (left.node_id, right.node_id, operation.relation)
                if key in seen_operations or key in group_operation_keys:
                    group_error = "crosslink provider returned a duplicate operation"
                    break
                group_operation_keys.add(key)
                group_evidence[left.evidence_id] = left
                group_evidence[right.evidence_id] = right
                patch_operation = self._crosslink_patch_operation(
                    ctx, stable_group_id, operation, left, right
                )
                patches.append(patch_operation)
                if operation.supersedes_edge_id:
                    patches.append(
                        MaintenancePatchOperation(
                            operation_id=f"retract:{operation.supersedes_edge_id}",
                            kind=MaintenanceOperationKind.TOMBSTONE_EDGE,
                            edge_id=operation.supersedes_edge_id,
                            reason=operation.rationale,
                            provenance=patch_operation.provenance,
                        )
                    )
            if group_error:
                artifact_id = self._persist_crosslink_group_rejection(
                    ctx, stable_group_id, group.model_dump(mode="json"), group_error
                )
                self._trace_crosslink_workflow_stage(
                    ctx, "validate", "rejected", group_id=stable_group_id,
                    artifact_id=artifact_id,
                )
                self._trace_crosslink_workflow_stage(
                    ctx, "outcome", "rejected", group_id=stable_group_id,
                    artifact_id=artifact_id,
                )
                rejected += 1
                continue
            seen_operations.update(group_operation_keys)
            if not patches:
                continue
            patch = MaintenancePatch(
                patch_id=str(stable_id("crosslink_group_patch", ctx.workspace_id, stable_group_id)),
                intent=MaintenanceIntent.DERIVE_CROSSLINK_CANDIDATE,
                scope=MaintenanceScope(workspace_id=ctx.workspace_id),
                rationale=group.rationale,
                operations=patches,
                requires_atomic_group=group.indivisible or any(
                    operation.supersedes_edge_id for operation in group.operations
                ),
            )
            self._trace_crosslink_workflow_stage(
                ctx, "validate", "started", group_id=stable_group_id
            )
            try:
                self._validate_crosslink_authority(ctx, patch)
            except Exception as exc:  # noqa: BLE001 - isolate one invalid group
                artifact_id = self._persist_crosslink_group_rejection(
                    ctx, stable_group_id, group.model_dump(mode="json"), str(exc)
                )
                self._trace_crosslink_workflow_stage(
                    ctx, "validate", "rejected", group_id=stable_group_id,
                    artifact_id=artifact_id,
                )
                self._trace_crosslink_workflow_stage(
                    ctx, "outcome", "rejected", group_id=stable_group_id,
                    artifact_id=artifact_id,
                )
                rejected += 1
                continue
            self._trace_crosslink_workflow_stage(
                ctx, "validate", "passed", group_id=stable_group_id,
                operation_count=len(patches),
            )
            self._trace_crosslink_workflow_stage(
                ctx, "critic", "started", group_id=stable_group_id
            )
            try:
                critic_raw = self._invoke_crosslink_critic(
                    group.model_dump(mode="json"),
                    [item.model_dump(mode="json") for item in group_evidence.values()],
                    ctx,
                )
                critic = CrosslinkCriticResponse.model_validate(critic_raw)
            except BudgetExhaustedError:
                critic = CrosslinkCriticResponse(
                    verdict="review",
                    explanation="Human review required because the provider-call budget is exhausted.",
                    evidence_ids=tuple(group_evidence),
                )
            except Exception as exc:  # noqa: BLE001 - isolate one invalid group
                artifact_id = self._persist_crosslink_group_rejection(
                    ctx, stable_group_id, group.model_dump(mode="json"), str(exc)
                )
                self._trace_crosslink_workflow_stage(
                    ctx, "critic", "rejected", group_id=stable_group_id,
                    artifact_id=artifact_id,
                )
                self._trace_crosslink_workflow_stage(
                    ctx, "outcome", "rejected", group_id=stable_group_id,
                    artifact_id=artifact_id,
                )
                rejected += 1
                continue
            self._trace_crosslink_workflow_stage(
                ctx, "critic", critic.verdict, group_id=stable_group_id
            )
            if not critic.evidence_ids or set(critic.evidence_ids) - set(group_evidence):
                error = (
                    "crosslink critic must cite at least one supplied evidence ID"
                    if not critic.evidence_ids
                    else "crosslink critic cited evidence outside its reviewed group"
                )
                artifact_id = self._persist_crosslink_group_rejection(
                    ctx, stable_group_id, group.model_dump(mode="json"), error
                )
                self._trace_crosslink_workflow_stage(
                    ctx, "critic", "rejected", group_id=stable_group_id,
                    artifact_id=artifact_id,
                )
                self._trace_crosslink_workflow_stage(
                    ctx, "outcome", "rejected", group_id=stable_group_id,
                    artifact_id=artifact_id,
                )
                rejected += 1
                continue
            mode = str(ctx.payload.get("crosslink_approval_mode") or "automatic").strip().lower()
            if mode not in {"automatic", "human"}:
                raise ValueError("crosslink_approval_mode must be automatic or human")
            artifact_id = self._persist_crosslink_group_review(
                ctx, stable_group_id, patch, group.model_dump(mode="json"),
                critic.model_dump(mode="json"), status=(
                    "pending" if critic.verdict == "review" or mode == "human" and critic.verdict == "approve"
                    else "rejected" if critic.verdict == "reject"
                    else "ready"
                ),
            )
            self._trace_crosslink_workflow_stage(
                ctx, "route", critic.verdict, group_id=stable_group_id,
                approval_mode=mode,
            )
            if critic.verdict == "review" or mode == "human" and critic.verdict == "approve":
                pending += 1
                self._trace_crosslink_workflow_stage(
                    ctx, "pending", "persisted", group_id=stable_group_id,
                    artifact_id=artifact_id,
                )
            elif mode == "automatic" and critic.verdict == "approve":
                self._trace_crosslink_workflow_stage(
                    ctx, "apply", "queued", group_id=stable_group_id
                )
                self._enqueue_crosslink_group_apply(ctx, stable_group_id, patch)
                automatic += 1
            else:
                rejected += 1
            self._emit_trace(
                "maintenance_crosslink_group_reviewed",
                workspace_id=ctx.workspace_id,
                job_id=ctx.job_id,
                group_id=stable_group_id,
                artifact_id=artifact_id,
                critic_verdict=critic.verdict,
                policy=mode,
                operation_count=len(patches),
            )
            self._trace_crosslink_workflow_stage(
                ctx, "outcome", "pending" if critic.verdict == "review" or mode == "human" and critic.verdict == "approve" else critic.verdict,
                group_id=stable_group_id,
                artifact_id=artifact_id,
            )
        self._finish_crosslink_proposal(
            ctx,
            groups=len(raw_groups),
            status="no_candidate" if not raw_groups else "groups_reviewed",
            pending=pending, automatic=automatic, rejected=rejected,
        )
        self._trace_crosslink_workflow_stage(
            ctx, "continue", "completed", group_count=len(raw_groups)
        )

    def _trace_crosslink_workflow_stage(
        self,
        ctx: MaintenanceJobExecutionContext,
        stage: str,
        outcome: str,
        **fields: object,
    ) -> None:
        """Record a bounded maintenance transition and its workflow node identity.

        The durable record deliberately uses the background maintenance lane.  It
        shares the conversation engine backend with other lanes, but it is not a
        user conversation and never stores provider rationale or hidden reasoning.
        """
        workflow_id = workflow_id_for_maintenance_kind(ctx.maintenance_kind)
        workflow_node_id = str(stable_id("wf_node", workflow_id, stage))
        self._emit_trace(
            "maintenance_crosslink_workflow_stage",
            **self._maintenance_run_identity(ctx),
            workflow_id=workflow_id,
            workflow_node_id=workflow_node_id,
            workflow_stage=stage,
            outcome=outcome,
            **fields,
        )
        try:
            safe_fields: dict[str, str | int | float | bool | None] = {}
            for key in _CROSSLINK_STAGE_FIELDS:
                value = fields.get(key)
                if isinstance(value, str):
                    safe_fields[key] = value[:200]
                elif value is None or isinstance(value, (bool, int, float)):
                    safe_fields[key] = value
            stage_payload = {
                **self._maintenance_run_identity(ctx),
                "workspace_id": ctx.workspace_id,
                "job_id": ctx.job_id,
                "request_node_id": ctx.request_node_id,
                "maintenance_kind": ctx.maintenance_kind,
                "workflow_id": workflow_id,
                "workflow_node_id": workflow_node_id,
                "workflow_stage": stage,
                "outcome": outcome,
                **safe_fields,
            }
            stage_key = str(
                stable_id(
                    "maintenance_workflow_stage",
                    ctx.workspace_id,
                    ctx.job_id,
                    ctx.request_node_id,
                    workflow_id,
                    stage,
                    outcome,
                    json.dumps(safe_fields, sort_keys=True, separators=(",", ":")),
                )
            )
            namespace = WorkspaceNamespaces(ctx.workspace_id).conv_bg
            with _background_namespace(self.engines.conversation, namespace):
                self.engines.conversation.send_lane_message(
                    conversation_id=f"maintenance:{ctx.request_node_id}",
                    inbox_id="inbox:worker:maintenance:trace",
                    sender_id="lane:worker:maintenance",
                    recipient_id="lane:worker:maintenance-trace",
                    msg_type="maintenance.workflow.stage",
                    purpose="internal",
                    payload=stage_payload,
                    idempotency_key=stage_key,
                )
        except Exception as exc:  # noqa: BLE001 - trace persistence must be visible
            ctx.payload["maintenance_trace_persistence_failed"] = True
            self._emit_trace(
                "maintenance_workflow_trace_persistence_failed",
                workspace_id=ctx.workspace_id,
                job_id=ctx.job_id,
                request_node_id=ctx.request_node_id,
                workflow_stage=stage,
                error_type=type(exc).__name__,
            )

    def _collect_crosslink_evidence(
        self, ctx: MaintenanceJobExecutionContext
    ) -> list[CrosslinkEvidence]:
        raw_candidates = ctx.payload.get("maintenance_candidates")
        candidate_ids = {
            str(item.get("candidate_id") or "").strip()
            for item in raw_candidates if isinstance(item, Mapping)
        } if isinstance(raw_candidates, list) else set()
        if not candidate_ids:
            candidate_ids.update(str(item).strip() for item in ctx.payload.get("seed_node_ids", []) if str(item).strip())
        else:
            candidate_ids.update(str(item).strip() for item in ctx.payload.get("seed_node_ids", []) if str(item).strip())
        namespace = WorkspaceNamespaces(ctx.workspace_id)
        with _temporary_namespace(self.engines.kg, namespace.curated_kg_space):
            nodes = self.engines.kg.read.get_nodes(ids=sorted(candidate_ids), limit=min(24, len(candidate_ids)))
        evidence: list[CrosslinkEvidence] = []
        with _temporary_namespace(self.engines.kg, namespace.source_space):
            for node in nodes:
                node_metadata = metadata_mapping(node)
                if str(node_metadata.get("workspace_id") or "") != ctx.workspace_id:
                    continue
                if not self._is_active_source_derivation(node, ctx.workspace_id):
                    continue
                acl = str(node_metadata.get("acl_scope") or node_metadata.get("security_scope") or "").strip()
                if acl and not can_access_security_scope(acl):
                    continue
                for grounding in list(getattr(node, "mentions", None) or [])[:2]:
                    for span in list(getattr(grounding, "spans", None) or [])[:2]:
                        doc_id = str(getattr(span, "doc_id", "") or "").strip()
                        start = getattr(span, "start_char", None)
                        end = getattr(span, "end_char", None)
                        if not doc_id or type(start) is not int or type(end) is not int:
                            continue
                        try:
                            source = self.engines.kg.read.get_document(doc_id)
                        except (KeyError, ValueError):
                            continue
                        source_metadata = metadata_mapping(source)
                        if str(source_metadata.get("workspace_id") or "") != ctx.workspace_id:
                            continue
                        source_acl = str(source_metadata.get("acl_scope") or source_metadata.get("security_scope") or "").strip()
                        if source_acl and not can_access_security_scope(source_acl):
                            continue
                        revision_id = str(source_metadata.get("source_revision_id") or source_metadata.get("revision_id") or "").strip()
                        revision_doc = str(
                            source_metadata.get("revision_document_id")
                            or source_metadata.get("source_revision_document_id")
                            or ""
                        ).strip()
                        if not revision_id or revision_doc != doc_id:
                            continue
                        raw_text = str(getattr(source, "content", None) or "")
                        digest = source_digest(raw_text)
                        recorded_digest = str(source_metadata.get("source_digest") or "").strip()
                        if recorded_digest and recorded_digest != digest:
                            continue
                        excerpt = raw_text[start:end]
                        if not excerpt or len(excerpt) > 1200:
                            continue
                        pointer = {
                            "doc_id": doc_id,
                            "source_cluster_id": doc_id,
                            "source_revision_id": revision_id,
                            "start_char": start,
                            "end_char": end,
                            "excerpt": excerpt,
                        }
                        if not source_pointer_has_character_span(pointer):
                            continue
                        validate_source_pointer(
                            pointer,
                            source_text_by_cluster={doc_id: raw_text},
                            end_mode="exclusive",
                            require_source_text=True,
                            require_text_match=True,
                        )
                        evidence_id = str(stable_id(
                            "crosslink_evidence", ctx.workspace_id, node.id, revision_id, start, end
                        ))
                        evidence.append(CrosslinkEvidence(
                            evidence_id=evidence_id,
                            node_id=str(node.id),
                            source_document_id=str(
                                source_metadata.get("logical_source_document_id")
                                or source_metadata.get("source_document_id")
                                or doc_id
                            ),
                            source_revision_id=revision_id,
                            revision_document_id=revision_doc,
                            source_digest=digest,
                            start_char=start,
                            end_char=end,
                            excerpt=excerpt,
                        ))
                        if len(evidence) >= 48:
                            return evidence
        return evidence

    def _invoke_crosslink_proposer(
        self, evidence: list[CrosslinkEvidence], ctx: MaintenanceJobExecutionContext
    ) -> Mapping[str, object]:
        usage_callback = self._reserve_crosslink_provider_call(ctx, "crosslink_proposal")
        callback = getattr(self, "crosslink_proposer", None)
        if callable(callback):
            return callback([item.model_dump(mode="json") for item in evidence], ctx)
        from kg_doc_parser.workflow_ingest.page_index import build_chat_model_for_role

        model = build_chat_model_for_role("parser", self.provider_settings)
        structured = model.with_structured_output(CrosslinkProposalResponse)
        prompt = {
            "task": "Propose only evidence-supported cross-document semantic links.",
            "constraints": [
                "Use only supplied evidence IDs; never invent node, document, or span IDs.",
                "Each relation must be supported by excerpts from two different source documents.",
                "Return an empty groups list when no defensible link exists.",
                "Do not emit hidden reasoning; rationale must be concise and evidence-based.",
            ],
            "evidence": [item.model_dump(mode="json") for item in evidence],
        }
        result = structured.invoke([
            ("system", "You produce bounded graph-link candidates, not authoritative facts."),
            ("human", json.dumps(prompt, ensure_ascii=False, sort_keys=True)),
        ], config={"callbacks": [usage_callback]})
        parsed = result.get("parsed") if isinstance(result, Mapping) and "parsed" in result else result
        if hasattr(parsed, "model_dump"):
            return parsed.model_dump(mode="json")
        if isinstance(parsed, Mapping):
            return parsed
        raise TypeError("crosslink proposer returned an invalid structured result")

    def _invoke_crosslink_critic(
        self, group: Mapping[str, object], evidence: list[Mapping[str, object]],
        ctx: MaintenanceJobExecutionContext,
    ) -> Mapping[str, object]:
        usage_callback = self._reserve_crosslink_provider_call(ctx, "crosslink_group_critic")
        callback = getattr(self, "crosslink_critic", None)
        if callable(callback):
            return callback({"group": dict(group), "evidence": evidence}, ctx)
        from kg_doc_parser.workflow_ingest.page_index import build_chat_model_for_role

        model = build_chat_model_for_role("parser", self.provider_settings)
        structured = model.with_structured_output(CrosslinkCriticResponse)
        result = structured.invoke([
            ("system", "Independently review this cross-link group. Return only a verdict, concise explanation, and cited evidence IDs. Reject unsupported, redundant, or ambiguous links."),
            ("human", json.dumps({"group": dict(group), "evidence": evidence}, ensure_ascii=False, sort_keys=True)),
        ], config={"callbacks": [usage_callback]})
        parsed = result.get("parsed") if isinstance(result, Mapping) and "parsed" in result else result
        if hasattr(parsed, "model_dump"):
            return parsed.model_dump(mode="json")
        if isinstance(parsed, Mapping):
            return parsed
        raise TypeError("crosslink critic returned an invalid structured result")

    def _reserve_crosslink_provider_call(
        self, ctx: MaintenanceJobExecutionContext, reason: str
    ) -> ProviderUsageCallback:
        """Debit and durably record one bounded provider call before invoking it."""
        from kogwistar.runtime import BudgetAttribution

        from ..usage.events import persist_usage_events

        request_budgets = ctx.payload.get("budgets")
        request_budgets = request_budgets if isinstance(request_budgets, Mapping) else {}
        request_limit = int(request_budgets.get("max_llm_calls") or 0)
        hard_limit = 13  # One proposal and at most one critic call for each of 12 groups.
        limit = min(request_limit, hard_limit) if request_limit > 0 else hard_limit
        job_id = str(ctx.job_id or ctx.request_node_id)
        namespace = WorkspaceNamespaces(ctx.workspace_id).usage_events
        durable_usage = _durable_maintenance_usage(
            self.engines.conversation.meta_sqlite,
            namespace=namespace,
            maintenance_job_id=job_id,
        )
        raw_state = ctx.payload.get("maintenance_budget_state")
        state = _maintenance_budget_state(
            ctx.payload,
            fair_scheduling=bool(getattr(self, "fair_scheduling", False)),
            maintenance_steps_per_slice=int(getattr(self, "maintenance_steps_per_slice", 8)),
            maintenance_llm_calls_per_slice=int(getattr(self, "maintenance_llm_calls_per_slice", 2)),
            maintenance_seconds_per_slice=int(getattr(self, "maintenance_seconds_per_slice", 30)),
            durable_usage=durable_usage,
        )
        if isinstance(raw_state, Mapping):
            state.update({
                key: value
                for key, value in raw_state.items()
                if key in {"token_budget", "step_budget", "time_budget_ms", "cost_budget"}
            })
        used = max(
            int(state.get("call_used") or 0),
            int(durable_usage.get("call_used") or 0),
        )
        provider_config = getattr(getattr(self, "provider_settings", None), "parser", None)
        provider = str(getattr(provider_config, "provider", "") or "")
        model = str(getattr(provider_config, "model", "") or "")
        ledger = StateBackedBudgetLedger({
            **state,
            "call_budget": limit,
            "call_used": used,
            "budget_scope": "maintenance_job",
        })
        ledger.debit_call(
            reason=reason,
            run_id=job_id,
            attribution=BudgetAttribution(
                workspace_id=ctx.workspace_id,
                operation_id=job_id,
                operation_kind=ctx.maintenance_kind,
                maintenance_job_id=job_id,
                provider=provider or None,
                model=model or None,
            ),
        )
        ctx.payload["maintenance_budget_state"] = _persisted_budget_state(ledger.state)
        event = ledger.events[-1]
        persist_usage_events(
            self.engines.conversation.meta_sqlite,
            namespace=namespace,
            events=[event],
            workspace_id=ctx.workspace_id,
            attempt_id=str(stable_id("crosslink_provider_call", job_id, ledger.call_used)),
            operation_id=job_id,
            operation_kind=reason,
            maintenance_job_id=job_id,
            provider=provider or None,
            model=model or None,
        )
        self._emit_trace(
            "maintenance_crosslink_provider_call_reserved",
            workspace_id=ctx.workspace_id,
            job_id=ctx.job_id,
            reason=reason,
            call_used=ledger.call_used,
            call_budget=limit,
        )

        def persist_provider_event(event: BudgetEvent) -> None:
            # The pre-call reservation already counts this attempted provider
            # invocation, so retain its failure record without double-counting.
            if event.unit == "llm_call":
                event = replace(event, unit="provider_failure")
            persist_usage_events(
                self.engines.conversation.meta_sqlite,
                namespace=namespace,
                events=[event],
                workspace_id=ctx.workspace_id,
                attempt_id=str(stable_id(
                    "crosslink_provider_usage",
                    job_id,
                    str(getattr(event, "event_id", "") or ledger.call_used),
                )),
                operation_id=job_id,
                operation_kind=reason,
                maintenance_job_id=job_id,
                provider=provider or None,
                model=model or None,
            )

        return ProviderUsageCallback(
            ledger=ledger,
            run_id=str(stable_id("crosslink_provider_run", job_id, ledger.call_used)),
            source_document_id=str(ctx.payload.get("source_document_id") or ""),
            provider=provider,
            model=model,
            pricing=resolve_token_pricing(provider=provider, model=model),
            event_sink=persist_provider_event,
        )

    def _crosslink_patch_operation(
        self, ctx: MaintenanceJobExecutionContext, group_id: str, operation: object,
        left: CrosslinkEvidence, right: CrosslinkEvidence,
    ) -> MaintenancePatchOperation:
        relation = str(operation.relation)
        edge_id = str(stable_id(
            "kogwistar_llm_wiki.derived_crosslink", ctx.workspace_id,
            left.node_id, right.node_id, relation,
            left.source_document_id, right.source_document_id,
            str(getattr(operation, "supersedes_edge_id", None) or ""),
        ))
        pointers = [
            {
                "doc_id": item.revision_document_id,
                "source_cluster_id": item.revision_document_id,
                "source_document_id": item.source_document_id,
                "source_revision_id": item.source_revision_id,
                "source_digest": item.source_digest,
                "workspace_id": ctx.workspace_id,
                "start_char": item.start_char,
                "end_char": item.end_char,
                "excerpt": item.excerpt,
            }
            for item in (left, right)
        ]
        provenance = MaintenanceProvenance(
            source_document_id=left.revision_document_id,
            source_pointers=pointers,
            maintenance_run_id=str(ctx.job_id or ctx.request_node_id),
            confidence=1.0,
        )
        properties: dict[str, str | int | float | bool | None] = {
            "crosslink_status": "candidate",
            "left_source_document_id": left.source_document_id,
            "right_source_document_id": right.source_document_id,
            "crosslink_group_id": group_id,
            "candidate_rationale": str(operation.rationale),
        }
        supersedes_edge_id = getattr(operation, "supersedes_edge_id", None)
        if supersedes_edge_id:
            properties["supersedes_edge_id"] = str(supersedes_edge_id)
        return MaintenancePatchOperation(
            operation_id=f"group:{group_id}:edge:{edge_id}",
            kind=MaintenanceOperationKind.ADD_EDGE,
            edge_id=edge_id,
            from_node_id=left.node_id,
            to_node_id=right.node_id,
            relation=relation,
            properties=properties,
            provenance=provenance,
        )

    def _persist_crosslink_group_review(
        self, ctx: MaintenanceJobExecutionContext, group_id: str, patch: MaintenancePatch,
        group: Mapping[str, object], critic: Mapping[str, object], *, status: str,
    ) -> str:
        artifact_id = str(stable_id("crosslink_group_review", ctx.workspace_id, group_id))
        fences = self._source_revision_fences_for_patch(patch)
        metadata = {
            "artifact_kind": "crosslink_group_review",
            "workspace_id": ctx.workspace_id,
            "conversation_lane": "background",
            "group_id": group_id,
            "review_status": status,
            "source_revision_fences": json.dumps(fences, sort_keys=True, separators=(",", ":")),
            "source_revision_id": fences[0]["source_revision_id"],
            "revision_document_id": fences[0]["revision_document_id"],
            "source_document_id": fences[0]["source_document_id"],
            "source_digest": fences[0]["source_digest"],
            **{
                key: str(ctx.payload.get(key) or "")
                for key in (
                    "crosslink_parse_quality",
                    "parse_quality_status",
                    "source_region_status",
                    "source_view_status",
                )
                if ctx.payload.get(key) is not None
            },
            "patch_json": json.dumps(patch.model_dump(mode="json"), sort_keys=True, separators=(",", ":")),
            "group_json": json.dumps(dict(group), sort_keys=True, separators=(",", ":")),
            "critic_json": json.dumps(dict(critic), sort_keys=True, separators=(",", ":")),
            "decision_version": 1,
            "created_by_job_id": ctx.job_id,
            **self._maintenance_run_identity(ctx),
        }
        node = Node(
            id=artifact_id,
            label=f"Cross-link review {group_id}",
            type="entity",
            summary=str(group.get("rationale") or "Background cross-link candidate group"),
            doc_id=str(ctx.payload.get("source_document_id") or group_id),
            mentions=[Grounding(spans=[Span.from_dummy_for_workflow(artifact_id)])],
            metadata=metadata,
        )
        with _background_namespace(self.engines.conversation, WorkspaceNamespaces(ctx.workspace_id).conv_bg):
            self.engines.conversation.write.add_node(node)
        return artifact_id

    def _persist_crosslink_group_rejection(
        self,
        ctx: MaintenanceJobExecutionContext,
        group_id: str,
        group: object,
        error: str,
    ) -> str:
        """Persist a bounded per-group rejection without fabricating evidence."""

        artifact_id = str(stable_id("crosslink_group_review", ctx.workspace_id, group_id))
        group_json = json.dumps(group, sort_keys=True, separators=(",", ":"), default=str)
        metadata = {
            "artifact_kind": "crosslink_group_review",
            "workspace_id": ctx.workspace_id,
            "conversation_lane": "background",
            "group_id": group_id,
            "review_status": "rejected",
            "rejection_stage": "validation",
            "rejection_error": error[:1000],
            "group_json": group_json[:12000],
            "patch_json": "null",
            "critic_json": "null",
            "decision_version": 1,
            "created_by_job_id": ctx.job_id,
            **self._maintenance_run_identity(ctx),
        }
        node = Node(
            id=artifact_id,
            label=f"Rejected cross-link review {group_id}",
            type="entity",
            summary=f"Cross-link group rejected during validation: {error[:240]}",
            doc_id=group_id,
            mentions=[Grounding(spans=[Span.from_dummy_for_workflow(artifact_id)])],
            metadata=metadata,
        )
        with _background_namespace(self.engines.conversation, WorkspaceNamespaces(ctx.workspace_id).conv_bg):
            self.engines.conversation.write.add_node(node)
        return artifact_id

    def _enqueue_crosslink_group_apply(
        self, ctx: MaintenanceJobExecutionContext, group_id: str, patch: MaintenancePatch
    ) -> None:
        payload = dict(ctx.payload)
        payload.update({
            "workspace_id": ctx.workspace_id,
            "request_node_id": str(stable_id("crosslink_apply_request", ctx.workspace_id, group_id)),
            "maintenance_kind": "document_validate_crosslinks",
            "crosslink_candidate_group_id": group_id,
            "patch": patch.model_dump(mode="json"),
            "accepted_confidence": 0.8,
            "required_stage": "parsed_graph_persisted",
            **self._maintenance_run_identity(ctx),
        })
        payload["source_revision_fences"] = self._source_revision_fences_for_patch(patch)
        self.engines.conversation.jobs.enqueue(
            job_id=str(stable_id("crosslink_apply_job", ctx.workspace_id, group_id)),
            namespace=WorkspaceNamespaces(ctx.workspace_id).maintenance_jobs,
            entity_kind="maintenance_job",
            entity_id=str(ctx.payload.get("source_document_id") or group_id),
            job_kind="maintenance_job:document_validate_crosslinks",
            op="UPSERT",
            payload=payload,
        )

    @staticmethod
    def _source_revision_fences_for_patch(patch: MaintenancePatch) -> list[dict[str, str]]:
        fences = {
            (
                str(pointer.get("source_document_id") or ""),
                str(pointer.get("source_revision_id") or ""),
                str(pointer.get("doc_id") or ""),
                str(pointer.get("source_digest") or ""),
            )
            for operation in patch.operations
            if operation.provenance is not None
            for pointer in operation.provenance.source_pointers
        }
        if not fences or any(not all(item) for item in fences):
            raise ValueError("cross-link patch requires complete immutable source revision fences")
        return [
            {
                "source_document_id": source_id,
                "source_revision_id": revision_id,
                "revision_document_id": revision_document_id,
                "source_digest": digest,
            }
            for source_id, revision_id, revision_document_id, digest in sorted(fences)
        ]

    def _finish_crosslink_proposal(
        self, ctx: MaintenanceJobExecutionContext, *, groups: int, status: str,
        pending: int = 0, automatic: int = 0, rejected: int = 0,
        applied: int = 0, stale: int = 0, partial: int = 0, failed: int = 0,
    ) -> None:
        summary_id = self._persist_crosslink_run_summary(
            ctx,
            groups=groups,
            status=status,
            pending=pending,
            automatic=automatic,
            rejected=rejected,
            applied=applied,
            stale=stale,
            partial=partial,
            failed=failed,
        )
        self._emit_lane_reply(
            workspace_id=ctx.workspace_id,
            source_document_id=str(ctx.payload.get("source_document_id") or ""),
            request_node_id=ctx.request_node_id,
            reply_to_message_id=ctx.lane_message_id or None,
            status="completed",
            payload={
                "maintenance_kind": ctx.maintenance_kind,
                "crosslink_lifecycle": status,
                "groups": groups,
                "pending_groups": pending,
                "automatic_groups": automatic,
                "rejected_groups": rejected,
                "graph_mutation": False,
                "run_summary_id": summary_id,
                **self._maintenance_run_identity(ctx),
            },
        )
        if ctx.job_id and not self._advance_maintenance_plan(ctx):
            self._acknowledge_job(ctx)

    def _persist_crosslink_run_summary(
        self,
        ctx: MaintenanceJobExecutionContext,
        *,
        groups: int,
        status: str,
        pending: int,
        automatic: int,
        rejected: int,
        applied: int,
        stale: int,
        partial: int,
        failed: int,
    ) -> str:
        """Persist the terminal bounded outcome in the background lane."""

        identity = self._maintenance_run_identity(ctx)
        summary_id = str(stable_id("maintenance_run_summary", identity["maintenance_run_id"]))
        metadata = {
            "artifact_kind": "maintenance_run_summary",
            "workspace_id": ctx.workspace_id,
            "conversation_lane": "background",
            "summary_status": status,
            "groups_proposed": groups,
            "groups_pending": pending,
            "groups_queued": automatic,
            "groups_rejected": rejected,
            "groups_applied": applied,
            "groups_stale": stale,
            "groups_partial": partial,
            "groups_failed": failed,
            "graph_mutation": applied > 0,
            "trace_persistence_complete": not bool(
                ctx.payload.get("maintenance_trace_persistence_failed")
            ),
            "created_at_ms": int(time.time() * 1000),
            **identity,
        }
        node = Node(
            id=summary_id,
            label=f"Maintenance run summary {identity['maintenance_run_id']}",
            type="entity",
            summary=f"Background cross-link run {status}",
            doc_id=summary_id,
            mentions=[Grounding(spans=[Span.from_dummy_for_workflow(summary_id)])],
            metadata=metadata,
        )
        with _background_namespace(
            self.engines.conversation, WorkspaceNamespaces(ctx.workspace_id).conv_bg
        ):
            self.engines.conversation.write.add_node(node)
        return summary_id

    def _validate_crosslink_authority(
        self,
        ctx: MaintenanceJobExecutionContext,
        patch: MaintenancePatch,
    ) -> None:
        """Verify endpoints and immutable evidence against current stores."""

        if patch.scope.workspace_id != ctx.workspace_id:
            raise ValueError("crosslink patch scope does not match the claimed workspace")
        endpoint_ids = {
            str(value)
            for operation in patch.operations
            for value in (operation.from_node_id, operation.to_node_id)
            if value
        }
        if endpoint_ids:
            namespace = WorkspaceNamespaces(ctx.workspace_id).curated_kg_space
            with _temporary_namespace(self.engines.kg, namespace):
                nodes = list(self.engines.kg.read.get_nodes(ids=sorted(endpoint_ids), limit=len(endpoint_ids)))
            found = {str(node.id) for node in nodes}
            if found != endpoint_ids:
                raise ValueError("crosslink endpoints must be existing workspace nodes")
            for node in nodes:
                metadata = metadata_mapping(node)
                if not self._is_active_source_derivation(node, ctx.workspace_id):
                    raise ValueError("crosslink endpoint is not selected by the active ParseView")
                if str(metadata.get("workspace_id") or "") != ctx.workspace_id:
                    raise ValueError("crosslink endpoints are not owned by the claimed workspace")
                security_scope = str(
                    metadata.get("acl_scope")
                    or metadata.get("security_scope")
                    or ""
                ).strip()
                if security_scope and not can_access_security_scope(security_scope):
                    raise PermissionError("crosslink endpoint is outside the current security scope")

        pointers = [
            pointer
            for operation in patch.operations
            if operation.provenance is not None
            for pointer in operation.provenance.source_pointers
        ]
        expected_fences_raw = ctx.payload.get("source_revision_fences")
        if expected_fences_raw is not None:
            if not isinstance(expected_fences_raw, list):
                raise ValueError("crosslink source revision fences must be a list")
            expected_fences = {
                (
                    str(item.get("source_document_id") or ""),
                    str(item.get("source_revision_id") or ""),
                    str(item.get("revision_document_id") or ""),
                    str(item.get("source_digest") or ""),
                )
                for item in expected_fences_raw
                if isinstance(item, Mapping)
            }
            actual_fences = {
                (
                    str(pointer.get("source_document_id") or ""),
                    str(pointer.get("source_revision_id") or pointer.get("revision_id") or ""),
                    str(pointer.get("doc_id") or pointer.get("source_document_id") or ""),
                    str(pointer.get("source_digest") or ""),
                )
                for pointer in pointers
            }
            if not expected_fences or expected_fences != actual_fences:
                raise ValueError("crosslink evidence does not match the reviewed source revision fences")
        if pointers:
            source_namespace = WorkspaceNamespaces(ctx.workspace_id).source_space
            document_ids = {
                str(pointer.get("doc_id") or pointer.get("source_document_id") or "").strip()
                for pointer in pointers
            }
            if "" in document_ids:
                raise ValueError("crosslink source pointers require document IDs")
            with _temporary_namespace(self.engines.kg, source_namespace):
                for document_id in sorted(document_ids):
                    document = self.engines.kg.read.get_document(document_id)
                    metadata = metadata_mapping(document)
                    if str(metadata.get("workspace_id") or "") != ctx.workspace_id:
                        raise ValueError("crosslink evidence is outside the claimed workspace")
                    security_scope = str(
                        metadata.get("acl_scope")
                        or metadata.get("security_scope")
                        or ""
                    ).strip()
                    if security_scope and not can_access_security_scope(security_scope):
                        raise PermissionError("crosslink evidence is outside the current security scope")
                    revision_id = str(metadata.get("source_revision_id") or metadata.get("revision_id") or "")
                    logical_source_id = str(
                        metadata.get("logical_source_document_id")
                        or metadata.get("source_document_id")
                        or ""
                    )
                    revision_document_id = str(
                        metadata.get("revision_document_id")
                        or metadata.get("source_revision_document_id")
                        or ""
                    )
                    for pointer in pointers:
                        pointer_document = str(pointer.get("doc_id") or pointer.get("source_document_id") or "").strip()
                        if pointer_document != document_id:
                            continue
                        pointer_revision = str(pointer.get("source_revision_id") or pointer.get("revision_id") or "")
                        if pointer_revision and revision_id and pointer_revision != revision_id:
                            raise ValueError("crosslink evidence is pinned to a stale source revision")
                        if str(pointer.get("source_document_id") or "") != logical_source_id:
                            raise ValueError("crosslink evidence is pinned to a different logical source")
                        if revision_document_id != document_id:
                            raise ValueError("crosslink evidence must resolve to its immutable revision document")
                        pointer_digest = str(pointer.get("source_digest") or "").strip()
                        actual_digest = source_digest(str(document.content or ""))
                        stored_digest = str(metadata.get("source_digest") or "").strip()
                        if (
                            not pointer_digest
                            or pointer_digest != actual_digest
                            or stored_digest and stored_digest != actual_digest
                        ):
                            raise ValueError("crosslink evidence is not pinned to the current immutable source digest")
                        if not source_pointer_has_character_span(pointer):
                            raise ValueError("crosslink evidence requires an exact character span")
                        validate_source_pointer(
                            pointer,
                            source_text_by_cluster={
                                document_id: str(document.content or ""),
                                str(pointer.get("source_cluster_id") or ""): str(document.content or ""),
                            },
                            end_mode="exclusive",
                            require_source_text=True,
                            require_text_match=True,
                        )

        superseded_edge_ids = {
            str(operation.properties.get("supersedes_edge_id") or "").strip()
            for operation in patch.operations
            if operation.kind == MaintenanceOperationKind.ADD_EDGE
            and operation.properties.get("supersedes_edge_id")
        }
        if superseded_edge_ids:
            namespace = WorkspaceNamespaces(ctx.workspace_id).curated_kg_space
            with _temporary_namespace(self.engines.kg, namespace):
                edges = self.engines.kg.read.get_edges(
                    ids=sorted(superseded_edge_ids), limit=len(superseded_edge_ids)
                )
            edges_by_id = {str(edge.id): edge for edge in edges}
            if set(edges_by_id) != superseded_edge_ids:
                raise ValueError("crosslink replacement must target existing workspace edges")
            for operation in patch.operations:
                target_id = str(operation.properties.get("supersedes_edge_id") or "").strip()
                if not target_id:
                    continue
                edge = edges_by_id[target_id]
                metadata = metadata_mapping(edge)
                status = str(metadata.get("crosslink_status") or "").strip().lower()
                edge_kind = str(metadata.get("edge_kind") or "").strip().lower()
                if (
                    str(metadata.get("workspace_id") or "") != ctx.workspace_id
                    or bool(metadata.get("source_native"))
                    or edge_kind in {"source_native", "has_child", "source_map"}
                    or status not in {"candidate", "accepted", "stale", "needs_revalidation"}
                ):
                    raise ValueError("crosslink replacement may supersede only a derived crosslink")
                edge_scope = str(metadata.get("acl_scope") or metadata.get("security_scope") or "").strip()
                if edge_scope and not can_access_security_scope(edge_scope):
                    raise PermissionError("superseded crosslink is outside the current security scope")
                endpoints = set(map(str, [*(edge.source_ids or []), *(edge.target_ids or [])]))
                proposed = {str(operation.from_node_id or ""), str(operation.to_node_id or "")}
                if endpoints != proposed:
                    raise ValueError("crosslink replacement must preserve the superseded edge endpoints")

    @staticmethod
    def _promote_crosslink_candidate(
        ctx: MaintenanceJobExecutionContext,
        patch: MaintenancePatch,
        *,
        authority_validated: bool = False,
    ) -> MaintenancePatch:
        if patch.intent != MaintenanceIntent.DERIVE_CROSSLINK_CANDIDATE:
            raise ValueError("crosslink validation requires a derived candidate patch")
        if any(
            str(operation.properties.get("crosslink_status") or "").strip().lower() != "candidate"
            for operation in patch.operations
        ):
            raise ValueError("crosslink validation requires candidate-status operations")
        blocked_statuses = {
            "expanding",
            "quality_unknown",
            "review_required",
            "stale",
            "inactive",
            "historical",
            "failed",
            "unknown",
        }
        for field_name in (
            "crosslink_parse_quality",
            "parse_quality_status",
            "source_region_status",
            "source_view_status",
        ):
            status = str(ctx.payload.get(field_name) or "").strip().lower()
            if status in blocked_statuses:
                raise ValueError(f"crosslink acceptance blocked by {field_name}={status}")
        if not authority_validated:
            raise ValueError("crosslink acceptance requires host-side current authority and evidence validation")
        for operation in patch.operations:
            provenance = operation.provenance
            if provenance is None or not provenance.source_pointers:
                raise ValueError("crosslink acceptance requires source pointers with exact character spans")
            if any(not source_pointer_has_character_span(pointer) for pointer in provenance.source_pointers):
                raise ValueError("crosslink acceptance requires source pointers with exact character spans")
        accepted_confidence = float(ctx.payload.get("accepted_confidence") or 0.0)
        if accepted_confidence < 0.8:
            raise ValueError("crosslink acceptance requires accepted_confidence >= 0.8")
        operations = [
            operation.model_copy(
                update={
                    "properties": {
                        **operation.properties,
                        "crosslink_status": "accepted",
                    },
                    "provenance": (
                        operation.provenance.model_copy(update={"confidence": accepted_confidence})
                        if operation.provenance is not None
                        else None
                    ),
                }
            )
            for operation in patch.operations
        ]
        promoted_operations: list[MaintenancePatchOperation] = []
        for operation in operations:
            superseded_id = str(operation.properties.get("supersedes_edge_id") or "").strip()
            if not superseded_id:
                promoted_operations.append(operation)
                continue
            if ctx.payload.get("superseded_edge_source_native") is True:
                raise ValueError("crosslink replacement cannot tombstone a source-native edge")
            promoted_operations.append(operation.model_copy(update={"supersedes_ids": [superseded_id]}))
            promoted_operations.append(
                MaintenancePatchOperation(
                    operation_id=f"retract:{superseded_id}",
                    kind=MaintenanceOperationKind.TOMBSTONE_EDGE,
                    edge_id=superseded_id,
                    reason=str(
                        ctx.payload.get("replacement_reason")
                        or "accepted crosslink replacement supersedes stale evidence"
                    ),
                    provenance=operation.provenance,
                )
            )
        operations = promoted_operations
        return patch.model_copy(
            update={"intent": MaintenanceIntent.ADD_CROSSLINK, "operations": operations}
        )

    @staticmethod
    def _dependency_invalidation_payload(
        plan: DependencyInvalidationPlan,
    ) -> dict[str, object]:
        return {
            "workspace_id": plan.workspace_id,
            "changed_entity_ids": list(plan.changed_entity_ids),
            "affected_entity_ids": list(plan.affected_entity_ids),
            "affected_source_document_ids": list(plan.affected_source_document_ids),
            "follow_up_kinds": list(plan.follow_up_kinds),
            "projection_entity_ids": list(plan.projection_entity_ids),
            "skipped_cross_workspace_ids": list(plan.skipped_cross_workspace_ids),
            "truncated": plan.truncated,
        }

    def _plan_and_enqueue_dependency_invalidation(
        self,
        ctx: MaintenanceJobExecutionContext,
        patch: MaintenancePatch,
    ) -> DependencyInvalidationPlan | None:
        """Schedule one bounded same-workspace follow-up per affected source."""

        if ctx.payload.get("maintenance_origin") == "dependency_invalidation":
            return None
        graph_engine = self.engines.kg
        namespace = WorkspaceNamespaces(ctx.workspace_id).curated_kg_space
        if patch.scope.scope_kind != "workspace":
            graph_engine = self.engines.conversation
            namespace = WorkspaceNamespaces(ctx.workspace_id).conv_bg
        changed_ids = {
            str(value).strip()
            for operation in patch.operations
            for value in (
                operation.node_id,
                operation.edge_id,
                operation.tombstone_target_id,
                operation.from_node_id,
                operation.to_node_id,
            )
            if value and str(value).strip()
        }
        if not changed_ids:
            return None
        try:
            with _temporary_namespace(graph_engine, namespace):
                nodes = graph_engine.read.get_nodes(limit=None)
                edges = graph_engine.read.get_edges(limit=None)
            plan = plan_dependency_invalidation(
                workspace_id=ctx.workspace_id,
                changed_entity_ids=changed_ids,
                nodes=nodes,
                edges=edges,
            )
            if plan.affected_source_document_ids and plan.follow_up_kinds:
                self._enqueue_dependency_jobs(ctx, plan)
            if plan.projection_entity_ids:
                self._enqueue_projection_dependency_jobs(ctx, plan)
            self._emit_trace(
                "maintenance_dependency_invalidation_planned",
                job_id=ctx.job_id,
                patch_id=patch.patch_id,
                **self._dependency_invalidation_payload(plan),
            )
            return plan
        except Exception as exc:
            self._emit_trace(
                "maintenance_dependency_invalidation_failed",
                workspace_id=ctx.workspace_id,
                job_id=ctx.job_id,
                patch_id=patch.patch_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            logger.exception("Dependency invalidation failed after patch %s", patch.patch_id)
            # Patch application is idempotent, so retrying the leased job is
            # safe and prevents a committed mutation losing its invalidation.
            raise

    def _enqueue_dependency_jobs(
        self,
        ctx: MaintenanceJobExecutionContext,
        plan: DependencyInvalidationPlan,
    ) -> None:
        ns = WorkspaceNamespaces(ctx.workspace_id)
        source_metadata: dict[str, dict[str, object]] = {}
        with _temporary_namespace(self.engines.kg, ns.source_space):
            revisions = self.engines.kg.read.get_nodes(
                where={"artifact_kind": "source_revision"},
                limit=None,
            )
        for revision in revisions:
            metadata = dict(getattr(revision, "metadata", {}) or {})
            if str(metadata.get("workspace_id") or "") != ctx.workspace_id:
                continue
            source_id = str(metadata.get("source_document_id") or "").strip()
            if not source_id or source_id not in plan.affected_source_document_ids:
                continue
            created_at = int(metadata.get("created_at_ms") or 0)
            previous = source_metadata.get(source_id)
            if previous is None or created_at > int(previous.get("created_at_ms") or 0):
                source_metadata[source_id] = metadata

        self.engines.conversation.jobs.require_available(enqueue=True)
        for source_id in plan.affected_source_document_ids:
            metadata = source_metadata.get(source_id)
            if metadata is None:
                continue
            for maintenance_kind in plan.follow_up_kinds:
                job_id = str(
                    stable_id(
                        "kogwistar_llm_wiki.dependency_invalidation_job",
                        ctx.workspace_id,
                        ctx.job_id,
                        source_id,
                        maintenance_kind,
                    )
                )
                self.engines.conversation.jobs.enqueue(
                    job_id=job_id,
                    namespace=ns.maintenance_jobs,
                    entity_kind="maintenance_job",
                    entity_id=source_id,
                    job_kind=f"maintenance_job:{maintenance_kind}",
                    op="UPSERT",
                    payload={
                        "workspace_id": ctx.workspace_id,
                        "request_node_id": job_id,
                        "source_document_id": source_id,
                        "maintenance_kind": maintenance_kind,
                        "maintenance_origin": "dependency_invalidation",
                        "mode": "invalidation",
                        "selection_strategy": "dependency_invalidation",
                        "seed_node_ids": [source_id],
                        "maintenance_round": 0,
                        "maintenance_max_rounds": 1,
                        "maintenance_plan": [maintenance_kind],
                        "maintenance_phase_index": 0,
                        "source_revision_id": metadata.get("source_revision_id"),
                        "source_digest": metadata.get("source_digest"),
                        "revision_document_id": metadata.get("revision_document_id"),
                        "required_stage": "parsed_graph_persisted",
                        "objective": "refresh dependencies after an accepted graph patch",
                        "budgets": {"max_steps": 1},
                        "dependency_invalidation": self._dependency_invalidation_payload(plan),
                        **self._derived_authority_payload(ctx),
                    },
                )

    def _enqueue_projection_dependency_jobs(
        self,
        ctx: MaintenanceJobExecutionContext,
        plan: DependencyInvalidationPlan,
    ) -> None:
        """Refresh existing projections without routing them through maintenance.

        Projection jobs have their own queue and worker.  Keeping this path
        separate prevents a graph patch from accidentally becoming a new
        maintenance thread or from losing the projection worker's CAS and
        manifest semantics.
        """

        ns = WorkspaceNamespaces(ctx.workspace_id)
        self.engines.conversation.jobs.require_available(enqueue=True)
        for entity_id in plan.projection_entity_ids[:256]:
            job_id = str(
                stable_id(
                    "kogwistar_llm_wiki.dependency_projection_request",
                    ctx.workspace_id,
                    ctx.job_id,
                    entity_id,
                )
            )
            self.engines.conversation.jobs.enqueue(
                job_id=job_id,
                namespace=ns.projection_jobs,
                entity_kind="projection_request",
                entity_id=entity_id,
                job_kind="projection_request",
                op="UPSERT",
                payload={
                    "workspace_id": ctx.workspace_id,
                    "promoted_entity_id": entity_id,
                    "projection_origin": "dependency_invalidation",
                    "maintenance_job_id": ctx.job_id,
                    "dependency_invalidation": self._dependency_invalidation_payload(plan),
                },
            )

    def _handle_runtime_workflow_strategy(self, ctx: MaintenanceJobExecutionContext) -> None:
        # Background distillation operates on already-materialized curated
        # candidates and deliberately has no single source revision to pin.
        # Request-bound workflows remain fenced by the immutable source
        # revision guard before any runtime work begins.
        if str(ctx.payload.get("mode") or "request") != "background":
            decision = self._evaluate_maintenance_guard(ctx)
            if decision.status != "ready":
                self._block_guarded_job(ctx, decision)
                return
        ns = WorkspaceNamespaces(ctx.workspace_id)
        workflow_id: str = workflow_id_for_maintenance_kind(ctx.maintenance_kind)
        payload: dict[str, object] = dict(ctx.payload)
        import warnings
        durable_usage = _durable_maintenance_usage(
            self.engines.conversation.meta_sqlite,
            namespace=ns.usage_events,
            maintenance_job_id=str(ctx.job_id or ctx.request_node_id),
        )
        budget_state = _maintenance_budget_state(
            ctx.payload,
            fair_scheduling=self.fair_scheduling,
            maintenance_steps_per_slice=self.maintenance_steps_per_slice,
            maintenance_llm_calls_per_slice=self.maintenance_llm_calls_per_slice,
            maintenance_seconds_per_slice=self.maintenance_seconds_per_slice,
            durable_usage=durable_usage,
        )
        if self.fair_scheduling:
            budget_state["budget_scope"] = "maintenance_slice"
        else:
            budget_state["budget_scope"] = "maintenance_job"
        budget_state.setdefault("budget_kind", "token")
        budget_ledger: StateBackedBudgetLedger = StateBackedBudgetLedger(budget_state)
        usage_persisted = False
        started_ms = int(time.time() * 1000)
        self._emit_trace(
            "maintenance_runtime_attempt_start",
            workspace_id=ctx.workspace_id,
            source_document_id=str(ctx.payload.get("source_document_id") or ""),
            request_node_id=ctx.request_node_id,
            job_id=ctx.job_id,
            maintenance_kind=ctx.maintenance_kind,
            continuation_run_id=str(ctx.payload.get("continuation_run_id") or "") or None,
            step_budget=budget_state.get("step_budget", 0),
            call_budget=budget_state.get("call_budget", 0),
            time_budget_ms=budget_state.get("time_budget_ms", 0),
            cost_budget=budget_state.get("cost_budget", 0),
            token_used=budget_state.get("token_used", 0),
            call_used=budget_state.get("call_used", 0),
            step_used=budget_state.get("step_used", 0),
            time_used_ms=budget_state.get("time_used_ms", 0),
            cost_used=budget_state.get("cost_used", 0),
        )
        with _background_namespace(self.engines.conversation, ns.conv_bg), _temporary_namespace(
            self.engines.workflow, ns.workflow_maintenance
        ):
            workflow_exists = self.engines.workflow.read.node_exists(
                where={
                    "$and": [
                        {"entity_type": "workflow_node"},
                        {"workflow_id": workflow_id},
                    ]
                },
            )
            if not workflow_exists:
                materialize_maintenance_designs(self.engines.workflow)
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    category=RuntimeWarning,
                    message="Using advanced underscore state key '_deps'",
                )
                try:
                    runtime_deps = {
                        "engines": self.engines,
                        "provider_settings": self.provider_settings,
                        "budget_ledger": budget_ledger,
                        "before_authoritative_write": lambda _subject=None: self._assert_claim_owned(
                            ctx,
                            reason="claim_lost_before_maintenance_artifact",
                        ),
                    }
                    continuation_run_id = str(payload.get("continuation_run_id") or "")
                    suspended_node_id = str(payload.get("suspended_node_id") or "")
                    suspended_token_id = str(payload.get("suspended_token_id") or "")
                    if continuation_run_id and suspended_node_id and suspended_token_id:
                        resume_kwargs = {
                            "run_id": continuation_run_id,
                            "suspended_node_id": suspended_node_id,
                            "suspended_token_id": suspended_token_id,
                            "client_result": RunSuccess(
                                state_update=[("u", {"_deps": runtime_deps})]
                            ),
                            "workflow_id": workflow_id,
                            "conversation_id": ns.conv_bg,
                            "turn_node_id": ctx.request_node_id,
                        }
                        # Kogwistar added authority propagation to resume_run
                        # after older deployed cores were already in use. Keep
                        # the worker compatible with both API shapes without
                        # retrying a possibly-mutating runtime call.
                        resume_signature = inspect.signature(self.runtime.resume_run)
                        if (
                            "_parent_authority_context" in resume_signature.parameters
                            or any(
                                parameter.kind is inspect.Parameter.VAR_KEYWORD
                                for parameter in resume_signature.parameters.values()
                            )
                        ):
                            resume_kwargs["_parent_authority_context"] = runtime_authority_context(
                                ctx.payload.get("authority_claims")
                                if isinstance(ctx.payload.get("authority_claims"), Mapping)
                                else None,
                                workspace_id=ctx.workspace_id,
                            )
                        result = self.runtime.resume_run(**resume_kwargs)
                    else:
                        result = self.runtime.run(
                            workflow_id=workflow_id,
                            initial_state={
                                "workspace_id": ctx.workspace_id,
                                "request_id": ctx.request_node_id,
                                "source_document_id": str(ctx.payload.get("source_document_id") or ""),
                                "maintenance_kind": ctx.maintenance_kind,
                                "maintenance_mode": str(ctx.payload.get("mode") or "request"),
                                "maintenance_candidates": list(ctx.payload.get("maintenance_candidates") or []),
                                "selection_strategy": str(ctx.payload.get("selection_strategy") or ""),
                                "maintenance_context": bound_maintenance_context(
                                    ctx.payload.get("maintenance_context")
                                    if isinstance(ctx.payload.get("maintenance_context"), Mapping)
                                    else None
                                ),
                                "_deps": runtime_deps,
                            },
                            conversation_id=ns.conv_bg,
                            turn_node_id=ctx.request_node_id,
                            _authority_context=runtime_authority_context(
                                ctx.payload.get("authority_claims")
                                if isinstance(ctx.payload.get("authority_claims"), Mapping)
                                else None,
                                workspace_id=ctx.workspace_id,
                            ),
                        )
                    status: str = result.status if hasattr(result, "status") else "finished"
                    runtime_errors = [
                        str(error) for error in (getattr(result, "errors", []) or [])
                    ]
                    logger.info(
                        "Maintenance job %s execution finished: %s (%s) errors=%s",
                        ctx.request_node_id,
                        status,
                        workflow_id,
                        runtime_errors,
                    )
                    self._emit_trace(
                        "maintenance_runtime_attempt_complete",
                        workspace_id=ctx.workspace_id,
                        source_document_id=str(ctx.payload.get("source_document_id") or ""),
                        request_node_id=ctx.request_node_id,
                        job_id=ctx.job_id,
                        maintenance_kind=ctx.maintenance_kind,
                        workflow_id=workflow_id,
                        runtime_status=status,
                        errors=runtime_errors,
                        run_id=str(getattr(result, "run_id", "") or "") or None,
                        duration_ms=int(time.time() * 1000) - started_ms,
                    )
                    terminal_success = status in {"succeeded", "completed", "success", "finished"}
                    reply_status = "completed" if terminal_success else status
                    if terminal_success and ctx.job_id:
                        if self._claim_lost.is_set():
                            self._emit_stale_claim_discarded(ctx, reason="claim_lost_before_runtime_commit")
                            return
                        if self._advance_maintenance_plan(ctx, budget_state=budget_state):
                            return
                        if self._claim_lost.is_set():
                            self._emit_stale_claim_discarded(ctx, reason="claim_lost_after_runtime_plan")
                            return
                        self._persist_maintenance_usage(ctx, budget_ledger, result)
                        usage_persisted = True
                        self._emit_lane_reply(
                            workspace_id=ctx.workspace_id,
                            source_document_id=str(ctx.payload.get("source_document_id") or ""),
                            request_node_id=ctx.request_node_id,
                            reply_to_message_id=ctx.lane_message_id or None,
                            status=reply_status,
                            payload={
                                "maintenance_kind": ctx.maintenance_kind,
                                "workflow_id": workflow_id,
                                "runtime_status": status,
                                **self._selection_result_payload(ctx),
                            },
                        )
                        self._acknowledge_job(ctx)
                    elif terminal_success:
                        self._emit_lane_reply(
                            workspace_id=ctx.workspace_id,
                            source_document_id=str(ctx.payload.get("source_document_id") or ""),
                            request_node_id=ctx.request_node_id,
                            reply_to_message_id=ctx.lane_message_id or None,
                            status=reply_status,
                            payload={
                                "maintenance_kind": ctx.maintenance_kind,
                                "workflow_id": workflow_id,
                                "runtime_status": status,
                            },
                        )
                    elif status == "suspended":
                        self._emit_lane_reply(
                            workspace_id=ctx.workspace_id,
                            source_document_id=str(ctx.payload.get("source_document_id") or ""),
                            request_node_id=ctx.request_node_id,
                            reply_to_message_id=ctx.lane_message_id or None,
                            status=reply_status,
                            payload={
                                "maintenance_kind": ctx.maintenance_kind,
                                "workflow_id": workflow_id,
                                "runtime_status": status,
                            },
                        )
                        if ctx.job_id:
                            self._requeue_suspended_maintenance_job(
                                ctx,
                                result,
                                budget_state=budget_state,
                            )
                    elif ctx.job_id:
                        self.engines.conversation.jobs.retry_or_fail(
                            ctx.job,
                            RuntimeError(f"maintenance workflow ended with status={status!r}"),
                        )
                except Exception as e:
                    self._emit_trace(
                        "maintenance_runtime_attempt_failed",
                        workspace_id=ctx.workspace_id,
                        source_document_id=str(ctx.payload.get("source_document_id") or ""),
                        request_node_id=ctx.request_node_id,
                        job_id=ctx.job_id,
                        maintenance_kind=ctx.maintenance_kind,
                        error_type=type(e).__name__,
                        error=str(e),
                        duration_ms=int(time.time() * 1000) - started_ms,
                    )
                    logger.exception("Maintenance job %s encountered runtime error", ctx.request_node_id)
                    self._emit_lane_reply(
                        workspace_id=ctx.workspace_id,
                        source_document_id=str(ctx.payload.get("source_document_id") or ""),
                        request_node_id=ctx.request_node_id,
                        reply_to_message_id=ctx.lane_message_id or None,
                        status="failed",
                            payload={
                                "maintenance_kind": ctx.maintenance_kind,
                                "workflow_id": workflow_id,
                                "error": str(e),
                                **self._selection_result_payload(ctx),
                        },
                    )
                    if ctx.job_id:
                        self.engines.conversation.jobs.retry_or_fail(ctx.job, e)
                finally:
                    if not usage_persisted:
                        try:
                            self._persist_maintenance_usage(
                                ctx,
                                budget_ledger,
                                locals().get("result"),
                            )
                        except Exception:
                            logger.exception(
                                "Failed to persist usage events for maintenance job %s",
                                ctx.request_node_id,
                            )
