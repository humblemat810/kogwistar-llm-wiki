"""Execution context guards for maintenance-owned work."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar, Token

_MAINTENANCE_EXECUTION: ContextVar[bool] = ContextVar(
    "kogwistar_llm_wiki_maintenance_execution",
    default=False,
)

MAX_MAINTENANCE_CONTEXT_TOKENS = 10_000
_MAX_CONTEXT_CHARACTERS = MAX_MAINTENANCE_CONTEXT_TOKENS
_MAX_TURNS = 10
_MAX_IDS = 64
_MAX_REASON_ENTRIES = 24


def _bounded_string(value: object, *, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _bounded_ids(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    result: list[str] = []
    for item in value:
        item_id = _bounded_string(item, limit=256)
        if item_id and item_id not in result:
            result.append(item_id)
        if len(result) >= _MAX_IDS:
            break
    return result


def _bounded_turn(value: object) -> dict[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    reasons_value = value.get("selection_reasons")
    reasons: list[dict[str, object]] = []
    if isinstance(reasons_value, Sequence) and not isinstance(reasons_value, (str, bytes, bytearray)):
        for reason in reasons_value[:_MAX_REASON_ENTRIES]:
            if not isinstance(reason, Mapping):
                continue
            score = reason.get("score")
            reasons.append(
                {
                    "candidate_id": _bounded_string(reason.get("candidate_id"), limit=256),
                    "reason": _bounded_string(reason.get("reason"), limit=64),
                    "score": score if isinstance(score, (int, float)) else None,
                }
            )
    return {
        "round": max(0, int(value.get("round") or 0)),
        "summary": _bounded_string(value.get("summary"), limit=2_000),
        "touched_node_ids": _bounded_ids(value.get("touched_node_ids")),
        "touched_edge_ids": _bounded_ids(value.get("touched_edge_ids")),
        "next_seed_node_ids": _bounded_ids(value.get("next_seed_node_ids")),
        "hop_limit": max(0, min(8, int(value.get("hop_limit") or 0))),
        "selection_reasons": reasons,
    }


def bound_maintenance_context(value: Mapping[str, object] | None) -> dict[str, object]:
    """Keep structured continuation state bounded without accepting transcripts."""
    if not isinstance(value, Mapping):
        return {"version": 1, "turns": [], "compressed_summary": "", "truncated": False}
    turns_value = value.get("turns")
    turns: list[dict[str, object]] = []
    if isinstance(turns_value, Sequence) and not isinstance(turns_value, (str, bytes, bytearray)):
        for item in turns_value[-_MAX_TURNS:]:
            turn = _bounded_turn(item)
            if turn is not None:
                turns.append(turn)
    result: dict[str, object] = {
        "version": 1,
        "turns": turns,
        "compressed_summary": _bounded_string(value.get("compressed_summary"), limit=4_000),
        "compressed_node_ids": _bounded_ids(value.get("compressed_node_ids")),
        "compressed_edge_ids": _bounded_ids(value.get("compressed_edge_ids")),
        "truncated": bool(value.get("truncated", False)),
    }
    while len(json.dumps(result, sort_keys=True, separators=(",", ":"))) > _MAX_CONTEXT_CHARACTERS:
        if turns:
            turns.pop(0)
            result["truncated"] = True
            continue
        summary = str(result["compressed_summary"])
        if summary:
            result["compressed_summary"] = summary[: max(0, len(summary) - 256)]
            result["truncated"] = True
            continue
        result["compressed_node_ids"] = list(result["compressed_node_ids"])[:-8]
        result["compressed_edge_ids"] = list(result["compressed_edge_ids"])[:-8]
        result["truncated"] = True
        if len(json.dumps(result, sort_keys=True, separators=(",", ":"))) <= _MAX_CONTEXT_CHARACTERS:
            break
    return result


def append_maintenance_round(
    value: Mapping[str, object] | None,
    *,
    round_number: int,
    summary: str,
    touched_node_ids: Sequence[str] = (),
    touched_edge_ids: Sequence[str] = (),
    next_seed_node_ids: Sequence[str] = (),
    hop_limit: int = 0,
    selection_reasons: Sequence[Mapping[str, object]] = (),
) -> dict[str, object]:
    """Append a structured round while preserving the context bound."""
    context = bound_maintenance_context(value)
    turns = list(context.get("turns") or [])
    turns.append(
        {
            "round": max(0, int(round_number)),
            "summary": summary,
            "touched_node_ids": list(touched_node_ids),
            "touched_edge_ids": list(touched_edge_ids),
            "next_seed_node_ids": list(next_seed_node_ids),
            "hop_limit": hop_limit,
            "selection_reasons": list(selection_reasons),
        }
    )
    context["turns"] = turns
    return bound_maintenance_context(context)


def maintenance_execution_active() -> bool:
    """Return whether the current call stack belongs to a maintenance worker."""

    return _MAINTENANCE_EXECUTION.get()


@contextmanager
def maintenance_execution_context() -> Iterator[None]:
    """Mark nested application calls as maintenance-owned until they return."""

    token: Token[bool] = _MAINTENANCE_EXECUTION.set(True)
    try:
        yield
    finally:
        _MAINTENANCE_EXECUTION.reset(token)


__all__ = [
    "MAX_MAINTENANCE_CONTEXT_TOKENS",
    "append_maintenance_round",
    "bound_maintenance_context",
    "maintenance_execution_active",
    "maintenance_execution_context",
]
