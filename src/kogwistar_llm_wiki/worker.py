from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping

from kogwistar.engine_core.jobs import JobQueueItem
from kg_doc_parser.semantic_document_splitting_layerwise_edits import parser_llm_cache_transaction
from kg_doc_parser.workflow_ingest.providers import WorkflowProviderSettings
from kogwistar.engine_core.models import Grounding, Node, Span, GraphExtractionWithIDs
from kogwistar.id_provider import stable_id
from kogwistar.maintenance.models import MaintenanceTemplateResult
from kogwistar.runtime import RunResult
from kogwistar.runtime import budget_event_from_dict
from kogwistar.runtime.budget_adapters import summarize_budget_events
from kogwistar.runtime.models import RunSuccess, StepRunResult
from kogwistar.runtime.resolvers import MappingStepResolver
from kogwistar.runtime.runtime import StepContext, WorkflowRuntime
from kogwistar.maintenance.template import run_grouped_maintenance_template
from kogwistar.wisdom.template import write_execution_wisdom_artifacts

from .models import NamespaceEngines
from .policies import LlmWikiPolicies, build_default_policies
from .maintenance_policy import (
    is_execution_wisdom_kind,
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
from .maintenance_statistics import operation_category
from .maintenance_planner import decide_next_maintenance_phase
from .ingest_pipeline import IngestPipeline, IngestPipelineRequest


logger = logging.getLogger(__name__)


def _and_where(*clauses: dict[str, object]) -> dict[str, list[dict[str, object]]]:
    """Compose a Chroma-compatible conjunction filter from simple metadata clauses."""
    return {"$and": [dict(clause) for clause in clauses]}


def _persisted_budget_state(state: Mapping[str, object]) -> dict[str, object]:
    """Keep only JSON-safe cumulative budget fields on a requeued job."""
    allowed = {
        "token_budget", "token_used", "step_budget", "step_used", "call_budget",
        "call_used", "time_budget_ms", "time_used_ms", "cost_budget", "cost_used",
        "request_token_budget", "request_step_budget", "request_call_budget",
        "request_time_budget_ms", "request_cost_budget",
    }
    return {
        key: value
        for key, value in state.items()
        if key in allowed and isinstance(value, (int, float)) and not isinstance(value, bool)
    }


def _maintenance_budget_state(
    payload: Mapping[str, object],
    *,
    fair_scheduling: bool,
    maintenance_steps_per_slice: int,
    maintenance_llm_calls_per_slice: int,
    maintenance_seconds_per_slice: int,
    durable_usage: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build the current attempt's ledger from durable request/job state.

    Request limits and usage survive phase requeues. Fair-scheduling limits are
    raised one slice at a time so they do not turn a cumulative job counter into
    an accidental per-slice reset.
    """
    previous = payload.get("maintenance_budget_state")
    state: dict[str, object] = dict(previous) if isinstance(previous, Mapping) else {}
    budgets = payload.get("budgets")
    budgets = budgets if isinstance(budgets, Mapping) else {}

    token_limit = int(budgets.get("max_tokens") or 10_000_000)
    call_limit = int(budgets.get("max_llm_calls") or 0)
    step_limit = int(budgets.get("max_steps") or 0)
    time_limit_ms = int(float(budgets.get("max_time_seconds") or 0) * 1000)
    cost_limit = float(budgets.get("max_cost_usd") or 0.0)
    request_limits = {
        "request_token_budget": token_limit,
        "request_call_budget": call_limit,
        "request_step_budget": step_limit,
        "request_time_budget_ms": time_limit_ms,
        "request_cost_budget": cost_limit,
    }
    state.update(request_limits)
    used = {
        key: state.get(key, 0)
        for key in ("token_used", "call_used", "step_used", "time_used_ms", "cost_used")
    }
    for key, value in (durable_usage or {}).items():
        if key not in used or isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        used[key] = max(float(used[key] or 0), float(value))
        state[key] = int(used[key]) if key != "cost_used" else float(used[key])
    state["token_budget"] = token_limit
    state["call_budget"] = call_limit
    state["step_budget"] = step_limit
    state["time_budget_ms"] = time_limit_ms
    state["cost_budget"] = cost_limit

    if fair_scheduling:
        slice_limits = {
            "step_budget": maintenance_steps_per_slice,
            "call_budget": maintenance_llm_calls_per_slice,
            "time_budget_ms": maintenance_seconds_per_slice * 1000,
        }
        request_keys = {
            "step_budget": "request_step_budget",
            "call_budget": "request_call_budget",
            "time_budget_ms": "request_time_budget_ms",
        }
        used_keys = {
            "step_budget": "step_used",
            "call_budget": "call_used",
            "time_budget_ms": "time_used_ms",
        }
        for limit_key, slice_limit in slice_limits.items():
            if not slice_limit:
                continue
            request_limit = int(state[request_keys[limit_key]] or 0)
            slice_cap = int(used[used_keys[limit_key]] or 0) + slice_limit
            state[limit_key] = min(request_limit, slice_cap) if request_limit else slice_cap
    return state


def _durable_maintenance_usage(
    meta: object,
    *,
    namespace: str,
    maintenance_job_id: str,
) -> dict[str, object]:
    """Recover budget usage from authoritative events after a failed retry."""
    iterator = getattr(meta, "iter_entity_events", None)
    if not callable(iterator) or not maintenance_job_id:
        return {}
    events = []
    try:
        rows = iterator(namespace=namespace, from_seq=1, batch_size=500)
        for _seq, _event_id, _entity_kind, _entity_id, payload_json in rows:
            try:
                payload = json.loads(payload_json)
                attribution = payload.get("attribution")
                if not isinstance(attribution, Mapping):
                    continue
                if str(attribution.get("maintenance_job_id") or "") != maintenance_job_id:
                    continue
                if payload.get("artifact_kind") != "usage_event":
                    continue
                events.append(budget_event_from_dict(payload))
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
    except Exception:
        logger.exception("Failed to recover durable usage for maintenance job %s", maintenance_job_id)
        return {}
    summary = summarize_budget_events(events)
    token_used = sum(
        float(event.amount or 0)
        for event in events
        if event.kind in {"debit", "token"}
        and event.unit not in {"call", "llm_call", "step", "ms"}
    )
    return {
        "token_used": int(token_used),
        "call_used": sum(1 for event in events if event.unit in {"call", "llm_call"}),
        "step_used": sum(int(event.amount or 0) for event in events if event.unit == "step"),
        "time_used_ms": int(summary.get("time_ms", 0) or 0),
        "cost_used": float(summary.get("total_cost", 0.0) or 0.0),
    }


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
        worker_id: str | None = None,
        trace_sink: Callable[[dict[str, object]], None] | None = None,
        document_parser: Callable[[MaintenanceJobExecutionContext], Mapping[str, object]] | None = None,
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
        self.worker_id = str(worker_id or f"maintenance-worker-{uuid.uuid4().hex[:12]}")
        self.lease_seconds = 150
        self.lease_renew_interval_seconds = 30
        self.lease_progress_grace_seconds = 90
        self._last_progress_monotonic = time.monotonic()
        self._claim_lost = threading.Event()
        self.trace_sink = trace_sink
        self.document_parser = document_parser or self._parse_seeded_document
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
        poll_started_ms = int(time.time() * 1000)
        self._emit_trace(
            "maintenance_poll_start",
            workspace_id=workspace_id,
            namespace=ns.maintenance_jobs,
            fair_scheduling=self.fair_scheduling,
        )
        self.engines.conversation.jobs.require_available(claim=True)
        claim_limit = 1 if self.fair_scheduling else 50
        while True:
            jobs = self.engines.conversation.jobs.claim(
                limit=claim_limit,
                lease_seconds=getattr(self, "lease_seconds", 150),
                namespace=ns.maintenance_jobs,
            )
            if not jobs:
                self._emit_trace(
                    "maintenance_poll_complete",
                    workspace_id=workspace_id,
                    namespace=ns.maintenance_jobs,
                    duration_ms=int(time.time() * 1000) - poll_started_ms,
                    reason="queue_empty",
                )
                break
            self._emit_trace(
                "maintenance_jobs_claimed",
                workspace_id=workspace_id,
                namespace=ns.maintenance_jobs,
                claim_limit=claim_limit,
                claimed_count=len(jobs),
                job_ids=[str(job.job_id) for job in jobs],
            )
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
                    self._emit_trace(
                        "maintenance_poll_complete",
                        workspace_id=workspace_id,
                        namespace=ns.maintenance_jobs,
                        duration_ms=int(time.time() * 1000) - poll_started_ms,
                        reason="fair_slice_complete",
                    )
                    return

    def _handle_job(self, workspace_id: str, job: JobQueueItem) -> None:
        job = self.engines.conversation.jobs.coerce(job)
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
        job_started_ms = int(time.time() * 1000)
        self._emit_trace(
            "maintenance_job_start",
            workspace_id=workspace_id,
            source_document_id=str(payload.get("source_document_id") or ""),
            request_node_id=req_node_id,
            job_id=job_id,
            maintenance_kind=maintenance_kind,
            continuation=bool(payload.get("continuation_run_id")),
            lease_seconds=self.lease_seconds,
        )
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
        # History-wide wisdom extraction has no source revision to fence.  It
        # intentionally consumes workflow history across the workspace, while
        # source-dependent strategies must still pass the revision/readiness
        # guard before touching graph state.
        if not is_execution_wisdom_kind(maintenance_kind):
            decision = self._evaluate_maintenance_guard(ctx)
            if decision.status != "ready":
                self._block_guarded_job(ctx, decision)
                self._emit_trace(
                    "maintenance_job_blocked",
                    workspace_id=workspace_id,
                    source_document_id=str(payload.get("source_document_id") or ""),
                    request_node_id=req_node_id,
                    job_id=job_id,
                    reason=decision.reason,
                    duration_ms=int(time.time() * 1000) - job_started_ms,
                )
                return
        strategy: MaintenanceStrategy = self.strategy_registry.resolve(maintenance_kind)
        self._emit_trace(
            "maintenance_strategy_start",
            workspace_id=workspace_id,
            source_document_id=str(payload.get("source_document_id") or ""),
            request_node_id=req_node_id,
            job_id=job_id,
            maintenance_kind=maintenance_kind,
        )
        lease_stop = threading.Event()
        self._claim_lost.clear()
        lease_thread = threading.Thread(
            target=self._renew_claim_while_progressing,
            args=(ctx, lease_stop),
            name=f"{self.worker_id}-lease",
            daemon=True,
        )
        lease_thread.start()
        try:
            strategy.handle(self, ctx)
        finally:
            lease_stop.set()
            lease_thread.join(timeout=2)
            self._emit_trace(
                "maintenance_job_dispatch_complete",
                workspace_id=workspace_id,
                source_document_id=str(payload.get("source_document_id") or ""),
                request_node_id=req_node_id,
                job_id=job_id,
                maintenance_kind=maintenance_kind,
                duration_ms=int(time.time() * 1000) - job_started_ms,
            )

    def _renew_claim_while_progressing(
        self, ctx: MaintenanceJobExecutionContext, stop: threading.Event
    ) -> None:
        """Renew only an owned, unexpired lease while traces show progress."""
        while not stop.wait(self.lease_renew_interval_seconds):
            idle_seconds = time.monotonic() - self._last_progress_monotonic
            if idle_seconds > self.lease_progress_grace_seconds:
                self._emit_trace(
                    "maintenance_lease_renewal_stopped",
                    job_id=ctx.job_id,
                    reason="no_progress",
                    idle_seconds=round(idle_seconds, 2),
                )
                self._claim_lost.set()
                return
            try:
                renewed = self.engines.conversation.jobs.renew_lease(
                    ctx.job, lease_seconds=self.lease_seconds
                )
            except Exception as exc:  # noqa: BLE001
                self._emit_trace(
                    "maintenance_lease_renewal_failed",
                    job_id=ctx.job_id,
                    error_type=type(exc).__name__,
                    error=str(exc),
                )
                self._claim_lost.set()
                return
            if not renewed:
                self._emit_trace(
                    "maintenance_stale_claim_rejected",
                    job_id=ctx.job_id,
                    reason="lease_ownership_lost",
                )
                self._claim_lost.set()
                return
            self._emit_trace(
                "maintenance_lease_renewed",
                job_id=ctx.job_id,
                lease_seconds=self.lease_seconds,
                idle_seconds=round(idle_seconds, 2),
            )

    def _handle_document_parse_strategy(self, ctx: MaintenanceJobExecutionContext) -> None:
        """Run the application parser as one bounded maintenance phase."""
        decision = self._evaluate_maintenance_guard(ctx)
        if decision.status != "ready":
            self._block_guarded_job(ctx, decision)
            return
        started_ms = int(time.time() * 1000)
        self._emit_trace(
            "maintenance_parse_start",
            workspace_id=ctx.workspace_id,
            source_document_id=str(ctx.payload.get("source_document_id") or ""),
            request_node_id=ctx.request_node_id,
            job_id=ctx.job_id,
            maintenance_kind=ctx.maintenance_kind,
        )
        try:
            result: Mapping[str, object] = self.document_parser(ctx)
            if bool(result.get("stale_claim")) or getattr(self, "_claim_lost", threading.Event()).is_set():
                self._emit_trace(
                    "maintenance_repeated_work_discarded",
                    workspace_id=ctx.workspace_id,
                    source_document_id=str(ctx.payload.get("source_document_id") or ""),
                    request_node_id=ctx.request_node_id,
                    job_id=ctx.job_id,
                    reason="claim_lost_before_commit",
                    comparison_result={str(k): v for k, v in result.items()},
                )
                return
            self._emit_trace(
                "maintenance_parse_complete",
                workspace_id=ctx.workspace_id,
                source_document_id=str(ctx.payload.get("source_document_id") or ""),
                request_node_id=ctx.request_node_id,
                job_id=ctx.job_id,
                node_count=int(result.get("node_count") or 0),
                edge_count=int(result.get("edge_count") or 0),
                llm_call_count=int(result.get("llm_call_count") or 0),
                duration_ms=int(time.time() * 1000) - started_ms,
            )
            if self._advance_maintenance_plan(ctx):
                return
            self._acknowledge_job(ctx)
        except Exception as exc:
            self._emit_trace(
                "maintenance_parse_failed",
                workspace_id=ctx.workspace_id,
                source_document_id=str(ctx.payload.get("source_document_id") or ""),
                request_node_id=ctx.request_node_id,
                job_id=ctx.job_id,
                error_type=type(exc).__name__,
                error=str(exc),
                duration_ms=int(time.time() * 1000) - started_ms,
            )
            self.engines.conversation.jobs.retry_or_fail(ctx.job, exc)

    def _parse_seeded_document(self, ctx: MaintenanceJobExecutionContext) -> Mapping[str, object]:
        """Parse and persist a source-map-seeded document without a transaction around the LLM call."""
        source_document_id = str(ctx.payload.get("source_document_id") or "")
        ns = WorkspaceNamespaces(ctx.workspace_id)
        with _temporary_namespace(self.engines.kg, ns.source_space):
            document = self.engines.kg.read.get_document(source_document_id)
        metadata = dict(document.metadata or {})
        request = IngestPipelineRequest(
            workspace_id=ctx.workspace_id,
            source_uri=str(metadata.get("source_uri") or source_document_id),
            title=str(metadata.get("title") or source_document_id),
            raw_text=str(document.content or ""),
            source_format=str(metadata.get("source_format") or "text"),
            operation_mode="parse_first",
            parser_mode=str(metadata.get("parser_mode") or "heuristic"),
            parser_lane=str(metadata.get("parser_lane") or "page_index"),
            promotion_mode=str(metadata.get("promotion_mode") or "pending"),
            llm_provider=(str(metadata["llm_provider"]) if metadata.get("llm_provider") else None),
            llm_model=(str(metadata["llm_model"]) if metadata.get("llm_model") else None),
        )
        pipeline = IngestPipeline(self.engines)
        accepted_candidate = self.engines.conversation.jobs.accepted_candidate(ctx.job)
        accepted_extraction = None
        if isinstance(accepted_candidate, dict) and accepted_candidate.get("kind") == "document_parse":
            raw_extraction = accepted_candidate.get("extraction")
            if isinstance(raw_extraction, dict):
                accepted_extraction = GraphExtractionWithIDs.model_validate(raw_extraction)
                self._emit_trace(
                    "maintenance_accepted_candidate_reused",
                    workspace_id=ctx.workspace_id,
                    source_document_id=source_document_id,
                    job_id=ctx.job_id,
                    candidate_kind="document_parse",
                )
        # This is an in-memory parser-cache transaction, not a database
        # transaction: the long LLM call never holds a graph connection open.
        with parser_llm_cache_transaction() as parser_cache_transaction:
            parse_result = None
            extraction = accepted_extraction
            if extraction is None:
                parse_result = pipeline.parse_source(
                    request=request,
                    source_document_id=source_document_id,
                )
                extraction = pipeline.translate_parse_result(
                    parse_result=parse_result,
                    source_document_id=source_document_id,
                )
                candidate = {
                    "kind": "document_parse",
                    "source_document_id": source_document_id,
                    "extraction": extraction.model_dump(mode="json"),
                }
                decision = self.engines.conversation.jobs.accept_candidate(ctx.job, candidate)
                if decision.get("status") == "rejected":
                    self._emit_trace(
                        "maintenance_repeated_work_discarded",
                        workspace_id=ctx.workspace_id,
                        source_document_id=source_document_id,
                        job_id=ctx.job_id,
                        reason=str(decision.get("reason") or "claim_not_valid"),
                    )
                    return {
                        "node_count": 0,
                        "edge_count": 0,
                        "llm_call_count": int(dict(getattr(parse_result, "usage_summary", {}) or {}).get("llm_call_count") or 0),
                        "stale_claim": True,
                    }
                if decision.get("status") == "existing":
                    winner = self.engines.conversation.jobs.accepted_candidate(ctx.job)
                    if isinstance(winner, dict) and isinstance(winner.get("extraction"), dict):
                        extraction = GraphExtractionWithIDs.model_validate(winner["extraction"])
                        self._emit_trace(
                            "maintenance_repeated_work_discarded",
                            workspace_id=ctx.workspace_id,
                            source_document_id=source_document_id,
                            job_id=ctx.job_id,
                            reason="candidate_already_accepted",
                        )
            if self._claim_lost.is_set():
                return {
                    "node_count": 0,
                    "edge_count": 0,
                    "llm_call_count": int(dict(getattr(parse_result, "usage_summary", {}) or {}).get("llm_call_count") or 0),
                    "stale_claim": True,
                    "comparison_result": {"parse_completed": True},
                }
            if parse_result is not None:
                pipeline.create_parse_retry_history(
                    request=request,
                    source_document_id=source_document_id,
                    parse_result=parse_result,
                    namespace=ns.conv_bg,
                )
            pipeline.ingest_parse_result(
                request=request,
                source_document_id=source_document_id,
                graph_extraction=extraction,
                namespace=ns.conv_fg,
            )
            parser_cache_transaction.promote()
        pipeline.record_source_readiness(
            request=request,
            source_document_id=source_document_id,
            stage="parsed_graph_persisted",
        )
        return {
            "node_count": len(extraction.nodes or []),
            "edge_count": len(extraction.edges or []),
            "llm_call_count": int(
                dict(getattr(parse_result, "usage_summary", {}) or {}).get("llm_call_count") or 0
            ),
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
                    "execution_wisdom_emitted": emitted,
                },
            )
            if ctx.job_id:
                self._acknowledge_job(ctx)
        except Exception as e:
            if self._claim_lost.is_set():
                self._emit_stale_claim_discarded(ctx, reason="claim_lost_during_wisdom")
                return
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
                    self._acknowledge_job(ctx)
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

    def _persist_maintenance_usage(
        self,
        ctx: MaintenanceJobExecutionContext,
        budget_ledger: StateBackedBudgetLedger,
        result: object | None,
    ) -> None:
        ns = WorkspaceNamespaces(ctx.workspace_id)
        persist_usage_events(
            self.engines.conversation.meta_sqlite,
            namespace=ns.usage_events,
            events=budget_ledger.events,
            workspace_id=ctx.workspace_id,
            attempt_id=str(getattr(result, "run_id", "") or uuid.uuid4()),
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

    def _requeue_suspended_maintenance_job(
        self,
        ctx: MaintenanceJobExecutionContext,
        result: RunResult,
        *,
        budget_state: Mapping[str, object] | None = None,
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
        if budget_state is not None:
            next_payload["maintenance_budget_state"] = _persisted_budget_state(budget_state)
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
        self._emit_trace(
            "maintenance_job_requeued",
            workspace_id=str(getattr(ctx, "workspace_id", "")),
            source_document_id=str(ctx.payload.get("source_document_id") or ""),
            request_node_id=str(getattr(ctx, "request_node_id", "")),
            job_id=ctx.job_id,
            continuation_run_id=next_payload["continuation_run_id"],
            suspended_node_id=next_payload["suspended_node_id"],
            suspended_token_id=next_payload["suspended_token_id"],
        )

    def _acknowledge_job(self, ctx: MaintenanceJobExecutionContext) -> bool:
        """Acknowledge only the claim that produced this maintenance result."""
        acknowledged = self.engines.conversation.jobs.mark_done(
            ctx.job_id,
            claim_token=ctx.job.claim_token,
        )
        self._emit_trace(
            "maintenance_job_acknowledged" if acknowledged else "maintenance_stale_claim_rejected",
            workspace_id=ctx.workspace_id,
            source_document_id=str(ctx.payload.get("source_document_id") or ""),
            request_node_id=ctx.request_node_id,
            job_id=ctx.job_id,
            maintenance_kind=ctx.maintenance_kind,
            claim_token_present=bool(ctx.job.claim_token),
        )
        return acknowledged

    def _emit_trace(self, event: str, **fields: object) -> None:
        if fields.get("job_id") is not None and not event.startswith("maintenance_lease_"):
            self._last_progress_monotonic = time.monotonic()
        payload: dict[str, object] = {
            "event": event,
            "worker_id": getattr(self, "worker_id", "maintenance-worker-unknown"),
            "thread_name": threading.current_thread().name,
            "ts_ms": int(time.time() * 1000),
            **fields,
        }
        logger.info("maintenance_trace %s", payload)
        sink = getattr(self, "trace_sink", None)
        if sink is not None:
            try:
                sink(payload)
            except Exception:
                logger.exception("Maintenance trace sink failed for event %s", event)

    def _emit_stale_claim_discarded(
        self,
        ctx: MaintenanceJobExecutionContext,
        *,
        reason: str,
    ) -> None:
        """Record a late attempt without replying, retrying, or acknowledging it."""
        self._emit_trace(
            "maintenance_repeated_work_discarded",
            workspace_id=ctx.workspace_id,
            source_document_id=str(ctx.payload.get("source_document_id") or ""),
            request_node_id=ctx.request_node_id,
            job_id=ctx.job_id,
            maintenance_kind=ctx.maintenance_kind,
            reason=reason,
            authoritative_result=False,
        )

    def _assert_claim_owned(self, ctx: MaintenanceJobExecutionContext, *, reason: str) -> None:
        if self._claim_lost.is_set():
            self._emit_stale_claim_discarded(ctx, reason=reason)
            raise RuntimeError("maintenance claim is no longer owned")

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
            completed_replies = self.engines.conversation.read.get_nodes(
                where={
                    "$and": [
                        {"artifact_kind": "lane_message"},
                        {"reply_to_message_id": reply_to_message_id},
                        {"msg_type": "reply.maintenance.completed"},
                    ],
                },
                limit=1,
            )
            if completed_replies and lane_status != "completed":
                self._emit_trace(
                    "maintenance_lane_reply_suppressed",
                    workspace_id=workspace_id,
                    source_document_id=source_document_id,
                    request_node_id=request_node_id,
                    reply_to_message_id=reply_to_message_id,
                    attempted_status=lane_status,
                    winning_status="completed",
                )
                return
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
            projected_update = getattr(
                self.engines.conversation.meta_sqlite,
                "update_projected_lane_message_status",
                None,
            )
            if callable(projected_update):
                projected_update(
                    message_id=reply_to_message_id,
                    status=lane_status,
                    error_json=(
                        json.dumps(lane_error, sort_keys=True, separators=(",", ":"))
                        if lane_error is not None
                        else None
                    ),
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
            before_write = _deps_raw.get("before_authoritative_write")
        else:
            engines = _deps_raw
            before_write = None
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
            self._emit_trace(
                "maintenance_graph_effect",
                workspace_id=str(workspace_id),
                source_document_id=str(ctx.state_view.get("source_document_id") or ""),
                maintenance_kind="distill",
                operation_category="distillation",
                source_node_count=0,
                derived_node_count=0,
            )
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
            before_write=before_write if callable(before_write) else None,
        )
        for result in template_result.grouped_results:
            logger.info(
                "Derived knowledge synthesis for entity '%s' with %s source nodes.",
                result.group_key,
                result.source_node_count,
            )

        self._emit_trace(
            "maintenance_graph_effect",
            workspace_id=str(workspace_id),
            source_document_id=str(ctx.state_view.get("source_document_id") or ""),
            maintenance_kind="distill",
            operation_category=operation_category("distill"),
            source_node_count=sum(
                result.source_node_count for result in template_result.grouped_results
            ),
            derived_node_count=len(template_result.grouped_results),
            derived_node_ids=list(template_result.emitted_group_keys),
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

    def _emit_execution_wisdom_from_history(
        self,
        workspace_id: str,
        engines: NamespaceEngines,
        *,
        before_write: Callable[[object], None] | None = None,
    ) -> list[str]:
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
            before_write=before_write,
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
        before_write = _deps_raw.get("before_authoritative_write") if isinstance(_deps_raw, dict) else None
        emitted = self._emit_execution_wisdom_from_history(
            workspace_id,
            engines,
            before_write=before_write if callable(before_write) else None,
        )
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
