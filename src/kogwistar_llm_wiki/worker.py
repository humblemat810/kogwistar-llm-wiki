from __future__ import annotations

import logging
import threading
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping

from kg_doc_parser.workflow_ingest.providers import WorkflowProviderSettings
from kogwistar.runtime.resolvers import MappingStepResolver
from kogwistar.runtime.runtime import WorkflowRuntime

from .configuration.workspace import WorkspaceNamespaces
from .maintenance import (
    MaintenanceJobExecutionContext,
    build_default_maintenance_strategy_registry,
)
from .maintenance import worker_selection as _worker_selection
from .maintenance.job_dispatch import MaintenanceJobDispatchMixin
from .maintenance.maintenance_designs import (
    materialize_maintenance_designs,  # noqa: F401
)
from .maintenance.maintenance_patch_apply import (
    apply_maintenance_patch_for_scope,  # noqa: F401
)
from .maintenance.state import (
    durable_maintenance_usage as _durable_maintenance_usage,  # noqa: F401 - legacy test seam
)
from .maintenance.state import (
    maintenance_budget_state as _maintenance_budget_state,  # noqa: F401 - legacy test seam
)
from .maintenance.worker_derived import DerivedMaintenanceWorkerMixin
from .maintenance.worker_execution import MaintenanceExecutionWorkerMixin
from .maintenance.worker_parse import DurableParseMaintenanceWorkerMixin
from .maintenance.worker_runtime import MaintenanceRuntimeWorkerMixin
from .maintenance.worker_selection import MaintenanceSelectionWorkerMixin
from .models import NamespaceEngines
from .parsing.parse_views import (
    ParseFrontierItem,
    ParseSessionState,
)
from .policies.rules import LlmWikiPolicies, build_default_policies
from .provider_config import resolve_maintenance_provider_settings
from .utils import _temporary_namespace

logger = logging.getLogger(__name__)


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
            except Exception:
                logger.exception("Worker error in workspace %s", workspace_id)
            time.sleep(interval)

    @abstractmethod
    def process_pending_jobs(self, workspace_id: str) -> None:
        """Subclasses implement specific polling/processing logic."""


class MaintenanceWorker(
    MaintenanceJobDispatchMixin,
    DurableParseMaintenanceWorkerMixin,
    MaintenanceExecutionWorkerMixin,
    DerivedMaintenanceWorkerMixin,
    MaintenanceRuntimeWorkerMixin,
    MaintenanceSelectionWorkerMixin,
    BaseWorker,
):
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
        usage_sink: Callable[[str, Mapping[str, float]], None] | None = None,
        document_parser: Callable[[MaintenanceJobExecutionContext], Mapping[str, object]] | None = None,
        layered_parser: Callable[
            [MaintenanceJobExecutionContext, ParseSessionState, list[ParseFrontierItem]],
            Mapping[str, object],
        ] | None = None,
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
        self.usage_sink = usage_sink
        self.request_enabled = True
        self.background_enabled = True
        self.document_parser = document_parser or self._parse_seeded_document
        # The built-in callback reconstructs its bounded parser request from
        # immutable source evidence. Deployments can still inject a richer
        # layered parser, but absence must never be treated as completion.
        self.layered_parser = layered_parser or self._expand_durable_parse_frontier
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
                    job_payload = getattr(job, "payload", {})
                    mode = (
                        str(job_payload.get("mode") or "request")
                        if isinstance(job_payload, Mapping)
                        else "request"
                    )
                    mode_disabled = (
                        mode == "background"
                        and not getattr(self, "background_enabled", True)
                    ) or (
                        mode != "background"
                        and not getattr(self, "request_enabled", True)
                    )
                    if mode_disabled:
                        self.engines.conversation.jobs.requeue_at_tail(job, delay_seconds=1)
                        self._emit_trace(
                            "maintenance_job_paused",
                            workspace_id=workspace_id,
                            job_id=str(job.job_id),
                            mode=mode,
                            reason="mode_disabled",
                        )
                        continue
                    self._handle_job(workspace_id, job)
                except Exception as exc:
                    logger.exception(
                        "Maintenance worker failed to process claimed job for workspace %s",
                        workspace_id,
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

    def _attach_request_selection(self, ctx: MaintenanceJobExecutionContext) -> None:
        """Preserve the historical worker-level namespace hook for tests/callers."""
        original = _worker_selection._temporary_namespace
        _worker_selection._temporary_namespace = _temporary_namespace
        try:
            return super()._attach_request_selection(ctx)
        finally:
            _worker_selection._temporary_namespace = original

    def _persist_selection_audit(self, ctx: MaintenanceJobExecutionContext) -> None:
        """Preserve the historical worker-level namespace hook for audit writes."""
        original = _worker_selection._temporary_namespace
        _worker_selection._temporary_namespace = _temporary_namespace
        try:
            return super()._persist_selection_audit(ctx)
        finally:
            _worker_selection._temporary_namespace = original
