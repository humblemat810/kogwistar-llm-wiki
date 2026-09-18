"""Durable parse-run statistics records and SQLite storage.

This module owns parser-quality measurements; trace formatting and live debug
output remain in ``debug_run``.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


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
    from ..diagnostics.debug_helpers import dump_json, now_ms, summarize_semantic_tree

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
