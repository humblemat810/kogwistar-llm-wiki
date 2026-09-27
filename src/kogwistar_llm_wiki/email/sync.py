"""Bounded email source synchronization with commit-after-ingest semantics."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .bindings import EmailConnectorBinding, EmailConnectorBindingStore
from .leases import EmailSyncLeaseStore
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


@dataclass(frozen=True, slots=True)
class EmailMailboxEvent:
    """Durable, content-free mailbox event derived from a source adapter."""

    workspace_id: str
    stream_id: str
    event_id: str
    kind: str
    payload: Mapping[str, object]


class EmailMailboxEventStore(Protocol):
    def append(self, events: tuple[EmailMailboxEvent, ...]) -> None: ...

    def list(self, *, workspace_id: str, stream_id: str) -> tuple[EmailMailboxEvent, ...]: ...


class InMemoryEmailMailboxEventStore:
    def __init__(self) -> None:
        self._events: dict[str, EmailMailboxEvent] = {}

    def append(self, events: tuple[EmailMailboxEvent, ...]) -> None:
        for event in events:
            existing = self._events.get(event.event_id)
            if existing is not None and existing != event:
                raise ValueError("email event ID already exists with different payload")
            self._events[event.event_id] = event

    def list(self, *, workspace_id: str, stream_id: str) -> tuple[EmailMailboxEvent, ...]:
        return tuple(
            event
            for event in self._events.values()
            if event.workspace_id == workspace_id and event.stream_id == stream_id
        )


class SQLiteEmailMailboxEventStore:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS email_mailbox_events (
                    workspace_id TEXT NOT NULL,
                    stream_id TEXT NOT NULL,
                    event_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def append(self, events: tuple[EmailMailboxEvent, ...]) -> None:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            for event in events:
                payload_json = json.dumps(
                    dict(event.payload), sort_keys=True, separators=(",", ":")
                )
                cursor = connection.execute(
                    "SELECT workspace_id, stream_id, kind, payload_json "
                    "FROM email_mailbox_events WHERE event_id = ?",
                    (event.event_id,),
                )
                try:
                    existing = cursor.fetchone()
                finally:
                    cursor.close()
                if existing is not None:
                    if tuple(existing) != (
                        event.workspace_id,
                        event.stream_id,
                        event.kind,
                        payload_json,
                    ):
                        raise ValueError("email event ID already exists with different payload")
                    continue
                connection.execute(
                    "INSERT INTO email_mailbox_events (workspace_id, stream_id, event_id, kind, payload_json) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (event.workspace_id, event.stream_id, event.event_id, event.kind, payload_json),
                )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def list(self, *, workspace_id: str, stream_id: str) -> tuple[EmailMailboxEvent, ...]:
        connection = self._connect()
        try:
            cursor = connection.execute(
                "SELECT workspace_id, stream_id, event_id, kind, payload_json "
                "FROM email_mailbox_events WHERE workspace_id = ? AND stream_id = ? "
                "ORDER BY rowid",
                (workspace_id, stream_id),
            )
            try:
                rows = cursor.fetchall()
            finally:
                cursor.close()
        finally:
            connection.close()
        return tuple(
            EmailMailboxEvent(
                workspace_id=str(row[0]),
                stream_id=str(row[1]),
                event_id=str(row[2]),
                kind=str(row[3]),
                payload=json.loads(str(row[4])),
            )
            for row in rows
        )


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
            cursor = connection.execute(
                "SELECT snapshot_json FROM email_sync_state WHERE workspace_id = ? AND stream_id = ?",
                (workspace_id, stream_id),
            )
            try:
                row = cursor.fetchone()
            finally:
                cursor.close()
        if row is None:
            return None
        payload = json.loads(str(row[0]))
        if not isinstance(payload, dict):
            raise TypeError("stored email sync snapshot must be an object")
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
        binding_store: EmailConnectorBindingStore | None = None,
        lease_store: EmailSyncLeaseStore | None = None,
        event_store: EmailMailboxEventStore | None = None,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self.runtime = runtime
        self.state_store = state_store or InMemoryEmailSyncStateStore()
        self.authorize_stream = authorize_stream or runtime.authorize_stream
        self.binding_store = binding_store
        self.lease_store = lease_store
        self.event_store = event_store
        self.clock_ms = clock_ms or (lambda: int(time.time() * 1000))

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
            message_stream_id = _stream_id(message)
            if message_stream_id is not None and message_stream_id != stream_id:
                raise PermissionError("email adapter returned a message from another stream")
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
        mailbox_events = tuple(
            _validated_mailbox_event(
                workspace_id=workspace_id,
                stream_id=stream_id,
                event=event,
            )
            for event in batch.events
        )
        if self.event_store is not None:
            self.event_store.append(mailbox_events)
        # Commit the cursor only after immutable evidence and mailbox events
        # are durable. A retry is safe because both writes are idempotent.
        self.state_store.put(
            workspace_id=workspace_id,
            stream_id=stream_id,
            snapshot=batch.snapshot,
        )
        event_kinds = tuple(event.kind for event in mailbox_events)
        return EmailSyncResult(
            workspace_id=workspace_id,
            stream_id=stream_id,
            ingested_source_revision_ids=tuple(revision_ids),
            event_kinds=event_kinds,
            has_more=bool(batch.has_more),
            snapshot_committed=True,
        )

    def sync_binding(
        self,
        *,
        workspace_id: str,
        connector_id: str,
        owner_id: str,
        adapter_factory: Callable[[EmailConnectorBinding, str], EmailSourceAdapter],
        title_prefix: str = "Email",
        lease_duration_ms: int = 60_000,
    ) -> EmailSyncResult:
        """Synchronize one durable binding under an exclusive short lease.

        ``adapter_factory`` receives the opaque credential reference, never a
        secret value. Secret resolution remains an operator-owned boundary.
        Authorization is checked before the factory is called, and the lease
        is released even when parsing or ingestion fails.
        """

        if self.binding_store is None or self.lease_store is None:
            raise RuntimeError("sync_binding requires binding_store and lease_store")
        binding = self.binding_store.get(
            workspace_id=workspace_id,
            connector_id=connector_id,
        )
        if binding is None:
            raise KeyError(f"email connector binding not found: {connector_id}")
        if not binding.enabled:
            raise ValueError("email connector binding is disabled")
        if not self.authorize_stream(workspace_id, binding.stream_id):
            raise PermissionError("email stream is not authorized for workspace")
        lease = self.lease_store.claim(
            workspace_id=workspace_id,
            connector_id=binding.connector_id,
            stream_id=binding.stream_id,
            owner_id=owner_id,
            now_ms=self.clock_ms(),
            lease_duration_ms=lease_duration_ms,
        )
        try:
            adapter = adapter_factory(binding, binding.credential_ref)
            return self.sync(
                workspace_id=workspace_id,
                stream_id=binding.stream_id,
                adapter=adapter,
                title_prefix=title_prefix,
            )
        finally:
            self.lease_store.release(lease)


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
    if hasattr(snapshot, "recheck_offset"):
        values["recheck_offset"] = int(getattr(snapshot, "recheck_offset"))
    return values


def _mailbox_event(*, workspace_id: str, stream_id: str, event: object) -> EmailMailboxEvent:
    if isinstance(event, Mapping):
        source = {str(key): value for key, value in event.items()}
    else:
        source = {
            name: getattr(event, name)
            for name in (
                "kind",
                "stream_id",
                "source_key",
                "source_revision_id",
                "content_sha256",
                "mailbox_id",
                "uid_validity",
                "uid",
                "flags",
            )
            if hasattr(event, name)
        }
    payload = {key: _json_value(value) for key, value in source.items()}
    kind = str(payload.get("kind", ""))
    if not kind:
        raise ValueError("email mailbox event kind must not be empty")
    identity = json.dumps(
        {"workspace_id": workspace_id, "stream_id": stream_id, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
    )
    return EmailMailboxEvent(
        workspace_id=workspace_id,
        stream_id=stream_id,
        event_id="email-event:" + hashlib.sha256(identity.encode("utf-8")).hexdigest(),
        kind=kind,
        payload=payload,
    )


def _validated_mailbox_event(*, workspace_id: str, stream_id: str, event: object) -> EmailMailboxEvent:
    event_stream_id = _stream_id(event)
    if event_stream_id is not None and event_stream_id != stream_id:
        raise PermissionError("email adapter returned an event from another stream")
    return _mailbox_event(workspace_id=workspace_id, stream_id=stream_id, event=event)


def _stream_id(value: object) -> str | None:
    raw = value.get("stream_id") if isinstance(value, Mapping) else getattr(value, "stream_id", None)
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _json_value(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    raise TypeError(f"email event field is not JSON-safe: {type(value).__name__}")
