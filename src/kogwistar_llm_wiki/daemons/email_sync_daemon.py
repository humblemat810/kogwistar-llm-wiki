"""Long-running worker for secret-free email synchronization jobs."""

from __future__ import annotations

import logging
import threading
import uuid

from ..email.jobs import AdapterFactory, EmailSyncJobOutcome, EmailSyncJobScheduler
from .runtime_support import (
    _declare_service_health,
    _heartbeat_service_health,
    _stop_service_health,
)

logger = logging.getLogger(__name__)


class EmailSyncDaemon:
    """Drain one workspace's email queue with an injected local adapter factory.

    Credential resolution stays outside the queue payload and inside the caller's
    adapter factory. This class only owns polling, health reporting, and stop
    behavior; it never reads credentials or constructs an adapter itself.
    """

    def __init__(
        self,
        *,
        scheduler: EmailSyncJobScheduler,
        workspace_id: str,
        adapter_factory: AdapterFactory,
        poll_interval: float = 5.0,
        batch_limit: int = 1,
        queue_lease_seconds: int = 300,
    ) -> None:
        if not str(workspace_id).strip():
            raise ValueError("workspace_id must not be empty")
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        if batch_limit <= 0:
            raise ValueError("batch_limit must be positive")
        if queue_lease_seconds <= 0:
            raise ValueError("queue_lease_seconds must be positive")
        if not callable(adapter_factory):
            raise TypeError("adapter_factory must be callable")
        self.scheduler = scheduler
        self.workspace_id = str(workspace_id).strip()
        self.adapter_factory = adapter_factory
        self.poll_interval = float(poll_interval)
        self.batch_limit = int(batch_limit)
        self.queue_lease_seconds = int(queue_lease_seconds)
        self._stop_event = threading.Event()
        self._instance_id = f"email-sync-{uuid.uuid4().hex}"

    def stop(self) -> None:
        """Signal the daemon to exit after the current poll cycle."""

        self._stop_event.set()

    def poll_once(self) -> tuple[EmailSyncJobOutcome, ...]:
        """Process one bounded batch; useful for supervisors and tests."""

        return self.scheduler.process_pending_jobs(
            workspace_id=self.workspace_id,
            adapter_factory=self.adapter_factory,
            limit=self.batch_limit,
            queue_lease_seconds=self.queue_lease_seconds,
        )

    def run(self) -> None:
        """Block and poll until ``stop()`` is called."""

        engines = self.scheduler.engines
        _declare_service_health(
            engines,
            workspace_id=self.workspace_id,
            service_kind="email_sync_daemon",
            instance_id=self._instance_id,
            deterministic=True,
            llm_assisted=False,
            operator_tags=["email", "sync", "plugin"],
            status="starting",
        )
        logger.info(
            "EmailSyncDaemon started - workspace=%s interval=%.1fs batch=%s",
            self.workspace_id,
            self.poll_interval,
            self.batch_limit,
        )
        try:
            while not self._stop_event.is_set():
                try:
                    _heartbeat_service_health(
                        engines,
                        workspace_id=self.workspace_id,
                        service_kind="email_sync_daemon",
                        instance_id=self._instance_id,
                    )
                    self.poll_once()
                except Exception as exc:
                    _heartbeat_service_health(
                        engines,
                        workspace_id=self.workspace_id,
                        service_kind="email_sync_daemon",
                        instance_id=self._instance_id,
                        status="failed",
                        last_error=f"{type(exc).__name__}: {exc}",
                    )
                    logger.exception("EmailSyncDaemon: unhandled error in poll cycle")
                self._stop_event.wait(timeout=self.poll_interval)
        finally:
            _stop_service_health(
                engines,
                workspace_id=self.workspace_id,
                service_kind="email_sync_daemon",
                instance_id=self._instance_id,
            )
            logger.info("EmailSyncDaemon stopped - workspace=%s", self.workspace_id)


__all__ = ["EmailSyncDaemon"]
