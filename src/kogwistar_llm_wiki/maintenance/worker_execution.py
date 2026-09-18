"""Maintenance planning, guards, patches, and runtime execution."""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping

from kogwistar.engine_core.models import Grounding, Node, Span
from kogwistar.id_provider import stable_id
from kogwistar.runtime.budget import StateBackedBudgetLedger
from kogwistar.runtime.models import RunSuccess

from ..configuration.workspace import WorkspaceNamespaces
from ..maintenance import (
    MaintenanceJobExecutionContext,
    workflow_id_for_maintenance_kind,
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
    required_stage_for_maintenance,
)
from ..maintenance.maintenance_patch_apply import (
    apply_maintenance_patch_for_scope as _default_apply_maintenance_patch_for_scope,
)
from ..maintenance.maintenance_patches import MaintenancePatch
from ..maintenance.maintenance_planner import decide_next_maintenance_phase
from ..utils import _temporary_namespace
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
from .state import (
    persisted_budget_state as _persisted_budget_state,
)

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
        return evaluate_maintenance_guard(
            source_revision=current_revision,
            requested_revision_id=requested_revision_id,
            requested_digest=requested_digest,
            requested_revision_document_id=requested_revision_document_id,
            required_stage=required_stage,
            ready_revision_ids=ready_revision_ids,
        )

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
        with _temporary_namespace(self.engines.conversation, ns.conv_bg):
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
            result = apply_maintenance_patch_for_scope(
                self.engines,
                patch,
                namespace_prefix=str(ctx.payload.get("namespace_prefix") or "") or None,
            )
            invalidation = None
            if result.status.value == "applied":
                invalidation = self._plan_and_enqueue_dependency_invalidation(ctx, patch)
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
            self._emit_trace(
                "maintenance_dependency_invalidation_planned",
                job_id=ctx.job_id,
                patch_id=patch.patch_id,
                **self._dependency_invalidation_payload(plan),
            )
            return plan
        except Exception as exc:  # pragma: no cover - post-commit reporting guard
            self._emit_trace(
                "maintenance_dependency_invalidation_failed",
                workspace_id=ctx.workspace_id,
                job_id=ctx.job_id,
                patch_id=patch.patch_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
            logger.exception("Dependency invalidation failed after patch %s", patch.patch_id)
            return None

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
                        "budgets": {"steps": 1},
                        "dependency_invalidation": self._dependency_invalidation_payload(plan),
                    },
                )

    def _handle_runtime_workflow_strategy(self, ctx: MaintenanceJobExecutionContext) -> None:
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
        with _temporary_namespace(self.engines.conversation, ns.conv_bg), _temporary_namespace(
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
                        result = self.runtime.resume_run(
                            run_id=continuation_run_id,
                            suspended_node_id=suspended_node_id,
                            suspended_token_id=suspended_token_id,
                            client_result=RunSuccess(
                                state_update=[("u", {"_deps": runtime_deps})]
                            ),
                            workflow_id=workflow_id,
                            conversation_id=ns.conv_bg,
                            turn_node_id=ctx.request_node_id,
                        )
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
