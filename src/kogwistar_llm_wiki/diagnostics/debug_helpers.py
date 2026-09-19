"""Debug-run helpers, live tracing, and timing summaries."""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..parsing import parse_statistics as _parse_statistics


def now_ms() -> int:
    return int(time.time() * 1000)


def summarize_stage_timings(layer_log: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Summarize paired ``*_start``/completion events from a parser layer log.

    This is deliberately derived from the existing event log so it remains useful
    for persisted runs and does not require a second timing system in the parser.
    Repeated nested stages are handled with a stack; unfinished stages are visible
    instead of being silently reported as zero duration.
    """
    terminal_suffixes = ("_complete", "_completed", "_done", "_returned", "_failed", "_skipped")
    starts: dict[str, list[int]] = {}
    durations: dict[str, list[int]] = {}
    open_counts: dict[str, int] = {}
    for event in layer_log:
        stage = str(event.get("stage") or "")
        timestamp = event.get("timestamp_ms")
        if not stage or not isinstance(timestamp, int):
            continue
        if stage.endswith("_start"):
            base = stage[: -len("_start")]
            starts.setdefault(base, []).append(timestamp)
            continue
        base = next((stage[: -len(suffix)] for suffix in terminal_suffixes if stage.endswith(suffix)), None)
        if base is None:
            continue
        pending = starts.get(base)
        if not pending:
            continue
        started_at = pending.pop()
        durations.setdefault(base, []).append(max(0, timestamp - started_at))
    for base, pending in starts.items():
        if pending:
            open_counts[base] = len(pending)
    stages: dict[str, dict[str, Any]] = {}
    for base in sorted(set(durations) | set(open_counts)):
        values = durations.get(base, [])
        stages[base] = {
            "count": len(values),
            "total_ms": sum(values),
            "average_ms": round(sum(values) / len(values), 2) if values else None,
            "max_ms": max(values) if values else None,
            "open_count": open_counts.get(base, 0),
        }
    ranked = sorted(stages.items(), key=lambda item: (item[1]["total_ms"], item[0]), reverse=True)
    operation_ranked = [
        item for item in ranked
        if not item[0].endswith("_parse") and item[0] not in {"parse", "workflow_layered_parse"}
    ]
    return {
        "stage_count": len(stages),
        "stages": stages,
        "dominant_stage": ranked[0][0] if ranked and ranked[0][1]["total_ms"] > 0 else None,
        "dominant_stage_total_ms": ranked[0][1]["total_ms"] if ranked else 0,
        "dominant_operation_stage": operation_ranked[0][0] if operation_ranked else None,
        "dominant_operation_stage_total_ms": operation_ranked[0][1]["total_ms"] if operation_ranked else 0,
    }


def aggregate_stage_timings(summaries: list[Mapping[str, Any]]) -> dict[str, Any]:
    """Aggregate per-document timing summaries for a run-level report."""
    stages: dict[str, dict[str, int | float]] = {}
    for summary in summaries:
        for name, values in dict(summary.get("stages") or {}).items():
            target = stages.setdefault(name, {"count": 0, "total_ms": 0, "open_count": 0})
            target["count"] += int(values.get("count") or 0)
            target["total_ms"] += int(values.get("total_ms") or 0)
            target["open_count"] += int(values.get("open_count") or 0)
    for values in stages.values():
        values["average_ms"] = round(float(values["total_ms"]) / int(values["count"]), 2) if values["count"] else 0
    ranked = sorted(stages.items(), key=lambda item: (item[1]["total_ms"], item[0]), reverse=True)
    operation_ranked = [
        item for item in ranked
        if not item[0].endswith("_parse") and item[0] not in {"parse", "workflow_layered_parse"}
    ]
    return {
        "stages": dict(ranked),
        "dominant_stage": ranked[0][0] if ranked else None,
        "dominant_stage_total_ms": ranked[0][1]["total_ms"] if ranked else 0,
        "dominant_operation_stage": operation_ranked[0][0] if operation_ranked else None,
        "dominant_operation_stage_total_ms": operation_ranked[0][1]["total_ms"] if operation_ranked else 0,
    }


def _json_default(value: object) -> object:
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="json")
        except TypeError:
            return value.model_dump()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, set):
        return sorted(str(item) for item in value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def dump_json(obj: object, *, indent: int | None = None) -> str:
    return json.dumps(obj, indent=indent, sort_keys=True, default=_json_default)


def append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(dump_json(dict(payload)))
        handle.write("\n")


def env_flag_enabled(*names: str, default: bool = False) -> bool:
    for name in names:
        value = os.getenv(name)
        if value is None:
            continue
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


_LIVE_TRACE_FIELD_ORDER = (
    "workspace_id",
    "source_document_id",
    "doc_id",
    "run_id",
    "token_id",
    "turn_node_id",
    "node_id",
    "step_name",
    "step_seq",
    "attempt",
    "step",
    "current_step",
    "workflow_id",
    "parser_lane",
    "parser_mode",
    "proposal_mode",
    "operation_mode",
    "status",
    "duration_ms",
    "timeout_seconds",
    "configured_parse_timeout_seconds",
    "remaining_runtime_seconds",
    "max_runtime_seconds",
    "max_llm_calls",
    "child_exitcode",
    "predicate",
    "value",
    "edge_id",
    "to_node_id",
    "reason",
    "next_nodes",
    "trace_path",
    "failure_path",
    "error_type",
    "error_message",
    "errors",
    "message",
)


def _compact_live_trace_value(value: object) -> str:
    if isinstance(value, str):
        text = value
    elif isinstance(value, (int, float, bool)):
        text = str(value)
    else:
        text = dump_json(value)
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    if len(text) > 180:
        return f"{text[:177]}..."
    return text


def _decode_payload_json(payload: Mapping[str, Any]) -> dict[str, Any]:
    raw_payload = payload.get("payload_json")
    if not isinstance(raw_payload, str) or not raw_payload.strip():
        return {}
    try:
        decoded = json.loads(raw_payload)
    except json.JSONDecodeError:
        return {"message": raw_payload}
    return dict(decoded) if isinstance(decoded, Mapping) else {}


def format_live_trace(prefix: str, payload: Mapping[str, Any]) -> str:
    stage = payload.get("stage") or payload.get("type") or payload.get("phase") or "event"
    parts = [f"[{prefix}] {stage}"]
    decoded_payload = _decode_payload_json(payload)
    for key in _LIVE_TRACE_FIELD_ORDER:
        value = payload.get(key, decoded_payload.get(key))
        if value is not None:
            parts.append(f"{key}={_compact_live_trace_value(value)}")
    return " ".join(parts)


class LiveTracePrinter:
    __slots__ = ("prefix",)

    def __init__(self, *, prefix: str = "llm-wiki") -> None:
        self.prefix = prefix

    def emit(self, event: Mapping[str, Any]) -> None:
        print(format_live_trace(self.prefix, event), file=sys.stderr, flush=True)


def configure_debug_logging(debug_dir: str | Path | None) -> Path | None:
    if not debug_dir:
        return None
    path = Path(debug_dir).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    log_path = path / "llm_wiki.log"
    root = logging.getLogger()
    if not any(isinstance(handler, logging.FileHandler) and Path(getattr(handler, "baseFilename", "")).resolve() == log_path for handler in root.handlers):
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        root.addHandler(handler)
    root.setLevel(min(root.level or logging.INFO, logging.INFO))
    return path


def _max_tree_depth(node: object) -> int:
    children = getattr(node, "child_nodes", None)
    if children is None:
        children = getattr(node, "children", None)
    if not children:
        return 1
    return 1 + max(_max_tree_depth(child) for child in children)


def _count_tree_nodes(node: object) -> int:
    children = getattr(node, "child_nodes", None)
    if children is None:
        children = getattr(node, "children", None)
    if not children:
        return 1
    return 1 + sum(_count_tree_nodes(child) for child in children)


def _count_tree_leaves(node: object) -> int:
    children = getattr(node, "child_nodes", None)
    if children is None:
        children = getattr(node, "children", None)
    if not children:
        return 1
    return sum(_count_tree_leaves(child) for child in children)


def summarize_semantic_tree(tree: object) -> dict[str, int]:
    return {
        "tree_depth": _max_tree_depth(tree),
        "tree_node_count": _count_tree_nodes(tree),
        "tree_leaf_count": _count_tree_leaves(tree),
    }

ParseStatisticsRecord = _parse_statistics.ParseStatisticsRecord
ParseStatisticsStore = _parse_statistics.ParseStatisticsStore
build_parse_statistics_record = _parse_statistics.build_parse_statistics_record
