"""Projection daemon lifecycle for one workspace."""

from __future__ import annotations

import logging
import threading
import uuid

from kogwistar.engine_core import RecoveryReport

from ..models import NamespaceEngines
from ..projections.worker_impl import ProjectionWorker
from .runtime_support import (
    _core_startup_recovery,
    _declare_service_health,
    _heartbeat_service_health,
    _log_startup_recovery,
    _stop_service_health,
)

logger = logging.getLogger(__name__)


class ProjectionDaemon:
    """Polls and drains the Obsidian projection queue for one workspace."""

    def __init__(
        self,
        engines: NamespaceEngines,
        workspace_id: str,
        vault_root: str,
        poll_interval: float = 5.0,
    ) -> None:
        self.engines = engines
        self.workspace_id = workspace_id
        self.vault_root = vault_root
        self.poll_interval = poll_interval
        self._worker = ProjectionWorker(engines)
        self._stop_event = threading.Event()
        self._instance_id = f"projection-{uuid.uuid4().hex}"

    def stop(self) -> None:
        """Signal the daemon to exit after the current poll cycle."""
        self._stop_event.set()

    def recover_startup_state(self) -> RecoveryReport:
        _declare_service_health(
            self.engines,
            workspace_id=self.workspace_id,
            service_kind="projection_daemon",
            instance_id=self._instance_id,
            deterministic=True,
            llm_assisted=False,
            operator_tags=["projection", "obsidian", "manifest"],
            status="starting",
        )
        return _core_startup_recovery(
            self.engines,
            self.workspace_id,
            daemon_id="projection-daemon",
            include_maintenance=False,
            include_projection=True,
            vault_root=self.vault_root,
        )

    def run(self) -> None:
        """Block and poll until ``stop()`` is called."""
        logger.info(
            "ProjectionDaemon started - workspace=%s vault=%s interval=%.1fs",
            self.workspace_id,
            self.vault_root,
            self.poll_interval,
        )
        _log_startup_recovery("ProjectionDaemon", self.recover_startup_state())
        while not self._stop_event.is_set():
            try:
                _heartbeat_service_health(
                    self.engines,
                    workspace_id=self.workspace_id,
                    service_kind="projection_daemon",
                    instance_id=self._instance_id,
                )
                self._worker.process_pending_projections(
                    self.workspace_id, self.vault_root
                )
            except Exception as exc:
                _heartbeat_service_health(
                    self.engines,
                    workspace_id=self.workspace_id,
                    service_kind="projection_daemon",
                    instance_id=self._instance_id,
                    status="failed",
                    last_error=f"{type(exc).__name__}: {exc}",
                )
                logger.exception("ProjectionDaemon: unhandled error in poll cycle")
            self._stop_event.wait(timeout=self.poll_interval)
        _stop_service_health(
            self.engines,
            workspace_id=self.workspace_id,
            service_kind="projection_daemon",
            instance_id=self._instance_id,
        )
        logger.info("ProjectionDaemon stopped - workspace=%s", self.workspace_id)


__all__ = ["ProjectionDaemon"]
