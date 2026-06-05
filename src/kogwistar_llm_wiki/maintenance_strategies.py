from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .maintenance_policy import (
    GRAPH_PATCH_APPLY_KINDS,
    GRAPH_PATCH_PROPOSAL_KINDS,
    is_execution_wisdom_kind,
    normalize_maintenance_kind,
)


@dataclass(frozen=True)
class MaintenanceJobExecutionContext:
    workspace_id: str
    job: Any
    job_id: str
    payload: dict[str, Any]
    request_node: Any | None
    request_node_id: str
    lane_message_id: str
    maintenance_kind: str


class MaintenanceStrategy(Protocol):
    name: str

    def can_handle(self, maintenance_kind: str) -> bool:
        ...

    def handle(self, worker: Any, ctx: MaintenanceJobExecutionContext) -> None:
        ...


class MaintenanceStrategyRegistry:
    def __init__(self, strategies: list[MaintenanceStrategy] | None = None) -> None:
        self._strategies = list(strategies or [])

    def register(self, strategy: MaintenanceStrategy) -> "MaintenanceStrategyRegistry":
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

    def handle(self, worker: Any, ctx: MaintenanceJobExecutionContext) -> None:
        worker._handle_execution_wisdom_strategy(ctx)


class GraphPatchApplyMaintenanceStrategy:
    name = "graph_patch_apply"

    def can_handle(self, maintenance_kind: str) -> bool:
        return normalize_maintenance_kind(maintenance_kind) in GRAPH_PATCH_APPLY_KINDS

    def handle(self, worker: Any, ctx: MaintenanceJobExecutionContext) -> None:
        worker._handle_graph_patch_apply_strategy(ctx)


class GraphPatchProposalMaintenanceStrategy:
    name = "graph_patch_proposal"

    def can_handle(self, maintenance_kind: str) -> bool:
        return normalize_maintenance_kind(maintenance_kind) in GRAPH_PATCH_PROPOSAL_KINDS

    def handle(self, worker: Any, ctx: MaintenanceJobExecutionContext) -> None:
        worker._handle_runtime_workflow_strategy(ctx)


class RuntimeWorkflowMaintenanceStrategy:
    name = "runtime_workflow"

    def can_handle(self, maintenance_kind: str) -> bool:
        return True

    def handle(self, worker: Any, ctx: MaintenanceJobExecutionContext) -> None:
        worker._handle_runtime_workflow_strategy(ctx)


def build_default_maintenance_strategy_registry() -> MaintenanceStrategyRegistry:
    return MaintenanceStrategyRegistry(
        [
            ExecutionWisdomMaintenanceStrategy(),
            GraphPatchApplyMaintenanceStrategy(),
            GraphPatchProposalMaintenanceStrategy(),
            RuntimeWorkflowMaintenanceStrategy(),
        ]
    )
