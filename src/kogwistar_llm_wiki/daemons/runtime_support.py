"""Shared health and startup-recovery helpers for product daemons."""

from __future__ import annotations

import logging
import os
import socket
from pathlib import Path

from kogwistar.engine_core import (
    OutputReconciliationState,
    RecoveryReport,
    RecoverySurface,
)

from ..configuration.workspace import WorkspaceNamespaces
from ..models import NamespaceEngines

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
        namespace=str(
            getattr(engines.conversation, "namespace", "conversation") or "conversation"
        ),
        version="1",
        config_metadata={"workspace_id": workspace_id, **(config_metadata or {})},
        operator_tags=operator_tags,
    )
    registry.start_instance(
        service_id=service_id,
        workspace_id=workspace_id,
        namespace=str(
            getattr(engines.conversation, "namespace", "conversation") or "conversation"
        ),
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
            namespace=str(
                getattr(engines.conversation, "namespace", "conversation") or "conversation"
            ),
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
            namespace=str(
                getattr(engines.conversation, "namespace", "conversation") or "conversation"
            ),
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
        observed_version=str((row or {}).get("projection_schema_version") or "") or None,
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
