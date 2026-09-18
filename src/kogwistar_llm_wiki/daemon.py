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

from .maintenance import (
    MaintenanceProfileLadderDecision,
    add_usage,
    budget_fits,
    configured_maintenance_budget,
    configured_prices,
    configured_profile_ladder,
    configured_token_budget_rate,
    next_window_reset,
    reset_expired_spend,
    resolve_profile_ladder,
    select_embedding_exploration,
)
from .maintenance.maintenance_control import MaintenanceControl, MaintenanceControlState
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
        self._budget_state_path = (
            Path(data_dir) / "maintenance" / "budget_state.json" if data_dir else None
        )
        self._budget_state = self._load_budget_state()
        self._empty_poll_streak = 0
        self._last_profile_reason = "configured"
        self._active_profile_level: str | None = None
        self._recorded_usage_attempts: set[str] = {
            str(item)
            for item in (self._budget_state.get("usage_attempt_ids") or [])
            if str(item).strip()
        }
        self.provider_settings = resolve_maintenance_provider_settings()
        self.telemetry = LlmWikiTelemetry.from_environment()
        self._worker = MaintenanceWorker(
            engines,
            provider_settings=self.provider_settings,
            # A ladder must be re-evaluated between jobs so one quota level
            # cannot consume a whole claim batch after it is exhausted.
            fair_scheduling=bool(self._effective_profile_ladder(self.control_state)),
            trace_sink=self.telemetry.instrument_event,
            usage_sink=self._record_profile_usage,
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

    def _load_budget_state(self) -> dict[str, Any]:
        path = getattr(self, "_budget_state_path", None)
        if path is None:
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    def _effective_budget(self, state: MaintenanceControlState) -> dict[str, dict[str, float]]:
        return state.budget or configured_maintenance_budget()

    def _effective_profile_ladder(self, state: MaintenanceControlState) -> list[dict[str, object]]:
        if state.profile_ladder_configured or state.profile_ladder:
            return state.profile_ladder
        return [
            {
                "name": level.name,
                "provider": level.provider,
                "model": level.model,
                "profile": level.profile,
                "budget": level.budget,
            }
            for level in configured_profile_ladder()
        ]

    @staticmethod
    def _profile_estimate() -> dict[str, float]:
        return {"tokens": 1000.0, "input_tokens": 800.0, "output_tokens": 200.0, "money": 0.0}

    def _select_profile_level(self, state: MaintenanceControlState) -> MaintenanceProfileLadderDecision:
        """Select the first affordable level; always rechecks primary for recovery."""
        decision = resolve_profile_ladder(
            enabled=state.enabled,
            requested=state.profile,
            budget=self._effective_budget(state),
            ladder=self._effective_profile_ladder(state),
            ladder_spend=self._budget_state.get("ladder_spend", {}),
            estimate=self._profile_estimate(),
        )
        self._last_profile_reason = decision.reason
        self._active_profile_level = decision.level.name if decision.level else None
        if decision.level is not None:
            self.provider_settings = resolve_maintenance_provider_settings(
                provider=decision.level.provider,
                model=decision.level.model,
                include_provider_chain=False,
            )
            self._worker.provider_settings = self.provider_settings
        return decision

    def _persist_budget_state(self) -> None:
        path = getattr(self, "_budget_state_path", None)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self._budget_state, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(path)

    def profile_status(self, state: MaintenanceControlState | None = None) -> dict[str, object]:
        """Return redaction-free operational state for local diagnostics."""
        state = state or self.control_state
        ladder_decision = resolve_profile_ladder(
            enabled=state.enabled,
            requested=state.profile,
            budget=self._effective_budget(state),
            ladder=self._effective_profile_ladder(state),
            ladder_spend=getattr(self, "_budget_state", {}).get("ladder_spend", {}),
            estimate=self._profile_estimate(),
        )
        decision = ladder_decision.profile
        budget_state = getattr(self, "_budget_state", {})
        return {
            "enabled": state.enabled,
            "requested_profile": decision.requested,
            "effective_profile": decision.effective,
            "profile_reason": self._last_profile_reason if self._last_profile_reason != "configured" else decision.reason,
            "request_enabled": state.request_enabled,
            "background_enabled": state.background_enabled,
            "budget": self._effective_budget(state),
            "spend": budget_state.get("spend", {}) or state.spend,
            "next_window_reset": {
                window: next_window_reset(
                    float(budget_state.get("window_started_at", {}).get(window, time.time())),
                    window,
                    mode=os.getenv("LLM_WIKI_MAINTENANCE_BUDGET_WINDOW_MODE", "rolling").strip().lower(),
                )
                for window in self._effective_budget(state)
            },
            "deferred_cycle": state.deferred_cycle,
            "budget_window_mode": os.getenv("LLM_WIKI_MAINTENANCE_BUDGET_WINDOW_MODE", "rolling"),
            "poll_interval_seconds": self._current_poll_interval(),
            "profile_ladder": self._effective_profile_ladder(state),
            "active_profile_level": self._active_profile_level,
            "ladder_spend": budget_state.get("ladder_spend", {}),
        }

    def _current_poll_interval(self) -> float:
        streak = int(getattr(self, "_empty_poll_streak", 0))
        return min(float(self.poll_interval) * (2 ** min(streak, 5)), 300.0)

    def _record_profile_usage(self, attempt_id: str, usage: Mapping[str, float]) -> None:
        """Reconcile measured worker usage into durable window spend."""
        if attempt_id in self._recorded_usage_attempts:
            return
        self._recorded_usage_attempts.add(attempt_id)
        recorded_ids = list(self._budget_state.get("usage_attempt_ids") or [])
        recorded_ids.append(attempt_id)
        self._budget_state["usage_attempt_ids"] = recorded_ids[-2048:]
        self._refresh_budget_state(time.time())
        current_spend = self._budget_state.get("spend", {}) or self.control_state.spend
        updated_spend = add_usage(current_spend, usage)
        accumulated = float(self._budget_state.get("accumulated_call_tokens", 0) or 0)
        accumulated += float(usage.get("tokens", 0) or 0)
        self._budget_state.update({"spend": updated_spend, "accumulated_call_tokens": accumulated})
        active_profile_level = getattr(self, "_active_profile_level", None)
        if active_profile_level:
            ladder_spend = self._budget_state.setdefault("ladder_spend", {})
            level_spend = ladder_spend.get(active_profile_level, {})
            ladder_spend[active_profile_level] = add_usage(level_spend, usage)
            ladder_started = self._budget_state.setdefault("ladder_window_started_at", {})
            level_started = ladder_started.setdefault(active_profile_level, {})
            now = time.time()
            for window in ladder_spend[active_profile_level]:
                if isinstance(level_started, dict):
                    level_started.setdefault(window, now)
        self._persist_budget_state()
        if self.control is not None:
            self.control.update(spend=updated_spend, actor="maintenance-worker")

    def _refresh_budget_state(self, now: float) -> None:
        mode = os.getenv("LLM_WIKI_MAINTENANCE_BUDGET_WINDOW_MODE", "rolling").strip().lower()
        budget_state = getattr(self, "_budget_state", {})
        spend, starts = reset_expired_spend(
            budget_state.get("spend", {}),
            budget_state.get("window_started_at", {}),
            now=now,
            mode=mode,
        )
        budget_state["spend"] = spend
        budget_state["window_started_at"] = starts
        self._budget_state = budget_state
        ladder_spend = budget_state.get("ladder_spend", {})
        ladder_started = budget_state.get("ladder_window_started_at", {})
        if isinstance(ladder_spend, Mapping) and isinstance(ladder_started, Mapping):
            refreshed_ladder: dict[str, object] = {}
            refreshed_starts: dict[str, object] = {}
            for level_name, spend in ladder_spend.items():
                if not isinstance(spend, Mapping):
                    continue
                starts = ladder_started.get(level_name, {})
                if not isinstance(starts, Mapping):
                    starts = {}
                refreshed, starts = reset_expired_spend(spend, starts, now=now, mode=mode)
                refreshed_ladder[str(level_name)] = refreshed
                refreshed_starts[str(level_name)] = starts
            budget_state["ladder_spend"] = refreshed_ladder
            budget_state["ladder_window_started_at"] = refreshed_starts
        self._persist_budget_state()

    def _queue_has_work(self) -> bool:
        try:
            jobs = self.engines.conversation.jobs
            namespace = WorkspaceNamespaces(self.workspace_id).maintenance_jobs
            return bool(jobs.list(namespace=namespace, status="PENDING", limit=1))
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
            return True

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
                "maintenance_enabled": self.control_state.enabled,
                "maintenance_profile": self.control_state.profile,
                "maintenance_profile_status": self.profile_status(self.control_state),
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
                previous_state = self.control_state
                if self.control:
                    self.control_state = self.control.get()
                state_changed = self.control_state != previous_state
                had_work = self._queue_has_work()
                _heartbeat_service_health(
                    self.engines,
                    workspace_id=self.workspace_id,
                    service_kind="maintenance_daemon",
                    instance_id=self._instance_id,
                )
                if self.control_state.request_enabled or self.control_state.background_enabled:
                    self._schedule_background_cycle(self.control_state)
                self._worker.request_enabled = self.control_state.request_enabled
                self._worker.background_enabled = self.control_state.background_enabled
                self._worker.fair_scheduling = bool(self._effective_profile_ladder(self.control_state))
                if self.control_state.request_enabled or self.control_state.background_enabled:
                    selected_profile = self._select_profile_level(self.control_state)
                    ladder_exhausted = bool(self._effective_profile_ladder(self.control_state)) and not selected_profile.profile.enabled
                    if ladder_exhausted:
                        self._last_profile_reason = selected_profile.reason
                    else:
                        self._worker.process_pending_jobs(self.workspace_id)
                else:
                    self._last_profile_reason = "both_modes_disabled_worker_paused"
                if had_work or state_changed:
                    self._empty_poll_streak = 0
                else:
                    self._empty_poll_streak += 1
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
            self._stop_event.wait(timeout=self._current_poll_interval())
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
        self._refresh_budget_state(now_ms / 1000.0)
        last_cycle_at_ms = int(getattr(self, "_last_background_cycle_at_ms", 0) or 0)
        ladder_decision = self._select_profile_level(state)
        decision = ladder_decision.profile
        self._last_profile_reason = decision.reason
        if not state.background_enabled or decision.effective == "off" or (
            not self._effective_profile_ladder(state) and decision.effective == "lite"
        ) or (
            last_cycle_at_ms and now_ms - last_cycle_at_ms < self.background_interval * 1000
        ):
            return
        selected_budget = (
            ladder_decision.level.budget
            if ladder_decision.level is not None and ladder_decision.level.budget
            else self._effective_budget(state)
        )
        if decision.effective == "budgeted":
            if float(self._budget_state.get("accumulated_call_tokens", 0) or 0) < configured_token_budget_rate():
                self._last_profile_reason = "waiting_for_token_budget_rate"
                return
            if any(
                "money" in metrics
                for metrics in selected_budget.values()
            ) and os.getenv("LLM_WIKI_MAINTENANCE_MODEL_CLASS", "unknown").strip().lower() != "free":
                prices = configured_prices()
                if prices["input"] is None or prices["output"] is None:
                    self._last_profile_reason = "deferred:money_price_unavailable"
                    return
            prices = configured_prices()
            estimated_money = 0.0
            if prices["input"] is not None and prices["output"] is not None:
                estimated_money = (
                    800.0 * prices["input"] + 200.0 * prices["output"]
                ) / 1_000_000.0
            estimate = {
                "tokens": 1000.0,
                "input_tokens": 800.0,
                "output_tokens": 200.0,
                "money": estimated_money,
            }
            fits, reason = budget_fits(
                selected_budget,
                (
                    self._budget_state.get("ladder_spend", {}).get(ladder_decision.level.name, {})
                    if ladder_decision.level is not None
                    else self._budget_state.get("spend", {}) or state.spend
                ),
                estimate,
            )
            if not fits:
                self._last_profile_reason = f"deferred:{reason}"
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
        if decision.effective == "budgeted":
            self._budget_state["accumulated_call_tokens"] = max(
                0.0,
                float(self._budget_state.get("accumulated_call_tokens", 0) or 0)
                - configured_token_budget_rate(),
            )
            self._persist_budget_state()
        self._worker._emit_trace(
            "maintenance_background_cycle_scheduled",
            workspace_id=self.workspace_id,
            cycle_number=cycle_number,
            cycle_seed=cycle_seed,
            selected_count=len(selected),
            exploration_strategy=strategy,
            maintenance_profile=decision.effective,
        )


__all__ = ["MaintenanceDaemon", "ProjectionDaemon"]
