from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from kogwistar_llm_wiki.email import (
    EmailConnectorBinding,
    EmailIngestRequest,
    EmailRuntime,
    EmailSyncService,
    InMemoryEmailConnectorBindingStore,
    InMemoryEmailEvidenceStore,
    InMemoryEmailMailboxEventStore,
    InMemoryEmailSyncLeaseStore,
    InMemoryEmailSyncStateStore,
    SQLiteEmailSyncStateStore,
)

EMAIL_PLUGIN_SRC = Path(__file__).parents[2] / "kogwistar-email-plugin" / "src"
if str(EMAIL_PLUGIN_SRC) not in sys.path:
    sys.path.insert(0, str(EMAIL_PLUGIN_SRC))

pytestmark = pytest.mark.requires_email_plugin
pytest.importorskip("kogwistar_email_plugin")


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


def test_email_sync_persists_idempotent_mailbox_events(pipeline) -> None:
    runtime = EmailRuntime(pipeline=pipeline, store=InMemoryEmailEvidenceStore())
    events = InMemoryEmailMailboxEventStore()
    snapshot = Snapshot("stream-a", "rfc822", ("uid:1",), {})
    service = EmailSyncService(
        runtime=runtime,
        event_store=events,
    )
    batch = Batch(
        messages=(Message("uid:1", RAW_EMAIL, "revision-1"),),
        events=(Event("message_discovered"),),
        snapshot=snapshot,
    )

    service.sync(workspace_id="w", stream_id="stream-a", adapter=Adapter(batch))
    service.sync(workspace_id="w", stream_id="stream-a", adapter=Adapter(batch))

    stored = events.list(workspace_id="w", stream_id="stream-a")
    assert len(stored) == 1
    assert stored[0].kind == "message_discovered"
    assert stored[0].event_id.startswith("email-event:")


def test_email_sync_does_not_advance_snapshot_when_event_persistence_fails(pipeline) -> None:
    class FailingEventStore:
        def append(self, _events) -> None:
            raise RuntimeError("event persistence failure")

    old = Snapshot("stream-a", "rfc822", ("uid:old",), {})
    new = Snapshot("stream-a", "rfc822", ("uid:new",), {})
    state = InMemoryEmailSyncStateStore()
    state.put(workspace_id="w", stream_id="stream-a", snapshot=old)
    runtime = EmailRuntime(pipeline=pipeline, store=InMemoryEmailEvidenceStore())
    service = EmailSyncService(
        runtime=runtime,
        state_store=state,
        event_store=FailingEventStore(),
    )

    with pytest.raises(RuntimeError, match="event persistence failure"):
        service.sync(
            workspace_id="w",
            stream_id="stream-a",
            adapter=Adapter(
                Batch(
                    messages=(Message("uid:new", RAW_EMAIL, "new-revision"),),
                    events=(Event("message_discovered"),),
                    snapshot=new,
                )
            ),
        )

    assert state.get(workspace_id="w", stream_id="stream-a") == old


def test_email_sync_binding_authorizes_before_factory_and_releases_lease() -> None:
    class Runtime:
        authorize_stream = staticmethod(lambda _workspace, _stream: True)

        def __init__(self) -> None:
            self.requests: list[EmailIngestRequest] = []

        def ingest(self, request: EmailIngestRequest) -> None:
            self.requests.append(request)

    runtime = Runtime()
    binding_store = InMemoryEmailConnectorBindingStore()
    binding = binding_store.put(
        EmailConnectorBinding(
            tenant_id="tenant-a",
            workspace_id="w",
            connector_id="connector-a",
            account_principal_id="account-a",
            mailbox_id="mailbox-a",
            credential_ref="secret/email/connector-a",
        )
    )
    leases = InMemoryEmailSyncLeaseStore()
    state = InMemoryEmailSyncStateStore()
    service = EmailSyncService(
        runtime=runtime,
        state_store=state,
        binding_store=binding_store,
        lease_store=leases,
        clock_ms=lambda: 100,
    )
    credentials: list[str] = []

    result = service.sync_binding(
        workspace_id="w",
        connector_id="connector-a",
        owner_id="worker-a",
        adapter_factory=lambda stored, reference: (
            credentials.append(reference) or Adapter(
                Batch((Message("uid:1", RAW_EMAIL, "revision-1"),), (), Snapshot(
                    stored.stream_id, "rfc822", ("uid:1",), {},
                ))
            )
        ),
    )

    assert result.snapshot_committed is True
    assert credentials == [binding.credential_ref]
    assert len(runtime.requests) == 1
    # The finally block released the lease, so another owner can claim it.
    replacement = leases.claim(
        workspace_id="w",
        connector_id="connector-a",
        stream_id=binding.stream_id,
        owner_id="worker-b",
        now_ms=100,
        lease_duration_ms=100,
    )
    assert replacement.owner_id == "worker-b"


def test_email_sync_binding_does_not_call_factory_for_unauthorized_stream() -> None:
    class Runtime:
        authorize_stream = staticmethod(lambda _workspace, _stream: False)

        def ingest(self, _request: EmailIngestRequest) -> None:
            raise AssertionError("ingest must not run")

    binding_store = InMemoryEmailConnectorBindingStore()
    binding_store.put(
        EmailConnectorBinding(
            tenant_id="tenant-a",
            workspace_id="w",
            connector_id="connector-a",
            account_principal_id="account-a",
            mailbox_id="mailbox-a",
            credential_ref="secret/email/connector-a",
        )
    )
    service = EmailSyncService(
        runtime=Runtime(),
        binding_store=binding_store,
        lease_store=InMemoryEmailSyncLeaseStore(),
    )
    with pytest.raises(PermissionError, match="not authorized"):
        service.sync_binding(
            workspace_id="w",
            connector_id="connector-a",
            owner_id="worker-a",
            adapter_factory=lambda _binding, _reference: pytest.fail("factory called"),
        )


def test_email_sync_rejects_adapter_message_from_another_stream(pipeline) -> None:
    class CrossStreamAdapter:
        def sync(self, snapshot=None):
            return type(
                "Batch",
                (),
                {
                    "messages": (
                        {
                            "stream_id": "other-stream",
                            "source_key": "uid:1",
                            "raw_bytes": b"raw",
                            "source_revision_id": "revision-1",
                        },
                    ),
                    "events": (),
                    "snapshot": {"stream_id": "stream-a"},
                    "has_more": False,
                },
            )()

    service = EmailSyncService(
        runtime=EmailRuntime(pipeline=pipeline, plugin=object()),
        authorize_stream=lambda _workspace, _stream: True,
    )
    with pytest.raises(PermissionError, match="another stream"):
        service.sync(
            workspace_id="w",
            stream_id="stream-a",
            adapter=CrossStreamAdapter(),
        )
