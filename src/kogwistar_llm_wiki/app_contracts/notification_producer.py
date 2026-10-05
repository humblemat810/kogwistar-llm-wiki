"""Bounded, ACL-gated notification window production over existing delivery jobs."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from itertools import islice
from pathlib import Path
from typing import Protocol

from .notification_delivery import (
    AuthorizeRecipient,
    AuthorizeSource,
    NotificationDeliveryScheduler,
)
from .notification_digest import (
    NotificationEvent,
    build_notification_digest,
    closed_notification_windows,
)


class NotificationCursorStore(Protocol):
    """Durable caller-owned cursor with compare-and-set advancement."""

    def get_cursor(self, *, workspace_id: str, recipient_id: str) -> datetime | None: ...

    def compare_and_set_cursor(
        self,
        *,
        workspace_id: str,
        recipient_id: str,
        expected: datetime | None,
        value: datetime,
        schedule_revision: int | None = None,
    ) -> bool: ...


class NotificationScheduleStore(NotificationCursorStore, Protocol):
    """Durable user policy store; service boundaries enforce caller ACL."""

    def get_schedule(
        self, *, workspace_id: str, recipient_id: str
    ) -> NotificationSchedule | None: ...

    def save_schedule(
        self,
        schedule: NotificationSchedule,
        *,
        expected_revision: int | None,
        reset_cursor: bool = False,
    ) -> NotificationSchedule: ...

    def list_schedules(
        self,
        *,
        workspace_id: str,
        limit: int = 100,
        after_recipient_id: str | None = None,
    ) -> tuple[NotificationSchedule, ...]: ...


class SQLiteNotificationCursorStore:
    """SQLite CAS cursor adapter for a dedicated LLM-Wiki application DB.

    Do not point this at Kogwistar's Rust-authoritative SQLite file. The cursor
    is operational scheduling state, not canonical message or graph content.
    """

    supports_schedule_revision = False

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        connection = self._connect()
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS notification_window_cursors (
                    workspace_id TEXT NOT NULL,
                    recipient_id TEXT NOT NULL,
                    cursor_us INTEGER NOT NULL,
                    PRIMARY KEY (workspace_id, recipient_id)
                )
                """
            )
            connection.commit()
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def get_cursor(self, *, workspace_id: str, recipient_id: str) -> datetime | None:
        _validate_cursor_identity(workspace_id, recipient_id)
        connection = self._connect()
        try:
            cursor = connection.execute(
                "SELECT cursor_us FROM notification_window_cursors "
                "WHERE workspace_id = ? AND recipient_id = ?",
                (workspace_id, recipient_id),
            )
            try:
                row = cursor.fetchone()
            finally:
                cursor.close()
        finally:
            connection.close()
        return None if row is None else _datetime_from_micros(int(row[0]))

    def compare_and_set_cursor(
        self,
        *,
        workspace_id: str,
        recipient_id: str,
        expected: datetime | None,
        value: datetime,
        schedule_revision: int | None = None,
    ) -> bool:
        _validate_cursor_identity(workspace_id, recipient_id)
        next_us = _datetime_to_micros(value)
        expected_us = None if expected is None else _datetime_to_micros(expected)
        if expected_us is not None and next_us <= expected_us:
            raise ValueError("notification cursor must advance monotonically")
        if schedule_revision is not None and not self.supports_schedule_revision:
            raise TypeError("schedule revision fencing requires a schedule-aware cursor store")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if schedule_revision is not None:
                row = connection.execute(
                    "SELECT revision FROM notification_schedules "
                    "WHERE workspace_id = ? AND recipient_id = ?",
                    (workspace_id, recipient_id),
                ).fetchone()
                if row is None or int(row[0]) != schedule_revision:
                    connection.rollback()
                    return False
            if expected_us is None:
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO notification_window_cursors "
                    "(workspace_id, recipient_id, cursor_us) VALUES (?, ?, ?)",
                    (workspace_id, recipient_id, next_us),
                )
            else:
                cursor = connection.execute(
                    "UPDATE notification_window_cursors SET cursor_us = ? "
                    "WHERE workspace_id = ? AND recipient_id = ? AND cursor_us = ?",
                    (next_us, workspace_id, recipient_id, expected_us),
                )
            changed = cursor.rowcount == 1
            cursor.close()
            connection.commit()
            return changed
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()


class NotificationScheduleConflict(RuntimeError):
    """Schedule revision changed or timing change lacked cursor reset consent."""


class SQLiteNotificationScheduleStore(SQLiteNotificationCursorStore):
    """Persist per-recipient notification policy and its monotonic window cursor.

    Service/API boundaries must authorize schedule reads and writes. SQLite file
    must be dedicated LLM-Wiki application state, never Kogwistar core DB.
    """

    supports_schedule_revision = True

    def __init__(self, path: str | Path) -> None:
        super().__init__(path)
        connection = self._connect()
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS notification_schedules (
                    workspace_id TEXT NOT NULL,
                    recipient_id TEXT NOT NULL,
                    start_us INTEGER NOT NULL,
                    enabled INTEGER NOT NULL CHECK (enabled IN (0, 1)),
                    cadence_seconds INTEGER NOT NULL,
                    finalize_delay_seconds INTEGER NOT NULL,
                    max_windows INTEGER NOT NULL,
                    max_sources INTEGER NOT NULL,
                    max_events_per_window INTEGER NOT NULL,
                    revision INTEGER NOT NULL,
                    PRIMARY KEY (workspace_id, recipient_id)
                )
                """
            )
            connection.commit()
        finally:
            connection.close()

    def get_schedule(
        self, *, workspace_id: str, recipient_id: str
    ) -> NotificationSchedule | None:
        _validate_cursor_identity(workspace_id, recipient_id)
        connection = self._connect()
        try:
            cursor = connection.execute(
                "SELECT start_us, enabled, cadence_seconds, finalize_delay_seconds, "
                "max_windows, max_sources, max_events_per_window, revision "
                "FROM notification_schedules WHERE workspace_id = ? AND recipient_id = ?",
                (workspace_id, recipient_id),
            )
            try:
                row = cursor.fetchone()
            finally:
                cursor.close()
        finally:
            connection.close()
        if row is None:
            return None
        return NotificationSchedule(
            workspace_id=workspace_id,
            recipient_id=recipient_id,
            start_at=_datetime_from_micros(int(row[0])),
            enabled=bool(row[1]),
            cadence_seconds=int(row[2]),
            finalize_delay_seconds=int(row[3]),
            max_windows=int(row[4]),
            max_sources=int(row[5]),
            max_events_per_window=int(row[6]),
            revision=int(row[7]),
        )

    def save_schedule(
        self,
        schedule: NotificationSchedule,
        *,
        expected_revision: int | None,
        reset_cursor: bool = False,
    ) -> NotificationSchedule:
        if not isinstance(schedule, NotificationSchedule):
            raise TypeError("schedule must be NotificationSchedule")
        if expected_revision is not None and (
            type(expected_revision) is not int or expected_revision < 1
        ):
            raise ValueError("expected_revision must be a positive integer or None")
        if type(reset_cursor) is not bool:
            raise TypeError("reset_cursor must be bool")
        workspace, recipient = schedule.workspace_id, schedule.recipient_id
        start_us = _datetime_to_micros(schedule.start_at)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            timing_changed = False
            existing = connection.execute(
                "SELECT start_us, cadence_seconds, revision FROM notification_schedules "
                "WHERE workspace_id = ? AND recipient_id = ?",
                (workspace, recipient),
            ).fetchone()
            if existing is None:
                if expected_revision is not None:
                    raise NotificationScheduleConflict("notification schedule does not exist")
                revision = 1
            else:
                current_revision = int(existing[2])
                if expected_revision != current_revision:
                    raise NotificationScheduleConflict("notification schedule revision changed")
                timing_changed = (
                    int(existing[0]) != start_us
                    or int(existing[1]) != schedule.cadence_seconds
                )
                if timing_changed and not reset_cursor:
                    raise NotificationScheduleConflict(
                        "changing schedule start or cadence requires explicit cursor reset"
                    )
                revision = current_revision + 1

            connection.execute(
                """INSERT INTO notification_schedules (
                       workspace_id, recipient_id, start_us, enabled, cadence_seconds,
                       finalize_delay_seconds, max_windows, max_sources,
                       max_events_per_window, revision
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(workspace_id, recipient_id) DO UPDATE SET
                       start_us=excluded.start_us, enabled=excluded.enabled,
                       cadence_seconds=excluded.cadence_seconds,
                       finalize_delay_seconds=excluded.finalize_delay_seconds,
                       max_windows=excluded.max_windows, max_sources=excluded.max_sources,
                       max_events_per_window=excluded.max_events_per_window,
                       revision=excluded.revision""",
                (
                    workspace,
                    recipient,
                    start_us,
                    int(schedule.enabled),
                    schedule.cadence_seconds,
                    schedule.finalize_delay_seconds,
                    schedule.max_windows,
                    schedule.max_sources,
                    schedule.max_events_per_window,
                    revision,
                ),
            )
            if existing is not None and reset_cursor:
                old_cursor = connection.execute(
                    "SELECT cursor_us FROM notification_window_cursors "
                    "WHERE workspace_id = ? AND recipient_id = ?",
                    (workspace, recipient),
                ).fetchone()
                if timing_changed and old_cursor is not None:
                    # Rewind to the new cadence boundary, never jump past
                    # events from the partially covered old window.
                    cursor_us = _floor_boundary_us(
                        int(old_cursor[0]), schedule.cadence_seconds
                    )
                    connection.execute(
                        "UPDATE notification_window_cursors SET cursor_us = ? "
                        "WHERE workspace_id = ? AND recipient_id = ?",
                        (cursor_us, workspace, recipient),
                    )
                else:
                    connection.execute(
                        "DELETE FROM notification_window_cursors "
                        "WHERE workspace_id = ? AND recipient_id = ?",
                        (workspace, recipient),
                    )
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
        return replace(schedule, revision=revision)

    def list_schedules(
        self,
        *,
        workspace_id: str,
        limit: int = 100,
        after_recipient_id: str | None = None,
    ) -> tuple[NotificationSchedule, ...]:
        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ValueError("workspace_id must be a non-empty string")
        if type(limit) is not int or limit < 1:
            raise ValueError("limit must be positive")
        if after_recipient_id is not None and (
            not isinstance(after_recipient_id, str) or not after_recipient_id.strip()
        ):
            raise ValueError("after_recipient_id must be a non-empty string or None")
        connection = self._connect()
        try:
            cursor = connection.execute(
                "SELECT recipient_id, start_us, enabled, cadence_seconds, "
                "finalize_delay_seconds, max_windows, max_sources, "
                "max_events_per_window, revision FROM notification_schedules "
                "WHERE workspace_id = ? AND (? IS NULL OR recipient_id > ?) "
                "ORDER BY recipient_id LIMIT ?",
                (workspace_id, after_recipient_id, after_recipient_id, limit),
            )
            try:
                rows = cursor.fetchall()
            finally:
                cursor.close()
        finally:
            connection.close()
        return tuple(
            NotificationSchedule(
                workspace_id=workspace_id,
                recipient_id=str(row[0]),
                start_at=_datetime_from_micros(int(row[1])),
                enabled=bool(row[2]),
                cadence_seconds=int(row[3]),
                finalize_delay_seconds=int(row[4]),
                max_windows=int(row[5]),
                max_sources=int(row[6]),
                max_events_per_window=int(row[7]),
                revision=int(row[8]),
            )
            for row in rows
        )


class ListNotificationSources(Protocol):
    """List source IDs visible to one authenticated notification recipient."""

    def __call__(self, workspace_id: str, recipient_id: str, /) -> Iterable[str]: ...


class ReadNotificationWindow(Protocol):
    """Read bounded notification events for one finalized time window."""

    def __call__(
        self,
        workspace_id: str,
        recipient_id: str,
        source_ids: tuple[str, ...],
        window_start: datetime,
        window_end: datetime,
        limit: int,
        /,
    ) -> Iterable[NotificationEvent]: ...


@dataclass(frozen=True, slots=True)
class NotificationSchedule:
    """Policy supplied by an application-owned, authenticated preference store."""

    workspace_id: str
    recipient_id: str
    start_at: datetime
    enabled: bool = True
    cadence_seconds: int = 3600
    finalize_delay_seconds: int = 120
    max_windows: int = 24
    max_sources: int = 100
    max_events_per_window: int = 1000
    revision: int = 0

    def __post_init__(self) -> None:
        for name in ("workspace_id", "recipient_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if not isinstance(self.start_at, datetime):
            raise TypeError("start_at must be datetime")
        if self.start_at.tzinfo is None or self.start_at.utcoffset() is None:
            raise ValueError("start_at must be timezone-aware")
        if type(self.enabled) is not bool:
            raise TypeError("enabled must be bool")
        for name, minimum in (
            ("cadence_seconds", 1),
            ("max_windows", 1),
            ("max_sources", 1),
            ("max_events_per_window", 1),
        ):
            value = getattr(self, name)
            if type(value) is not int or value < minimum:
                raise ValueError(f"{name} must be a positive integer")
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("revision must be a non-negative integer")
        if type(self.finalize_delay_seconds) is not int or self.finalize_delay_seconds < 0:
            raise ValueError("finalize_delay_seconds must be a non-negative integer")
        if self.max_windows > 168 or self.max_sources > 1000 or self.max_events_per_window > 10000:
            raise ValueError("notification schedule exceeds hard processing bounds")
        closed_notification_windows(
            after=self.start_at,
            now=self.start_at,
            cadence_seconds=self.cadence_seconds,
            finalize_delay_seconds=self.finalize_delay_seconds,
            max_windows=self.max_windows,
        )


@dataclass(frozen=True, slots=True)
class NotificationProductionResult:
    windows_processed: int
    events_considered: int
    delivery_job_ids: tuple[str, ...]
    cursor: datetime | None


class NotificationWindowProducer:
    """Turn finalized windows into existing durable delivery jobs.

    The event source and preference/cursor stores remain application adapters.
    Enqueue happens before cursor CAS: a crash in between safely replays the
    deterministic digest through the existing queue; failed work never advances
    the cursor. This is at-least-once production, not a cross-store outbox.
    """

    def __init__(
        self,
        *,
        scheduler: NotificationDeliveryScheduler,
        cursor_store: NotificationCursorStore,
        list_sources: ListNotificationSources,
        read_window: ReadNotificationWindow,
        authorize_recipient: AuthorizeRecipient,
        authorize_source: AuthorizeSource,
    ) -> None:
        if not callable(getattr(scheduler, "enqueue_digest", None)):
            raise TypeError("scheduler must implement enqueue_digest()")
        if not callable(getattr(cursor_store, "get_cursor", None)) or not callable(
            getattr(cursor_store, "compare_and_set_cursor", None)
        ):
            raise TypeError("cursor_store must implement durable read and compare-and-set")
        if not callable(list_sources) or not callable(read_window):
            raise TypeError("notification source adapters are required")
        if not callable(authorize_recipient) or not callable(authorize_source):
            raise TypeError("recipient and source ACL callbacks are required")
        self.scheduler = scheduler
        self.cursor_store = cursor_store
        self.list_sources = list_sources
        self.read_window = read_window
        self.authorize_recipient = authorize_recipient
        self.authorize_source = authorize_source

    def produce_once(
        self,
        schedule: NotificationSchedule,
        *,
        now: datetime,
    ) -> NotificationProductionResult:
        if not isinstance(schedule, NotificationSchedule):
            raise TypeError("schedule must be NotificationSchedule")
        if not schedule.enabled:
            return NotificationProductionResult(0, 0, (), None)
        workspace = schedule.workspace_id
        recipient = schedule.recipient_id
        if not self.authorize_recipient(workspace, recipient):
            raise PermissionError("notification recipient is not authorized")

        stored_cursor = self.cursor_store.get_cursor(
            workspace_id=workspace,
            recipient_id=recipient,
        )
        cursor = stored_cursor if stored_cursor is not None else schedule.start_at
        windows = closed_notification_windows(
            after=cursor,
            now=now,
            cadence_seconds=schedule.cadence_seconds,
            finalize_delay_seconds=schedule.finalize_delay_seconds,
            max_windows=schedule.max_windows,
        )
        if not windows:
            return NotificationProductionResult(0, 0, (), cursor)

        source_batch = tuple(islice(self.list_sources(workspace, recipient), schedule.max_sources + 1))
        if len(source_batch) > schedule.max_sources:
            raise ValueError("notification source set exceeds configured bound")
        if any(not isinstance(source, str) or not source.strip() for source in source_batch):
            raise ValueError("notification source IDs must be non-empty strings")
        if len(set(source_batch)) != len(source_batch):
            raise ValueError("notification source IDs must be unique")
        for source in source_batch:
            if not self.authorize_source(workspace, recipient, source):
                raise PermissionError("notification source is not authorized")

        processed = considered = 0
        job_ids: list[str] = []
        for start, end in windows:
            events = self.read_window(
                workspace,
                recipient,
                source_batch,
                start,
                end,
                schedule.max_events_per_window,
            )
            digest = build_notification_digest(
                events,
                workspace_id=workspace,
                window_start=start,
                window_end=end,
                authorize_source=lambda event_workspace, source: (
                    event_workspace == workspace
                    and self.authorize_source(workspace, recipient, source)
                ),
                max_events=schedule.max_events_per_window,
            )
            considered += sum(len(item.source_event_ids) for item in (*digest.routine, *digest.urgent))
            if digest.routine or digest.urgent:
                job_ids.extend(
                    self.scheduler.enqueue_digest(
                        recipient_id=recipient,
                        digest=digest,
                        authorize_recipient=self.authorize_recipient,
                        authorize_source=self.authorize_source,
                    )
                )
            if not self.cursor_store.compare_and_set_cursor(
                workspace_id=workspace,
                recipient_id=recipient,
                expected=stored_cursor,
                value=end,
                schedule_revision=schedule.revision if schedule.revision > 0 else None,
            ):
                raise RuntimeError("notification cursor changed concurrently")
            cursor = end
            stored_cursor = end
            processed += 1

        return NotificationProductionResult(
            windows_processed=processed,
            events_considered=considered,
            delivery_job_ids=tuple(job_ids),
            cursor=cursor,
        )


__all__ = [
    "NotificationCursorStore",
    "NotificationProductionResult",
    "NotificationSchedule",
    "NotificationScheduleConflict",
    "NotificationScheduleStore",
    "NotificationWindowProducer",
    "SQLiteNotificationCursorStore",
    "SQLiteNotificationScheduleStore",
]


def _validate_cursor_identity(workspace_id: str, recipient_id: str) -> None:
    for name, value in (("workspace_id", workspace_id), ("recipient_id", recipient_id)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")


def _datetime_to_micros(value: datetime) -> int:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("notification cursor timestamps must be timezone-aware")
    delta = value.astimezone(UTC) - datetime(1970, 1, 1, tzinfo=UTC)
    return ((delta.days * 86400 + delta.seconds) * 1_000_000) + delta.microseconds


def _datetime_from_micros(value: int) -> datetime:
    return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=value)


def _floor_boundary_us(value_us: int, cadence_seconds: int) -> int:
    interval_us = cadence_seconds * 1_000_000
    return (value_us // interval_us) * interval_us
