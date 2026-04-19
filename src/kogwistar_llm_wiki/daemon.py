"""Long-running daemon loops for background workers.

Usage (foreground, blocking):
    python -m kogwistar_llm_wiki daemon projection --workspace demo --vault /path/to/vault
    python -m kogwistar_llm_wiki daemon maintenance --workspace demo

Both daemons can also be imported and embedded in any host process:

    from kogwistar_llm_wiki.daemon import ProjectionDaemon, MaintenanceDaemon

Design notes
------------
- Each daemon is a single-threaded polling loop with configurable sleep.
- They share the caller-provided ``NamespaceEngines`` — no daemon-internal
  engine construction.  The caller owns engine lifecycle.
- ``stop()`` is thread-safe (sets a threading.Event) so a signal handler or
  supervisor thread can gracefully shut down the loop.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from .models import NamespaceEngines
from .projection_worker import ProjectionWorker
from .worker import MaintenanceWorker

logger = logging.getLogger(__name__)


class ProjectionDaemon:
    """Polls and drains the Obsidian projection queue for one workspace.

    Parameters
    ----------
    engines:
        Shared ``NamespaceEngines`` bundle (caller owns lifecycle).
    workspace_id:
        The workspace this daemon is responsible for.
    vault_root:
        Filesystem path to the Obsidian vault root.
    poll_interval:
        Seconds to sleep between poll cycles when the queue is empty.
    """

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

    def stop(self) -> None:
        """Signal the daemon to exit after the current poll cycle."""
        self._stop_event.set()

    def run(self) -> None:
        """Block and poll until ``stop()`` is called."""
        logger.info(
            "ProjectionDaemon started — workspace=%s vault=%s interval=%.1fs",
            self.workspace_id,
            self.vault_root,
            self.poll_interval,
        )
        while not self._stop_event.is_set():
            try:
                self._worker.process_pending_projections(
                    self.workspace_id, self.vault_root
                )
            except Exception:
                logger.exception("ProjectionDaemon: unhandled error in poll cycle")
            self._stop_event.wait(timeout=self.poll_interval)
        logger.info("ProjectionDaemon stopped — workspace=%s", self.workspace_id)


class MaintenanceDaemon:
    """Polls and drains the maintenance job queue for one workspace.

    Parameters
    ----------
    engines:
        Shared ``NamespaceEngines`` bundle (caller owns lifecycle).
    workspace_id:
        The workspace this daemon is responsible for.
    poll_interval:
        Seconds to sleep between poll cycles when the queue is empty.
    """

    def __init__(
        self,
        engines: NamespaceEngines,
        workspace_id: str,
        poll_interval: float = 10.0,
    ) -> None:
        self.engines = engines
        self.workspace_id = workspace_id
        self.poll_interval = poll_interval
        self._worker = MaintenanceWorker(engines)
        self._stop_event = threading.Event()

    def stop(self) -> None:
        """Signal the daemon to exit after the current poll cycle."""
        self._stop_event.set()

    def run(self) -> None:
        """Block and poll until ``stop()`` is called."""
        logger.info(
            "MaintenanceDaemon started — workspace=%s interval=%.1fs",
            self.workspace_id,
            self.poll_interval,
        )
        while not self._stop_event.is_set():
            try:
                self._worker.process_pending_jobs(self.workspace_id)
            except Exception:
                logger.exception("MaintenanceDaemon: unhandled error in poll cycle")
            self._stop_event.wait(timeout=self.poll_interval)
        logger.info("MaintenanceDaemon stopped — workspace=%s", self.workspace_id)
