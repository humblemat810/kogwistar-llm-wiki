"""Long-running daemon loops for background workers.

Usage (foreground, blocking):
    python -m kogwistar_llm_wiki daemon projection --workspace demo --vault /path/to/vault
    python -m kogwistar_llm_wiki daemon maintenance --workspace demo

Both daemons can also be imported and embedded in any host process:

    from kogwistar_llm_wiki.daemon import ProjectionDaemon, MaintenanceDaemon

Design notes
------------
- Each daemon is a single-threaded polling loop with configurable sleep.
- They share the caller-provided ``NamespaceEngines``; no daemon-internal
  engine construction. The caller owns engine lifecycle.
- ``stop()`` is thread-safe (sets a threading.Event) so a signal handler or
  supervisor thread can gracefully shut down the loop.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
import socket
import threading
import time
from typing import Optional
import uuid

from kogwistar.engine_core import (
    OutputReconciliationState,
    RecoveryReport,
    RecoverySurface,
)

from .models import NamespaceEngines
from .namespaces import WorkspaceNamespaces
from .projection_worker import ProjectionWorker
from .worker import MaintenanceWorker

logger = logging.getLogger(__name__)


def _host_name() -> str | None:
    try:
        return socket.gethostname()
    except Exception:
        return None


def _service_id(workspace_id: str, service_kind: str) -> str:
    return f"kogwistar-llm-wiki:{workspace_id}:{service_kind}"


def _declare_service_health(
    engines: NamespaceEngines,
    *,
    workspace_id: str,
    service_kind: str,
    instance_id: str,
    deterministic: bool,
    llm_assisted: bool,
    operator_tags: list[str],
    status: str = "starting",
) -> None:
    conversation = getattr(engines, "conversation", None)
    registry = getattr(conversation, "service_health", None)
    if registry is None:
        return
    service_id = _service_id(workspace_id, service_kind)
    registry.declare_service(
        service_id=service_id,
        service_kind=service_kind,
        owner_app="kogwistar-llm-wiki",
        deterministic=deterministic,
        llm_assisted=llm_assisted,
        workspace_id=workspace_id,
        namespace=str(getattr(engines.conversation, "namespace", "conversation") or "conversation"),
        version="1",
        config_metadata={"workspace_id": workspace_id},
        operator_tags=operator_tags,
    )
    registry.start_instance(
        service_id=service_id,
        workspace_id=workspace_id,
        namespace=str(getattr(engines.conversation, "namespace", "conversation") or "conversation"),
        instance_id=instance_id,
        status=status,
        host=_host_name(),
        pid=os.getpid(),
    )


def _heartbeat_service_health(
    engines: NamespaceEngines,
    *,
    workspace_id: str,
    service_kind: str,
    instance_id: str,
    status: str = "healthy",
    last_error: str | None = None,
) -> None:
    conversation = getattr(engines, "conversation", None)
    registry = getattr(conversation, "service_health", None)
    if registry is None:
        return
    try:
        registry.heartbeat(
            service_id=_service_id(workspace_id, service_kind),
            workspace_id=workspace_id,
            namespace=str(getattr(engines.conversation, "namespace", "conversation") or "conversation"),
            instance_id=instance_id,
            status=status,
            last_error=last_error,
            host=_host_name(),
            pid=os.getpid(),
        )
    except KeyError:
        return


def _stop_service_health(
    engines: NamespaceEngines,
    *,
    workspace_id: str,
    service_kind: str,
    instance_id: str,
) -> None:
    conversation = getattr(engines, "conversation", None)
    registry = getattr(conversation, "service_health", None)
    if registry is None:
        return
    try:
        registry.stop_service(
            service_id=_service_id(workspace_id, service_kind),
            workspace_id=workspace_id,
            namespace=str(getattr(engines.conversation, "namespace", "conversation") or "conversation"),
            instance_id=instance_id,
            status="stopped",
        )
    except KeyError:
        return


def _log_startup_recovery(prefix: str, result: RecoveryReport) -> None:
    repaired = ", ".join(
        f"{item.namespace}:repaired={item.repaired_count}/scanned={item.scanned_count}"
        for item in result.repaired_lane_projections
    )
    logger.info(
        "%s startup recovery finished - workspace=%s repaired=%s scanned=%s "
        "queues=%s lanes=%s checkpoints=%s runs=%s dead_letters=%s findings=%s details=[%s]",
        prefix,
        result.workspace_id,
        result.repaired_count,
        result.scanned_count,
        len(result.queues),
        len(result.lane_rows),
        len(result.checkpoints),
        len(result.run_history),
        len(result.dead_letters),
        len(result.findings),
        repaired,
    )


def _startup_namespaces(
    engines: NamespaceEngines,
    workspace_id: str,
    *,
    include_maintenance: bool,
    include_projection: bool,
) -> list[str]:
    ns = WorkspaceNamespaces(workspace_id)
    candidates = [
        str(getattr(engines.conversation, "namespace", "conversation") or "conversation"),
        ns.conv_bg,
        ns.conv_fg,
    ]
    if include_maintenance:
        candidates.append(ns.maintenance_jobs)
    if include_projection:
        candidates.append(ns.projection_jobs)

    out: list[str] = []
    for namespace in candidates:
        if namespace not in out:
            out.append(namespace)
    return out


def _projection_manifest_surface(
    engines: NamespaceEngines,
    workspace_id: str,
) -> OutputReconciliationState:
    ns = WorkspaceNamespaces(workspace_id)
    get_projection = getattr(engines.conversation.meta_sqlite, "get_named_projection", None)
    row = get_projection(ns.projection_manifest, workspace_id) if callable(get_projection) else None
    payload = row.get("payload") if isinstance(row, dict) else {}
    payload = payload if isinstance(payload, dict) else {}
    status = str(
        (row or {}).get("materialization_status")
        or payload.get("status")
        or ("missing" if row is None else "unknown")
    )
    projected_ids = payload.get("projected_ids")
    return OutputReconciliationState(
        surface_id=f"{workspace_id}:projection_manifest",
        surface_kind="projection_manifest",
        status=status,
        observed_version=str((row or {}).get("projection_schema_version") or "")
        or None,
        drift_detected=status in {"missing", "failed", "error"},
        namespace=ns.projection_manifest,
        details={
            "workspace_id": workspace_id,
            "projected_count": len(projected_ids) if isinstance(projected_ids, list) else 0,
        },
    )


def _vault_surface(workspace_id: str, vault_root: str | None) -> RecoverySurface:
    if not vault_root:
        return RecoverySurface(
            surface_id=f"{workspace_id}:vault",
            surface_kind="vault_materialization",
            status="not_configured",
            details={"workspace_id": workspace_id},
        )
    root = Path(vault_root)
    return RecoverySurface(
        surface_id=f"{workspace_id}:vault",
        surface_kind="vault_materialization",
        status="present" if root.exists() else "missing",
        details={"workspace_id": workspace_id, "vault_root": str(root)},
    )


def _daemon_surface(daemon_id: str) -> RecoverySurface:
    return RecoverySurface(
        surface_id=daemon_id,
        surface_kind="daemon_health",
        status="starting",
        details={
            "desired_state": "running",
            "observed_state": "starting",
            "last_heartbeat_at": None,
            "restart_count": None,
        },
    )


def _core_startup_recovery(
    engines: NamespaceEngines,
    workspace_id: str,
    *,
    daemon_id: str,
    include_maintenance: bool,
    include_projection: bool,
    vault_root: str | None = None,
) -> RecoveryReport:
    app_surfaces: list[RecoverySurface | OutputReconciliationState] = []
    if getattr(getattr(engines, "conversation", None), "service_health", None) is None:
        app_surfaces.append(_daemon_surface(daemon_id))
    if include_projection:
        app_surfaces.append(_projection_manifest_surface(engines, workspace_id))
        app_surfaces.append(_vault_surface(workspace_id, vault_root))
    return engines.conversation.recovery.recover_startup(
        workspace_id=workspace_id,
        namespaces=_startup_namespaces(
            engines,
            workspace_id,
            include_maintenance=include_maintenance,
            include_projection=include_projection,
        ),
        app_surfaces=app_surfaces,
    )


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


class MaintenanceDaemon:
    """Polls and drains the maintenance job queue for one workspace."""

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
        self._instance_id = f"maintenance-{uuid.uuid4().hex}"

    def stop(self) -> None:
        """Signal the daemon to exit after the current poll cycle."""
        self._stop_event.set()

    def recover_startup_state(self) -> RecoveryReport:
        _declare_service_health(
            self.engines,
            workspace_id=self.workspace_id,
            service_kind="maintenance_daemon",
            instance_id=self._instance_id,
            deterministic=False,
            llm_assisted=True,
            operator_tags=["maintenance", "distillation", "execution_wisdom"],
            status="starting",
        )
        return _core_startup_recovery(
            self.engines,
            self.workspace_id,
            daemon_id="maintenance-daemon",
            include_maintenance=True,
            include_projection=False,
        )

    def run(self) -> None:
        """Block and poll until ``stop()`` is called."""
        logger.info(
            "MaintenanceDaemon started - workspace=%s interval=%.1fs",
            self.workspace_id,
            self.poll_interval,
        )
        _log_startup_recovery("MaintenanceDaemon", self.recover_startup_state())
        while not self._stop_event.is_set():
            try:
                _heartbeat_service_health(
                    self.engines,
                    workspace_id=self.workspace_id,
                    service_kind="maintenance_daemon",
                    instance_id=self._instance_id,
                )
                self._worker.process_pending_jobs(self.workspace_id)
            except Exception as exc:
                _heartbeat_service_health(
                    self.engines,
                    workspace_id=self.workspace_id,
                    service_kind="maintenance_daemon",
                    instance_id=self._instance_id,
                    status="failed",
                    last_error=f"{type(exc).__name__}: {exc}",
                )
                logger.exception("MaintenanceDaemon: unhandled error in poll cycle")
            self._stop_event.wait(timeout=self.poll_interval)
        _stop_service_health(
            self.engines,
            workspace_id=self.workspace_id,
            service_kind="maintenance_daemon",
            instance_id=self._instance_id,
        )
        logger.info("MaintenanceDaemon stopped - workspace=%s", self.workspace_id)


__all__ = ["MaintenanceDaemon", "ProjectionDaemon"]
