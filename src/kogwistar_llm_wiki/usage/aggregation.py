"""Usage aggregation rules shared by projection rebuilds and snapshots."""

from __future__ import annotations

from collections.abc import Mapping

from kogwistar.json_types import JsonObject, JsonValue
from kogwistar.runtime import BudgetEvent
from kogwistar.runtime.budget_adapters import summarize_budget_events

USAGE_PROJECTION_SCHEMA_VERSION = 1


def _mapping(value: object) -> Mapping[str, JsonValue]:
    return value if isinstance(value, Mapping) else {}


def _number(value: object, *, default: float = 0.0) -> float:
    if isinstance(value, (int, float, str)):
        try:
            return float(value)
        except ValueError:
            return default
    return default


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
        aggregate[key] = int(_number(aggregate.get(key))) + int(_number(summary.get(key)))
    cost_observed = bool(aggregate.get("cost_observed")) or event.kind == "cost" or event.unit == "total_cost"
    aggregate["cost_observed"] = cost_observed
    aggregate["total_cost"] = (
        round(
            _number(aggregate.get("total_cost"))
            + _number(summary.get("total_cost")),
            6,
        )
        if cost_observed
        else None
    )
    for field in ("event_counts", "by_unit"):
        target = dict(_mapping(aggregate.get(field)))
        for key, value in _mapping(summary.get(field)).items():
            target[str(key)] = int(_number(target.get(str(key)))) + int(_number(value))
        aggregate[field] = target
    attribution = event.attribution
    if attribution is not None:
        providers_value = aggregate.get("providers")
        providers = [str(item) for item in providers_value] if isinstance(providers_value, list) else []
        if attribution.provider and attribution.provider not in providers:
            aggregate["providers"] = sorted([*providers, attribution.provider])
        models_value = aggregate.get("models")
        models = [str(item) for item in models_value] if isinstance(models_value, list) else []
        if attribution.model and attribution.model not in models:
            aggregate["models"] = sorted([*models, attribution.model])


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


def decode_projection(row: Mapping[str, JsonValue]) -> JsonObject:
    payload = row.get("payload")
    if not isinstance(payload, dict):
        raise ValueError("usage projection payload must be an object")  # noqa: TRY004
    schema_version = row.get("projection_schema_version")
    payload_schema_version = payload.get("projection_schema_version")
    if not isinstance(schema_version, (int, float, str)) or int(schema_version or 0) != USAGE_PROJECTION_SCHEMA_VERSION:
        raise ValueError("usage projection schema version is incompatible")
    if not isinstance(payload_schema_version, (int, float, str)) or int(payload_schema_version or 0) != USAGE_PROJECTION_SCHEMA_VERSION:
        raise ValueError("usage projection payload schema version is incompatible")
    return payload
