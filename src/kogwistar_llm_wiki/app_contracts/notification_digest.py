"""Deterministic, ACL-gated grouping for low-noise app notifications."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import islice
from typing import Literal, Protocol, cast

Severity = Literal["info", "normal", "high", "critical"]
_SEVERITY_RANK: dict[Severity, int] = {
    "info": 0,
    "normal": 1,
    "high": 2,
    "critical": 3,
}
_DIRECTIONAL_CONTROLS = frozenset(
    {"\u061c", "\u200e", "\u200f", *(chr(codepoint) for codepoint in range(0x202A, 0x202F))}
    | {chr(codepoint) for codepoint in range(0x2066, 0x206A)}
)


@dataclass(frozen=True, slots=True)
class NotificationEvent:
    """One source-grounded event eligible for user notification."""

    event_id: str
    workspace_id: str
    source_id: str
    source_revision_id: str
    occurred_at: datetime
    title: str
    severity: Severity = "normal"
    dedupe_key: str | None = None
    action_required: bool = False
    duplicate_key: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.title, str):
            object.__setattr__(self, "title", _safe_display_text(self.title))
        for field in ("event_id", "workspace_id", "source_id", "source_revision_id", "title"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field} must be a non-empty string")
            if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
                raise ValueError(f"{field} must not contain control characters")
        if self.dedupe_key is not None:
            if not isinstance(self.dedupe_key, str):
                raise TypeError("dedupe_key must be a string or None")
            if not self.dedupe_key.strip() or any(
                ord(char) < 0x20 or ord(char) == 0x7F for char in self.dedupe_key
            ):
                raise ValueError("dedupe_key must be non-empty and contain no controls")
        if self.duplicate_key is not None:
            if not isinstance(self.duplicate_key, str):
                raise TypeError("duplicate_key must be a string or None")
            if not self.duplicate_key.strip() or any(
                ord(char) < 0x20 or ord(char) == 0x7F for char in self.duplicate_key
            ):
                raise ValueError("duplicate_key must be non-empty and contain no controls")
        if not isinstance(self.severity, str) or self.severity not in _SEVERITY_RANK:
            raise ValueError("unsupported notification severity")
        if not isinstance(self.occurred_at, datetime):
            raise TypeError("occurred_at must be datetime")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        if type(self.action_required) is not bool:
            raise TypeError("action_required must be bool")


@dataclass(frozen=True, slots=True)
class NotificationDigestItem:
    """A concise deterministic summary with complete event-level drilldown."""

    item_id: str
    workspace_id: str
    summary: str
    severity: Severity
    action_required: bool
    source_event_ids: tuple[str, ...]
    evidence_refs: tuple[tuple[str, str, str], ...]
    urgent: bool
    logical_event_count: int = 1
    duplicate_copy_count: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.summary, str):
            object.__setattr__(self, "summary", _safe_display_text(self.summary))
        for name in ("item_id", "workspace_id", "summary"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
            if any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
                raise ValueError(f"{name} must not contain control characters")
        if self.severity not in _SEVERITY_RANK:
            raise ValueError("unsupported notification severity")
        if type(self.action_required) is not bool or type(self.urgent) is not bool:
            raise TypeError("notification flags must be bool")
        if not isinstance(self.source_event_ids, tuple) or not self.source_event_ids:
            raise ValueError("source_event_ids must be a non-empty tuple")
        if any(not isinstance(event_id, str) or not event_id.strip() for event_id in self.source_event_ids):
            raise ValueError("source_event_ids must contain non-empty strings")
        if len(set(self.source_event_ids)) != len(self.source_event_ids):
            raise ValueError("source_event_ids must be unique")
        if not isinstance(self.evidence_refs, tuple) or any(
            not isinstance(reference, tuple)
            or len(reference) != 3
            or any(not isinstance(part, str) or not part.strip() for part in reference)
            for reference in self.evidence_refs
        ):
            raise ValueError("evidence_refs must contain source, revision, and event IDs")
        if len(self.evidence_refs) != len(self.source_event_ids) or {
            reference[2] for reference in self.evidence_refs
        } != set(self.source_event_ids):
            raise ValueError("source event IDs must match evidence references")
        if type(self.logical_event_count) is not int or self.logical_event_count < 1:
            raise ValueError("logical_event_count must be a positive integer")
        if type(self.duplicate_copy_count) is not int or self.duplicate_copy_count < 0:
            raise ValueError("duplicate_copy_count must be a non-negative integer")
        if self.logical_event_count + self.duplicate_copy_count != len(self.source_event_ids):
            raise ValueError("logical and duplicate counts must match source event count")


@dataclass(frozen=True, slots=True)
class NotificationClassification:
    """Trusted application policy result; source claims are not authority."""

    severity: Severity = "normal"
    action_required: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.severity, str) or self.severity not in _SEVERITY_RANK:
            raise ValueError("unsupported notification severity")
        if type(self.action_required) is not bool:
            raise TypeError("action_required must be bool")


@dataclass(frozen=True, slots=True)
class NotificationDigest:
    workspace_id: str
    window_start: datetime
    window_end: datetime
    routine: tuple[NotificationDigestItem, ...]
    urgent: tuple[NotificationDigestItem, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.workspace_id, str) or not self.workspace_id.strip():
            raise ValueError("workspace_id must be a non-empty string")
        _require_aware(self.window_start, "window_start")
        _require_aware(self.window_end, "window_end")
        if self.window_start.astimezone(UTC) >= self.window_end.astimezone(UTC):
            raise ValueError("window_start must be earlier than window_end")
        if not isinstance(self.routine, tuple) or not isinstance(self.urgent, tuple):
            raise TypeError("digest item collections must be tuples")
        items = (*self.routine, *self.urgent)
        if any(not isinstance(item, NotificationDigestItem) for item in items):
            raise TypeError("digest collections must contain NotificationDigestItem values")
        if any(item.workspace_id != self.workspace_id for item in items):
            raise ValueError("digest items must belong to the digest workspace")
        if any(item.urgent or item.severity in ("high", "critical") for item in self.routine):
            raise ValueError("routine digest items must not be urgent")
        if any(not item.urgent or item.severity not in ("high", "critical") for item in self.urgent):
            raise ValueError("urgent digest items must have high or critical severity")
        item_ids = [item.item_id for item in items]
        if len(set(item_ids)) != len(item_ids):
            raise ValueError("digest item IDs must be unique")
        event_ids = [event_id for item in items for event_id in item.source_event_ids]
        if len(set(event_ids)) != len(event_ids):
            raise ValueError("source event IDs must appear in only one digest item")


class AuthorizeNotificationSource(Protocol):
    """Authorize one event source before digest aggregation."""

    def __call__(self, workspace_id: str, source_id: str, /) -> bool: ...


def build_notification_digest(
    events: Iterable[NotificationEvent],
    *,
    workspace_id: str,
    window_start: datetime,
    window_end: datetime,
    authorize_source: AuthorizeNotificationSource,
    max_events: int = 1000,
) -> NotificationDigest:
    """Group routine notices; keep urgent events separate except exact copies.

    ACL is checked for every input before aggregation. Out-of-window events,
    cross-workspace input, and duplicate IDs with conflicting payloads fail
    closed rather than being silently omitted. ``duplicate_key`` is an
    explicit source-derived identity for copies of the same underlying event;
    ``dedupe_key`` groups related routine updates and never groups urgent items.
    """

    _require_aware(window_start, "window_start")
    _require_aware(window_end, "window_end")
    start = window_start.astimezone(UTC)
    end = window_end.astimezone(UTC)
    if start >= end:
        raise ValueError("window_start must be earlier than window_end")
    if not callable(authorize_source):
        raise TypeError("authorize_source callback is required")
    if type(max_events) is not int or max_events < 1:
        raise ValueError("max_events must be a positive integer")

    bounded_events = tuple(islice(events, max_events + 1))
    if len(bounded_events) > max_events:
        raise ValueError("notification digest event batch exceeds max_events")

    unique: dict[str, NotificationEvent] = {}
    for event in bounded_events:
        if not isinstance(event, NotificationEvent):
            raise TypeError("events must contain NotificationEvent values")
        if event.workspace_id != workspace_id:
            raise ValueError("notification digest cannot mix workspaces")
        if not authorize_source(event.workspace_id, event.source_id):
            raise PermissionError("notification source is not authorized")
        occurred = event.occurred_at.astimezone(UTC)
        if not start <= occurred < end:
            raise ValueError("notification event falls outside digest window")
        prior = unique.get(event.event_id)
        if prior is not None and prior != event:
            raise ValueError("event_id identifies conflicting notification payloads")
        unique[event.event_id] = event

    ordered = sorted(
        unique.values(),
        key=lambda event: (event.occurred_at.astimezone(UTC), event.event_id),
    )
    routine_groups: dict[str, list[NotificationEvent]] = {}
    urgent_groups: dict[str, list[NotificationEvent]] = {}
    for event in ordered:
        if event.severity in ("high", "critical"):
            group_key = (
                "duplicate:" + event.duplicate_key
                if event.duplicate_key is not None
                else "event:" + event.event_id
            )
            urgent_groups.setdefault(group_key, []).append(event)
            continue
        group_key = (
            "dedupe:" + event.dedupe_key
            if event.dedupe_key is not None
            else "event:" + event.event_id
        )
        routine_groups.setdefault(group_key, []).append(event)

    routine = tuple(
        _make_item(workspace_id, start, end, key, group, urgent=False)
        for key, group in sorted(routine_groups.items())
    )
    urgent = tuple(
        _make_item(workspace_id, start, end, key, group, urgent=True)
        for key, group in sorted(urgent_groups.items())
    )
    return NotificationDigest(
        workspace_id=workspace_id,
        window_start=start,
        window_end=end,
        routine=routine,
        urgent=urgent,
    )


def closed_notification_windows(
    *,
    after: datetime,
    now: datetime,
    cadence_seconds: int = 3600,
    finalize_delay_seconds: int = 120,
    max_windows: int = 24,
) -> tuple[tuple[datetime, datetime], ...]:
    """Return every bounded, finalized UTC window after a durable cursor.

    The caller owns cursor persistence and advances it only after processing a
    window succeeds (including confirming it is empty). Excess backlog raises
    instead of silently skipping windows. ``finalize_delay_seconds`` lets late
    source events settle before a window closes; it is not a substitute for
    source reconciliation.
    """

    for name, value in (("after", after), ("now", now)):
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{name} must be timezone-aware")
    if type(cadence_seconds) is not int or cadence_seconds <= 0:
        raise ValueError("cadence_seconds must be a positive integer")
    if type(finalize_delay_seconds) is not int or finalize_delay_seconds < 0:
        raise ValueError("finalize_delay_seconds must be a non-negative integer")
    if type(max_windows) is not int or max_windows <= 0:
        raise ValueError("max_windows must be a positive integer")

    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    cursor = after.astimezone(UTC)
    current = now.astimezone(UTC)
    cursor_delta = cursor - epoch
    cursor_seconds = cursor_delta // timedelta(seconds=1)
    if cursor_delta != timedelta(seconds=cursor_seconds) or cursor_seconds % cadence_seconds:
        raise ValueError("after must align to a UTC cadence boundary")
    if current < cursor:
        raise ValueError("now must not precede the notification cursor")

    cutoff = current - timedelta(seconds=finalize_delay_seconds)
    cutoff_seconds = (cutoff - epoch) // timedelta(seconds=1)
    finalized_end = (cutoff_seconds // cadence_seconds) * cadence_seconds
    if finalized_end <= cursor_seconds:
        return ()

    count = (finalized_end - cursor_seconds) // cadence_seconds
    if count > max_windows:
        raise ValueError("finalized notification window backlog exceeds max_windows")
    return tuple(
        (
            epoch + timedelta(seconds=start),
            epoch + timedelta(seconds=start + cadence_seconds),
        )
        for start in range(cursor_seconds, finalized_end, cadence_seconds)
    )


def _make_item(
    workspace_id: str,
    start: datetime,
    end: datetime,
    group_key: str,
    events: list[NotificationEvent],
    *,
    urgent: bool,
) -> NotificationDigestItem:
    source_events = sorted(events, key=lambda event: (event.event_id, event.source_id))
    latest = max(
        source_events,
        key=lambda event: (event.occurred_at.astimezone(UTC), event.event_id),
    )
    logical_events = {
        event.duplicate_key or f"event:{event.event_id}" for event in source_events
    }
    logical_count = len(logical_events)
    if logical_count > 1:
        summary = f"{latest.title} ({logical_count} updates)"
        duplicate_count = len(source_events) - logical_count
        if duplicate_count:
            summary += f" plus {duplicate_count} duplicate copies"
    elif len(source_events) > 1:
        summary = f"{latest.title} ({len(source_events)} copies)"
    else:
        summary = latest.title
    severity = cast(Severity, max(
        (event.severity for event in source_events),
        key=lambda item: _SEVERITY_RANK[cast(Severity, item)],
    ))
    identity = json.dumps(
        [workspace_id, start.isoformat(), end.isoformat(), group_key],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    item_id = "notification:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:32]
    return NotificationDigestItem(
        item_id=item_id,
        workspace_id=workspace_id,
        summary=summary,
        severity=severity,
        action_required=any(event.action_required for event in source_events),
        source_event_ids=tuple(event.event_id for event in source_events),
        evidence_refs=tuple(
            (event.source_id, event.source_revision_id, event.event_id)
            for event in source_events
        ),
        urgent=urgent,
        logical_event_count=logical_count,
        duplicate_copy_count=len(source_events) - logical_count,
    )


def _require_aware(value: datetime, field: str) -> None:
    if not isinstance(value, datetime):
        raise TypeError(f"{field} must be datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


def _safe_display_text(value: str) -> str:
    return "".join(char for char in value if char not in _DIRECTIONAL_CONTROLS)


__all__ = [
    "NotificationClassification",
    "NotificationDigest",
    "NotificationDigestItem",
    "NotificationEvent",
    "build_notification_digest",
]
