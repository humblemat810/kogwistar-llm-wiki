"""Maintenance daemon lifecycle and polling loop."""

from __future__ import annotations

import logging
import os
import threading
import uuid
from collections.abc import Callable
from pathlib import Path

from kogwistar.engine_core import RecoveryReport

from ..daemons.maintenance_budget import MaintenanceBudgetMixin
from ..maintenance.maintenance_control import (
    MaintenanceControl,
    MaintenanceControlState,
)
from ..models import NamespaceEngines
from ..otel import LlmWikiTelemetry
from ..providers.role_config import provider_config_summary
from ..worker import MaintenanceWorker
from .runtime_support import (
    _core_startup_recovery,
    _declare_service_health,
    _heartbeat_service_health,
    _log_startup_recovery,
)

logger = logging.getLogger(__name__)

ProviderResolver = Callable[[], object]
WorkerFactory = Callable[..., MaintenanceWorker]


class MaintenanceDaemonRuntime(MaintenanceBudgetMixin):
    """Poll and drain maintenance jobs with injected construction seams."""

    def __init__(
        self,
        engines: NamespaceEngines,
        workspace_id: str,
        poll_interval: float = 10.0,
        *,
        data_dir: str | os.PathLike[str] | None = None,
        background_interval: float = 600.0,
        worker_factory: WorkerFactory = MaintenanceWorker,
        provider_resolver: ProviderResolver,
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
        self.provider_settings = provider_resolver()
        self.telemetry = LlmWikiTelemetry.from_environment()
        self._worker = worker_factory(
            engines,
            provider_settings=self.provider_settings,
            fair_scheduling=bool(self._effective_profile_ladder(self.control_state)),
            trace_sink=self.telemetry.instrument_event,
            usage_sink=self._record_profile_usage,
        )
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
        self._stop_service_health(
            self.engines,
            workspace_id=self.workspace_id,
            service_kind="maintenance_daemon",
            instance_id=self._instance_id,
        )
        logger.info("MaintenanceDaemon stopped - workspace=%s", self.workspace_id)


__all__ = ["MaintenanceDaemonRuntime"]
