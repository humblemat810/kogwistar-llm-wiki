"""Bounded email source synchronization with commit-after-ingest semantics."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Any, Protocol

from .runtime import EmailIngestRequest, EmailRuntime


class EmailSourceMessage(Protocol):
    source_key: str
    raw_bytes: bytes
    source_revision_id: str


class EmailSyncBatch(Protocol):
    messages: tuple[EmailSourceMessage, ...]
    events: tuple[object, ...]
    snapshot: object
    has_more: bool


class EmailSourceAdapter(Protocol):
    def sync(self, snapshot: object | None = None) -> EmailSyncBatch:
        """Read one bounded batch from an authorized source."""


class EmailSyncStateStore(Protocol):
    def get(self, *, workspace_id: str, stream_id: str) -> object | None: ...

    def put(self, *, workspace_id: str, stream_id: str, snapshot: object) -> None: ...


class InMemoryEmailSyncStateStore:
    def __init__(self) -> None:
        self._snapshots: dict[tuple[str, str], object] = {}

    def get(self, *, workspace_id: str, stream_id: str) -> object | None:
        return self._snapshots.get((workspace_id, stream_id))

    def put(self, *, workspace_id: str, stream_id: str, snapshot: object) -> None:
        self._snapshots[(workspace_id, stream_id)] = snapshot


class SQLiteEmailSyncStateStore:
    """Durable cursor state with an explicit snapshot decoder."""

    def __init__(self, path: str | Path, *, snapshot_type: type[Any]) -> None:
        self.path = str(path)
        self.snapshot_type = snapshot_type
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS email_sync_state (
                    workspace_id TEXT NOT NULL,
                    stream_id TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    PRIMARY KEY (workspace_id, stream_id)
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def get(self, *, workspace_id: str, stream_id: str) -> object | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT snapshot_json FROM email_sync_state WHERE workspace_id = ? AND stream_id = ?",
                (workspace_id, stream_id),
            ).fetchone()
        if row is None:
            return None
        payload = json.loads(str(row[0]))
        if not isinstance(payload, dict):
            raise ValueError("stored email sync snapshot must be an object")
        if isinstance(payload.get("known_keys"), list):
            payload["known_keys"] = tuple(payload["known_keys"])
        return self.snapshot_type(**payload)

    def put(self, *, workspace_id: str, stream_id: str, snapshot: object) -> None:
        payload = _snapshot_payload(snapshot)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO email_sync_state(workspace_id, stream_id, snapshot_json)
                VALUES (?, ?, ?)
                ON CONFLICT(workspace_id, stream_id) DO UPDATE SET snapshot_json=excluded.snapshot_json
                """,
                (workspace_id, stream_id, json.dumps(payload, sort_keys=True, separators=(",", ":"))),
            )


@dataclass(frozen=True, slots=True)
class EmailSyncResult:
    workspace_id: str
    stream_id: str
    ingested_source_revision_ids: tuple[str, ...]
    event_kinds: tuple[str, ...]
    has_more: bool
    snapshot_committed: bool


class EmailSyncService:
    def __init__(
        self,
        *,
        runtime: EmailRuntime,
        state_store: EmailSyncStateStore | None = None,
        authorize_stream: Callable[[str, str], bool] | None = None,
    ) -> None:
        self.runtime = runtime
        self.state_store = state_store or InMemoryEmailSyncStateStore()
        self.authorize_stream = authorize_stream or runtime.authorize_stream

    def sync(
        self,
        *,
        workspace_id: str,
        stream_id: str,
        adapter: EmailSourceAdapter,
        title_prefix: str = "Email",
    ) -> EmailSyncResult:
        authorize = self.authorize_stream
        if not callable(authorize) or not authorize(workspace_id, stream_id):
            raise PermissionError("email stream is not authorized for workspace")
        previous = self.state_store.get(workspace_id=workspace_id, stream_id=stream_id)
        batch = adapter.sync(snapshot=previous)
        revision_ids: list[str] = []
        for message in batch.messages:
            self.runtime.ingest(
                EmailIngestRequest(
                    workspace_id=workspace_id,
                    stream_id=stream_id,
                    source_key=str(message.source_key),
                    raw_bytes=message.raw_bytes,
                    source_revision_id=str(message.source_revision_id),
                    title=f"{title_prefix}: {message.source_key}",
                )
            )
            revision_ids.append(str(message.source_revision_id))
        self.state_store.put(
            workspace_id=workspace_id,
            stream_id=stream_id,
            snapshot=batch.snapshot,
        )
        event_kinds = tuple(
            str(event.get("kind", ""))
            if isinstance(event, Mapping)
            else str(getattr(event, "kind", ""))
            for event in batch.events
        )
        return EmailSyncResult(
            workspace_id=workspace_id,
            stream_id=stream_id,
            ingested_source_revision_ids=tuple(revision_ids),
            event_kinds=event_kinds,
            has_more=bool(batch.has_more),
            snapshot_committed=True,
        )


def _snapshot_payload(snapshot: object) -> dict[str, object]:
    if isinstance(snapshot, Mapping):
        return {str(key): value for key, value in snapshot.items()}
    values: dict[str, object] = {}
    for name in ("stream_id", "source_kind", "known_keys", "content_sha256_by_key"):
        if not hasattr(snapshot, name):
            raise TypeError("email sync snapshot does not expose a supported field set")
        value = getattr(snapshot, name)
        if isinstance(value, Mapping):
            value = dict(value)
        elif isinstance(value, tuple):
            value = list(value)
        values[name] = value
    return values
