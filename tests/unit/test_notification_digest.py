from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from kogwistar_llm_wiki.app_contracts.notification_digest import (
    NotificationDigest,
    NotificationDigestItem,
    NotificationEvent,
    build_notification_digest,
    closed_notification_windows,
)

pytestmark = pytest.mark.ci

START = datetime(2026, 9, 1, tzinfo=UTC)
END = START + timedelta(hours=1)


def test_closed_notification_windows_catches_up_without_overlap() -> None:
    windows = closed_notification_windows(
        after=datetime(2026, 9, 1, 8, tzinfo=UTC),
        now=datetime(2026, 9, 1, 10, tzinfo=UTC),
        cadence_seconds=3600,
        finalize_delay_seconds=0,
    )

    assert windows == (
        (datetime(2026, 9, 1, 8, tzinfo=UTC), datetime(2026, 9, 1, 9, tzinfo=UTC)),
        (datetime(2026, 9, 1, 9, tzinfo=UTC), datetime(2026, 9, 1, 10, tzinfo=UTC)),
    )


def test_notification_finalize_delay_defers_recent_window() -> None:
    windows = closed_notification_windows(
        after=datetime(2026, 9, 1, 8, tzinfo=UTC),
        now=datetime(2026, 9, 1, 10, 3, tzinfo=UTC),
        cadence_seconds=3600,
        finalize_delay_seconds=5 * 60,
    )

    assert windows == (
        (datetime(2026, 9, 1, 8, tzinfo=UTC), datetime(2026, 9, 1, 9, tzinfo=UTC)),
    )


def test_notification_window_planner_normalizes_timezone_offsets_to_utc() -> None:
    windows = closed_notification_windows(
        after=datetime.fromisoformat("2026-09-01T09:00:00+02:00"),
        now=datetime.fromisoformat("2026-09-01T10:00:00+02:00"),
        cadence_seconds=3600,
        finalize_delay_seconds=0,
    )

    assert windows == (
        (datetime(2026, 9, 1, 7, tzinfo=UTC), datetime(2026, 9, 1, 8, tzinfo=UTC)),
    )


def test_notification_window_planner_rejects_backlog_instead_of_skipping() -> None:
    with pytest.raises(ValueError, match="backlog exceeds"):
        closed_notification_windows(
            after=datetime(2026, 9, 1, tzinfo=UTC),
            now=datetime(2026, 9, 4, tzinfo=UTC),
            cadence_seconds=3600,
            finalize_delay_seconds=0,
            max_windows=24,
        )


@pytest.mark.parametrize(
    "after, now, cadence, delay, max_windows, message",
    [
        (datetime.fromisoformat("2026-09-01T00:00:00"), datetime(2026, 9, 1, tzinfo=UTC), 3600, 0, 24, "after must be timezone-aware"),
        (datetime(2026, 9, 1, tzinfo=UTC), datetime.fromisoformat("2026-09-01T00:00:00"), 3600, 0, 24, "now must be timezone-aware"),
        (datetime(2026, 9, 1, 0, 1, tzinfo=UTC), datetime(2026, 9, 1, 2, tzinfo=UTC), 3600, 0, 24, "align"),
        (datetime(2026, 9, 1, tzinfo=UTC), datetime(2026, 9, 1, tzinfo=UTC), 0, 0, 24, "cadence_seconds"),
        (datetime(2026, 9, 1, 2, tzinfo=UTC), datetime(2026, 9, 1, tzinfo=UTC), 3600, 0, 24, "must not precede"),
    ],
)
def test_notification_window_planner_rejects_invalid_cursor_and_policy(
    after, now, cadence, delay, max_windows, message
) -> None:
    with pytest.raises(ValueError, match=message):
        closed_notification_windows(
            after=after,
            now=now,
            cadence_seconds=cadence,
            finalize_delay_seconds=delay,
            max_windows=max_windows,
        )


def _event(event_id: str, **changes) -> NotificationEvent:
    values = {
        "event_id": event_id,
        "workspace_id": "workspace-a",
        "source_id": "stream-mail",
        "source_revision_id": f"revision-{event_id}",
        "occurred_at": START + timedelta(minutes=5),
        "title": "Build status changed",
    }
    values.update(changes)
    return NotificationEvent(**values)


def _build(events, *, allowed=True):
    return build_notification_digest(
        events,
        workspace_id="workspace-a",
        window_start=START,
        window_end=END,
        authorize_source=lambda _workspace, _source: allowed,
    )


def test_routine_repeats_coalesce_and_keep_event_drilldown() -> None:
    events = [
        _event("e2", dedupe_key="project:42:build-status", source_id="stream-chat"),
        _event("e1", dedupe_key="project:42:build-status", action_required=True),
        _event("e1", dedupe_key="project:42:build-status", action_required=True),
        _event("e3", dedupe_key="project:43:build-status"),
    ]

    digest = _build(events)

    assert len(digest.routine) == 2
    grouped = next(item for item in digest.routine if len(item.source_event_ids) == 2)
    assert grouped.summary == "Build status changed (2 updates)"
    assert grouped.source_event_ids == ("e1", "e2")
    assert len(grouped.evidence_refs) == 2
    assert grouped.action_required is True
    assert len({item.item_id for item in digest.routine}) == 2


def test_untrusted_notification_title_cannot_use_directional_overrides() -> None:
    event = _event("spoofed-title", title="Invoice \u202eexe.scr")

    assert event.title == "Invoice exe.scr"
    assert "\u202e" not in _build([event]).routine[0].summary


def test_urgent_events_bypass_coalescing_even_with_same_dedupe_key() -> None:
    digest = _build(
        [
            _event("urgent-1", severity="high", dedupe_key="same"),
            _event("urgent-2", severity="critical", dedupe_key="same"),
            _event("routine-1", severity="normal", dedupe_key="same"),
            _event("routine-2", severity="info", dedupe_key="same"),
        ]
    )

    assert len(digest.urgent) == 2
    assert all(len(item.source_event_ids) == 1 for item in digest.urgent)
    assert len(digest.routine) == 1
    assert digest.routine[0].source_event_ids == ("routine-1", "routine-2")


def test_exact_duplicate_key_coalesces_urgent_copies_but_keeps_provenance() -> None:
    digest = _build(
        [
            _event(
                "urgent-mail-a",
                severity="critical",
                source_id="mail-a",
                duplicate_key="same-origin-copy",
            ),
            _event(
                "urgent-mail-b",
                severity="high",
                source_id="mail-b",
                duplicate_key="same-origin-copy",
            ),
        ]
    )

    assert len(digest.urgent) == 1
    item = digest.urgent[0]
    assert item.summary == "Build status changed (2 copies)"
    assert item.severity == "critical"
    assert item.source_event_ids == ("urgent-mail-a", "urgent-mail-b")
    assert {source for source, _revision, _event in item.evidence_refs} == {
        "mail-a",
        "mail-b",
    }


def test_digest_identity_and_order_are_deterministic() -> None:
    first = _event("e1", dedupe_key="same")
    second = _event("e2", dedupe_key="same", occurred_at=START + timedelta(minutes=10))

    forward = _build([first, second])
    reverse = _build([second, first])

    assert forward == reverse


def test_coalesced_summary_uses_latest_event_state() -> None:
    earlier = _event("e1", title="Meeting invitation: Review", dedupe_key="thread:1")
    later = _event(
        "e2",
        title="Meeting cancelled: Review",
        dedupe_key="thread:1",
        occurred_at=START + timedelta(minutes=10),
    )

    forward = _build([earlier, later])
    reverse = _build([later, earlier])

    assert forward == reverse
    assert forward.routine[0].summary == "Meeting cancelled: Review (2 updates)"


def test_acl_denial_fails_closed_before_digest_is_returned() -> None:
    event = _event("private")

    with pytest.raises(PermissionError, match="not authorized"):
        _build([event], allowed=False)


def test_event_batch_is_bounded_before_authorization_or_aggregation() -> None:
    authorized: list[str] = []

    with pytest.raises(ValueError, match="exceeds max_events"):
        build_notification_digest(
            (_event(f"e{index}") for index in range(3)),
            workspace_id="workspace-a",
            window_start=START,
            window_end=END,
            authorize_source=lambda _workspace, source: authorized.append(source) or True,
            max_events=2,
        )

    assert authorized == []


@pytest.mark.parametrize("max_events", [0, -1, True, 1.5])
def test_event_batch_limit_must_be_a_positive_integer(max_events) -> None:
    with pytest.raises(ValueError, match="max_events"):
        build_notification_digest(
            [],
            workspace_id="workspace-a",
            window_start=START,
            window_end=END,
            authorize_source=lambda _workspace, _source: True,
            max_events=max_events,
        )


@pytest.mark.parametrize(
    "events, message",
    [
        ([_event("other", workspace_id="workspace-b")], "mix workspaces"),
        ([_event("late", occurred_at=END)], "outside digest window"),
        (
            [
                _event("duplicate", title="A"),
                _event("duplicate", title="B"),
            ],
            "conflicting notification payloads",
        ),
    ],
)
def test_unsafe_or_ambiguous_inputs_are_rejected(events, message) -> None:
    with pytest.raises(ValueError, match=message):
        _build(events)


def test_digest_rejects_urgent_item_in_routine_collection() -> None:
    urgent_item = _build([_event("urgent", severity="high")]).urgent[0]

    with pytest.raises(ValueError, match="routine digest items"):
        NotificationDigest(
            workspace_id="workspace-a",
            window_start=START,
            window_end=END,
            routine=(urgent_item,),
            urgent=(),
        )


def test_digest_item_rejects_mismatched_event_and_evidence_provenance() -> None:
    item = _build([_event("event-1")]).routine[0]

    with pytest.raises(ValueError, match="source event IDs must match evidence"):
        NotificationDigestItem(
            item_id=item.item_id,
            workspace_id=item.workspace_id,
            summary=item.summary,
            severity=item.severity,
            action_required=item.action_required,
            source_event_ids=item.source_event_ids,
            evidence_refs=(("mail", "revision-event-1", "different-event"),),
            urgent=item.urgent,
            logical_event_count=item.logical_event_count,
            duplicate_copy_count=item.duplicate_copy_count,
        )


def test_event_requires_timezone_aware_timestamp() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        _event("naive", occurred_at=datetime.fromisoformat("2026-09-01T00:00:00"))


@pytest.mark.parametrize(
    "changes, error, message",
    [
        ({"dedupe_key": 3}, TypeError, "dedupe_key must be a string"),
        ({"duplicate_key": "\n"}, ValueError, "duplicate_key must be non-empty"),
        ({"severity": "emergency"}, ValueError, "unsupported notification severity"),
        ({"occurred_at": "2026-09-01T00:00:00Z"}, TypeError, "occurred_at must be datetime"),
    ],
)
def test_notification_event_rejects_invalid_runtime_types(changes, error, message) -> None:
    with pytest.raises(error, match=message):
        _event("invalid", **changes)
