from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from threading import Event
from typing import TYPE_CHECKING, ClassVar, Protocol

from kg_doc_parser.workflow_ingest.providers import WorkflowProviderSettings
from kogwistar.engine_core.jobs import JobQueueItem
from kogwistar.engine_core.models import Node
from kogwistar.maintenance.contracts import BeforeWrite
from kogwistar.runtime import RunResult
from kogwistar.runtime.budget import StateBackedBudgetLedger
from kogwistar.runtime.runtime import WorkflowRuntime

from ..models import NamespaceEngines
from ..policies.rules import LlmWikiPolicies
from ..disambiguation.contact_matching import ContactIdentityObservation
from .maintenance_guards import MaintenanceGuardDecision
from .maintenance_policy import (
    GRAPH_PATCH_APPLY_KINDS,
    GRAPH_PATCH_PROPOSAL_KINDS,
    is_execution_wisdom_kind,
    normalize_maintenance_kind,
)

if TYPE_CHECKING:
    from ..parsing.parse_views import ParseFrontierItem, ParseSessionState


class LayeredMaintenanceParser(Protocol):
    """Bounded durable parser callback used by maintenance expansion."""

    def __call__(
        self,
        ctx: "MaintenanceJobExecutionContext",
        session: "ParseSessionState",
        frontier: list["ParseFrontierItem"],
        /,
    ) -> Mapping[str, object]: ...


class MaintenanceTraceSink(Protocol):
    """Receive one bounded, structured maintenance trace payload."""

    def __call__(self, payload: dict[str, object], /) -> None: ...


class MaintenanceUsageSink(Protocol):
    """Record one named maintenance usage sample."""

    def __call__(self, event_name: str, metrics: Mapping[str, float], /) -> None: ...


class MaintenanceDocumentParser(Protocol):
    """Parse one immutable source request into bounded evidence."""

    def __call__(
        self, ctx: MaintenanceJobExecutionContext, /
    ) -> Mapping[str, object]: ...


class MaintenanceObservationCritic(Protocol):
    """Assess one bounded observation context without mutating the graph."""

    def __call__(
        self, subject: object, ctx: MaintenanceJobExecutionContext, /
    ) -> Mapping[str, object]: ...


class MaintenanceContextLimitSink(Protocol):
    """Record that a maintenance context limit was reached."""

    def __call__(self) -> None: ...


class CrosslinkProposer(Protocol):
    """Propose bounded cross-links from already authorized context."""

    def __call__(
        self,
        context: list[Mapping[str, object]],
        ctx: MaintenanceJobExecutionContext,
        /,
    ) -> Mapping[str, object]: ...


class CrosslinkCritic(Protocol):
    """Critique proposed cross-links before any patch is applied."""

    def __call__(
        self,
        proposal: Mapping[str, object],
        ctx: MaintenanceJobExecutionContext,
        /,
    ) -> Mapping[str, object]: ...


class ContactObservationProvider(Protocol):
    """Read bounded contact observations for one authorized workspace."""

    def __call__(
        self,
        workspace_id: str,
        payload: Mapping[str, object],
        /,
    ) -> Iterable[ContactIdentityObservation]: ...


@dataclass(frozen=True, slots=True)
class MaintenanceJobExecutionContext:
    workspace_id: str
    job: JobQueueItem
    job_id: str
    payload: dict[str, object]
    request_node: Node | None
    request_node_id: str
    lane_message_id: str
    maintenance_kind: str


class MaintenanceWorkerLike(Protocol):
    engines: NamespaceEngines
    worker_id: str
    lease_seconds: int
    lease_renew_interval_seconds: int
    strategy_registry: MaintenanceStrategyRegistry
    _claim_lost: Event
    _last_progress_monotonic: float
    usage_sink: MaintenanceUsageSink | None
    trace_sink: MaintenanceTraceSink | None
    provider_settings: WorkflowProviderSettings
    policies: LlmWikiPolicies
    runtime: WorkflowRuntime
    fair_scheduling: bool
    maintenance_steps_per_slice: int
    maintenance_llm_calls_per_slice: int
    maintenance_seconds_per_slice: int
    document_parser: MaintenanceDocumentParser
    layered_parser: "LayeredMaintenanceParser"

    def _load_request_node(self, workspace_id: str, req_node_id: str) -> Node | None: ...
    def _emit_trace(self, event: str, **fields: object) -> None: ...
    def _attach_request_selection(self, ctx: MaintenanceJobExecutionContext) -> None: ...
    def _evaluate_maintenance_guard(self, ctx: MaintenanceJobExecutionContext) -> MaintenanceGuardDecision: ...
    def _block_guarded_job(self, ctx: MaintenanceJobExecutionContext, decision: MaintenanceGuardDecision) -> None: ...
    def _renew_claim_while_progressing(self, ctx: MaintenanceJobExecutionContext, stop: Event) -> None: ...
    def _retry_or_fail_maintenance_job(
        self,
        job: JobQueueItem,
        error: Exception | str,
        *,
        workspace_id: str = "",
        maintenance_kind: str = "",
    ) -> None: ...
    def _emit_lane_reply(
        self,
        *,
        workspace_id: str,
        source_document_id: str,
        request_node_id: str,
        reply_to_message_id: str | None,
        status: str,
        payload: dict[str, object],
    ) -> None: ...
    def _emit_stale_claim_discarded(self, ctx: MaintenanceJobExecutionContext, *, reason: str) -> None: ...
    def _assert_claim_owned(self, ctx: MaintenanceJobExecutionContext, *, reason: str) -> None: ...
    @staticmethod
    def _selection_result_payload(ctx: MaintenanceJobExecutionContext) -> dict[str, object]: ...
    def _emit_execution_wisdom_from_history(
        self,
        workspace_id: str,
        engines: NamespaceEngines,
        *,
        before_write: BeforeWrite[object] | None = None,
    ) -> list[str]: ...

    def _handle_review_maintenance_subject(self, ctx: MaintenanceJobExecutionContext) -> None: ...

    def _handle_crosslink_maintenance_strategy(self, ctx: MaintenanceJobExecutionContext) -> None: ...

    def _handle_document_parse_strategy(self, ctx: MaintenanceJobExecutionContext) -> None: ...

    def _handle_document_expand_parse_children_strategy(
        self, ctx: MaintenanceJobExecutionContext
    ) -> None: ...

    def _handle_execution_wisdom_strategy(self, ctx: MaintenanceJobExecutionContext) -> None: ...

    def _handle_contact_disambiguation_scan(self, ctx: MaintenanceJobExecutionContext) -> None: ...

    def _acknowledge_job(self, ctx: MaintenanceJobExecutionContext) -> bool: ...

    def _handle_graph_patch_apply_strategy(self, ctx: MaintenanceJobExecutionContext) -> None: ...

    def _handle_runtime_workflow_strategy(self, ctx: MaintenanceJobExecutionContext) -> None: ...

    def _advance_maintenance_plan(
        self,
        ctx: MaintenanceJobExecutionContext,
        *,
        budget_state: Mapping[str, object] | None = None,
    ) -> bool: ...
    def _is_active_source_derivation(self, node: object, workspace_id: str) -> bool: ...
    def _persist_maintenance_usage(
        self,
        ctx: MaintenanceJobExecutionContext,
        budget_ledger: StateBackedBudgetLedger,
        result: object | None,
    ) -> None: ...
    def _requeue_suspended_maintenance_job(
        self,
        ctx: MaintenanceJobExecutionContext,
        result: RunResult,
        *,
        budget_state: Mapping[str, object] | None = None,
    ) -> None: ...


class MaintenanceStrategy(Protocol):
    name: str

    def can_handle(self, maintenance_kind: str) -> bool:
        ...

    def handle(self, worker: MaintenanceWorkerLike, ctx: MaintenanceJobExecutionContext) -> None:
        ...


class MaintenanceStrategyRegistry:
    __slots__ = ("_strategies",)

    def __init__(self, strategies: list[MaintenanceStrategy] | None = None) -> None:
        self._strategies = list(strategies or [])

    def register(self, strategy: MaintenanceStrategy) -> MaintenanceStrategyRegistry:
        self._strategies.append(strategy)
        return self

    def resolve(self, maintenance_kind: str) -> MaintenanceStrategy:
        normalized = normalize_maintenance_kind(maintenance_kind)
        for strategy in self._strategies:
            if strategy.can_handle(normalized):
                return strategy
        raise KeyError(f"No maintenance strategy registered for {maintenance_kind!r}")


class ExecutionWisdomMaintenanceStrategy:
    name = "execution_wisdom"

    def can_handle(self, maintenance_kind: str) -> bool:
        return is_execution_wisdom_kind(maintenance_kind)

    def handle(self, worker: MaintenanceWorkerLike, ctx: MaintenanceJobExecutionContext) -> None:
        worker._handle_execution_wisdom_strategy(ctx)


class GraphPatchApplyMaintenanceStrategy:
    name = "graph_patch_apply"

    def can_handle(self, maintenance_kind: str) -> bool:
        return normalize_maintenance_kind(maintenance_kind) in GRAPH_PATCH_APPLY_KINDS

    def handle(self, worker: MaintenanceWorkerLike, ctx: MaintenanceJobExecutionContext) -> None:
        worker._handle_graph_patch_apply_strategy(ctx)


class GraphPatchProposalMaintenanceStrategy:
    name = "graph_patch_proposal"

    def can_handle(self, maintenance_kind: str) -> bool:
        return normalize_maintenance_kind(maintenance_kind) in GRAPH_PATCH_PROPOSAL_KINDS

    def handle(self, worker: MaintenanceWorkerLike, ctx: MaintenanceJobExecutionContext) -> None:
        worker._handle_runtime_workflow_strategy(ctx)


class MaintenanceObservationStrategy:
    name = "maintenance_observation"

    def can_handle(self, maintenance_kind: str) -> bool:
        return normalize_maintenance_kind(maintenance_kind) == "review_maintenance_subject"

    def handle(self, worker: MaintenanceWorkerLike, ctx: MaintenanceJobExecutionContext) -> None:
        worker._handle_review_maintenance_subject(ctx)


class ContactDisambiguationScanStrategy:
    name = "contact_disambiguation_scan"

    def can_handle(self, maintenance_kind: str) -> bool:
        return normalize_maintenance_kind(maintenance_kind) == "entity_disambiguation_scan"

    def handle(self, worker: MaintenanceWorkerLike, ctx: MaintenanceJobExecutionContext) -> None:
        worker._handle_contact_disambiguation_scan(ctx)


class CrosslinkMaintenanceStrategy:
    name = "crosslink_lifecycle"
    _KINDS: ClassVar[set[str]] = {
        "document_propose_crosslinks",
        "document_validate_crosslinks",
        "document_revalidate_crosslinks",
        "document_retract_crosslinks",
    }

    def can_handle(self, maintenance_kind: str) -> bool:
        return normalize_maintenance_kind(maintenance_kind) in self._KINDS

    def handle(self, worker: MaintenanceWorkerLike, ctx: MaintenanceJobExecutionContext) -> None:
        worker._handle_crosslink_maintenance_strategy(ctx)


class RuntimeWorkflowMaintenanceStrategy:
    name = "runtime_workflow"

    def can_handle(self, maintenance_kind: str) -> bool:
        return True

    def handle(self, worker: MaintenanceWorkerLike, ctx: MaintenanceJobExecutionContext) -> None:
        worker._handle_runtime_workflow_strategy(ctx)


class DocumentParseMaintenanceStrategy:
    name = "document_parse"

    def can_handle(self, maintenance_kind: str) -> bool:
        return normalize_maintenance_kind(maintenance_kind) == "document_parse_graph"

    def handle(self, worker: MaintenanceWorkerLike, ctx: MaintenanceJobExecutionContext) -> None:
        worker._handle_document_parse_strategy(ctx)


class DocumentExpandParseChildrenMaintenanceStrategy:
    name = "document_expand_parse_children"

    def can_handle(self, maintenance_kind: str) -> bool:
        return normalize_maintenance_kind(maintenance_kind) in {
            "document_expand_parse_children",
            "document_reparse_region",
        }

    def handle(self, worker: MaintenanceWorkerLike, ctx: MaintenanceJobExecutionContext) -> None:
        worker._handle_document_expand_parse_children_strategy(ctx)


def build_default_maintenance_strategy_registry() -> MaintenanceStrategyRegistry:
    return MaintenanceStrategyRegistry(
        [
            ExecutionWisdomMaintenanceStrategy(),
            GraphPatchApplyMaintenanceStrategy(),
            MaintenanceObservationStrategy(),
            ContactDisambiguationScanStrategy(),
            CrosslinkMaintenanceStrategy(),
            DocumentParseMaintenanceStrategy(),
            DocumentExpandParseChildrenMaintenanceStrategy(),
            GraphPatchProposalMaintenanceStrategy(),
            RuntimeWorkflowMaintenanceStrategy(),
        ]
    )
