"""Maintenance state helpers for workspace filtering, fingerprints, and budgets."""

from __future__ import annotations

import inspect
import json
import logging
from collections.abc import Iterable, Mapping
from hashlib import sha256
from typing import cast

from kogwistar.engine_core.models import GraphExtractionWithIDs
from kogwistar.runtime.budget import budget_event_from_dict
from kogwistar.runtime.budget_adapters import summarize_budget_events

logger = logging.getLogger(__name__)


def _as_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float, str)):
        return float(value)
    return default


def _as_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, (int, float, str)):
        return int(value)
    return default


def and_where(*clauses: Mapping[str, object]) -> dict[str, object]:
    """Compose a Chroma-compatible conjunction filter."""

    return {"$and": [dict(clause) for clause in clauses]}


def belongs_to_workspace(node: object, workspace_id: str) -> bool:
    """Apply a metadata boundary check after namespace/ACL filtering."""

    metadata = metadata_mapping(node)
    if not metadata:
        return True
    declared_workspace = str(metadata.get("workspace_id") or "").strip()
    return not declared_workspace or declared_workspace == str(workspace_id)


def metadata_mapping(entity: object) -> dict[str, object]:
    """Normalize graph metadata, including backends that wrap it as JSON."""

    raw = getattr(entity, "metadata", None)
    if not isinstance(raw, Mapping):
        return {}
    metadata = {str(key): value for key, value in raw.items()}
    nested = metadata.get("metadata")
    if isinstance(nested, str) and nested:
        try:
            nested = json.loads(nested)
        except json.JSONDecodeError:
            nested = None
    if isinstance(nested, Mapping):
        metadata.update({str(key): value for key, value in nested.items()})
    return metadata


def semantic_fingerprint(extraction: GraphExtractionWithIDs) -> str:
    """Fingerprint semantic output while excluding derivation event IDs."""

    try:
        payload = extraction.model_dump(dump_format="json")
    except TypeError:
        payload = extraction.model_dump(mode="json")
    for key in ("nodes", "edges"):
        values = payload.get(key)
        if not isinstance(values, list):
            continue
        normalized: list[dict[str, object]] = []
        for value in values:
            if not isinstance(value, dict):
                continue
            item = dict(value)
            item.pop("id", None)
            metadata = item.get("metadata")
            if isinstance(metadata, dict):
                metadata = dict(metadata)
                for dynamic_key in (
                    "parse_generation_event_id",
                    "parse_generation_member_id",
                ):
                    metadata.pop(dynamic_key, None)
                item["metadata"] = metadata
            normalized.append(item)
        payload[key] = sorted(
            normalized,
            key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
        )
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(encoded.encode("utf-8")).hexdigest()


def edge_ids(edge: object) -> set[str]:
    """Return all node IDs referenced by an edge."""

    ids: set[str] = set()
    for field in ("source_ids", "target_ids"):
        value = getattr(edge, field, ()) or ()
        if isinstance(value, str):
            ids.add(value)
        else:
            ids.update(str(item) for item in value if str(item).strip())
    return ids


def persisted_budget_state(state: Mapping[str, object]) -> dict[str, object]:
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


def maintenance_budget_state(
    payload: Mapping[str, object],
    *,
    fair_scheduling: bool,
    maintenance_steps_per_slice: int,
    maintenance_llm_calls_per_slice: int,
    maintenance_seconds_per_slice: int,
    durable_usage: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build a cumulative request ledger with optional fair slices."""

    previous = payload.get("maintenance_budget_state")
    state: dict[str, object] = dict(previous) if isinstance(previous, Mapping) else {}
    budgets = payload.get("budgets")
    budgets = budgets if isinstance(budgets, Mapping) else {}

    token_limit = int(budgets.get("max_tokens") or 10_000_000)
    call_limit = int(budgets.get("max_llm_calls") or 0)
    step_limit = int(budgets.get("max_steps") or 0)
    time_limit_ms = int(float(budgets.get("max_time_seconds") or 0) * 1000)
    cost_limit = float(budgets.get("max_cost_usd") or 0.0)
    state.update(
        {
            "request_token_budget": token_limit,
            "request_call_budget": call_limit,
            "request_step_budget": step_limit,
            "request_time_budget_ms": time_limit_ms,
            "request_cost_budget": cost_limit,
        }
    )
    used = {
        key: state.get(key, 0)
        for key in ("token_used", "call_used", "step_used", "time_used_ms", "cost_used")
    }
    for key, value in (durable_usage or {}).items():
        if key not in used or isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        used[key] = max(_as_float(used[key]), _as_float(value))
        state[key] = _as_int(used[key]) if key != "cost_used" else _as_float(used[key])
    state.update(
        {
            "token_budget": token_limit,
            "call_budget": call_limit,
            "step_budget": step_limit,
            "time_budget_ms": time_limit_ms,
            "cost_budget": cost_limit,
        }
    )

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
            request_limit = _as_int(state[request_keys[limit_key]])
            slice_cap = _as_int(used[used_keys[limit_key]]) + slice_limit
            state[limit_key] = min(request_limit, slice_cap) if request_limit else slice_cap
    return state


def durable_maintenance_usage(
    meta: object,
    *,
    namespace: str,
    maintenance_job_id: str,
) -> dict[str, object]:
    """Recover usage from authoritative events after a failed retry."""

    iterator = getattr(meta, "iter_entity_events", None)
    if not callable(iterator) or not maintenance_job_id:
        return {}
    events = []
    try:
        parameters = inspect.signature(iterator).parameters.values()
        supports_batch_size = any(
            parameter.name == "batch_size"
            or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )
        iterator_kwargs: dict[str, object] = {"namespace": namespace, "from_seq": 1}
        if supports_batch_size:
            iterator_kwargs["batch_size"] = 500
        rows = cast(Iterable[object], iterator(**iterator_kwargs))
        for row in rows:
            # Core metadata stores expose five columns; older test seams and
            # SQLite adapters may include an additional event-id column.
            if not isinstance(row, (tuple, list)) or len(row) < 5:
                continue
            payload_json = row[-1]
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
        "time_used_ms": _as_int(summary.get("time_ms")),
        "cost_used": _as_float(summary.get("total_cost")),
    }


__all__ = [
    "and_where",
    "belongs_to_workspace",
    "durable_maintenance_usage",
    "edge_ids",
    "maintenance_budget_state",
    "persisted_budget_state",
    "semantic_fingerprint",
]
