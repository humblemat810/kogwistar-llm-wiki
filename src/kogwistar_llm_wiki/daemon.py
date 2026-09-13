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

import hashlib
import json
import logging
import os
import socket
import threading
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from kogwistar.engine_core import (
    OutputReconciliationState,
    RecoveryReport,
    RecoverySurface,
)

from .maintenance_control import MaintenanceControl, MaintenanceControlState
from .maintenance_selection import select_embedding_exploration
from .models import NamespaceEngines
from .namespaces import WorkspaceNamespaces
from .otel import LlmWikiTelemetry
from .projection_worker import ProjectionWorker
from .provider_config import (
    provider_config_summary,
    resolve_maintenance_provider_settings,
)
from .utils import _temporary_namespace
from .worker import MaintenanceWorker

logger = logging.getLogger(__name__)


def _host_name() -> str | None:
    try:
        return socket.gethostname()
    except OSError:
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
    config_metadata: dict[str, object] | None = None,
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
        config_metadata={"workspace_id": workspace_id, **(config_metadata or {})},
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
    ready_ids = payload.get("ready_projected_ids")
    if not isinstance(ready_ids, list):
        ready_ids = payload.get("projected_ids")
    desired_ids = payload.get("desired_projected_ids")
    failed_ids = payload.get("failed_projected_ids")
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
            "ready_count": len(ready_ids) if isinstance(ready_ids, list) else 0,
            "projected_count": len(ready_ids) if isinstance(ready_ids, list) else 0,
            "desired_count": len(desired_ids) if isinstance(desired_ids, list) else 0,
            "failed_count": len(failed_ids) if isinstance(failed_ids, list) else 0,
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
        *,
        data_dir: str | os.PathLike[str] | None = None,
        background_interval: float = 600.0,
    ) -> None:
        self.engines = engines
        self.workspace_id = workspace_id
        self.poll_interval = poll_interval
        self.background_interval = max(1.0, float(background_interval))
        self.control = MaintenanceControl(data_dir) if data_dir else None
        self.control_state = self.control.get() if self.control else MaintenanceControlState()
        self._background_state_path = (
            Path(data_dir) / "maintenance" / "background_state.json" if data_dir else None
        )
        self._background_state = self._load_background_state()
        self._last_background_cycle_at_ms = int(self._background_state.get("last_cycle_at_ms") or 0)
        self._cycle_number = int(self._background_state.get("cycle_number") or 0)
        self._recent_background_ids = {
            str(item)
            for item in (self._background_state.get("recent_candidate_ids") or [])
            if str(item).strip()
        }
        self.provider_settings = resolve_maintenance_provider_settings()
        self.telemetry = LlmWikiTelemetry.from_environment()
        self._worker = MaintenanceWorker(
            engines,
            provider_settings=self.provider_settings,
            trace_sink=self.telemetry.instrument_event,
        )
        self._stop_event = threading.Event()
        self._instance_id = f"maintenance-{uuid.uuid4().hex}"

    def stop(self) -> None:
        """Signal the daemon to exit after the current poll cycle."""
        self._stop_event.set()

    def _load_background_state(self) -> dict[str, Any]:
        path = getattr(self, "_background_state_path", None)
        if path is None:
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _persist_background_state(
        self,
        *,
        cycle_number: int,
        cycle_seed: int,
        selected_ids: set[str],
        now_ms: int,
    ) -> None:
        path = getattr(self, "_background_state_path", None)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "cycle_number": int(cycle_number),
            "cycle_seed": int(cycle_seed),
            "last_cycle_at_ms": int(now_ms),
            "recent_selection_watermark": int(now_ms),
            "recent_candidate_ids": sorted(selected_ids),
        }
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(path)

    def recover_startup_state(self) -> RecoveryReport:
        _declare_service_health(
            self.engines,
            workspace_id=self.workspace_id,
            service_kind="maintenance_daemon",
            instance_id=self._instance_id,
            deterministic=False,
            llm_assisted=True,
            operator_tags=["maintenance", "distillation", "execution_wisdom"],
            config_metadata={
                "provider_settings": provider_config_summary(self.provider_settings),
                "request_enabled": self.control_state.request_enabled,
                "background_enabled": self.control_state.background_enabled,
                "background_interval_seconds": self.background_interval,
            },
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
        if self.control:
            self.control.serve(self._stop_event)
        while not self._stop_event.is_set():
            try:
                if self.control:
                    self.control_state = self.control.get()
                _heartbeat_service_health(
                    self.engines,
                    workspace_id=self.workspace_id,
                    service_kind="maintenance_daemon",
                    instance_id=self._instance_id,
                )
                self._schedule_background_cycle(self.control_state)
                self._worker.request_enabled = self.control_state.request_enabled
                self._worker.background_enabled = self.control_state.background_enabled
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

    def _schedule_background_cycle(self, state: MaintenanceControlState) -> None:
        """Queue one bounded, auditable background pass when the LLM is free."""
        now_ms = int(time.time() * 1000)
        last_cycle_at_ms = int(getattr(self, "_last_background_cycle_at_ms", 0) or 0)
        if not state.background_enabled or (
            last_cycle_at_ms and now_ms - last_cycle_at_ms < self.background_interval * 1000
        ):
            return
        jobs = self.engines.conversation.jobs
        active = jobs.list(namespace=WorkspaceNamespaces(self.workspace_id).maintenance_jobs, status="DOING", limit=50)
        queued = jobs.list(namespace=WorkspaceNamespaces(self.workspace_id).maintenance_jobs, status="PENDING", limit=50)
        if active or any(
            str(getattr(job, "payload", {}).get("mode") or "request") != "background"
            for job in queued
        ):
            return
        cycle_number = int(getattr(self, "_cycle_number", 0)) + 1
        seed_material = f"{self.workspace_id}:{cycle_number}".encode()
        cycle_seed = int.from_bytes(hashlib.sha256(seed_material).digest()[:8], "big")
        ns = WorkspaceNamespaces(self.workspace_id)
        try:
            with _temporary_namespace(self.engines.kg, ns.curated_kg_space):
                nodes = self.engines.kg.read.get_nodes(limit=500)
        except Exception as exc:  # noqa: BLE001 - backend failures degrade exploration only
            logger.warning("Background maintenance selection degraded: %s", exc)
            nodes = []
        scoped_nodes = []
        for node in nodes:
            metadata = getattr(node, "metadata", {})
            declared_workspace = (
                str(metadata.get("workspace_id") or "").strip()
                if isinstance(metadata, Mapping)
                else ""
            )
            if not declared_workspace or declared_workspace == self.workspace_id:
                scoped_nodes.append(node)
        nodes = scoped_nodes
        recent = sorted(
            (node for node in nodes if getattr(node, "safe_get_id", lambda: "")()),
            key=lambda node: str(getattr(node, "metadata", {}).get("updated_at_ms", "")),
            reverse=True,
        )[:6]
        explored, strategy = select_embedding_exploration(
            nodes,
            dimension=next(
                (
                    len(getattr(node, "embedding", []))
                    for node in nodes
                    if getattr(node, "embedding", None)
                ),
                1,
            ),
            cycle_seed=cycle_seed,
            max_candidates=6,
            excluded_ids={str(node.safe_get_id()) for node in recent}
            | set(getattr(self, "_recent_background_ids", set())),
        )
        selected = [
            {"candidate_id": str(node.safe_get_id()), "reason": "recent_interest", "score": None}
            for node in recent
        ] + [item.as_dict() for item in explored]
        payload = {
            "workspace_id": self.workspace_id,
            "maintenance_kind": "distill",
            "mode": "background",
            "maintenance_origin": "background",
            "selection_strategy": "recent_interest_and_embedding_probe",
            "embedding_exploration": {
                "profile": os.environ.get("KOGWISTAR_LLM_WIKI_EMBED_PROFILE", "unknown"),
                "dimension": len(getattr(nodes[0], "embedding", []) or []) if nodes else None,
                "probe_seed": cycle_seed,
                "strategy": strategy,
                "candidates": [item.as_dict() for item in explored],
            },
            "candidates": selected,
            "candidate_exclusions": sorted({str(node.safe_get_id()) for node in recent}),
            "recent_selection_watermark": int(time.time() * 1000),
            "stop_reason": None,
            "maintenance_round": 0,
            "maintenance_max_rounds": 1,
            "budgets": {"max_steps": 1},
        }
        selected_ids = {
            str(item["candidate_id"])
            for item in selected
            if str(item.get("candidate_id") or "").strip()
        }
        # Reserve the cycle before queueing. A crash may skip a cycle, but it
        # can never reuse a previously committed seed or job identity.
        self._persist_background_state(
            cycle_number=cycle_number,
            cycle_seed=cycle_seed,
            selected_ids=selected_ids,
            now_ms=now_ms,
        )
        self._cycle_number = cycle_number
        self._last_background_cycle_at_ms = now_ms
        self._recent_background_ids = selected_ids
        job_id = f"background-maintenance:{self.workspace_id}:{cycle_number}"
        jobs.enqueue(
            job_id=job_id,
            namespace=ns.maintenance_jobs,
            entity_kind="maintenance_cycle",
            entity_id=job_id,
            job_kind="maintenance_job:distill",
            payload=payload,
            max_retries=1,
        )
        self._worker._emit_trace(
            "maintenance_background_cycle_scheduled",
            workspace_id=self.workspace_id,
            cycle_number=cycle_number,
            cycle_seed=cycle_seed,
            selected_count=len(selected),
            exploration_strategy=strategy,
        )


__all__ = ["MaintenanceDaemon", "ProjectionDaemon"]
