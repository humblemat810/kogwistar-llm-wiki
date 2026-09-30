from __future__ import annotations

from datetime import UTC, datetime

import pytest

from kogwistar_llm_wiki.app_contracts.notification_digest import NotificationEvent
from kogwistar_llm_wiki.app_contracts.notification_producer import (
    NotificationSchedule,
    NotificationWindowProducer,
)
from kogwistar_llm_wiki.app_contracts.notification_sources import (
    NotificationSourceCollection,
)
from kogwistar_llm_wiki.cli import server_commands

pytestmark = pytest.mark.ci


class FakeSource:
    def __init__(self, source_ids, events=()):
        self.source_ids = tuple(source_ids)
        self.events = tuple(events)
        self.reads = []

    def list_sources(self, _workspace, _recipient):
        return self.source_ids

    def read_window(self, workspace, _recipient, source_ids, _start, _end, _limit):
        self.reads.append(tuple(source_ids))
        return tuple(event for event in self.events if event.source_id in source_ids)


def _event(event_id, source_id):
    return NotificationEvent(
        event_id=event_id,
        workspace_id="workspace-a",
        source_id=source_id,
        source_revision_id=f"revision:{event_id}",
        occurred_at=datetime(2026, 9, 30, tzinfo=UTC),
        title=f"Event {event_id}",
    )


def _collection(adapters, allowed):
    return NotificationSourceCollection(
        adapters,
        authorize_source=lambda workspace, _recipient, source: (
            workspace == "workspace-a" and source in allowed
        ),
    )


def test_multiple_message_channels_compose_with_stable_source_ownership():
    feed = FakeSource(("source:inbox",), (_event("source-1", "source:inbox"),))
    chat = FakeSource(("chat:team",), (_event("chat-1", "chat:team"),))
    collection = _collection({"feed": feed, "chat": chat}, {"source:inbox", "chat:team"})

    sources = collection.list_sources("workspace-a", "user-a")
    events = collection.read_window(
        "workspace-a",
        "user-a",
        sources,
        datetime(2026, 9, 30, tzinfo=UTC),
        datetime(2026, 10, 1, tzinfo=UTC),
        10,
    )

    assert sources == ("chat:team", "source:inbox")
    assert {event.event_id for event in events} == {"source-1", "chat-1"}
    assert feed.reads == [("source:inbox",)]
    assert chat.reads == [("chat:team",)]


def test_denied_source_fails_before_any_channel_event_read():
    feed = FakeSource(("source:inbox",), (_event("source-1", "source:inbox"),))
    chat = FakeSource(("chat:private",), (_event("chat-1", "chat:private"),))
    collection = _collection({"feed": feed, "chat": chat}, {"source:inbox"})

    with pytest.raises(PermissionError, match="not authorized"):
        collection.list_sources("workspace-a", "user-a")
    assert feed.reads == []
    assert chat.reads == []


def test_acl_revocation_after_enumeration_blocks_window_read():
    allowed = {"source:inbox"}
    source = FakeSource(("source:inbox",), (_event("source-1", "source:inbox"),))
    collection = _collection({"feed": source}, allowed)
    sources = collection.list_sources("workspace-a", "user-a")
    allowed.clear()

    with pytest.raises(PermissionError, match="not authorized"):
        collection.read_window(
            "workspace-a",
            "user-a",
            sources,
            datetime(2026, 9, 30, tzinfo=UTC),
            datetime(2026, 10, 1, tzinfo=UTC),
            10,
        )
    assert source.reads == []


def test_source_ownership_collision_is_rejected():
    first = FakeSource(("shared:id",))
    second = FakeSource(("shared:id",))
    collection = _collection({"feed": first, "chat": second}, {"shared:id"})

    with pytest.raises(ValueError, match="unique across adapters"):
        collection.list_sources("workspace-a", "user-a")


def test_changed_source_set_between_listing_and_read_fails_closed():
    source = FakeSource(("source:inbox",))
    collection = _collection({"feed": source}, {"source:inbox", "source:added"})
    sources = collection.list_sources("workspace-a", "user-a")
    source.source_ids = ("source:inbox", "source:added")

    with pytest.raises(RuntimeError, match="source set changed"):
        collection.read_window(
            "workspace-a",
            "user-a",
            sources,
            datetime(2026, 9, 30, tzinfo=UTC),
            datetime(2026, 10, 1, tzinfo=UTC),
            10,
        )
    assert source.reads == []


def test_event_evidence_source_must_have_its_own_acl_grant():
    class OutOfScopeSource(FakeSource):
        def read_window(self, *_args):
            self.reads.append(("source:inbox",))
            return (_event("other", "chat:team"),)

    source = OutOfScopeSource(("source:inbox",))
    collection = _collection({"feed": source}, {"source:inbox"})
    sources = collection.list_sources("workspace-a", "user-a")

    with pytest.raises(PermissionError, match="evidence source is not authorized"):
        collection.read_window(
            "workspace-a",
            "user-a",
            sources,
            datetime(2026, 9, 30, tzinfo=UTC),
            datetime(2026, 10, 1, tzinfo=UTC),
            10,
        )


def test_cli_notification_plugins_load_only_explicit_allowlist(monkeypatch):
    loaded = []
    authorizer = lambda *_args: True
    adapter = FakeSource(("chat:team",))

    class EntryPoint:
        def __init__(self, name, factory):
            self.name = name
            self.factory = factory

        def load(self):
            loaded.append(self.name)
            return self.factory

    def factory(*, data_dir, authorize_source):
        assert data_dir == "data-root"
        assert authorize_source is authorizer
        return adapter

    monkeypatch.setattr(
        server_commands,
        "entry_points",
        lambda *, group: (
            EntryPoint("chat", factory),
            EntryPoint("calendar", lambda **_kwargs: pytest.fail("unselected adapter loaded")),
        ),
    )

    result = server_commands._load_notification_source_plugins(
        "data-root", "chat", authorizer
    )

    assert result == {"chat": adapter}
    assert loaded == ["chat"]


def test_cli_notification_plugin_discovery_fails_closed(monkeypatch):
    monkeypatch.setattr(server_commands, "entry_points", lambda **_kwargs: ())
    with pytest.raises(ValueError, match="not installed"):
        server_commands._load_notification_source_plugins("data", "unknown", lambda *_: True)
    with pytest.raises(ValueError, match="reserved"):
        server_commands._load_notification_source_plugins("data", "feed", lambda *_: True)
    with pytest.raises(ValueError, match="must not contain duplicates"):
        server_commands._load_notification_source_plugins("data", "chat,chat", lambda *_: True)


def test_cross_channel_events_flow_through_standard_digest_and_cursor_contract():
    start = datetime(2026, 9, 30, 9, tzinfo=UTC)
    feed = FakeSource(("source:inbox",), (_event("source-1", "source:inbox"),))
    chat_event = NotificationEvent(
        event_id="chat-1",
        workspace_id="workspace-a",
        source_id="chat:team",
        source_revision_id="revision:chat-1",
        occurred_at=datetime(2026, 9, 30, 9, 30, tzinfo=UTC),
        title="Build succeeded",
        dedupe_key="project:build:42",
    )
    feed.events = (
        NotificationEvent(
            event_id="source-1",
            workspace_id="workspace-a",
            source_id="source:inbox",
            source_revision_id="revision:source-1",
            occurred_at=datetime(2026, 9, 30, 9, 20, tzinfo=UTC),
            title="Build started",
            dedupe_key="project:build:42",
        ),
    )
    chat = FakeSource(("chat:team",), (chat_event,))
    collection = _collection({"feed": feed, "chat": chat}, {"source:inbox", "chat:team"})

    class CursorStore:
        value = None

        def get_cursor(self, **_kwargs):
            return self.value

        def compare_and_set_cursor(self, *, expected, value, **_kwargs):
            if self.value != expected:
                return False
            self.value = value
            return True

    class Scheduler:
        def __init__(self):
            self.digests = []

        def enqueue_digest(self, *, digest, **_kwargs):
            self.digests.append(digest)
            return ("delivery:one",)

    cursor = CursorStore()
    scheduler = Scheduler()
    producer = NotificationWindowProducer(
        scheduler=scheduler,
        cursor_store=cursor,
        list_sources=collection.list_sources,
        read_window=collection.read_window,
        authorize_recipient=lambda *_args: True,
        authorize_source=lambda workspace, _recipient, source: (
            workspace == "workspace-a" and source in {"source:inbox", "chat:team"}
        ),
    )

    result = producer.produce_once(
        NotificationSchedule(
            workspace_id="workspace-a",
            recipient_id="user-a",
            start_at=start,
            finalize_delay_seconds=0,
        ),
        now=datetime(2026, 9, 30, 10, tzinfo=UTC),
    )

    assert result.events_considered == 2
    assert result.delivery_job_ids == ("delivery:one",)
    assert cursor.value == datetime(2026, 9, 30, 10, tzinfo=UTC)
    assert len(scheduler.digests) == 1
    digest_item = scheduler.digests[0].routine[0]
    assert set(digest_item.source_event_ids) == {"source-1", "chat-1"}
    assert {reference[0] for reference in digest_item.evidence_refs} == {
        "source:inbox",
        "chat:team",
    }
