"""Bounded context contracts for provider-backed cross-link review."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import ceil

from kogwistar.conversation.conversation_context import (
    ContextItem,
    ContextMessage,
    ConversationContextBuilder,
)


def _integer_value(value: object, *, field: str) -> int:
    if isinstance(value, bool):
        raise TypeError(f"{field} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError as exc:
            raise ValueError(f"{field} must be an integer") from exc
    raise TypeError(f"{field} must be an integer")


@dataclass(frozen=True, slots=True)
class CrosslinkContextBudget:
    """Optional limits for graph context sent to a cross-link provider.

    Zero means that dimension is unbounded by the caller. The worker still
    applies conservative hard ceilings while reading the graph.
    """

    max_nodes: int = 0
    max_edges: int = 0
    max_tokens: int = 0
    max_characters: int = 0

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> CrosslinkContextBudget:
        raw = payload.get("crosslink_context_budget")
        if raw is None:
            raw = payload.get("crosslink_context")
        source = raw if isinstance(raw, Mapping) else payload
        keys = {
            "max_nodes": ("max_nodes", "crosslink_context_max_nodes"),
            "max_edges": ("max_edges", "crosslink_context_max_edges"),
            "max_tokens": ("max_tokens", "crosslink_context_max_tokens"),
            "max_characters": (
                "max_characters",
                "max_chars",
                "crosslink_context_max_characters",
            ),
        }
        configured = any(name in source for names in keys.values() for name in names)
        if not configured:
            return cls()

        # Once context expansion is requested, give omitted count limits a
        # bounded default rather than accidentally scanning an entire graph.
        defaults = {
            "max_nodes": 16,
            "max_edges": 24,
            "max_tokens": 0,
            "max_characters": 0,
        }
        values: dict[str, int] = {}
        for field_name, names in keys.items():
            value = next(
                (source[name] for name in names if name in source),
                defaults[field_name],
            )
            integer = _integer_value(value, field=field_name)
            if integer < 0:
                raise ValueError(f"{field_name} must be non-negative")
            values[field_name] = integer
        return cls(**values)


class _StaticContextSource:
    def __init__(self, items: Sequence[ContextItem]) -> None:
        self._items = list(items)

    def gather(self, *, conversation_id: str, purpose: str) -> list[ContextItem]:
        del conversation_id, purpose
        return list(self._items)


class _ContextRenderer:
    def render(self, items: Sequence[ContextItem], *, purpose: str) -> list[ContextMessage]:
        del items, purpose
        return [ContextMessage(role="tool", content="", source="kg_ref")]


class _ApproximateTokenizer:
    """Conservative tokenizer-compatible estimate for JSON context packing."""

    def count_tokens(self, text: str) -> int:
        return max(1, ceil(len(text) / 4))


def pack_crosslink_context(
    node_records: Sequence[Mapping[str, object]],
    edge_records: Sequence[Mapping[str, object]],
    *,
    budget: CrosslinkContextBudget,
) -> dict[str, object]:
    """Pack deterministic graph records using Kogwistar context semantics."""

    nodes = sorted((dict(item) for item in node_records), key=_record_key)
    edges = sorted((dict(item) for item in edge_records), key=_record_key)
    if budget.max_nodes:
        nodes, omitted_nodes_by_count = nodes[: budget.max_nodes], max(
            0, len(nodes) - budget.max_nodes
        )
    else:
        omitted_nodes_by_count = 0
    if budget.max_edges:
        edges, omitted_edges_by_count = edges[: budget.max_edges], max(
            0, len(edges) - budget.max_edges
        )
    else:
        omitted_edges_by_count = 0

    records: list[tuple[str, dict[str, object]]] = [
        ("node", item) for item in nodes
    ] + [("edge", item) for item in edges]
    items = [
        ContextItem(
            kind="kg_ref",
            role="tool",
            text=json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            extra={"crosslink_record": record, "crosslink_kind": kind},
            node_id=str(record.get("node_id") or record.get("edge_id") or ""),
            edge_ids=(str(record["edge_id"]),) if record.get("edge_id") else (),
            priority=20 if kind == "node" else 30,
            source="kg_ref",
        )
        for kind, record in records
    ]
    token_limit = budget.max_tokens or max(1, len(items) * 100_000)
    packed = ConversationContextBuilder(
        sources=_StaticContextSource(items),
        tokenizer=_ApproximateTokenizer(),
        renderer=_ContextRenderer(),
    ).build(
        conversation_id="crosslink-context",
        purpose="background-crosslink-review",
        budget_tokens=token_limit,
        ordering_strategy="graph_derived",
    )

    kept: list[dict[str, object]] = []
    omitted_nodes = omitted_nodes_by_count
    omitted_edges = omitted_edges_by_count
    used_characters = 0
    for item in packed.items:
        record = dict((item.extra or {}).get("crosslink_record") or {})
        kind = str((item.extra or {}).get("crosslink_kind") or "")
        characters = len(item.text)
        if budget.max_characters and used_characters + characters > budget.max_characters:
            if kind == "node":
                omitted_nodes += 1
            else:
                omitted_edges += 1
            continue
        kept.append(record)
        used_characters += characters

    dropped_by_builder: dict[str, int] = {}
    for item in packed.dropped:
        # DroppedItem retains accounting metadata, not ContextItem.extra.
        kind = str(item.kind)
        dropped_by_builder[kind] = dropped_by_builder.get(kind, 0) + 1

    return {
        "nodes": [item for item in kept if item.get("context_kind") == "neighbor_node"],
        "edges": [item for item in kept if item.get("context_kind") == "neighbor_edge"],
        "omitted_nodes": omitted_nodes,
        "omitted_edges": omitted_edges,
        "estimated_tokens": sum(max(1, ceil(len(json.dumps(item, sort_keys=True)) / 4)) for item in kept),
        "characters": used_characters,
        "budget": {
            "max_nodes": budget.max_nodes,
            "max_edges": budget.max_edges,
            "max_tokens": budget.max_tokens,
            "max_characters": budget.max_characters,
        },
        "dropped_by_builder": dropped_by_builder,
    }


def _record_key(record: Mapping[str, object]) -> tuple[str, str]:
    return (
        str(record.get("context_kind") or ""),
        str(record.get("node_id") or record.get("edge_id") or ""),
    )


__all__ = ["CrosslinkContextBudget", "pack_crosslink_context"]
