from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from kogwistar.engine_core.jobs import JobQueueItem

from kogwistar_llm_wiki.app_contracts.notification_delivery import (
    NotificationDeliveryScheduler,
)
from kogwistar_llm_wiki.app_contracts.notification_digest import (
    NotificationEvent,
    build_notification_digest,
)
from kogwistar_llm_wiki.configuration.workspace import WorkspaceNamespaces

pytestmark = pytest.mark.ci

START = datetime(2026, 9, 1, tzinfo=UTC)
END = START + timedelta(hours=1)


class FakeQueue:
    def __init__(self) -> None:
        self.pending: list[JobQueueItem] = []
        self.coalesce_keys: dict[tuple[str, str, str, str], str] = {}
        self.done: list[str] = []
        self.failed: list[tuple[str, str]] = []
        self.retried: list[str] = []

    def require_available(self, **_kwargs) -> None:
        return None

    @contextmanager
    def transaction(self):
        pending_before = deepcopy(self.pending)
        coalesce_keys_before = dict(self.coalesce_keys)
        try:
            yield
        except BaseException:
            self.pending = pending_before
            self.coalesce_keys = coalesce_keys_before
            raise

    def enqueue(self, **kwargs) -> str:
        job_id = str(kwargs["job_id"])
        key = (
            str(kwargs["namespace"]),
            str(kwargs["entity_kind"]),
            str(kwargs["entity_id"]),
            str(kwargs["job_kind"]),
        )
        pending_id = self.coalesce_keys.get(key)
        if pending_id is not None:
            for index, job in enumerate(self.pending):
                if job.job_id == pending_id:
                    self.pending[index] = replace(
                        job,
                        op=str(kwargs["op"]),
                        payload=dict(kwargs["payload"]),
                    )
                    return pending_id
        if not any(job.job_id == job_id for job in self.pending):
            self.pending.append(
                JobQueueItem(
                    job_id=job_id,
                    namespace=key[0],
                    entity_kind=key[1],
                    entity_id=key[2],
                    job_kind=key[3],
                    op=str(kwargs["op"]),
                    payload=dict(kwargs["payload"]),
                    retry_count=0,
                    max_retries=int(kwargs["max_retries"]),
                )
            )
            self.coalesce_keys[key] = job_id
        return job_id

    def claim(self, *, namespace: str, limit: int, lease_seconds: int) -> list[JobQueueItem]:
        del lease_seconds
        selected = [job for job in self.pending if job.namespace == namespace][:limit]
        self.pending = [job for job in self.pending if job not in selected]
        for key, job_id in tuple(self.coalesce_keys.items()):
            if job_id in {job.job_id for job in selected}:
                self.coalesce_keys.pop(key, None)
        return [replace(job, claim_token=f"claim:{job.job_id}") for job in selected]

    def mark_done(self, job_id: str, *, claim_token: str | None = None) -> bool:
        assert claim_token == f"claim:{job_id}"
        self.done.append(job_id)
        return True

    def mark_failed(self, job_id: str, error: str, **_kwargs) -> None:
        self.failed.append((job_id, error))

    def retry_or_fail(self, job: JobQueueItem, _error) -> None:
        self.retried.append(job.job_id)


class FakeDelivery:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, str, tuple[str, ...]]] = []

    def deliver(self, *, recipient_id, digest, idempotency_key) -> None:
        if self.fail:
            raise TimeoutError("delivery unavailable")
        items = (*digest.urgent, *digest.routine)
        self.calls.append(
            (
                recipient_id,
                idempotency_key,
                tuple(event_id for item in items for event_id in item.source_event_ids),
            )
        )


def _event(
    event_id: str,
    *,
    severity: str = "normal",
    source_id: str = "mail-stream",
    title: str = "Account activity",
    workspace_id: str = "workspace-a",
):
    return NotificationEvent(
        event_id=event_id,
        workspace_id=workspace_id,
        source_id=source_id,
        source_revision_id=f"revision-{event_id}",
        occurred_at=START + timedelta(minutes=5),
        title=title,
        severity=severity,  # type: ignore[arg-type]
        dedupe_key="account-activity" if severity == "normal" else None,
    )


def _scheduler(queue: FakeQueue) -> NotificationDeliveryScheduler:
    class FakeConversation:
        jobs = queue

        @contextmanager
        def uow(self):
            with queue.transaction():
                yield

    engines = SimpleNamespace(conversation=FakeConversation())
    return NotificationDeliveryScheduler(engines=engines)


def _digest():
    return build_notification_digest(
        [_event("routine-1"), _event("routine-2"), _event("urgent-1", severity="critical")],
        workspace_id="workspace-a",
        window_start=START,
        window_end=END,
        authorize_source=lambda _workspace, _source: True,
    )


def test_enqueue_splits_urgent_from_routine_and_is_idempotent() -> None:
    queue = FakeQueue()
    scheduler = _scheduler(queue)
    acl_calls: list[tuple[str, str, str]] = []
    kwargs = {
        "recipient_id": "principal-1",
        "digest": _digest(),
        "authorize_recipient": lambda workspace, recipient: workspace == "workspace-a"
        and recipient == "principal-1",
        "authorize_source": lambda workspace, recipient, source: acl_calls.append(
            (workspace, recipient, source)
        ) is None
        and workspace == "workspace-a"
        and recipient == "principal-1",
    }

    job_ids = scheduler.enqueue_digest(**kwargs)
    repeated_ids = scheduler.enqueue_digest(**kwargs)

    assert job_ids == repeated_ids
    assert len(job_ids) == 2
    assert len(queue.pending) == 2
    assert [job.payload["delivery_class"] for job in queue.pending] == ["urgent", "routine"]
    namespaces = WorkspaceNamespaces("workspace-a")
    assert queue.pending[0].namespace == namespaces.urgent_notification_jobs
    assert queue.pending[1].namespace == namespaces.notification_jobs
    assert acl_calls.count(("workspace-a", "principal-1", "mail-stream")) == 2


def test_urgent_delivery_claim_precedes_older_routine_job() -> None:
    queue = FakeQueue()
    scheduler = _scheduler(queue)
    common = {
        "recipient_id": "principal-1",
        "authorize_recipient": lambda *_: True,
        "authorize_source": lambda *_: True,
    }
    scheduler.enqueue_digest(
        **common,
        digest=build_notification_digest(
            [_event("routine-old")],
            workspace_id="workspace-a",
            window_start=START,
            window_end=END,
            authorize_source=lambda *_: True,
        ),
    )
    scheduler.enqueue_digest(
        **common,
        digest=build_notification_digest(
            [_event("urgent-new", severity="critical")],
            workspace_id="workspace-a",
            window_start=START,
            window_end=END,
            authorize_source=lambda *_: True,
        ),
    )

    adapter = FakeDelivery()
    outcomes = scheduler.process_pending_jobs(
        workspace_id="workspace-a",
        adapter=adapter,
        authorize_recipient=lambda *_: True,
        authorize_source=lambda *_: True,
    )

    assert [outcome.status for outcome in outcomes] == ["delivered", "delivered"]
    assert [call[2] for call in adapter.calls] == [("urgent-new",), ("routine-old",)]


def test_pending_queue_coalescing_does_not_overwrite_sibling_deliveries() -> None:
    queue = FakeQueue()
    scheduler = _scheduler(queue)
    digest = build_notification_digest(
        [
            _event("urgent-a", severity="critical"),
            _event("urgent-b", severity="high"),
            _event("urgent-c", severity="critical"),
            _event("routine-a"),
            _event("routine-b"),
        ],
        workspace_id="workspace-a",
        window_start=START,
        window_end=END,
        authorize_source=lambda *_: True,
    )

    job_ids = scheduler.enqueue_digest(
        recipient_id="principal-1",
        digest=digest,
        authorize_recipient=lambda *_: True,
        authorize_source=lambda *_: True,
    )

    assert len(job_ids) == 4
    assert len(queue.pending) == 4
    assert len({(job.entity_kind, job.entity_id, job.job_kind) for job in queue.pending}) == 4
    assert [job.payload["delivery_class"] for job in queue.pending] == [
        "urgent",
        "urgent",
        "urgent",
        "routine",
    ]


def test_digest_enqueue_is_atomic_and_full_batch_can_be_retried(
    namespace_engines, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = f"notification-atomic-{uuid4()}"
    digest = build_notification_digest(
        [
            _event("urgent-a", severity="critical", source_id="urgent-a", workspace_id=workspace),
            _event("urgent-b", severity="high", source_id="urgent-b", workspace_id=workspace),
            _event("routine", source_id="routine", workspace_id=workspace),
        ],
        workspace_id=workspace,
        window_start=START,
        window_end=END,
        authorize_source=lambda *_: True,
    )
    scheduler = NotificationDeliveryScheduler(engines=namespace_engines)
    queue = namespace_engines.conversation.jobs
    namespaces = WorkspaceNamespaces(workspace)
    original_enqueue = queue.enqueue
    enqueue_count = 0

    def fail_second_enqueue(**kwargs):
        nonlocal enqueue_count
        enqueue_count += 1
        if enqueue_count == 2:
            raise RuntimeError("injected queue failure")
        return original_enqueue(**kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(queue, "enqueue", fail_second_enqueue)
        with pytest.raises(RuntimeError, match="injected queue failure"):
            scheduler.enqueue_digest(
                recipient_id="principal-1",
                digest=digest,
                authorize_recipient=lambda *_: True,
                authorize_source=lambda *_: True,
            )

    assert queue.list(namespace=namespaces.notification_jobs, status="PENDING") == []
    assert queue.list(namespace=namespaces.urgent_notification_jobs, status="PENDING") == []

    job_ids = scheduler.enqueue_digest(
        recipient_id="principal-1",
        digest=digest,
        authorize_recipient=lambda *_: True,
        authorize_source=lambda *_: True,
    )
    assert len(job_ids) == 3
    assert len(queue.list(namespace=namespaces.notification_jobs, status="PENDING")) == 1
    assert len(queue.list(namespace=namespaces.urgent_notification_jobs, status="PENDING")) == 2


def test_delivery_rechecks_acl_and_uses_stable_idempotency_key() -> None:
    queue = FakeQueue()
    scheduler = _scheduler(queue)
    digest = _digest()
    scheduler.enqueue_digest(
        recipient_id="principal-1",
        digest=digest,
        authorize_recipient=lambda *_: True,
        authorize_source=lambda *_: True,
    )
    adapter = FakeDelivery()
    source_checks: list[tuple[str, str]] = []

    outcomes = scheduler.process_pending_jobs(
        workspace_id="workspace-a",
        adapter=adapter,
        authorize_recipient=lambda *_: True,
        authorize_source=lambda _workspace, principal, source: source_checks.append(
            (principal, source)
        ) is None,
    )

    assert [outcome.status for outcome in outcomes] == ["delivered", "delivered"]
    assert queue.done == [adapter_key for _recipient, adapter_key, _events in adapter.calls]
    assert len(source_checks) == 2
    assert all(principal == "principal-1" for principal, _source in source_checks)
    assert all(recipient == "principal-1" for recipient, _key, _events in adapter.calls)
    assert {event for _recipient, _key, events in adapter.calls for event in events} == {
        "routine-1",
        "routine-2",
        "urgent-1",
    }


def test_delivery_reads_queued_digest_payloads_without_new_copy_counts() -> None:
    queue = FakeQueue()
    scheduler = _scheduler(queue)
    scheduler.enqueue_digest(
        recipient_id="principal-1",
        digest=_digest(),
        authorize_recipient=lambda *_: True,
        authorize_source=lambda *_: True,
    )
    for index, job in enumerate(queue.pending):
        digest = dict(job.payload["digest"])
        for section in ("routine", "urgent"):
            digest[section] = [
                {
                    key: value
                    for key, value in item.items()
                    if key not in {"logical_event_count", "duplicate_copy_count"}
                }
                for item in digest[section]
            ]
        queue.pending[index] = replace(job, payload={**job.payload, "digest": digest})

    adapter = FakeDelivery()
    outcomes = scheduler.process_pending_jobs(
        workspace_id="workspace-a",
        adapter=adapter,
        authorize_recipient=lambda *_: True,
        authorize_source=lambda *_: True,
    )

    assert [outcome.status for outcome in outcomes] == ["delivered", "delivered"]
    assert len(adapter.calls) == 2


def test_revoked_source_prevents_delivery_and_finally_fails_job() -> None:
    queue = FakeQueue()
    scheduler = _scheduler(queue)
    scheduler.enqueue_digest(
        recipient_id="principal-1",
        digest=_digest(),
        authorize_recipient=lambda *_: True,
        authorize_source=lambda *_: True,
    )
    adapter = FakeDelivery()

    outcomes = scheduler.process_pending_jobs(
        workspace_id="workspace-a",
        adapter=adapter,
        authorize_recipient=lambda *_: True,
        authorize_source=lambda _workspace, _recipient, source: source != "mail-stream",
    )

    assert not adapter.calls
    assert [outcome.status for outcome in outcomes] == ["denied", "denied"]
    assert len(queue.failed) == 2
    assert not queue.retried


def test_source_authorization_is_recipient_scoped_at_enqueue_and_delivery() -> None:
    queue = FakeQueue()
    scheduler = _scheduler(queue)
    checks: list[tuple[str, str, str]] = []

    def authorize_source(workspace: str, recipient: str, source: str) -> bool:
        checks.append((workspace, recipient, source))
        return recipient == "principal-1" and source == "mail-stream"

    scheduler.enqueue_digest(
        recipient_id="principal-1",
        digest=_digest(),
        authorize_recipient=lambda *_: True,
        authorize_source=authorize_source,
    )
    adapter = FakeDelivery()
    outcomes = scheduler.process_pending_jobs(
        workspace_id="workspace-a",
        adapter=adapter,
        authorize_recipient=lambda *_: True,
        authorize_source=authorize_source,
    )

    assert checks
    assert set(checks) == {("workspace-a", "principal-1", "mail-stream")}
    assert all(outcome.status == "delivered" for outcome in outcomes)


def test_recipient_acl_is_required_at_enqueue_and_rechecked_before_send() -> None:
    queue = FakeQueue()
    scheduler = _scheduler(queue)
    with pytest.raises(PermissionError, match="recipient is not authorized"):
        scheduler.enqueue_digest(
            recipient_id="principal-1",
            digest=_digest(),
            authorize_recipient=lambda *_: False,
            authorize_source=lambda *_: True,
        )
    assert not queue.pending

    scheduler.enqueue_digest(
        recipient_id="principal-1",
        digest=_digest(),
        authorize_recipient=lambda *_: True,
        authorize_source=lambda *_: True,
    )
    adapter = FakeDelivery()
    outcomes = scheduler.process_pending_jobs(
        workspace_id="workspace-a",
        adapter=adapter,
        authorize_recipient=lambda *_: False,
        authorize_source=lambda *_: True,
    )

    assert [outcome.status for outcome in outcomes] == ["denied", "denied"]
    assert len(queue.failed) == 2
    assert not adapter.calls


def test_transient_delivery_failure_uses_existing_queue_retry_path() -> None:
    queue = FakeQueue()
    scheduler = _scheduler(queue)
    scheduler.enqueue_digest(
        recipient_id="principal-1",
        digest=_digest(),
        authorize_recipient=lambda *_: True,
        authorize_source=lambda *_: True,
    )

    outcomes = scheduler.process_pending_jobs(
        workspace_id="workspace-a",
        adapter=FakeDelivery(fail=True),
        authorize_recipient=lambda *_: True,
        authorize_source=lambda *_: True,
    )

    assert [outcome.status for outcome in outcomes] == ["retrying", "retrying"]
    assert len(queue.retried) == 2
    assert not queue.done


def test_workspace_and_queue_identity_are_checked_before_delivery() -> None:
    queue = FakeQueue()
    scheduler = _scheduler(queue)
    scheduler.enqueue_digest(
        recipient_id="principal-1",
        digest=_digest(),
        authorize_recipient=lambda *_: True,
        authorize_source=lambda *_: True,
    )
    queue.pending[0] = replace(
        queue.pending[0],
        payload={**queue.pending[0].payload, "workspace_id": "workspace-b"},
    )
    adapter = FakeDelivery()

    outcomes = scheduler.process_pending_jobs(
        workspace_id="workspace-a",
        adapter=adapter,
        authorize_recipient=lambda *_: True,
        authorize_source=lambda *_: True,
    )

    assert outcomes[0].status == "failed"
    assert outcomes[0].error_code == "invalid_job"
    assert len(adapter.calls) == 1
    assert adapter.calls[0][2] == ("routine-1", "routine-2")
    assert "urgent-1" not in adapter.calls[0][2]
    assert len(queue.failed) == 1


def test_oversized_digest_fails_before_any_partial_enqueue() -> None:
    queue = FakeQueue()
    digest = build_notification_digest(
        [_event("large", title="x" * (256 * 1024))],
        workspace_id="workspace-a",
        window_start=START,
        window_end=END,
        authorize_source=lambda *_: True,
    )

    with pytest.raises(ValueError, match="safety bound"):
        _scheduler(queue).enqueue_digest(
            recipient_id="principal-1",
            digest=digest,
            authorize_recipient=lambda *_: True,
            authorize_source=lambda *_: True,
        )

    assert not queue.pending


def test_routine_alert_storm_becomes_one_compact_delivery_with_full_drilldown() -> None:
    events = [
        replace(
            _event(f"routine-{index}", title=f"Update {index}"),
            dedupe_key=f"group-{index}",
            action_required=index == 3,
        )
        for index in range(8)
    ]
    digest = build_notification_digest(
        events,
        workspace_id="workspace-a",
        window_start=START,
        window_end=END,
        authorize_source=lambda *_: True,
    )
    queue = FakeQueue()
    scheduler = _scheduler(queue)

    first_ids = scheduler.enqueue_digest(
        recipient_id="principal-1",
        digest=digest,
        authorize_recipient=lambda *_: True,
        authorize_source=lambda *_: True,
    )
    repeated_ids = scheduler.enqueue_digest(
        recipient_id="principal-1",
        digest=digest,
        authorize_recipient=lambda *_: True,
        authorize_source=lambda *_: True,
    )

    assert first_ids == repeated_ids
    assert len(queue.pending) == 1
    routine = queue.pending[0].payload["digest"]["routine"]
    assert len(routine) == 1
    assert routine[0]["summary"] == "8 routine updates across 8 groups; some require action"
    assert routine[0]["action_required"] is True
    assert routine[0]["source_event_ids"] == [f"routine-{index}" for index in range(8)]
    assert len(routine[0]["evidence_refs"]) == 8


def test_compact_alert_storm_counts_logical_updates_not_duplicate_copies() -> None:
    copy_a = replace(
        _event("copy-a", title="Release notice"),
        dedupe_key="copy:release-1",
        duplicate_key="copy:release-1",
    )
    copy_b = replace(
        _event("copy-b", source_id="mail-stream-secondary", title="Release notice"),
        dedupe_key="copy:release-1",
        duplicate_key="copy:release-1",
    )
    events = [copy_a, copy_b] + [
        replace(_event(f"routine-{index}", title=f"Routine {index}"), dedupe_key=f"group-{index}")
        for index in range(5)
    ]
    digest = build_notification_digest(
        events,
        workspace_id="workspace-a",
        window_start=START,
        window_end=END,
        authorize_source=lambda *_: True,
    )
    queue = FakeQueue()
    scheduler = _scheduler(queue)
    scheduler.enqueue_digest(
        recipient_id="principal-1",
        digest=digest,
        authorize_recipient=lambda *_: True,
        authorize_source=lambda *_: True,
    )

    compact = queue.pending[0].payload["digest"]["routine"][0]
    assert compact["summary"] == "6 routine updates across 6 groups; 1 duplicate copy suppressed"
    assert compact["logical_event_count"] == 6
    assert compact["duplicate_copy_count"] == 1
    assert len(compact["source_event_ids"]) == 7


def test_acl_service_outage_retries_without_delivery() -> None:
    queue = FakeQueue()
    scheduler = _scheduler(queue)
    scheduler.enqueue_digest(
        recipient_id="principal-1",
        digest=_digest(),
        authorize_recipient=lambda *_: True,
        authorize_source=lambda *_: True,
    )
    adapter = FakeDelivery()

    def unavailable(_workspace: str, _recipient: str, _source: str) -> bool:
        raise RuntimeError("acl store unavailable")

    outcomes = scheduler.process_pending_jobs(
        workspace_id="workspace-a",
        adapter=adapter,
        authorize_recipient=lambda *_: True,
        authorize_source=unavailable,
    )

    assert [outcome.status for outcome in outcomes] == ["retrying", "retrying"]
    assert all(outcome.error_code == "authorization_unavailable" for outcome in outcomes)
    assert len(queue.retried) == 2
    assert not adapter.calls


def test_notification_delivery_uses_real_kogwistar_durable_queue(namespace_engines) -> None:
    workspace_id = f"notification-test-{uuid4().hex}"
    start = datetime.now(UTC) - timedelta(minutes=1)
    digest = build_notification_digest(
        [
            NotificationEvent(
                event_id=event_id,
                workspace_id=workspace_id,
                source_id="mail-stream",
                source_revision_id=f"revision-{event_id}",
                occurred_at=start + timedelta(seconds=10),
                title=title,
                severity=severity,
            )
            for event_id, title, severity in (
                ("event-r1", "New message", "normal"),
                ("event-r2", "New message", "normal"),
                ("event-u1", "Approval required", "high"),
                ("event-u2", "Account suspended", "critical"),
            )
        ],
        workspace_id=workspace_id,
        window_start=start,
        window_end=start + timedelta(minutes=1),
        authorize_source=lambda *_: True,
    )
    scheduler = NotificationDeliveryScheduler(engines=namespace_engines)

    job_ids = scheduler.enqueue_digest(
        recipient_id="principal-1",
        digest=digest,
        authorize_recipient=lambda *_: True,
        authorize_source=lambda *_: True,
    )
    namespaces = WorkspaceNamespaces(workspace_id)
    pending_jobs = namespace_engines.conversation.jobs.list(
        namespace=namespaces.notification_jobs, status="PENDING", limit=10
    ) + namespace_engines.conversation.jobs.list(
        namespace=namespaces.urgent_notification_jobs, status="PENDING", limit=10
    )
    outcomes = scheduler.process_pending_jobs(
        workspace_id=workspace_id,
        adapter=FakeDelivery(),
        authorize_recipient=lambda *_: True,
        authorize_source=lambda *_: True,
    )

    assert len(job_ids) == 3
    assert len(pending_jobs) == 3
    assert sum(job.namespace == namespaces.urgent_notification_jobs for job in pending_jobs) == 2
    assert len({(job.entity_kind, job.entity_id, job.job_kind) for job in pending_jobs}) == 3
    assert [outcome.job_id for outcome in outcomes] == list(job_ids)
    assert [outcome.status for outcome in outcomes] == ["delivered"] * 3


def test_real_queue_claims_new_urgent_before_older_routine(namespace_engines) -> None:
    workspace_id = f"notification-priority-{uuid4().hex}"
    scheduler = NotificationDeliveryScheduler(engines=namespace_engines)
    def authorize(*_):
        return True
    for event_id, severity in (("routine-old", "normal"), ("urgent-new", "critical")):
        digest = build_notification_digest(
            [_event(event_id, severity=severity, workspace_id=workspace_id)],
            workspace_id=workspace_id,
            window_start=START,
            window_end=END,
            authorize_source=authorize,
        )
        scheduler.enqueue_digest(
            recipient_id="principal-1",
            digest=digest,
            authorize_recipient=authorize,
            authorize_source=authorize,
        )

    adapter = FakeDelivery()
    outcomes = scheduler.process_pending_jobs(
        workspace_id=workspace_id,
        adapter=adapter,
        authorize_recipient=authorize,
        authorize_source=authorize,
    )

    assert [outcome.status for outcome in outcomes] == ["delivered", "delivered"]
    assert [call[2] for call in adapter.calls] == [("urgent-new",), ("routine-old",)]
