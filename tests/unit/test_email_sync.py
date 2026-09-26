from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from kogwistar_llm_wiki.email import (
    EmailIngestRequest,
    EmailRuntime,
    EmailSyncService,
    InMemoryEmailEvidenceStore,
    SQLiteEmailSyncStateStore,
)


EMAIL_PLUGIN_SRC = Path(__file__).parents[2] / "kogwistar-email-plugin" / "src"
if str(EMAIL_PLUGIN_SRC) not in sys.path:
    sys.path.insert(0, str(EMAIL_PLUGIN_SRC))


RAW_EMAIL = (
    b"From: Alice <alice@example.test>\r\n"
    b"To: Bob <bob@example.test>\r\n"
    b"Subject: Sync test\r\n"
    b"Message-ID: <sync@example.test>\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n\r\n"
    b"sync body\r\n"
)


@dataclass(frozen=True)
class Snapshot:
    stream_id: str
    source_kind: str
    known_keys: tuple[str, ...]
    content_sha256_by_key: dict[str, str]


@dataclass(frozen=True)
class Message:
    source_key: str
    raw_bytes: bytes
    source_revision_id: str


@dataclass(frozen=True)
class Event:
    kind: str


@dataclass(frozen=True)
class Batch:
    messages: tuple[Message, ...]
    events: tuple[Event, ...]
    snapshot: Snapshot
    has_more: bool = False


class Adapter:
    def __init__(self, batch: Batch) -> None:
        self.batch = batch
        self.snapshots: list[object | None] = []

    def sync(self, snapshot: object | None = None) -> Batch:
        self.snapshots.append(snapshot)
        return self.batch


def test_email_sync_ingests_batch_before_committing_snapshot(pipeline, tmp_path: Path) -> None:
    runtime = EmailRuntime(pipeline=pipeline, store=InMemoryEmailEvidenceStore())
    state = SQLiteEmailSyncStateStore(tmp_path / "sync.sqlite3", snapshot_type=Snapshot)
    snapshot = Snapshot("stream-a", "rfc822", (), {})
    adapter = Adapter(
        Batch(
            messages=(Message("uid:1", RAW_EMAIL, "revision-1"),),
            events=(Event("message_discovered"),),
            snapshot=snapshot,
        )
    )
    service = EmailSyncService(runtime=runtime, state_store=state)

    result = service.sync(workspace_id="w", stream_id="stream-a", adapter=adapter)

    assert result.ingested_source_revision_ids == ("revision-1",)
    assert result.event_kinds == ("message_discovered",)
    assert result.snapshot_committed is True
    assert state.get(workspace_id="w", stream_id="stream-a") == snapshot

    second = Adapter(Batch(messages=(), events=(), snapshot=snapshot))
    service.sync(workspace_id="w", stream_id="stream-a", adapter=second)
    assert second.snapshots == [snapshot]


def test_email_sync_does_not_commit_snapshot_after_ingest_failure(pipeline, tmp_path: Path) -> None:
    class FailingRuntime:
        authorize_stream = staticmethod(lambda _workspace, _stream: True)

        def ingest(self, _request: EmailIngestRequest) -> None:
            raise ValueError("parser failure")

    old = Snapshot("stream-a", "rfc822", ("old",), {})
    new = Snapshot("stream-a", "rfc822", ("old", "new"), {})
    state = SQLiteEmailSyncStateStore(tmp_path / "sync.sqlite3", snapshot_type=Snapshot)
    state.put(workspace_id="w", stream_id="stream-a", snapshot=old)
    service = EmailSyncService(
        runtime=FailingRuntime(),
        state_store=state,
    )
    with pytest.raises(ValueError, match="parser failure"):
        service.sync(
            workspace_id="w",
            stream_id="stream-a",
            adapter=Adapter(Batch((Message("new", RAW_EMAIL, "new-revision"),), (), new)),
        )
    assert state.get(workspace_id="w", stream_id="stream-a") == old
