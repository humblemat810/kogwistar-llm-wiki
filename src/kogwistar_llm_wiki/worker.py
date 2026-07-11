from __future__ import annotations

import logging
import time
import uuid
from abc import ABC, abstractmethod

from kogwistar.engine_core.jobs import JobQueueItem
from kg_doc_parser.workflow_ingest.providers import WorkflowProviderSettings
from kogwistar.engine_core.models import Grounding, Node, Span
from kogwistar.id_provider import stable_id
from kogwistar.maintenance.models import MaintenanceTemplateResult
from kogwistar.runtime import RunResult
from kogwistar.runtime.models import RunSuccess, StepRunResult
from kogwistar.runtime.resolvers import MappingStepResolver
from kogwistar.runtime.runtime import StepContext, WorkflowRuntime
from kogwistar.maintenance.template import run_grouped_maintenance_template
from kogwistar.wisdom.template import write_execution_wisdom_artifacts

from .models import NamespaceEngines
from .policies import LlmWikiPolicies, build_default_policies
from .maintenance_policy import (
    workflow_id_for_maintenance_kind,
)
from .maintenance_designs import materialize_maintenance_designs
from .maintenance_patch_apply import apply_maintenance_patch_for_scope
from .maintenance_patches import MaintenancePatch
from .maintenance_guards import (
    MaintenanceGuardDecision,
    SourceRevision,
    evaluate_maintenance_guard,
    required_stage_for_maintenance,
)
from .maintenance_strategies import (
    MaintenanceJobExecutionContext,
    MaintenanceStrategy,
    build_default_maintenance_strategy_registry,
)
from .namespaces import WorkspaceNamespaces
from .provider_config import resolve_maintenance_provider_settings
from .utils import _temporary_namespace
from kogwistar.runtime.budget import StateBackedBudgetLedger
from .usage_projection import UsageProjection, persist_usage_events


logger = logging.getLogger(__name__)


def _and_where(*clauses: dict[str, object]) -> dict[str, list[dict[str, object]]]:
    """Compose a Chroma-compatible conjunction filter from simple metadata clauses."""
    return {"$and": [dict(clause) for clause in clauses]}


class BaseWorker(ABC):
    """Base class for background workers polling the Kogwistar artifact stream."""

    def __init__(self, engines: NamespaceEngines) -> None:
        self.engines = engines

    def run_forever(self, workspace_id: str, interval: float = 5.0) -> None:
        """Main daemon loop."""
        logger.info(f"Starting worker loop for workspace {workspace_id}")
        while True:
            try:
                self.process_pending_jobs(workspace_id)
            except Exception as e:
                logger.error(f"Worker error in workspace {workspace_id}: {e}", exc_info=True)
            time.sleep(interval)

    @abstractmethod
    def process_pending_jobs(self, workspace_id: str) -> None:
        """Subclasses implement specific polling/processing logic."""
        pass


class MaintenanceWorker(BaseWorker):
    """
    Worker responsible for processing maintenance jobs using the durable job table
    and the graph-native runtime for the actual distillation work.
    """

    def __init__(
        self,
        engines: NamespaceEngines,
        eager_mode: bool = False,
        *,
        policies: LlmWikiPolicies | None = None,
        provider_settings: WorkflowProviderSettings | None = None,
        fair_scheduling: bool = False,
        maintenance_steps_per_slice: int = 0,
        maintenance_llm_calls_per_slice: int = 0,
        maintenance_seconds_per_slice: int = 0,
    ) -> None:
        """
        Initialize the MaintenanceWorker.

        Args:
            engines: The namespace engines to use.
            eager_mode: If True, the worker may skip certain delays or provide hooks for immediate execution.
        """
        super().__init__(engines)
        self.eager_mode = eager_mode
        self.policies = policies or build_default_policies()
        self.provider_settings: WorkflowProviderSettings = provider_settings or resolve_maintenance_provider_settings()
        self.fair_scheduling = bool(fair_scheduling)
        self.maintenance_steps_per_slice = max(0, int(maintenance_steps_per_slice))
        self.maintenance_llm_calls_per_slice = max(0, int(maintenance_llm_calls_per_slice))
        self.maintenance_seconds_per_slice = max(0, int(maintenance_seconds_per_slice))
        self.strategy_registry = build_default_maintenance_strategy_registry()
        self.resolver = MappingStepResolver()
        self.resolver.register("distill")(self._step_distill)
        self.resolver.register("check_done")(self._step_check_done)
        self.resolver.register("distill_from_history")(self.derive_problem_solving_wisdom_from_history)
        self.resolver.register("derive_problem_solving_wisdom_from_history")(self.derive_problem_solving_wisdom_from_history)
        self.resolver.register("noop")(self._step_noop)

        self.runtime = WorkflowRuntime(
            workflow_engine=self.engines.workflow,
            conversation_engine=self.engines.conversation,
            step_resolver=self.resolver,
            predicate_registry={},
        )

    def process_pending_jobs(self, workspace_id: str) -> None:
        """
        Finds and processes maintenance jobs for a given workspace.
        The durable index job table is authoritative; graph nodes are retained only
        as audit artifacts.
        """
        ns = WorkspaceNamespaces(workspace_id)
        self.engines.conversation.jobs.require_available(claim=True)
        claim_limit = 1 if self.fair_scheduling else 50
        while True:
            jobs = self.engines.conversation.jobs.claim(
                limit=claim_limit,
                lease_seconds=60,
                namespace=ns.maintenance_jobs,
            )
            if not jobs:
                break
            for job in jobs:
                try:
                    self._handle_job(workspace_id, job)
                except Exception as exc:
                    logger.error(
                        "Maintenance worker failed to process claimed job for workspace %s: %s",
                        workspace_id,
                        exc,
                        exc_info=True,
                    )
                    self.engines.conversation.jobs.retry_or_fail(job, exc)
                    raise
                if self.fair_scheduling:
                    return

    def _handle_job(self, workspace_id: str, job: JobQueueItem) -> None:
        job_id = str(job.job_id)
        payload = dict(job.payload)
        req_node_id = str(payload.get("request_node_id") or job_id)
        lane_message_id = str(payload.get("lane_message_id") or "")
        maintenance_kind = str(payload.get("maintenance_kind") or "distill")
        request_node = self._load_request_node(workspace_id, req_node_id)
        if request_node is not None:
            maintenance_kind = str(
                payload.get("maintenance_kind")
                or getattr(request_node, "metadata", {}).get("maintenance_kind")
                or "distill"
            )
        logger.info("Processing maintenance job %s", req_node_id)
        ctx = MaintenanceJobExecutionContext(
            workspace_id=workspace_id,
            job=job,
            job_id=job_id,
            payload=payload,
            request_node=request_node,
            request_node_id=req_node_id,
            lane_message_id=lane_message_id,
            maintenance_kind=maintenance_kind,
        )
        decision = self._evaluate_maintenance_guard(ctx)
        if decision.status != "ready":
            self._block_guarded_job(ctx, decision)
            return
        strategy: MaintenanceStrategy = self.strategy_registry.resolve(maintenance_kind)
        strategy.handle(self, ctx)

    def _evaluate_maintenance_guard(
        self,
        ctx: MaintenanceJobExecutionContext,
    ) -> MaintenanceGuardDecision:
        """Verify that a claimed job still targets the current ready source attempt."""
        source_document_id = str(ctx.payload.get("source_document_id") or "")
        requested_revision_id = str(ctx.payload.get("source_revision_id") or "")
        requested_digest = str(ctx.payload.get("source_digest") or "")
        required_stage = str(
            ctx.payload.get("required_stage")
            or required_stage_for_maintenance(ctx.maintenance_kind)
        )
        if not source_document_id or not requested_revision_id or not requested_digest:
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
            emitted: list[str] = self._emit_execution_wisdom_from_history(ctx.workspace_id, self.engines)
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
                    "execution_wisdom_emitted": emitted,
                },
            )
            if ctx.job_id:
                self.engines.conversation.jobs.mark_done(ctx.job_id)
        except Exception as e:
            logger.error(f"Maintenance job {ctx.request_node_id} encountered runtime error: {e}", exc_info=True)
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

    def _handle_graph_patch_apply_strategy(self, ctx: MaintenanceJobExecutionContext) -> None:
        decision = self._evaluate_maintenance_guard(ctx)
        if decision.status != "ready":
            self._block_guarded_job(ctx, decision)
            return
        patch_payload = ctx.payload.get("patch")
        if not isinstance(patch_payload, dict):
            self._handle_runtime_workflow_strategy(ctx)
            return
        try:
            patch: MaintenancePatch = MaintenancePatch.model_validate(patch_payload)
            result = apply_maintenance_patch_for_scope(
                self.engines,
                patch,
                namespace_prefix=str(ctx.payload.get("namespace_prefix") or "") or None,
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
                },
            )
            if result.status.value == "applied":
                if ctx.job_id:
                    self.engines.conversation.jobs.mark_done(ctx.job_id)
            else:
                raise RuntimeError(f"graph patch apply did not complete: {result.status.value}")
        except Exception as e:
            logger.error(f"Maintenance job {ctx.request_node_id} encountered graph patch apply error: {e}", exc_info=True)
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

    def _handle_runtime_workflow_strategy(self, ctx: MaintenanceJobExecutionContext) -> None:
        decision = self._evaluate_maintenance_guard(ctx)
        if decision.status != "ready":
            self._block_guarded_job(ctx, decision)
            return
        ns = WorkspaceNamespaces(ctx.workspace_id)
        workflow_id: str = workflow_id_for_maintenance_kind(ctx.maintenance_kind)
        payload: dict[str, object] = dict(ctx.payload)
        import warnings
        budget_state: dict[str, str | int] = {
            "token_budget": 10_000_000,
            "budget_scope": "run",
            "budget_kind": "token",
        }
        if self.fair_scheduling:
            if self.maintenance_steps_per_slice:
                budget_state["step_budget"] = self.maintenance_steps_per_slice
            if self.maintenance_llm_calls_per_slice:
                budget_state["call_budget"] = self.maintenance_llm_calls_per_slice
            if self.maintenance_seconds_per_slice:
                budget_state["time_budget_ms"] = self.maintenance_seconds_per_slice * 1000
        budget_ledger: StateBackedBudgetLedger = StateBackedBudgetLedger(budget_state)
        with _temporary_namespace(self.engines.conversation, ns.conv_bg), _temporary_namespace(
            self.engines.workflow, ns.workflow_maintenance
        ):
            try:
                workflow_exists = self.engines.workflow.read.node_exists(
                    where={
                        "$and": [
                            {"entity_type": "workflow_node"},
                            {"workflow_id": workflow_id},
                        ]
                    },
                )
            except Exception:
                raise
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
                                "maintenance_kind": ctx.maintenance_kind,
                                "_deps": runtime_deps,
                            },
                            conversation_id=ns.conv_bg,
                            turn_node_id=ctx.request_node_id,
                        )
                    status: str = result.status if hasattr(result, "status") else "finished"
                    logger.info(
                        "Maintenance job %s execution finished: %s (%s)",
                        ctx.request_node_id,
                        status,
                        workflow_id,
                    )
                    terminal_success = status in {"succeeded", "completed", "success", "finished"}
                    reply_status = "completed" if terminal_success else status
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
                    if terminal_success and ctx.job_id:
                        self.engines.conversation.jobs.mark_done(ctx.job_id)
                    elif status == "suspended" and ctx.job_id:
                        self._requeue_suspended_maintenance_job(ctx, result)
                    elif ctx.job_id:
                        self.engines.conversation.jobs.retry_or_fail(
                            ctx.job,
                            RuntimeError(f"maintenance workflow ended with status={status!r}"),
                        )
                except Exception as e:
                    logger.error(f"Maintenance job {ctx.request_node_id} encountered runtime error: {e}", exc_info=True)
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
                        },
                    )
                    if ctx.job_id:
                        self.engines.conversation.jobs.retry_or_fail(ctx.job, e)
                finally:
                    try:
                        persist_usage_events(
                            self.engines.conversation.meta_sqlite,
                            namespace=ns.usage_events,
                            events=budget_ledger.events,
                            workspace_id=ctx.workspace_id,
                            attempt_id=str(
                                getattr(locals().get("result"), "run_id", None)
                                or uuid.uuid4()
                            ),
                            source_document_id=str(ctx.payload.get("source_document_id") or "") or None,
                            operation_id=str(ctx.job_id or ctx.request_node_id),
                            operation_kind=ctx.maintenance_kind,
                            maintenance_job_id=str(ctx.job_id or ctx.request_node_id),
                            provider=self.provider_settings.parser.provider,
                            model=self.provider_settings.parser.model,
                        )
                        UsageProjection(
                            self.engines.conversation.meta_sqlite,
                            workspace_id=ctx.workspace_id,
                            source_namespace=ns.usage_events,
                            projection_namespace=ns.usage_projection,
                        ).refresh()
                    except Exception:
                        logger.exception(
                            "Failed to persist usage events for maintenance job %s",
                            ctx.request_node_id,
                        )

    def _requeue_suspended_maintenance_job(
        self,
        ctx: MaintenanceJobExecutionContext,
        result: RunResult,
    ) -> None:
        suspended = list(
            (getattr(result, "final_state", {}) or {})
            .get("_rt_join", {})
            .get("suspended", [])
        )
        if not suspended:
            raise RuntimeError("maintenance workflow suspended without a resumable frontier")
        next_node_id, _mask, next_token_id, _parent_token_id = suspended[0]
        next_payload = dict(ctx.payload)
        next_payload.update(
            {
                "continuation_run_id": str(getattr(result, "run_id", "")),
                "suspended_node_id": str(next_node_id),
                "suspended_token_id": str(next_token_id),
            }
        )
        self.engines.conversation.jobs.requeue_at_tail(
            ctx.job,
            payload=next_payload,
        )

    def _emit_lane_reply(
        self,
        *,
        workspace_id: str,
        source_document_id: str,
        request_node_id: str,
        reply_to_message_id: str | None,
        status: str,
        payload: dict[str, object],
    ) -> None:
        if not reply_to_message_id:
            return
        ns = WorkspaceNamespaces(workspace_id)
        msg_type = f"reply.maintenance.{status}"
        correlation_id = reply_to_message_id
        reply_idempotency_key = str(
            stable_id(
                "kogwistar_llm_wiki.maintenance_reply",
                workspace_id,
                reply_to_message_id,
                msg_type,
                correlation_id,
            )
        )
        with _temporary_namespace(self.engines.conversation, ns.conv_bg):
            lane_status = status if status in {"completed", "failed", "cancelled", "suspended"} else "failed"
            lane_error = payload if lane_status in {"failed", "cancelled"} else None
            lane_completed = lane_status in {"completed", "failed", "cancelled"}
            reply_message_id: str | None = None
            existing_reply = self.engines.conversation.read.get_nodes(
                where={
                    "$and": [
                        {"artifact_kind": "lane_message"},
                        {"idempotency_key": reply_idempotency_key},
                    ],
                },
                limit=1,
            )
            if not existing_reply:
                existing_reply = self.engines.conversation.read.get_nodes(
                    where={
                        "$and": [
                            {"artifact_kind": "lane_message"},
                            {"reply_to_message_id": reply_to_message_id},
                            {"msg_type": msg_type},
                            {"correlation_id": correlation_id},
                        ],
                    },
                    limit=1,
                )
            if not existing_reply:
                sent_reply = self.engines.conversation.send_lane_message(
                    conversation_id=f"maintenance:{source_document_id or request_node_id}",
                    inbox_id="inbox:foreground",
                    sender_id="lane:worker:maintenance",
                    recipient_id="lane:foreground",
                    msg_type=msg_type,
                    payload={
                        "workspace_id": workspace_id,
                        "request_node_id": request_node_id,
                        **payload,
                    },
                    reply_to=reply_to_message_id,
                    correlation_id=correlation_id,
                    idempotency_key=reply_idempotency_key,
                )
                reply_message_id = str(sent_reply.message_id)
            else:
                reply_message_id = str(existing_reply[0].id)
            if reply_message_id:
                self.engines.conversation.update_lane_message_status(
                    message_id=reply_message_id,
                    status=lane_status,
                    error=lane_error,
                    completed=lane_completed,
                )
            self.engines.conversation.update_lane_message_status(
                message_id=reply_to_message_id,
                status=lane_status,
                error=lane_error,
                completed=lane_completed,
            )

    def _load_request_node(self, workspace_id: str, req_node_id: str) -> Node | None:
        ns = WorkspaceNamespaces(workspace_id)
        with _temporary_namespace(self.engines.conversation, ns.conv_bg):
            nodes: list[Node] = self.engines.conversation.read.get_nodes(
                where={
                    "$and": [
                        {"workspace_id": workspace_id},
                        {"id": req_node_id},
                    ]
                }
            )
        if nodes:
            return nodes[0]
        return None

    def _step_distill(self, ctx: StepContext) -> StepRunResult:
        """
        Resolver step for distillation: aggregates promoted knowledge into
        derived-knowledge artifacts.
        """
        workspace_id = ctx.state_view.get("workspace_id")
        _deps_raw = ctx.state_view.get("_deps")
        if isinstance(_deps_raw, dict):
            engines: NamespaceEngines | None = _deps_raw.get("engines")
        else:
            engines = _deps_raw
        if not workspace_id or not engines:
            logger.error("Missing workspace_id or engines in distillation step context")
            return RunSuccess(state_update=[("u", {"error": "Missing context"})])

        ns = WorkspaceNamespaces(workspace_id)
        with _temporary_namespace(engines.kg, ns.curated_kg_space):
            promoted_nodes: list[Node] = engines.kg.read.get_nodes(
                where=_and_where(
                    {"artifact_kind": "promoted_knowledge"},
                    {"workspace_id": workspace_id},
                )
            )

        if not promoted_nodes:
            return RunSuccess(state_update=[("u", {"distillation_complete": True})])

        derived_engine = engines.derived_knowledge_engine()
        template_result: MaintenanceTemplateResult = run_grouped_maintenance_template(
            engines.kg,
            target_engine=derived_engine,
            source_namespace=ns.curated_kg_space,
            target_namespace=ns.derived_knowledge,
            source_where=self.policies.derived_knowledge.source_query(
                workspace_id=workspace_id,
            ).where,
            group_key_for_node=self.policies.derived_knowledge.group_key,
            match_where_for_group=lambda label: self.policies.derived_knowledge.match_where(
                workspace_id=workspace_id,
                label=label,
            ),
            build_node_for_group=lambda label, nodes, existing, created_at_ms: self._build_derived_node_for_group(
                workspace_id=workspace_id,
                label=label,
                nodes=nodes,
                existing=existing,
                created_at_ms=created_at_ms,
                policies=self.policies,
                fallback_span_factory=lambda: Grounding(spans=[Span(
                    collection_page_url=f"conversation/{ns.conv_bg}",
                    document_page_url=f"conversation/{ns.conv_bg}",
                    doc_id=f"conv:{ns.conv_bg}",
                    insertion_method="workflow_trace",
                    page_number=1,
                    start_char=0,
                    end_char=1,
                    excerpt=f"distilled:{label}",
                    context_before="",
                    context_after="",
                    chunk_id=None,
                    source_cluster_id=None,
                )]),
            ),
        )
        for result in template_result.grouped_results:
            logger.info(
                "Derived knowledge synthesis for entity '%s' with %s source nodes.",
                result.group_key,
                result.source_node_count,
            )

        return RunSuccess(
            state_update=[("u", {
                "distillation_complete": True,
                "derived_knowledge_complete": True,
                "distilled_entities": list(template_result.emitted_group_keys),
            })]
        )

    def _step_check_done(self, ctx: StepContext) -> StepRunResult:
        """Resolver step that cleanly finalizes derived-knowledge maintenance."""
        workspace_id = ctx.state_view.get("workspace_id")
        _deps_raw = ctx.state_view.get("_deps")
        engines: NamespaceEngines | None = _deps_raw.get("engines") if isinstance(_deps_raw, dict) else _deps_raw
        if not workspace_id or not engines:
            logger.error("Missing workspace_id or engines in maintenance completion step context")
            return RunSuccess(state_update=[("u", {"error": "Missing context"})])

        return RunSuccess(
            state_update=[("u", {
                "maintenance_complete": True,
            })]
        )

    def _build_derived_node_for_group(
        self,
        *,
        workspace_id: str,
        label: str,
        nodes: list[Node],
        existing: list[Node],
        created_at_ms: int,
        policies: LlmWikiPolicies,
        fallback_span_factory,
    ) -> Node:
        raw_mentions: list[Grounding] = []
        for node in nodes:
            if hasattr(node, "mentions") and node.mentions:
                raw_mentions.extend(node.mentions)

        merged_mentions: list[Grounding] = []
        seen_mentions: set[str] = set()
        for mention in raw_mentions:
            try:
                mention_key = mention.model_dump_json()
            except (AttributeError, Exception):
                mention_key = str(mention)

            if mention_key not in seen_mentions:
                merged_mentions.append(mention)
                seen_mentions.add(mention_key)

        if not merged_mentions:
            merged_mentions = [fallback_span_factory()]

        source_node_ids = sorted(str(node.id) for node in nodes)

        return Node(
            id=str(stable_id("derived_knowledge", workspace_id, label, *source_node_ids)),
            label=label,
            type="entity",
            summary=f"Derived knowledge synthesis for {label} aggregated from {len(nodes)} source documents.",
            mentions=merged_mentions,
            metadata=policies.derived_knowledge.build_metadata(
                workspace_id=workspace_id,
                label=label,
                source_node_ids=source_node_ids,
                replaces_ids=policies.lifecycle.replacement_ids(existing),
                created_at_ms=created_at_ms,
            ),
        )

    def _emit_execution_wisdom_from_history(self, workspace_id: str, engines: NamespaceEngines) -> list[str]:
        """Analyze completed execution history and emit execution-derived wisdom."""
        if not workspace_id or not engines:
            return []

        ns = WorkspaceNamespaces(workspace_id)
        result_items = write_execution_wisdom_artifacts(
            engines.conversation,
            target_engine=engines.wisdom,
            source_namespace=ns.conv_bg,
            target_namespace=ns.wisdom,
            source_where=self.policies.wisdom.source_query(workspace_id=workspace_id).where,
            min_failure_signals=self.policies.wisdom.min_failure_signals,
            match_where_for_pattern=lambda pattern: self.policies.wisdom.match_where(
                workspace_id=workspace_id,
                step_op=pattern.step_op,
            ),
            build_node_for_pattern=lambda pattern, existing, created_at_ms: Node(
                id=str(
                    stable_id(
                        "execution_wisdom",
                        workspace_id,
                        pattern.step_op,
                        *sorted(str(node.id) for node in pattern.failure_nodes),
                    )
                ),
                label=f"execution_failure_pattern:{pattern.step_op}",
                type="entity",
                summary=(
                    f"Repeated failure pattern detected for workflow step '{pattern.step_op}' "
                    f"({len(pattern.failure_nodes)} occurrences across {len(pattern.run_ids)} runs). "
                    "Investigate step resolver, input contract, or upstream data quality."
                ),
                mentions=[Grounding(spans=[Span(
                    collection_page_url=f"conversation/{ns.conv_bg}",
                    document_page_url=f"conversation/{ns.conv_bg}",
                    doc_id=f"conv:{ns.conv_bg}",
                    insertion_method="execution_history",
                    page_number=1,
                    start_char=0,
                    end_char=1,
                    excerpt=f"failure_pattern:{pattern.step_op} n={len(pattern.failure_nodes)}",
                    context_before="",
                    context_after="",
                    chunk_id=None,
                    source_cluster_id=None,
                )])],
                metadata=self.policies.wisdom.build_metadata(
                    workspace_id=workspace_id,
                    step_op=pattern.step_op,
                    failure_count=len(pattern.failure_nodes),
                    evidence_run_ids=list(pattern.run_ids),
                    replaces_ids=self.policies.lifecycle.replacement_ids(existing),
                    created_at_ms=created_at_ms,
                )
                | {
                    "label": f"execution_failure_pattern:{pattern.step_op}",
                },
            ),
        )

        emitted = [result.step_op for result in result_items]
        for result in result_items:
            logger.info(
                f"Emitted execution_wisdom for step_op='{result.step_op}' "
                f"(failures={result.failure_count}, runs={len(result.run_ids)})"
            )

        return emitted

    def derive_problem_solving_wisdom_from_history(self, ctx: StepContext) -> StepRunResult:
        """Resolver wrapper for workflow-native execution-wisdom extraction."""
        workspace_id = ctx.state_view.get("workspace_id")
        _deps_raw = ctx.state_view.get("_deps")
        engines = _deps_raw.get("engines") if isinstance(_deps_raw, dict) else _deps_raw
        emitted = self._emit_execution_wisdom_from_history(workspace_id, engines)
        return RunSuccess(
            state_update=[("u", {
                "history_wisdom_complete": True,
                "execution_wisdom_emitted": emitted,
            })]
        )

    def _step_distill_from_history(self, ctx: StepContext) -> StepRunResult:
        """Compatibility alias for older workflow step names."""
        return self.derive_problem_solving_wisdom_from_history(ctx)

    def _step_noop(self, ctx: StepContext) -> StepRunResult:
        """Resolver step for terminal/noop nodes."""
        return RunSuccess(state_update=[])
