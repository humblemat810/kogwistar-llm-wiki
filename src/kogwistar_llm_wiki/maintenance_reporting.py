from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from kogwistar.runtime.budget import BudgetEvent
from kogwistar.runtime.budget_adapters import summarize_budget_events


def summarize_maintenance_costs(
    events: Iterable[BudgetEvent],
    *,
    default_maintenance_kind: str = "unknown",
    default_model: str = "unknown",
) -> dict[str, dict[str, Any]]:
    """Group runtime budget events by maintenance kind and model.

    The token/cost arithmetic stays in ``kogwistar.runtime``; this helper only
    chooses the app-level grouping keys used by reports.
    """

    grouped: dict[str, list[BudgetEvent]] = defaultdict(list)
    for event in events:
        meta = dict(getattr(event, "meta", None) or {})
        maintenance_kind = str(
            meta.get("maintenance_kind")
            or meta.get("job_kind")
            or default_maintenance_kind
        )
        model = str(meta.get("model") or meta.get("llm_model") or default_model)
        grouped[f"{maintenance_kind}|{model}"].append(event)

    summaries: dict[str, dict[str, Any]] = {}
    for key, grouped_events in grouped.items():
        maintenance_kind, model = key.split("|", 1)
        summaries[key] = {
            "maintenance_kind": maintenance_kind,
            "model": model,
            **summarize_budget_events(grouped_events),
        }
    return summaries
