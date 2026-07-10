from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping


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


@dataclass(frozen=True, slots=True)
class ParseStatisticsRecord:
    created_at_ms: int
    workspace_id: str
    source_document_id: str
    source_uri: str
    title: str
    parser_lane: str
    parser_mode: str
    proposal_mode: str | None
    provider: str | None
    model: str | None
    document_length_chars: int
    document_length_words: int
    parse_runtime_ms: int
    tree_depth: int
    tree_node_count: int
    tree_leaf_count: int
    node_count: int
    edge_count: int
    basic_sense_score: float | None
    basic_sense_verdict: str | None
    coverage_ratio: float | None
    fallback_used: bool | None
    retry_used: bool | None
    status: str
    details_json: str


class ParseStatisticsStore:
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser().resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS parse_run_statistics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at_ms INTEGER NOT NULL,
                    workspace_id TEXT NOT NULL,
                    source_document_id TEXT NOT NULL,
                    source_uri TEXT NOT NULL,
                    title TEXT NOT NULL,
                    parser_lane TEXT NOT NULL,
                    parser_mode TEXT NOT NULL,
                    proposal_mode TEXT,
                    provider TEXT,
                    model TEXT,
                    document_length_chars INTEGER NOT NULL,
                    document_length_words INTEGER NOT NULL,
                    parse_runtime_ms INTEGER NOT NULL,
                    tree_depth INTEGER NOT NULL,
                    tree_node_count INTEGER NOT NULL,
                    tree_leaf_count INTEGER NOT NULL,
                    node_count INTEGER NOT NULL,
                    edge_count INTEGER NOT NULL,
                    basic_sense_score REAL,
                    basic_sense_verdict TEXT,
                    coverage_ratio REAL,
                    fallback_used INTEGER,
                    retry_used INTEGER,
                    status TEXT NOT NULL,
                    details_json TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_parse_run_statistics_workspace_time
                ON parse_run_statistics(workspace_id, created_at_ms DESC)
                """
            )

    def record_parse_run(self, record: ParseStatisticsRecord) -> None:
        payload = asdict(record)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO parse_run_statistics (
                    created_at_ms,
                    workspace_id,
                    source_document_id,
                    source_uri,
                    title,
                    parser_lane,
                    parser_mode,
                    proposal_mode,
                    provider,
                    model,
                    document_length_chars,
                    document_length_words,
                    parse_runtime_ms,
                    tree_depth,
                    tree_node_count,
                    tree_leaf_count,
                    node_count,
                    edge_count,
                    basic_sense_score,
                    basic_sense_verdict,
                    coverage_ratio,
                    fallback_used,
                    retry_used,
                    status,
                    details_json
                ) VALUES (
                    :created_at_ms,
                    :workspace_id,
                    :source_document_id,
                    :source_uri,
                    :title,
                    :parser_lane,
                    :parser_mode,
                    :proposal_mode,
                    :provider,
                    :model,
                    :document_length_chars,
                    :document_length_words,
                    :parse_runtime_ms,
                    :tree_depth,
                    :tree_node_count,
                    :tree_leaf_count,
                    :node_count,
                    :edge_count,
                    :basic_sense_score,
                    :basic_sense_verdict,
                    :coverage_ratio,
                    :fallback_used,
                    :retry_used,
                    :status,
                    :details_json
                )
                """,
                {
                    **payload,
                    "fallback_used": int(bool(record.fallback_used)) if record.fallback_used is not None else None,
                    "retry_used": int(bool(record.retry_used)) if record.retry_used is not None else None,
                },
            )

    def latest_rows(self, *, limit: int = 20) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT *
                FROM parse_run_statistics
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]


def _word_count(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


def build_parse_statistics_record(
    *,
    workspace_id: str,
    source_document_id: str,
    source_uri: str,
    title: str,
    raw_text: str,
    parser_lane: str,
    parser_mode: str,
    proposal_mode: str | None,
    provider: str | None,
    model: str | None,
    parse_runtime_ms: int,
    semantic_tree: object,
    graph_payload: Mapping[str, Any] | None,
    diagnostics: Mapping[str, Any] | None,
    evaluation: Mapping[str, Any] | None,
    status: str,
) -> ParseStatisticsRecord:
    tree_summary = summarize_semantic_tree(semantic_tree)
    graph_nodes = list((graph_payload or {}).get("nodes") or [])
    graph_edges = list((graph_payload or {}).get("edges") or [])
    diagnostics_dict = dict(diagnostics or {})
    evaluation_dict = dict(evaluation or {})
    details = {
        "diagnostics": diagnostics_dict,
        "evaluation": evaluation_dict,
        "graph_node_ids": [str(node.get("id") or "") for node in graph_nodes if isinstance(node, Mapping)],
        "graph_edge_ids": [str(edge.get("id") or "") for edge in graph_edges if isinstance(edge, Mapping)],
        "parser_lane": parser_lane,
        "proposal_mode": proposal_mode,
        "provider": provider,
        "model": model,
    }
    return ParseStatisticsRecord(
        created_at_ms=now_ms(),
        workspace_id=workspace_id,
        source_document_id=source_document_id,
        source_uri=source_uri,
        title=title,
        parser_lane=parser_lane,
        parser_mode=parser_mode,
        proposal_mode=proposal_mode,
        provider=provider,
        model=model,
        document_length_chars=len(raw_text or ""),
        document_length_words=_word_count(raw_text or ""),
        parse_runtime_ms=parse_runtime_ms,
        tree_depth=int(tree_summary["tree_depth"]),
        tree_node_count=int(tree_summary["tree_node_count"]),
        tree_leaf_count=int(tree_summary["tree_leaf_count"]),
        node_count=len(graph_nodes),
        edge_count=len(graph_edges),
        basic_sense_score=(
            float(evaluation_dict["basic_sense_score"])
            if evaluation_dict.get("basic_sense_score") is not None
            else None
        ),
        basic_sense_verdict=(
            str(evaluation_dict["basic_sense_verdict"])
            if evaluation_dict.get("basic_sense_verdict") is not None
            else None
        ),
        coverage_ratio=(
            float(evaluation_dict["coverage_ratio"])
            if evaluation_dict.get("coverage_ratio") is not None
            else None
        ),
        fallback_used=(
            bool(evaluation_dict["fallback_used"])
            if evaluation_dict.get("fallback_used") is not None
            else None
        ),
        retry_used=(
            bool(diagnostics_dict.get("retry_used"))
            if diagnostics_dict.get("retry_used") is not None
            else None
        ),
        status=status,
        details_json=dump_json(details),
    )
