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
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

DEFAULT_REQUEST_MAX_ROUNDS = 2
REQUEST_MAX_ROUNDS_ENV = "LLM_WIKI_MAINTENANCE_DEFAULT_REQUEST_MAX_ROUNDS"


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
    updated_at_ms: int = 0
    updated_by: str = "default"

    @classmethod
    def from_mapping(cls, raw: dict[str, Any] | None) -> MaintenanceControlState:
        raw = raw or {}
        return cls(
            request_enabled=_bool_value(raw.get("request_enabled"), default=True),
            background_enabled=_bool_value(raw.get("background_enabled"), default=False),
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
            return MaintenanceControlState.from_mapping(json.loads(self.path.read_text(encoding="utf-8")))
        except (FileNotFoundError, OSError, ValueError, TypeError, json.JSONDecodeError):
            return MaintenanceControlState()

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

    def update(self, *, request_enabled: object = None, background_enabled: object = None, actor: str = "docker-exec", persist: bool = True) -> MaintenanceControlState:
        with self._lock:
            current = self._state
            values: dict[str, object] = {"updated_by": actor}
            if request_enabled is not None:
                values["request_enabled"] = _bool_value(request_enabled, default=current.request_enabled)
            if background_enabled is not None:
                values["background_enabled"] = _bool_value(background_enabled, default=current.background_enabled)
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
                            result = self.update(
                                request_enabled=request.get("request_enabled"),
                                background_enabled=request.get("background_enabled"),
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
        if payload.get("persist", True) is False:
            return {
                "ok": False,
                "error": "runtime-only control requires the daemon Unix socket; use Docker/Linux or omit --runtime-only",
            }
        state = control.update(
            request_enabled=payload.get("request_enabled"),
            background_enabled=payload.get("background_enabled"),
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
