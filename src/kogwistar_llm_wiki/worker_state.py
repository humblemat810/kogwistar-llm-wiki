"""Pure worker helpers for workspace filtering, fingerprints, and budgets."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from hashlib import sha256

from kogwistar.engine_core.models import GraphExtractionWithIDs
from kogwistar.runtime import budget_event_from_dict
from kogwistar.runtime.budget_adapters import summarize_budget_events

logger = logging.getLogger(__name__)


def and_where(*clauses: dict[str, object]) -> dict[str, list[dict[str, object]]]:
    """Compose a Chroma-compatible conjunction filter."""

    return {"$and": [dict(clause) for clause in clauses]}


def belongs_to_workspace(node: object, workspace_id: str) -> bool:
    """Apply a metadata boundary check after namespace/ACL filtering."""

    metadata = getattr(node, "metadata", None)
    if not isinstance(metadata, Mapping):
        return True
    declared_workspace = str(metadata.get("workspace_id") or "").strip()
    return not declared_workspace or declared_workspace == str(workspace_id)


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
        used[key] = max(float(used[key] or 0), float(value))
        state[key] = int(used[key]) if key != "cost_used" else float(used[key])
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
            request_limit = int(state[request_keys[limit_key]] or 0)
            slice_cap = int(used[used_keys[limit_key]] or 0) + slice_limit
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


__all__ = [
    "and_where",
    "belongs_to_workspace",
    "durable_maintenance_usage",
    "edge_ids",
    "maintenance_budget_state",
    "persisted_budget_state",
    "semantic_fingerprint",
]
