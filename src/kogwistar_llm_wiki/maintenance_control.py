"""Local, durable controls for the maintenance daemon.

The control file is deliberately app-owned.  The socket is only a low-latency
transport for a process inside the deployment; it is not exposed by REST or
MCP and does not carry database credentials.
"""
from __future__ import annotations

import json
import os
import socket
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .maintenance_profiles import (
    configured_maintenance_enabled,
    configured_maintenance_profile,
    configured_profile_ladder,
    normalize_budget,
    normalize_profile_ladder,
)

DEFAULT_REQUEST_MAX_ROUNDS = 2
REQUEST_MAX_ROUNDS_ENV = "LLM_WIKI_MAINTENANCE_DEFAULT_REQUEST_MAX_ROUNDS"
REQUEST_ENABLED_ENV = "LLM_WIKI_MAINTENANCE_REQUEST_ENABLED"
BACKGROUND_ENABLED_ENV = "LLM_WIKI_MAINTENANCE_BACKGROUND_ENABLED"


def configured_default_request_max_rounds() -> int:
    """Return the bounded default for requests that omit ``max_rounds``."""
    raw = os.getenv(REQUEST_MAX_ROUNDS_ENV, str(DEFAULT_REQUEST_MAX_ROUNDS)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{REQUEST_MAX_ROUNDS_ENV} must be an integer from 1 to 100") from exc
    if not 1 <= value <= 100:
        raise ValueError(f"{REQUEST_MAX_ROUNDS_ENV} must be between 1 and 100")
    return value


def _configured_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    return _bool_value(raw, default=default)


def _bool_value(value: object, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid boolean value: {value!r}")


@dataclass(frozen=True, slots=True)
class MaintenanceControlState:
    request_enabled: bool = True
    background_enabled: bool = False
    # The dataclass default preserves the historical in-process constructor;
    # file/env loading applies the fail-closed deployment default.
    enabled: bool = True
    profile: str = "balanced"
    profile_ladder: list[dict[str, object]] = field(default_factory=list)
    profile_ladder_configured: bool = False
    budget: dict[str, dict[str, float]] = field(default_factory=dict)
    spend: dict[str, dict[str, float]] = field(default_factory=dict)
    deferred_cycle: bool = False
    status_reason: str = "configured"
    updated_at_ms: int = 0
    updated_by: str = "default"

    @classmethod
    def from_mapping(cls, raw: dict[str, Any] | None) -> MaintenanceControlState:
        raw = raw or {}
        profile = str(raw.get("profile") or configured_maintenance_profile()).strip().lower()
        if profile not in {"high", "balanced", "budgeted", "lite"}:
            raise ValueError(f"invalid maintenance profile: {profile}")
        ladder_levels = (
            normalize_profile_ladder(raw["profile_ladder"])
            if "profile_ladder" in raw
            else configured_profile_ladder()
        )
        return cls(
            request_enabled=_bool_value(
                raw.get("request_enabled"),
                default=_configured_bool(REQUEST_ENABLED_ENV, True),
            ),
            background_enabled=_bool_value(
                raw.get("background_enabled"),
                default=_configured_bool(BACKGROUND_ENABLED_ENV, False),
            ),
            enabled=_bool_value(raw.get("enabled"), default=configured_maintenance_enabled()),
            profile=profile,
            profile_ladder=[
                {
                    "name": level.name,
                    "provider": level.provider,
                    "model": level.model,
                    "profile": level.profile,
                    "budget": level.budget,
                }
                for level in ladder_levels
            ],
            profile_ladder_configured=bool(raw.get("profile_ladder_configured", "profile_ladder" in raw)),
            budget=normalize_budget(raw.get("budget") if isinstance(raw.get("budget"), dict) else None),
            spend=normalize_budget(raw.get("spend") if isinstance(raw.get("spend"), dict) else None),
            deferred_cycle=_bool_value(raw.get("deferred_cycle"), default=False),
            status_reason=str(raw.get("status_reason") or "configured"),
            updated_at_ms=int(raw.get("updated_at_ms") or 0),
            updated_by=str(raw.get("updated_by") or "default"),
        )

    def changed(self, **updates: object) -> MaintenanceControlState:
        values = asdict(self)
        values.update(updates)
        values["updated_at_ms"] = int(time.time() * 1000)
        return MaintenanceControlState.from_mapping(values)


class MaintenanceControl:
    """Thread-safe state plus a Unix socket for local docker-exec control."""

    def __init__(self, data_dir: str | os.PathLike[str], *, socket_path: str | None = None) -> None:
        self.root = Path(data_dir) / "maintenance"
        self.path = self.root / "control.json"
        self.socket_path = Path(socket_path) if socket_path else self.root / "maintenance.sock"
        self._lock = threading.RLock()
        self._state = self._read()
        try:
            self._state_mtime_ns = self.path.stat().st_mtime_ns
        except OSError:
            self._state_mtime_ns = None

    def _read(self) -> MaintenanceControlState:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return MaintenanceControlState.from_mapping(raw)
        except FileNotFoundError:
            return MaintenanceControlState.from_mapping({})
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            # A corrupt control file must not silently enable autonomous work.
            return MaintenanceControlState.from_mapping({"enabled": False})

    def get(self) -> MaintenanceControlState:
        with self._lock:
            try:
                mtime_ns: int | None = self.path.stat().st_mtime_ns
            except OSError:
                mtime_ns = None
            if mtime_ns != self._state_mtime_ns:
                self._state = self._read()
                self._state_mtime_ns = mtime_ns
            return self._state

    def update(
        self,
        *,
        request_enabled: object = None,
        background_enabled: object = None,
        enabled: object = None,
        profile: object = None,
        profile_ladder: object = None,
        budget: object = None,
        spend: object = None,
        deferred_cycle: object = None,
        status_reason: object = None,
        actor: str = "docker-exec",
        persist: bool = True,
    ) -> MaintenanceControlState:
        with self._lock:
            current = self._state
            values: dict[str, object] = {"updated_by": actor}
            if request_enabled is not None:
                values["request_enabled"] = _bool_value(request_enabled, default=current.request_enabled)
            if background_enabled is not None:
                values["background_enabled"] = _bool_value(background_enabled, default=current.background_enabled)
            if enabled is not None:
                values["enabled"] = _bool_value(enabled, default=current.enabled)
            if profile is not None:
                values["profile"] = str(profile).strip().lower()
            if profile_ladder is not None:
                values["profile_ladder"] = [
                    {
                        "name": level.name,
                        "provider": level.provider,
                        "model": level.model,
                        "profile": level.profile,
                        "budget": level.budget,
                    }
                    for level in normalize_profile_ladder(profile_ladder)
                ]
                values["profile_ladder_configured"] = True
            if budget is not None:
                if not isinstance(budget, dict):
                    raise ValueError("maintenance budget must be an object")
                values["budget"] = normalize_budget(budget)
            if spend is not None:
                if not isinstance(spend, dict):
                    raise ValueError("maintenance spend must be an object")
                values["spend"] = normalize_budget(spend)
            if deferred_cycle is not None:
                values["deferred_cycle"] = _bool_value(deferred_cycle, default=current.deferred_cycle)
            if status_reason is not None:
                values["status_reason"] = str(status_reason)
            self._state = current.changed(**values)
            if persist:
                self.root.mkdir(parents=True, exist_ok=True)
                temporary = self.path.with_suffix(".tmp")
                temporary.write_text(json.dumps(asdict(self._state), sort_keys=True) + "\n", encoding="utf-8")
                temporary.replace(self.path)
                try:
                    self._state_mtime_ns = self.path.stat().st_mtime_ns
                except OSError:
                    self._state_mtime_ns = None
            return self._state

    def serve(self, stop_event: threading.Event) -> threading.Thread:
        """Start a short JSON-lines control server and return its daemon thread."""
        self.root.mkdir(parents=True, exist_ok=True)
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            pass

        def run() -> None:
            if not hasattr(socket, "AF_UNIX"):
                return
            server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                server.bind(str(self.socket_path))
                os.chmod(self.socket_path, 0o600)
                server.listen(4)
                server.settimeout(0.5)
                while not stop_event.is_set():
                    try:
                        connection, _ = server.accept()
                    except TimeoutError:
                        continue
                    with connection:
                        try:
                            request = json.loads(connection.recv(8192).decode("utf-8"))
                            if request.get("status"):
                                response = {"ok": True, **asdict(self.get())}
                            else:
                                result = self.update(
                                    request_enabled=request.get("request_enabled"),
                                    background_enabled=request.get("background_enabled"),
                                    enabled=request.get("enabled"),
                                    profile=request.get("profile"),
                                    profile_ladder=request.get("profile_ladder"),
                                    budget=request.get("budget"),
                                    spend=request.get("spend"),
                                    actor=str(request.get("actor") or "docker-exec"),
                                    persist=bool(request.get("persist", True)),
                                )
                                response = {"ok": True, **asdict(result)}
                        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
                            response = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                        connection.sendall((json.dumps(response) + "\n").encode("utf-8"))
            finally:
                server.close()
                try:
                    self.socket_path.unlink()
                except FileNotFoundError:
                    pass

        thread = threading.Thread(target=run, name="maintenance-control", daemon=True)
        thread.start()
        return thread


def send_control_command(data_dir: str | os.PathLike[str], **command: object) -> dict[str, object]:
    """Send a command to a running daemon, falling back to durable state."""
    control = MaintenanceControl(data_dir)
    payload = {key: value for key, value in command.items() if value is not None}
    try:
        if not hasattr(socket, "AF_UNIX"):
            raise OSError("Unix-domain sockets are unavailable on this host")
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2.0)
            client.connect(str(control.socket_path))
            client.sendall((json.dumps(payload) + "\n").encode("utf-8"))
            return json.loads(client.recv(8192).decode("utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        if payload.get("status"):
            return {"ok": True, "persisted": True, **asdict(control.get())}
        if payload.get("persist", True) is False:
            return {
                "ok": False,
                "error": "runtime-only control requires the daemon Unix socket; use Docker/Linux or omit --runtime-only",
            }
        state = control.update(
            request_enabled=payload.get("request_enabled"),
            background_enabled=payload.get("background_enabled"),
            enabled=payload.get("enabled"),
            profile=payload.get("profile"),
            profile_ladder=payload.get("profile_ladder"),
            budget=payload.get("budget"),
            spend=payload.get("spend"),
            actor=str(payload.get("actor") or "docker-exec"),
            persist=bool(payload.get("persist", True)),
        )
        return {"ok": True, "persisted": True, **asdict(state)}


__all__ = [
    "DEFAULT_REQUEST_MAX_ROUNDS",
    "REQUEST_MAX_ROUNDS_ENV",
    "MaintenanceControl",
    "MaintenanceControlState",
    "configured_default_request_max_rounds",
    "send_control_command",
]
