"""Usage aggregation rules shared by projection rebuilds and snapshots."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from kogwistar.runtime import BudgetEvent
from kogwistar.runtime.budget_adapters import summarize_budget_events

USAGE_PROJECTION_SCHEMA_VERSION = 1


def empty_aggregate() -> dict[str, object]:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "total_cost": None,
        "cost_observed": False,
        "time_ms": 0,
        "event_count": 0,
        "event_counts": {},
        "by_unit": {},
        "providers": [],
        "models": [],
    }


def merge_event(aggregate: dict[str, object], event: BudgetEvent) -> None:
    summary = summarize_budget_events([event])
    for key in ("input_tokens", "output_tokens", "total_tokens", "time_ms", "event_count"):
        aggregate[key] = int(aggregate.get(key, 0) or 0) + int(summary.get(key, 0) or 0)
    cost_observed = bool(aggregate.get("cost_observed")) or event.kind == "cost" or event.unit == "total_cost"
    aggregate["cost_observed"] = cost_observed
    aggregate["total_cost"] = (
        round(
            float(aggregate.get("total_cost", 0.0) or 0.0)
            + float(summary.get("total_cost", 0.0) or 0.0),
            6,
        )
        if cost_observed
        else None
    )
    for field in ("event_counts", "by_unit"):
        target = dict(aggregate.get(field) or {})
        for key, value in dict(summary.get(field) or {}).items():
            target[str(key)] = int(target.get(str(key), 0) or 0) + int(value or 0)
        aggregate[field] = target
    attribution = event.attribution
    if attribution is not None:
        if attribution.provider and attribution.provider not in aggregate["providers"]:
            aggregate["providers"] = sorted([*aggregate["providers"], attribution.provider])
        if attribution.model and attribution.model not in aggregate["models"]:
            aggregate["models"] = sorted([*aggregate["models"], attribution.model])


def event_groups(event: BudgetEvent) -> dict[str, list[str]]:
    attribution = event.attribution
    groups: dict[str, list[str]] = {"run": [event.run_id] if event.run_id else []}
    domain_keys: list[str] = []
    if attribution is not None:
        groups.update(
            {
                "document": [attribution.source_document_id] if attribution.source_document_id else [],
                "operation": [attribution.operation_id] if attribution.operation_id else [],
                "maintenance_job": [attribution.maintenance_job_id] if attribution.maintenance_job_id else [],
                "dream_job": [attribution.dream_job_id] if attribution.dream_job_id else [],
            }
        )
        domain_keys = [
            key
            for dimension in ("document", "operation", "maintenance_job", "dream_job")
            for key in groups.get(dimension, [])
        ]
    if not domain_keys:
        groups["unattributed"] = ["unattributed"]
    return {dimension: keys for dimension, keys in groups.items() if keys}


def decode_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    payload = row.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("usage projection payload must be an object")  # noqa: TRY004
    if int(row.get("projection_schema_version") or 0) != USAGE_PROJECTION_SCHEMA_VERSION:
        raise ValueError("usage projection schema version is incompatible")
    if int(payload.get("projection_schema_version") or 0) != USAGE_PROJECTION_SCHEMA_VERSION:
        raise ValueError("usage projection payload schema version is incompatible")
    return payload
