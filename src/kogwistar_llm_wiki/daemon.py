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
from collections.abc import Mapping
from functools import partial

from .daemons import maintenance_budget as _maintenance_budget
from .daemons.maintenance_daemon import MaintenanceDaemonRuntime
from .daemons.projection_daemon import ProjectionDaemon
from .daemons.runtime_support import (
    _core_startup_recovery,  # noqa: F401 - compatibility helper seam
    _declare_service_health,  # noqa: F401 - compatibility helper seam
    _heartbeat_service_health,  # noqa: F401 - compatibility helper seam
    _log_startup_recovery,  # noqa: F401 - compatibility helper seam
    _stop_service_health,
)
from .disambiguation.contact_book import ContactScanObservationProvider
from .disambiguation.contact_matching import AuthorizeContactStream
from .maintenance import MaintenanceProfileLadderDecision
from .maintenance.maintenance_control import MaintenanceControlState
from .models import NamespaceEngines
from .providers.role_config import (
    resolve_maintenance_provider_settings,
)
from .utils import _temporary_namespace
from .worker import MaintenanceWorker

logger = logging.getLogger(__name__)


class MaintenanceDaemon(MaintenanceDaemonRuntime):
    """Compatibility facade for the grouped maintenance daemon runtime."""

    def __init__(
        self,
        engines: NamespaceEngines,
        workspace_id: str,
        poll_interval: float = 10.0,
        *,
        data_dir: str | os.PathLike[str] | None = None,
        background_interval: float = 600.0,
        contact_observation_providers: Mapping[str, ContactScanObservationProvider] | None = None,
        contact_stream_authorizer: AuthorizeContactStream | None = None,
    ) -> None:
        providers = dict(contact_observation_providers or {})
        if providers and not callable(contact_stream_authorizer):
            raise TypeError("contact scan providers require an explicit stream authorizer")
        if contact_stream_authorizer is not None and not callable(contact_stream_authorizer):
            raise TypeError("contact stream authorizer must be callable")
        worker_factory = (
            partial(
                MaintenanceWorker,
                contact_observation_providers=providers,
                contact_stream_authorizer=contact_stream_authorizer,
            )
            if providers
            else MaintenanceWorker
        )
        super().__init__(
            engines,
            workspace_id,
            poll_interval,
            data_dir=data_dir,
            background_interval=background_interval,
            worker_factory=worker_factory,
            provider_resolver=resolve_maintenance_provider_settings,
        )

    def _stop_service_health(self) -> None:
        """Keep the historical root helper monkeypatch seam working."""
        _stop_service_health(
            self.engines,
            workspace_id=self.workspace_id,
            service_kind="maintenance_daemon",
            instance_id=self._instance_id,
        )

    def _select_profile_level(self, state: MaintenanceControlState) -> MaintenanceProfileLadderDecision:
        """Preserve the root-module provider resolver monkeypatch seam."""
        resolver = _maintenance_budget.resolve_maintenance_provider_settings
        _maintenance_budget.resolve_maintenance_provider_settings = resolve_maintenance_provider_settings
        try:
            return super()._select_profile_level(state)
        finally:
            _maintenance_budget.resolve_maintenance_provider_settings = resolver

    def _schedule_background_cycle(self, state: MaintenanceControlState) -> None:
        """Preserve the root-module namespace-context monkeypatch seam."""
        namespace_context = _maintenance_budget._temporary_namespace
        _maintenance_budget._temporary_namespace = _temporary_namespace
        try:
            super()._schedule_background_cycle(state)
        finally:
            _maintenance_budget._temporary_namespace = namespace_context

__all__ = ["MaintenanceDaemon", "ProjectionDaemon"]
