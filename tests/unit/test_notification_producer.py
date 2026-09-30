from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kogwistar_llm_wiki.app_contracts.notification_digest import NotificationEvent
from kogwistar_llm_wiki.app_contracts.notification_producer import (
    NotificationSchedule,
    NotificationScheduleConflict,
    NotificationWindowProducer,
    SQLiteNotificationCursorStore,
    SQLiteNotificationScheduleStore,
)

pytestmark = pytest.mark.ci


class _CursorStore:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], datetime] = {}
        self.reject_next_advance = False

    def get_cursor(self, *, workspace_id: str, recipient_id: str) -> datetime | None:
        return self.values.get((workspace_id, recipient_id))

    def compare_and_set_cursor(
        self,
        *,
        workspace_id: str,
        recipient_id: str,
        expected: datetime | None,
        value: datetime,
        schedule_revision: int | None = None,
    ) -> bool:
        key = (workspace_id, recipient_id)
        if self.reject_next_advance:
            self.reject_next_advance = False
            return False
        if self.values.get(key) != expected:
            return False
        self.values[key] = value
        return True


class _Scheduler:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.digests = []

    def enqueue_digest(self, **kwargs):
        if self.fail:
            raise RuntimeError("queue unavailable")
        self.digests.append(kwargs["digest"])
        return (f"job:{len(self.digests)}",)


def _event(event_id: str, occurred_at: datetime) -> NotificationEvent:
    return NotificationEvent(
        event_id=event_id,
        workspace_id="workspace-a",
        source_id="mail:inbox",
        source_revision_id=f"revision:{event_id}",
        occurred_at=occurred_at,
        title=event_id,
    )


def _producer(*, source_ids=("mail:inbox",), read_window=None, scheduler=None, cursors=None):
    return NotificationWindowProducer(
        scheduler=scheduler or _Scheduler(),
        cursor_store=cursors or _CursorStore(),
        list_sources=lambda _workspace, _recipient: source_ids,
        read_window=read_window or (lambda *_args: ()),
        authorize_recipient=lambda _workspace, _recipient: True,
        authorize_source=lambda _workspace, _recipient, source: source in source_ids,
    )


def test_producer_enqueues_each_nonempty_closed_window_then_advances_cursor() -> None:
    start = datetime(2026, 9, 1, tzinfo=UTC)
    scheduler = _Scheduler()
    cursors = _CursorStore()
    reads = []

    def read_window(workspace, recipient, sources, window_start, window_end, max_events):
        reads.append((workspace, recipient, sources, window_start, window_end, max_events))
        if window_start == start:
            return (_event("event-1", start + timedelta(minutes=10)),)
        return ()

    producer = _producer(read_window=read_window, scheduler=scheduler, cursors=cursors)
    result = producer.produce_once(
        NotificationSchedule(
            workspace_id="workspace-a",
            recipient_id="user-a",
            start_at=start,
            cadence_seconds=3600,
            finalize_delay_seconds=0,
        ),
        now=start + timedelta(hours=3),
    )

    assert result.windows_processed == 3
    assert result.events_considered == 1
    assert result.delivery_job_ids == ("job:1",)
    assert result.cursor == start + timedelta(hours=3)
    assert cursors.get_cursor(workspace_id="workspace-a", recipient_id="user-a") == result.cursor
    assert len(reads) == 3
    assert len(scheduler.digests) == 1


def test_queue_failure_does_not_advance_window_cursor() -> None:
    start = datetime(2026, 9, 1, tzinfo=UTC)
    cursors = _CursorStore()
    producer = _producer(
        scheduler=_Scheduler(fail=True),
        cursors=cursors,
        read_window=lambda *_args: (_event("event-1", start + timedelta(minutes=10)),),
    )

    with pytest.raises(RuntimeError, match="queue unavailable"):
        producer.produce_once(
            NotificationSchedule(
                workspace_id="workspace-a",
                recipient_id="user-a",
                start_at=start,
                cadence_seconds=3600,
                finalize_delay_seconds=0,
            ),
            now=start + timedelta(hours=1),
        )

    assert cursors.get_cursor(workspace_id="workspace-a", recipient_id="user-a") is None


def test_empty_window_advances_cursor_without_enqueuing() -> None:
    start = datetime(2026, 9, 1, tzinfo=UTC)
    scheduler = _Scheduler()
    cursors = _CursorStore()
    result = _producer(scheduler=scheduler, cursors=cursors).produce_once(
        NotificationSchedule(
            workspace_id="workspace-a",
            recipient_id="user-a",
            start_at=start,
            finalize_delay_seconds=0,
        ),
        now=start + timedelta(hours=1),
    )

    assert result.windows_processed == 1
    assert result.delivery_job_ids == ()
    assert scheduler.digests == []
    assert result.cursor == start + timedelta(hours=1)


def test_source_acl_is_checked_before_event_window_read() -> None:
    start = datetime(2026, 9, 1, tzinfo=UTC)
    reads = []
    acl_checks = []
    producer = NotificationWindowProducer(
        scheduler=_Scheduler(),
        cursor_store=_CursorStore(),
        list_sources=lambda *_: ("mail:private",),
        read_window=lambda *args: reads.append(args) or (),
        authorize_recipient=lambda *_: True,
        authorize_source=lambda workspace, recipient, source: (
            acl_checks.append((workspace, recipient, source)) is None and False
        ),
    )

    with pytest.raises(PermissionError, match="source is not authorized"):
        producer.produce_once(
            NotificationSchedule(
                workspace_id="workspace-a",
                recipient_id="user-a",
                start_at=start,
                finalize_delay_seconds=0,
            ),
            now=start + timedelta(hours=1),
        )

    assert reads == []
    assert acl_checks == [("workspace-a", "user-a", "mail:private")]


def test_document_acl_denial_keeps_notification_window_cursor_unadvanced() -> None:
    start = datetime(2026, 9, 1, tzinfo=UTC)
    cursors = _CursorStore()
    scheduler = _Scheduler()
    producer = NotificationWindowProducer(
        scheduler=scheduler,
        cursor_store=cursors,
        list_sources=lambda *_: ("mail-stream",),
        read_window=lambda *_: (
            NotificationEvent(
                event_id="event-1",
                workspace_id="workspace-a",
                source_id="private-document",
                source_revision_id="revision-1",
                occurred_at=start + timedelta(minutes=1),
                title="Private message",
            ),
        ),
        authorize_recipient=lambda *_: True,
        authorize_source=lambda _workspace, _recipient, source: source == "mail-stream",
    )

    with pytest.raises(PermissionError, match="source is not authorized"):
        producer.produce_once(
            NotificationSchedule(
                workspace_id="workspace-a",
                recipient_id="user-a",
                start_at=start,
                finalize_delay_seconds=0,
            ),
            now=start + timedelta(hours=1),
        )

    assert scheduler.digests == []
    assert cursors.get_cursor(workspace_id="workspace-a", recipient_id="user-a") is None


def test_source_enumeration_is_bounded() -> None:
    start = datetime(2026, 9, 1, tzinfo=UTC)
    reads = []
    producer = NotificationWindowProducer(
        scheduler=_Scheduler(),
        cursor_store=_CursorStore(),
        list_sources=lambda *_: (f"source:{index}" for index in range(10_000)),
        read_window=lambda *args: reads.append(args) or (),
        authorize_recipient=lambda *_: True,
        authorize_source=lambda *_: True,
    )

    with pytest.raises(ValueError, match="source set exceeds"):
        producer.produce_once(
            NotificationSchedule(
                workspace_id="workspace-a",
                recipient_id="user-a",
                start_at=start,
                max_sources=2,
                finalize_delay_seconds=0,
            ),
            now=start + timedelta(hours=1),
        )

    assert reads == []


def test_cursor_race_after_enqueue_fails_without_claiming_window_complete() -> None:
    start = datetime(2026, 9, 1, tzinfo=UTC)
    scheduler = _Scheduler()
    cursors = _CursorStore()
    cursors.reject_next_advance = True
    producer = _producer(
        scheduler=scheduler,
        cursors=cursors,
        read_window=lambda *_args: (_event("event-1", start + timedelta(minutes=10)),),
    )

    with pytest.raises(RuntimeError, match="cursor changed concurrently"):
        producer.produce_once(
            NotificationSchedule(
                workspace_id="workspace-a",
                recipient_id="user-a",
                start_at=start,
                finalize_delay_seconds=0,
            ),
            now=start + timedelta(hours=1),
        )

    assert len(scheduler.digests) == 1
    assert cursors.get_cursor(workspace_id="workspace-a", recipient_id="user-a") is None


def test_disabled_schedule_does_not_read_sources_or_advance_cursor() -> None:
    start = datetime(2026, 9, 1, tzinfo=UTC)
    reads = []
    result = _producer(read_window=lambda *args: reads.append(args) or ()).produce_once(
        NotificationSchedule(
            workspace_id="workspace-a",
            recipient_id="user-a",
            start_at=start,
            enabled=False,
        ),
        now=start + timedelta(days=1),
    )

    assert result.windows_processed == 0
    assert result.cursor is None
    assert reads == []


def test_sqlite_cursor_survives_reopen_and_compare_and_set_is_fenced(tmp_path) -> None:
    path = tmp_path / "notifications.db"
    start = datetime(2026, 9, 1, tzinfo=UTC)
    first_end = start + timedelta(hours=1)
    store = SQLiteNotificationCursorStore(path)

    assert store.get_cursor(workspace_id="workspace-a", recipient_id="user-a") is None
    assert store.compare_and_set_cursor(
        workspace_id="workspace-a",
        recipient_id="user-a",
        expected=None,
        value=first_end,
    )
    assert not store.compare_and_set_cursor(
        workspace_id="workspace-a",
        recipient_id="user-a",
        expected=None,
        value=first_end + timedelta(hours=1),
    )

    reopened = SQLiteNotificationCursorStore(path)
    assert reopened.get_cursor(workspace_id="workspace-a", recipient_id="user-a") == first_end
    assert reopened.compare_and_set_cursor(
        workspace_id="workspace-a",
        recipient_id="user-a",
        expected=first_end,
        value=first_end + timedelta(hours=1),
    )
    with pytest.raises(ValueError, match="advance monotonically"):
        reopened.compare_and_set_cursor(
            workspace_id="workspace-a",
            recipient_id="user-a",
            expected=first_end + timedelta(hours=1),
            value=first_end,
        )


def test_sqlite_schedule_preferences_are_durable_versioned_and_acl_scoped(tmp_path) -> None:
    path = tmp_path / "notification-preferences.db"
    start = datetime(2026, 9, 1, tzinfo=UTC)
    store = SQLiteNotificationScheduleStore(path)
    configured = store.save_schedule(
        NotificationSchedule(
            workspace_id="workspace-a",
            recipient_id="user-a",
            start_at=start,
            enabled=False,
            cadence_seconds=7200,
            finalize_delay_seconds=300,
        ),
        expected_revision=None,
    )
    second = store.save_schedule(
        NotificationSchedule(
            workspace_id="workspace-a",
            recipient_id="user-b",
            start_at=start,
            enabled=True,
        ),
        expected_revision=None,
    )

    assert configured.revision == 1
    assert store.get_schedule(
        workspace_id="workspace-a", recipient_id="user-a"
    ) == configured
    assert store.list_schedules(workspace_id="workspace-a", limit=1) == (configured,)
    assert store.list_schedules(
        workspace_id="workspace-a", after_recipient_id="user-a"
    ) == (second,)
    assert store.list_schedules(workspace_id="workspace-b") == ()

    reopened = SQLiteNotificationScheduleStore(path)
    assert reopened.get_schedule(
        workspace_id="workspace-a", recipient_id="user-a"
    ) == configured
    updated = reopened.save_schedule(
        NotificationSchedule(
            workspace_id="workspace-a",
            recipient_id="user-a",
            start_at=start,
            enabled=True,
            cadence_seconds=7200,
            finalize_delay_seconds=300,
        ),
        expected_revision=1,
    )
    assert updated.enabled is True
    assert updated.revision == 2

    with pytest.raises(NotificationScheduleConflict, match="revision changed"):
        reopened.save_schedule(updated, expected_revision=1)


def test_schedule_timing_change_requires_explicit_cursor_reset_and_fences_old_worker(
    tmp_path,
) -> None:
    path = tmp_path / "notification-schedule-reset.db"
    start = datetime(2026, 9, 1, tzinfo=UTC)
    store = SQLiteNotificationScheduleStore(path)
    original = store.save_schedule(
        NotificationSchedule(
            workspace_id="workspace-a",
            recipient_id="user-a",
            start_at=start,
            finalize_delay_seconds=0,
        ),
        expected_revision=None,
    )
    end = start + timedelta(hours=1)
    assert store.compare_and_set_cursor(
        workspace_id="workspace-a",
        recipient_id="user-a",
        expected=None,
        value=end,
        schedule_revision=original.revision,
    )

    changed_timing = NotificationSchedule(
        workspace_id="workspace-a",
        recipient_id="user-a",
        start_at=start,
        cadence_seconds=7200,
        finalize_delay_seconds=0,
    )
    with pytest.raises(NotificationScheduleConflict, match="explicit cursor reset"):
        store.save_schedule(changed_timing, expected_revision=original.revision)

    revised = store.save_schedule(
        changed_timing,
        expected_revision=original.revision,
        reset_cursor=True,
    )
    assert revised.revision == original.revision + 1
    assert store.get_cursor(workspace_id="workspace-a", recipient_id="user-a") == start
    assert not store.compare_and_set_cursor(
        workspace_id="workspace-a",
        recipient_id="user-a",
        expected=None,
        value=start + timedelta(hours=2),
        schedule_revision=original.revision,
    )
