from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


DEFAULT_DOCUMENT_MAINTENANCE_PLAN: tuple[str, ...] = (
    "document_seed_graph",
    "document_parse_graph",
    "document_propose_crosslinks",
    "document_validate_crosslinks",
)


@dataclass(frozen=True, slots=True)
class MaintenancePlanDecision:
    current_kind: str
    next_kind: str | None
    current_index: int
    next_index: int
    reason: str

    @property
    def should_continue(self) -> bool:
        return self.next_kind is not None


def normalize_maintenance_plan(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    result = tuple(str(item).strip() for item in value if str(item).strip())
    return result


def decide_next_maintenance_phase(
    payload: Mapping[str, object],
    *,
    completed_kind: str,
) -> MaintenancePlanDecision:
    """Choose at most one continuation phase from durable job state.

    The planner is intentionally pure. It does not inspect or mutate the graph;
    evidence checks and patch application remain the responsibility of the next
    claimed maintenance job.
    """
    plan = normalize_maintenance_plan(payload.get("maintenance_plan"))
    current_kind = str(completed_kind).strip()
    current_index = int(payload.get("maintenance_phase_index") or 0)
    completed_rounds = int(payload.get("maintenance_round") or 0)
    max_rounds = int(payload.get("maintenance_max_rounds") or 0)
    if payload.get("maintenance_stop_requested"):
        return MaintenancePlanDecision(
            current_kind=current_kind,
            next_kind=None,
            current_index=current_index,
            next_index=current_index,
            reason="stop_requested",
        )
    if max_rounds > 0 and completed_rounds >= max_rounds:
        return MaintenancePlanDecision(
            current_kind=current_kind,
            next_kind=None,
            current_index=current_index,
            next_index=current_index,
            reason="max_rounds_reached",
        )
    if not plan:
        return MaintenancePlanDecision(
            current_kind=current_kind,
            next_kind=None,
            current_index=current_index,
            next_index=current_index,
            reason="no_plan",
        )

    try:
        plan_index = plan.index(current_kind)
    except ValueError:
        plan_index = current_index
    next_index = max(current_index, plan_index) + 1
    if next_index >= len(plan):
        return MaintenancePlanDecision(
            current_kind=current_kind,
            next_kind=None,
            current_index=plan_index,
            next_index=next_index,
            reason="plan_complete",
        )
    return MaintenancePlanDecision(
        current_kind=current_kind,
        next_kind=plan[next_index],
        current_index=plan_index,
        next_index=next_index,
        reason="next_phase_available",
    )
