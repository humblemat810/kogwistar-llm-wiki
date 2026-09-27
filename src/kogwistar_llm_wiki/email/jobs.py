"""Durable email synchronization jobs over Kogwistar's shared queue."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from kogwistar.engine_core.jobs import JobQueueItem
from kogwistar.id_provider import stable_id

from ..configuration.workspace import WorkspaceNamespaces
from ..models import NamespaceEngines
from .bindings import EmailConnectorBinding, EmailConnectorBindingStore
from .sync import EmailSourceAdapter, EmailSyncResult, EmailSyncService


def _required(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise ValueError(f"{field_name} must not contain control characters")
    return value.strip()


@dataclass(frozen=True, slots=True)
class EmailSyncJobRequest:
    """Non-secret request data persisted in a queue payload."""

    workspace_id: str
    connector_id: str
    cycle_id: str
    owner_id: str
    title_prefix: str = "Email"
    lease_duration_ms: int = 60_000
    max_retries: int = 5

    def __post_init__(self) -> None:
        for name in ("workspace_id", "connector_id", "cycle_id", "owner_id", "title_prefix"):
            object.__setattr__(self, name, _required(getattr(self, name), name))
        if type(self.lease_duration_ms) is not int or self.lease_duration_ms <= 0:
            raise ValueError("lease_duration_ms must be positive")
        if type(self.max_retries) is not int or self.max_retries <= 0:
            raise ValueError("max_retries must be positive")


@dataclass(frozen=True, slots=True)
class EmailSyncJobOutcome:
    job_id: str
    workspace_id: str
    connector_id: str
    status: str
    result: EmailSyncResult | None = None
    error: str | None = None


AdapterFactory = Callable[[EmailConnectorBinding, str], EmailSourceAdapter]


class EmailSyncJobScheduler:
    """Schedule and drain bounded email sync cycles using the shared queue."""

    def __init__(
        self,
        *,
        engines: NamespaceEngines,
        service: EmailSyncService,
        binding_store: EmailConnectorBindingStore,
    ) -> None:
        self.engines = engines
        self.service = service
        self.binding_store = binding_store

    def enqueue(self, request: EmailSyncJobRequest) -> str:
        binding = self.binding_store.get(
            workspace_id=request.workspace_id,
            connector_id=request.connector_id,
        )
        if binding is None:
            raise KeyError(f"email connector binding not found: {request.connector_id}")
        if not binding.enabled:
            raise ValueError("email connector binding is disabled")
        if not self.service.authorize_stream(request.workspace_id, binding.stream_id):
            raise PermissionError("email stream is not authorized for workspace")
        jobs = self.engines.conversation.jobs
        jobs.require_available(enqueue=True)
        job_id = str(
            stable_id(
                "kogwistar_llm_wiki.email_sync_job",
                request.workspace_id,
                request.connector_id,
                request.cycle_id,
            )
        )
        jobs.enqueue(
            job_id=job_id,
            namespace=WorkspaceNamespaces(request.workspace_id).email_sync_jobs,
            entity_kind="email_connector_binding",
            entity_id=binding.connector_id,
            job_kind="email_sync",
            op="SYNC",
            max_retries=request.max_retries,
            payload={
                "job_type": "email_sync",
                "workspace_id": request.workspace_id,
                "connector_id": request.connector_id,
                "cycle_id": request.cycle_id,
                "owner_id": request.owner_id,
                "title_prefix": request.title_prefix,
                "lease_duration_ms": request.lease_duration_ms,
            },
        )
        return job_id

    def process_pending_jobs(
        self,
        *,
        workspace_id: str,
        adapter_factory: AdapterFactory,
        limit: int = 1,
        queue_lease_seconds: int = 300,
    ) -> tuple[EmailSyncJobOutcome, ...]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        if queue_lease_seconds <= 0:
            raise ValueError("queue_lease_seconds must be positive")
        jobs = self.engines.conversation.jobs
        jobs.require_available(claim=True)
        namespace = WorkspaceNamespaces(workspace_id).email_sync_jobs
        claimed = jobs.claim(
            namespace=namespace,
            limit=limit,
            lease_seconds=queue_lease_seconds,
        )
        outcomes: list[EmailSyncJobOutcome] = []
        for job in claimed:
            request: EmailSyncJobRequest | None = None
            try:
                request = self._request_from_job(job, workspace_id=workspace_id)
                result = self.service.sync_binding(
                    workspace_id=request.workspace_id,
                    connector_id=request.connector_id,
                    owner_id=request.owner_id,
                    adapter_factory=adapter_factory,
                    title_prefix=request.title_prefix,
                    lease_duration_ms=request.lease_duration_ms,
                )
            except Exception as exc:  # noqa: BLE001 - queue owns retry policy
                if request is None:
                    jobs.mark_failed(
                        job.job_id,
                        f"{type(exc).__name__}: {exc}",
                        final=True,
                        claim_token=job.claim_token,
                    )
                    status = "failed"
                else:
                    jobs.retry_or_fail(job, exc)
                    final = int(job.retry_count) + 1 >= int(job.max_retries)
                    status = "failed" if final else "retrying"
                outcomes.append(
                    EmailSyncJobOutcome(
                        job_id=job.job_id,
                        workspace_id=workspace_id,
                        connector_id=(
                            request.connector_id
                            if request is not None
                            else str(job.payload.get("connector_id") or "")
                        ),
                        status=status,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                )
                continue
            acknowledged = jobs.mark_done(job.job_id, claim_token=job.claim_token)
            outcomes.append(
                EmailSyncJobOutcome(
                    job_id=job.job_id,
                    workspace_id=workspace_id,
                    connector_id=request.connector_id,
                    status="completed" if acknowledged else "stale_claim",
                    result=result,
                )
            )
        return tuple(outcomes)

    @staticmethod
    def _request_from_job(job: JobQueueItem, *, workspace_id: str) -> EmailSyncJobRequest:
        payload = job.payload
        if payload.get("job_type") != "email_sync":
            raise ValueError("email sync queue item has an invalid job type")
        stored_workspace = _required(str(payload.get("workspace_id") or ""), "workspace_id")
        if stored_workspace != workspace_id:
            raise ValueError("email sync queue item crosses workspace boundary")
        return EmailSyncJobRequest(
            workspace_id=stored_workspace,
            connector_id=_required(str(payload.get("connector_id") or ""), "connector_id"),
            cycle_id=_required(str(payload.get("cycle_id") or ""), "cycle_id"),
            owner_id=_required(str(payload.get("owner_id") or ""), "owner_id"),
            title_prefix=_required(str(payload.get("title_prefix") or "Email"), "title_prefix"),
            lease_duration_ms=int(payload.get("lease_duration_ms") or 60_000),
        )


__all__ = [
    "AdapterFactory",
    "EmailSyncJobOutcome",
    "EmailSyncJobRequest",
    "EmailSyncJobScheduler",
]
