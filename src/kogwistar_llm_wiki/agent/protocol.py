"""Bounded request parsing and budget helpers for agent gateways."""

from __future__ import annotations

from collections.abc import Mapping
from numbers import Real
from typing import Any

from ..models import IngestPipelineRequest


def content_to_text(value: object) -> str:
    """Extract text from OpenAI/A2A content parts without Python reprs."""
    if isinstance(value, str):
        return value
    if isinstance(value, Mapping):
        if "text" in value:
            return content_to_text(value["text"])
        if "content" in value:
            return content_to_text(value["content"])
        return ""
    if isinstance(value, (list, tuple)):
        return "\n".join(text for text in (content_to_text(item) for item in value) if text)
    return "" if value is None else str(value)


def request_payload(payload: Mapping[str, Any]) -> dict[str, object]:
    messages = payload.get("messages")
    if isinstance(messages, list):
        text = next(
            (
                content_to_text(item.get("content"))
                for item in reversed(messages)
                if isinstance(item, Mapping) and item.get("role") == "user"
            ),
            "",
        )
    else:
        value = payload.get("input", payload.get("query_text", ""))
        if isinstance(payload.get("message"), Mapping):
            parts = payload["message"].get("parts")
            if isinstance(parts, list):
                value = "\n".join(content_to_text(part) for part in parts)
        if isinstance(value, list):
            text = "\n".join(
                content_to_text(item.get("content", ""))
                for item in value
                if isinstance(item, Mapping)
            )
        else:
            text = content_to_text(value)
    metadata = payload.get("metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    request: dict[str, object] = {
        "workspace_id": str(payload.get("workspace_id") or metadata.get("workspace_id") or "default"),
        "query_text": text,
        "session_id": str(payload.get("session_id") or metadata.get("session_id") or "default"),
        "mode": str(payload.get("mode") or metadata.get("mode") or "deterministic"),
    }
    for key in (
        "graph_spaces",
        "semantic_retrieval",
        "explicit_anchor_ids",
        "hop_limit",
        "max_nodes",
        "max_edges",
        "max_hyperedges",
        "pinned_node_ids",
        "source_watermark",
        "include_tombstones",
        "interaction_id",
    ):
        if key in payload:
            request[key] = payload[key]
    return request


def budgets(arguments: Mapping[str, Any]) -> dict[str, object]:
    names = ("max_time_seconds", "max_llm_calls", "max_tokens", "max_cost_usd", "max_steps")
    integer_names = {"max_llm_calls", "max_tokens", "max_steps"}
    result: dict[str, object] = {}
    for name in names:
        if name in arguments and arguments[name] is not None:
            value = arguments[name]
            if isinstance(value, bool) or not isinstance(value, Real) or value < 0:
                raise ValueError(f"{name} must be a non-negative number")
            if name in integer_names and not isinstance(value, int):
                raise ValueError(f"{name} must be a non-negative integer")
            result[name] = value
    return result


def limit_budgeted_sources(
    source_requests: list[tuple[str, IngestPipelineRequest]],
    budget_values: Mapping[str, object],
) -> tuple[list[tuple[str, IngestPipelineRequest]], list[str]]:
    """Prevent request-level call/step quotas from multiplying per document."""
    limits = [
        int(budget_values[name])
        for name in ("max_llm_calls", "max_steps")
        if name in budget_values and int(budget_values[name]) > 0
    ]
    if not limits:
        return source_requests, []
    allowed = min(len(source_requests), min(limits))
    return source_requests[:allowed], [source_id for source_id, _ in source_requests[allowed:]]


def partition_budgets(
    budget_values: Mapping[str, object], count: int, index: int
) -> dict[str, object]:
    """Partition additive request budgets deterministically across source jobs."""
    if count <= 0:
        return dict(budget_values)
    result: dict[str, object] = {}
    for name, value in budget_values.items():
        if not isinstance(value, Real) or isinstance(value, bool):
            result[name] = value
            continue
        if name in {"max_llm_calls", "max_tokens", "max_steps"}:
            total = int(value)
            base, remainder = divmod(total, count)
            result[name] = base + (1 if index < remainder else 0)
        else:
            result[name] = float(value) / count
    return result


def bounded_lens_arguments(arguments: Mapping[str, Any]) -> dict[str, object]:
    """Apply server-side result bounds instead of trusting agent limits."""
    result = dict(arguments)
    limits = {"hop_limit": 8, "max_nodes": 500, "max_edges": 2000, "max_hyperedges": 250}
    for name, upper_bound in limits.items():
        if name not in result or result[name] is None:
            continue
        value = result[name]
        if isinstance(value, bool):
            raise TypeError(f"{name} must be a non-negative integer")
        try:
            value = int(value)
        except (TypeError, ValueError) as exc:
            raise TypeError(f"{name} must be a non-negative integer") from exc
        result[name] = max(0, min(value, upper_bound))
    return result


__all__ = [
    "bounded_lens_arguments",
    "budgets",
    "content_to_text",
    "limit_budgeted_sources",
    "partition_budgets",
    "request_payload",
]
