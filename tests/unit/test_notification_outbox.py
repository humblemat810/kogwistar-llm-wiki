from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from kogwistar_llm_wiki.app_contracts.notification_delivery import _serialize_digest
from kogwistar_llm_wiki.app_contracts.notification_digest import (
    NotificationEvent,
    build_notification_digest,
)
from kogwistar_llm_wiki.app_contracts.notification_outbox import (
    NotificationOutboxReader,
    _bounded_text,
)
from kogwistar_llm_wiki.configuration.workspace import WorkspaceNamespaces

pytestmark = pytest.mark.ci


@dataclass(frozen=True)
class FakeCursor:
    created_at_us: int
    job_id: str
    filter_fingerprint: str


class FakeQueue:
    def __init__(self, items, next_cursor=None) -> None:
        self.items = tuple(items)
        self.next_cursor = next_cursor
        self.calls = []

    def list_page(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(items=self.items, next_cursor=self.next_cursor)


def _job(*, workspace_id="ws-a", recipient_id="user-a", source_id="mail-a"):
    start = datetime(2026, 9, 20, tzinfo=UTC)
    digest = build_notification_digest(
        [
            NotificationEvent(
                event_id="event-1",
                workspace_id=workspace_id,
                source_id=source_id,
                source_revision_id="revision-1",
                occurred_at=start + timedelta(seconds=1),
                title="Quarterly report arrived",
            )
        ],
        workspace_id=workspace_id,
        window_start=start,
        window_end=start + timedelta(minutes=1),
        authorize_source=lambda *_: True,
    )
    delivery_id = "delivery-1"
    namespace = WorkspaceNamespaces(workspace_id).notification_jobs
    return SimpleNamespace(
        job_id=delivery_id,
        namespace=namespace,
        entity_kind="notification_delivery",
        entity_id=delivery_id,
        job_kind="notification_delivery",
        op="DELIVER",
        payload={
            "job_type": "notification_delivery",
            "workspace_id": workspace_id,
            "recipient_id": recipient_id,
            "delivery_class": "routine",
            "digest": _serialize_digest(digest),
        },
        retry_count=2,
        max_retries=5,
        last_error="smtp failed for secret@example.com",
        claim_attempts=3,
        status="PENDING",
        created_at_ms=1_800_000_000_000,
        updated_at_ms=1_800_000_001_000,
    )


def _reader(queue, *, recipient_acl=None, source_acl=None):
    engines = SimpleNamespace(conversation=SimpleNamespace(jobs=queue))
    return NotificationOutboxReader(
        engines=engines,
        authorize_recipient=recipient_acl or (lambda *_: True),
        authorize_source=source_acl or (lambda *_: True),
    )


def test_outbox_returns_compact_status_and_never_raw_job_secrets():
    queue = FakeQueue([_job()])
    page = _reader(queue).list_page(
        workspace_id="ws-a",
        principal_id="user-a",
        delivery_class="routine",
    )

    assert len(page.items) == 1
    item = page.items[0]
    assert item.status == "PENDING"
    assert item.retry_count == 2 and item.max_retries == 5
    assert item.claim_attempts == 3
    assert item.failure_reported is True
    assert item.logical_event_count == 1
    assert item.evidence_ref_count == 1
    rendered = repr(item)
    assert "secret@example.com" not in rendered
    assert "user-a" not in rendered
    assert "mail-a" not in rendered


def test_outbox_keeps_action_and_severity_attached_to_each_digest_item():
    job = _job()
    start = datetime(2026, 9, 20, tzinfo=UTC)
    digest = build_notification_digest(
        [
            NotificationEvent(
                event_id="event-routine",
                workspace_id="ws-a",
                source_id="mail-a",
                source_revision_id="revision-routine",
                occurred_at=start + timedelta(seconds=1),
                title="Routine receipt",
                severity="normal",
            ),
            NotificationEvent(
                event_id="event-action",
                workspace_id="ws-a",
                source_id="mail-a",
                source_revision_id="revision-action",
                occurred_at=start + timedelta(seconds=2),
                title="Reply requested",
                severity="normal",
                action_required=True,
            ),
        ],
        workspace_id="ws-a",
        window_start=start,
        window_end=start + timedelta(minutes=1),
        authorize_source=lambda *_: True,
    )
    job.payload["digest"] = _serialize_digest(digest)

    page = _reader(FakeQueue([job])).list_page(
        workspace_id="ws-a",
        principal_id="user-a",
        delivery_class="routine",
    )

    items = page.items[0].digest_items
    assert {item.summary: item.action_required for item in items} == {
        "Routine receipt": False,
        "Reply requested": True,
    }


@pytest.mark.parametrize("unknown_status", ["UNRECOGNIZED", ["malformed"]])
def test_outbox_bounds_long_summary_and_unknown_status(unknown_status):
    job = _job()
    job.payload["digest"]["routine"][0]["summary"] = "word " * 150
    job.status = unknown_status
    page = _reader(FakeQueue([job])).list_page(
        workspace_id="ws-a",
        principal_id="user-a",
        delivery_class="routine",
    )

    item = page.items[0]
    assert item.status == "UNKNOWN"
    assert len(item.summaries[0]) <= 360
    assert item.summaries[0].endswith("...")


def test_outbox_summary_truncation_is_effective():
    assert _bounded_text("x" * 20, 10) == "xxxxxxx..."


def test_legacy_queued_digest_suppresses_directional_overrides_without_mutation():
    job = _job()
    raw_summary = "Invoice \u202eexe.scr"
    job.payload["digest"]["routine"][0]["summary"] = raw_summary

    page = _reader(FakeQueue([job])).list_page(
        workspace_id="ws-a",
        principal_id="user-a",
        delivery_class="routine",
    )

    item = page.items[0]
    assert item.summaries == ("Invoice exe.scr",)
    assert item.digest_items[0].summary == "Invoice exe.scr"
    assert job.payload["digest"]["routine"][0]["summary"] == raw_summary


def test_outbox_rejects_recipient_before_query_and_rechecks_source_acl():
    queue = FakeQueue([_job()])
    reader = _reader(queue, recipient_acl=lambda *_: False)
    with pytest.raises(PermissionError, match="recipient"):
        reader.list_page(
            workspace_id="ws-a",
            principal_id="user-a",
            delivery_class="routine",
        )
    assert queue.calls == []

    authorized_sources = []

    def authorize_source(workspace, principal, source):
        authorized_sources.append((workspace, principal, source))
        return source != "mail-a"

    reader = _reader(queue, source_acl=authorize_source)
    page = reader.list_page(
        workspace_id="ws-a",
        principal_id="user-a",
        delivery_class="routine",
    )
    assert page.items == ()
    assert authorized_sources == [("ws-a", "user-a", "mail-a")]


def test_outbox_hides_another_principals_delivery():
    page = _reader(FakeQueue([_job(recipient_id="user-b")])).list_page(
        workspace_id="ws-a",
        principal_id="user-a",
        delivery_class="routine",
    )
    assert page.items == ()


def test_outbox_source_acl_is_principal_scoped_within_workspace():
    reader = _reader(
        FakeQueue([_job()]),
        source_acl=lambda _workspace, principal, _source: principal == "user-b",
    )

    page = reader.list_page(
        workspace_id="ws-a",
        principal_id="user-a",
        delivery_class="routine",
    )

    assert page.items == ()


def test_outbox_rejects_delivery_class_namespace_payload_mismatch():
    job = _job()
    job.namespace = WorkspaceNamespaces("ws-a").urgent_notification_jobs
    page = _reader(FakeQueue([job])).list_page(
        workspace_id="ws-a",
        principal_id="user-a",
        delivery_class="urgent",
    )

    assert page.items == ()


def test_outbox_cursor_is_bound_to_workspace_principal_class_and_status(monkeypatch):
    import kogwistar.engine_core.jobs as jobs_module

    monkeypatch.setattr(jobs_module, "JobQueueCursor", FakeCursor, raising=False)
    queue = FakeQueue(
        [_job()],
        FakeCursor(
            created_at_us=1_800_000_000_123_456,
            job_id="delivery-1",
            filter_fingerprint="filter-fingerprint",
        ),
    )
    reader = _reader(queue)
    first = reader.list_page(
        workspace_id="ws-a",
        principal_id="user-a",
        delivery_class="routine",
        status="PENDING",
    )
    assert first.next_cursor

    queue.next_cursor = None
    second = reader.list_page(
        workspace_id="ws-a",
        principal_id="user-a",
        delivery_class="routine",
        status="PENDING",
        cursor=first.next_cursor,
    )
    assert isinstance(queue.calls[-1]["cursor"], FakeCursor)
    assert queue.calls[-1]["cursor"].job_id == "delivery-1"
    assert second.next_cursor is None

    for changes in (
        {"workspace_id": "ws-b"},
        {"principal_id": "user-b"},
        {"delivery_class": "urgent"},
        {"status": "DONE"},
    ):
        with pytest.raises(ValueError, match="does not match request"):
            reader.list_page(
                workspace_id=changes.get("workspace_id", "ws-a"),
                principal_id=changes.get("principal_id", "user-a"),
                delivery_class=changes.get("delivery_class", "routine"),
                status=changes.get("status", "PENDING"),
                cursor=first.next_cursor,
            )


def test_outbox_refuses_old_core_without_ordered_history():
    reader = _reader(SimpleNamespace())
    with pytest.raises(RuntimeError, match="ordered job history"):
        reader.list_page(
            workspace_id="ws-a",
            principal_id="user-a",
            delivery_class="routine",
        )


@pytest.mark.parametrize("cursor", ["%%%", "AA==!", "////"])
def test_outbox_rejects_malformed_cursor_without_query(cursor):
    queue = FakeQueue([])
    with pytest.raises(ValueError, match="cursor is invalid"):
        _reader(queue).list_page(
            workspace_id="ws-a",
            principal_id="user-a",
            delivery_class="routine",
            cursor=cursor,
        )
    assert queue.calls == []


@pytest.mark.parametrize("limit", [0, 101, True, 1.5])
def test_outbox_validates_page_size(limit):
    with pytest.raises(ValueError, match="limit"):
        _reader(FakeQueue([])).list_page(
            workspace_id="ws-a",
            principal_id="user-a",
            delivery_class="routine",
            limit=limit,
        )
