"""Durable review state for email-derived mapping proposals."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True, slots=True)
class EmailReviewState:
    workspace_id: str
    stream_id: str
    source_revision_id: str
    mapping_id: str
    source_document_id: str
    status: str = "pending"
    patch_id: str | None = None
    result: Mapping[str, object] | None = None
    updated_at_ms: int = 0


class EmailReviewStateStore(Protocol):
    def get(
        self,
        *,
        workspace_id: str,
        source_revision_id: str,
        mapping_id: str,
    ) -> EmailReviewState | None: ...

    def put(self, state: EmailReviewState) -> None: ...


class InMemoryEmailReviewStateStore:
    def __init__(self) -> None:
        self._states: dict[tuple[str, str, str], EmailReviewState] = {}

    def get(
        self,
        *,
        workspace_id: str,
        source_revision_id: str,
        mapping_id: str,
    ) -> EmailReviewState | None:
        return self._states.get((workspace_id, source_revision_id, mapping_id))

    def put(self, state: EmailReviewState) -> None:
        self._states[(state.workspace_id, state.source_revision_id, state.mapping_id)] = state


class SQLiteEmailReviewStateStore:
    """App-owned mutable review state, separate from immutable email evidence."""

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS email_review_state (
                    workspace_id TEXT NOT NULL,
                    stream_id TEXT NOT NULL,
                    source_revision_id TEXT NOT NULL,
                    mapping_id TEXT NOT NULL,
                    source_document_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    patch_id TEXT,
                    result_json TEXT,
                    updated_at_ms INTEGER NOT NULL,
                    PRIMARY KEY (workspace_id, source_revision_id, mapping_id)
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def get(
        self,
        *,
        workspace_id: str,
        source_revision_id: str,
        mapping_id: str,
    ) -> EmailReviewState | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM email_review_state WHERE workspace_id = ? "
                "AND source_revision_id = ? AND mapping_id = ?",
                (workspace_id, source_revision_id, mapping_id),
            ).fetchone()
        if row is None:
            return None
        result_json = row["result_json"]
        result = json.loads(str(result_json)) if result_json is not None else None
        return EmailReviewState(
            workspace_id=str(row["workspace_id"]),
            stream_id=str(row["stream_id"]),
            source_revision_id=str(row["source_revision_id"]),
            mapping_id=str(row["mapping_id"]),
            source_document_id=str(row["source_document_id"]),
            status=str(row["status"]),
            patch_id=str(row["patch_id"]) if row["patch_id"] is not None else None,
            result=result,
            updated_at_ms=int(row["updated_at_ms"]),
        )

    def put(self, state: EmailReviewState) -> None:
        result_json = (
            json.dumps(state.result, sort_keys=True, separators=(",", ":"))
            if state.result is not None
            else None
        )
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO email_review_state (
                    workspace_id, stream_id, source_revision_id, mapping_id,
                    source_document_id, status, patch_id, result_json, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(workspace_id, source_revision_id, mapping_id) DO UPDATE SET
                    stream_id = excluded.stream_id,
                    source_document_id = excluded.source_document_id,
                    status = excluded.status,
                    patch_id = excluded.patch_id,
                    result_json = excluded.result_json,
                    updated_at_ms = excluded.updated_at_ms
                """,
                (
                    state.workspace_id,
                    state.stream_id,
                    state.source_revision_id,
                    state.mapping_id,
                    state.source_document_id,
                    state.status,
                    state.patch_id,
                    result_json,
                    state.updated_at_ms,
                ),
            )


def pending_review_state(
    *,
    workspace_id: str,
    stream_id: str,
    source_revision_id: str,
    mapping_id: str,
    source_document_id: str,
) -> EmailReviewState:
    return EmailReviewState(
        workspace_id=workspace_id,
        stream_id=stream_id,
        source_revision_id=source_revision_id,
        mapping_id=mapping_id,
        source_document_id=source_document_id,
        updated_at_ms=int(time.time() * 1000),
    )


def review_state_payload(state: EmailReviewState | None) -> dict[str, object]:
    if state is None:
        return {"status": "pending"}
    payload = asdict(state)
    payload["result"] = dict(state.result) if state.result is not None else None
    return payload


__all__ = [
    "EmailReviewState",
    "EmailReviewStateStore",
    "InMemoryEmailReviewStateStore",
    "SQLiteEmailReviewStateStore",
    "pending_review_state",
    "review_state_payload",
]
