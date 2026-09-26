from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from kogwistar.engine_core.jobs import JobQueueItem

from kogwistar_llm_wiki.daemons.email_sync_daemon import EmailSyncDaemon
from kogwistar_llm_wiki.email import (
    EmailConnectorBinding,
    EmailSyncJobRequest,
    EmailSyncJobScheduler,
    EmailSyncResult,
    InMemoryEmailConnectorBindingStore,
)


class FakeQueue:
    def __init__(self) -> None:
        self.pending: list[JobQueueItem] = []
        self.done: list[str] = []
        self.failed: list[tuple[str, str]] = []
        self.retried: list[str] = []
        self.enqueued_payloads: list[dict[str, object]] = []

    def require_available(self, **_kwargs) -> None:
        return None

    def enqueue(self, **kwargs) -> str:
        payload = dict(kwargs["payload"])
        self.enqueued_payloads.append(payload)
        item = JobQueueItem(
            job_id=str(kwargs["job_id"]),
            namespace=str(kwargs["namespace"]),
            entity_kind=str(kwargs["entity_kind"]),
            entity_id=str(kwargs["entity_id"]),
            job_kind=str(kwargs["job_kind"]),
            op=str(kwargs["op"]),
            payload=payload,
            retry_count=0,
            max_retries=int(kwargs["max_retries"]),
            claim_token=None,
        )
        if not any(existing.job_id == item.job_id for existing in self.pending):
            self.pending.append(item)
        return item.job_id

    def claim(self, *, namespace: str, limit: int, lease_seconds: int) -> list[JobQueueItem]:
        del lease_seconds
        selected = [item for item in self.pending if item.namespace == namespace][:limit]
        self.pending = [item for item in self.pending if item not in selected]
        return [replace(item, claim_token=f"claim:{item.job_id}") for item in selected]

    def mark_done(self, job_id: str, *, claim_token: str | None = None) -> bool:
        assert claim_token is not None
        self.done.append(job_id)
        return True

    def retry_or_fail(self, job: JobQueueItem, error: Exception | str, **_kwargs) -> None:
        self.retried.append(job.job_id)

    def mark_failed(self, job_id: str, error: str, **_kwargs) -> None:
        self.failed.append((job_id, error))


class FakeService:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, str, str]] = []

    def authorize_stream(self, _workspace_id: str, _stream_id: str) -> bool:
        return True

    def sync_binding(self, **kwargs) -> EmailSyncResult:
        self.calls.append(
            (
                str(kwargs["workspace_id"]),
                str(kwargs["connector_id"]),
                str(kwargs["owner_id"]),
            )
        )
        if self.fail:
            raise TimeoutError("mailbox timeout")
        return EmailSyncResult(
            workspace_id=str(kwargs["workspace_id"]),
            stream_id="stream-a",
            ingested_source_revision_ids=(),
            event_kinds=(),
            has_more=False,
            snapshot_committed=True,
        )


def _scheduler(queue: FakeQueue, service: FakeService) -> tuple[EmailSyncJobScheduler, EmailConnectorBinding]:
    bindings = InMemoryEmailConnectorBindingStore()
    binding = bindings.put(
        EmailConnectorBinding(
            tenant_id="tenant-a",
            workspace_id="workspace-a",
            connector_id="connector-a",
            account_principal_id="account-a",
            mailbox_id="mailbox-a",
            credential_ref="secret/email-a",
        )
    )
    engines = SimpleNamespace(conversation=SimpleNamespace(jobs=queue))
    return (
        EmailSyncJobScheduler(
            engines=engines,
            service=service,
            binding_store=bindings,
        ),
        binding,
    )


def test_email_sync_job_is_deterministic_and_secret_free() -> None:
    queue = FakeQueue()
    scheduler, _binding = _scheduler(queue, FakeService())
    request = EmailSyncJobRequest(
        workspace_id="workspace-a",
        connector_id="connector-a",
        cycle_id="cycle-1",
        owner_id="worker-a",
    )

    first = scheduler.enqueue(request)
    second = scheduler.enqueue(request)

    assert first == second
    assert len(queue.enqueued_payloads) == 2
    assert "credential_ref" not in queue.enqueued_payloads[0]
    assert queue.enqueued_payloads[0]["workspace_id"] == "workspace-a"


def test_email_sync_job_processes_and_acknowledges() -> None:
    queue = FakeQueue()
    service = FakeService()
    scheduler, _binding = _scheduler(queue, service)
    scheduler.enqueue(
        EmailSyncJobRequest(
            workspace_id="workspace-a",
            connector_id="connector-a",
            cycle_id="cycle-1",
            owner_id="worker-a",
        )
    )

    outcomes = scheduler.process_pending_jobs(
        workspace_id="workspace-a",
        adapter_factory=lambda _binding, _credential_ref: pytest.fail("not reached"),
    )

    assert outcomes[0].status == "completed"
    assert outcomes[0].result is not None
    assert queue.done == [outcomes[0].job_id]
    assert service.calls == [("workspace-a", "connector-a", "worker-a")]


def test_email_sync_job_retries_transient_failure() -> None:
    queue = FakeQueue()
    scheduler, _binding = _scheduler(queue, FakeService(fail=True))
    scheduler.enqueue(
        EmailSyncJobRequest(
            workspace_id="workspace-a",
            connector_id="connector-a",
            cycle_id="cycle-1",
            owner_id="worker-a",
            max_retries=3,
        )
    )

    outcomes = scheduler.process_pending_jobs(
        workspace_id="workspace-a",
        adapter_factory=lambda _binding, _credential_ref: pytest.fail("not reached"),
    )

    assert outcomes[0].status == "retrying"
    assert queue.retried == [outcomes[0].job_id]
    assert queue.done == []


def test_email_sync_job_rejects_cross_workspace_claim() -> None:
    queue = FakeQueue()
    scheduler, _binding = _scheduler(queue, FakeService())
    queue.pending.append(
        JobQueueItem(
            job_id="malformed",
            namespace="ws:workspace-a:email_sync_jobs",
            entity_kind="email_connector_binding",
            entity_id="connector-a",
            job_kind="email_sync",
            op="SYNC",
            payload={
                "job_type": "email_sync",
                "workspace_id": "workspace-b",
                "connector_id": "connector-a",
                "cycle_id": "cycle-1",
                "owner_id": "worker-a",
            },
            retry_count=0,
            max_retries=3,
            claim_token=None,
        )
    )

    outcomes = scheduler.process_pending_jobs(
        workspace_id="workspace-a",
        adapter_factory=lambda _binding, _credential_ref: pytest.fail("not reached"),
    )

    assert outcomes[0].status == "failed"
    assert queue.failed and queue.failed[0][0] == "malformed"


def test_email_sync_daemon_delegates_one_bounded_poll() -> None:
    calls: list[dict[str, object]] = []

    class Scheduler:
        engines = SimpleNamespace()

        def process_pending_jobs(self, **kwargs):
            calls.append(kwargs)
            return ()

    def adapter_factory(_binding, _credential_ref):
        pytest.fail("not reached")
    daemon = EmailSyncDaemon(
        scheduler=Scheduler(),  # type: ignore[arg-type]
        workspace_id="workspace-a",
        adapter_factory=adapter_factory,
        batch_limit=2,
        queue_lease_seconds=17,
    )

    assert daemon.poll_once() == ()
    assert calls == [
        {
            "workspace_id": "workspace-a",
            "adapter_factory": adapter_factory,
            "limit": 2,
            "queue_lease_seconds": 17,
        }
    ]
