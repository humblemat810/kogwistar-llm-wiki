"""Operator settings inspection and staged desired configuration."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping

from .multimodal_runtime import (
    configured_multimodal_dimension,
    configured_multimodal_model,
    configured_representation_service_url,
)
from .provider_config import resolve_maintenance_provider_settings, resolve_parser_provider_settings
from .model_catalog import _safe_endpoint
from .identity import auth_mode


_DESIRED_KEYS = frozenset({
    "auth_mode",
    "multimodal_enabled",
    "otel_enabled",
    "parser_model",
    "maintenance_model",
    "parser_provider",
    "maintenance_provider",
    "parser_base_url",
    "maintenance_base_url",
})
_SECRET_WORDS = ("token", "secret", "password", "api_key", "credential")
_WORKER_PROVIDERS = frozenset({"fake", "ollama", "gemini", "openai", "azure", "azure_openai", "vertex", "router", "llm_router"})


def _redact(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): ("[redacted]" if any(word in str(key).lower() for word in _SECRET_WORDS) else _redact(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


class SettingsService:
    """Expose safe operational state without becoming a second runtime config system."""

    def __init__(self, pipeline: Any, *, path: str | Path | None = None) -> None:
        self.pipeline = pipeline
        self._runtime_multimodal_enabled: bool | None = None
        self._runtime_otel_enabled: bool | None = None
        configured_path = path or os.getenv("LLM_WIKI_SETTINGS_PATH")
        data_dir = os.getenv("KOGWISTAR_DATA_DIR")
        self.path = Path(configured_path) if configured_path else (
            Path(data_dir) / "settings" / "desired.json" if data_dir else None
        )

    def _load_desired(self) -> dict[str, object]:
        if self.path is None or not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"cannot read desired settings: {exc}") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("desired settings must be a JSON object")
        values = payload.get("settings", payload)
        if not isinstance(values, dict):
            raise RuntimeError("desired settings.settings must be a JSON object")
        return {key: value for key, value in values.items() if key in _DESIRED_KEYS}

    def _save_desired(self, desired: Mapping[str, object]) -> None:
        if self.path is None:
            raise RuntimeError("desired settings persistence requires KOGWISTAR_DATA_DIR or LLM_WIKI_SETTINGS_PATH")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "updated_at_ms": int(time.time() * 1000), "settings": dict(desired)}
        fd, temp_name = tempfile.mkstemp(prefix="desired-", suffix=".json", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    def _effective_embeddings(self) -> dict[str, object]:
        result: dict[str, object] = {}
        for space in ("conversation", "workflow", "kg", "wisdom", "derived_knowledge"):
            engine = getattr(self.pipeline.engines, space, None)
            if engine is None:
                continue
            report = getattr(engine, "embedding_profile_report", None)
            if callable(report):
                report = report()
            if not isinstance(report, Mapping):
                profile = getattr(engine, "embedding_profile", None)
                report = profile.to_dict() if hasattr(profile, "to_dict") else {}
            profile_payload = report.get("registered") or report.get("configured") or report
            backend = getattr(engine, "backend", None)
            result[space] = {
                "backend": type(backend).__name__ if backend is not None else "unknown",
                "profile": _redact(dict(profile_payload)) if isinstance(profile_payload, Mapping) else {},
                "profile_locked": bool(report.get("registered")) if isinstance(report, Mapping) else bool(report),
            }
        return result

    def snapshot(self, *, workspace_id: str = "default") -> dict[str, object]:
        parser = resolve_parser_provider_settings().parser
        maintenance = resolve_maintenance_provider_settings().parser
        multimodal = getattr(self.pipeline, "multimodal_encoder", None)
        desired = self._load_desired()
        multimodal_enabled = (
            self._runtime_multimodal_enabled
            if self._runtime_multimodal_enabled is not None
            else multimodal is not None
        )
        telemetry = getattr(self.pipeline, "telemetry", None)
        otel_enabled = (
            self._runtime_otel_enabled
            if self._runtime_otel_enabled is not None
            else bool(getattr(telemetry, "enabled", False))
        )
        effective = {
            "workspace_id": workspace_id,
            "backend": self._backend_name(),
            "data_dir": os.getenv("KOGWISTAR_DATA_DIR"),
            "parser": {"provider": parser.provider, "model": parser.model, "base_url": _safe_endpoint(parser.base_url or ""), "temperature": parser.temperature},
            "maintenance": {"provider": maintenance.provider, "model": maintenance.model, "base_url": _safe_endpoint(maintenance.base_url or ""), "temperature": maintenance.temperature},
            "embeddings": self._effective_embeddings(),
            "multimodal": {
                "enabled": multimodal_enabled,
                "configured": bool(multimodal or configured_representation_service_url()),
                "model": getattr(getattr(multimodal, "profile", None), "model", configured_multimodal_model()),
                "dimension": getattr(getattr(multimodal, "profile", None), "dimension", configured_multimodal_dimension()),
                "service_url": configured_representation_service_url(),
            },
            "auth_mode": auth_mode(),
            "otel": {
                "enabled": otel_enabled,
                "configured": bool(os.getenv("LLM_WIKI_OTEL_ENABLED", "").strip()),
                "endpoint": os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"),
                "service_name": os.getenv("LLM_WIKI_OTEL_SERVICE_NAME", "kogwistar-llm-wiki"),
                "packages_available": telemetry is not None and getattr(telemetry, "packages_available", True),
            },
        }
        impact = self._impact(effective, desired)
        return _redact({
            "version": 1,
            "effective": effective,
            "desired": desired,
            "components": self._components(effective),
            **impact,
        })

    def health(self, *, workspace_id: str = "default", readiness: Mapping[str, object] | None = None) -> dict[str, object]:
        readiness = dict(readiness or self.pipeline_ready())
        multimodal = getattr(self.pipeline, "multimodal_encoder", None)
        representation: dict[str, object] = {"state": "disabled"}
        if multimodal is not None:
            try:
                probe = multimodal.readiness() if hasattr(multimodal, "readiness") else {"ready": True}
                representation = {"state": "up" if probe.get("ready") else "degraded", **probe}
            except Exception as exc:  # noqa: BLE001
                representation = {"state": "unavailable", "reason": str(exc)}
        return {"version": 1, "workspace_id": workspace_id, "state": "up" if readiness.get("ready") else "degraded", "readiness": readiness, "representation_service": _redact(representation), "checked_at_ms": int(time.time() * 1000)}

    def update_desired(self, changes: Mapping[str, object], *, workspace_id: str = "default") -> dict[str, object]:
        unknown = sorted(set(changes) - _DESIRED_KEYS)
        if unknown:
            raise ValueError(f"unsupported settings: {', '.join(unknown)}")
        current = self._load_desired()
        next_desired = {**current, **dict(changes)}
        if "multimodal_enabled" in next_desired and not isinstance(next_desired["multimodal_enabled"], bool):
            raise ValueError("multimodal_enabled must be boolean")
        if "auth_mode" in next_desired and next_desired["auth_mode"] not in {"disabled", "static_token", "kogwistar_jwt"}:
            raise ValueError("auth_mode must be disabled, static_token, or kogwistar_jwt")
        for key in ("parser_provider", "maintenance_provider"):
            if key in next_desired and str(next_desired[key]).strip().lower() not in _WORKER_PROVIDERS:
                raise ValueError(f"{key} must be a configured structured provider or router; Codex cockpit is workbench-only")
        self._save_desired(next_desired)
        return self.snapshot(workspace_id=workspace_id)

    def apply(self, *, workspace_id: str, confirmed: bool) -> dict[str, object]:
        if not confirmed:
            return {"status": "confirmation_required", **self.snapshot(workspace_id=workspace_id)}
        snapshot = self.snapshot(workspace_id=workspace_id)
        desired = snapshot["desired"]
        if isinstance(desired, Mapping) and isinstance(desired.get("otel_enabled"), bool):
            telemetry = getattr(self.pipeline, "telemetry", None)
            if desired["otel_enabled"] and (telemetry is None or not getattr(telemetry, "packages_available", True)):
                return {"status": "rejected", "reason": "opentelemetry_runtime_not_available", **snapshot}
            if telemetry is not None and hasattr(telemetry, "set_enabled"):
                telemetry.set_enabled(desired["otel_enabled"])
            self._runtime_otel_enabled = desired["otel_enabled"]
        if isinstance(desired, Mapping) and isinstance(desired.get("multimodal_enabled"), bool):
            if desired["multimodal_enabled"] and self.pipeline.multimodal_encoder is None:
                return {"status": "rejected", "reason": "multimodal_representation_service_not_configured", **snapshot}
            self._runtime_multimodal_enabled = desired["multimodal_enabled"]
            snapshot = self.snapshot(workspace_id=workspace_id)
        if snapshot["restart_required"] or snapshot["reembedding_required"]:
            return {"status": "staged", "reason": "restart_and_or_reembedding_required", **snapshot}
        return {"status": "applied", "applied_live": ["multimodal_enabled"] if self._runtime_multimodal_enabled is not None else [], **snapshot}

    def pipeline_ready(self) -> dict[str, object]:
        engines = self.pipeline.engines
        if getattr(engines, "_closed", False):
            return {"ready": False, "reason": "engines_closed"}
        return {"ready": True}

    def _backend_name(self) -> str:
        backend = getattr(self.pipeline.engines, "kg", None)
        backend = getattr(backend, "backend", None)
        return type(backend).__name__ if backend is not None else "unknown"

    def _components(self, effective: Mapping[str, object]) -> dict[str, object]:
        multimodal = effective["multimodal"]
        assert isinstance(multimodal, Mapping)
        otel = effective.get("otel", {})
        assert isinstance(otel, Mapping)
        return {
            "knowledge_text_embedding": {"state": "up", "toggleable": False, "description": "Local text embedding used by the configured graph backend."},
            "multimodal_embedding": {"state": "up" if multimodal["enabled"] else "disabled", "toggleable": True, "description": "Qwen3-VL remote projection route."},
            "parser": {"state": "up", "toggleable": False},
            "maintenance": {"state": "up", "toggleable": False},
            "otel_sink": {"state": "up" if otel.get("enabled") else "disabled", "toggleable": True, "description": "Optional OpenTelemetry trace sink for the configured collector."},
        }

    @staticmethod
    def _impact(effective: Mapping[str, object], desired: Mapping[str, object]) -> dict[str, object]:
        restart_keys = {"auth_mode", "parser_model", "maintenance_model", "parser_provider", "maintenance_provider", "parser_base_url", "maintenance_base_url"}
        parser = effective.get("parser", {})
        maintenance = effective.get("maintenance", {})
        effective_values = {
            "parser_model": parser.get("model") if isinstance(parser, Mapping) else None,
            "parser_provider": parser.get("provider") if isinstance(parser, Mapping) else None,
            "parser_base_url": parser.get("base_url") if isinstance(parser, Mapping) else None,
            "maintenance_model": maintenance.get("model") if isinstance(maintenance, Mapping) else None,
            "maintenance_provider": maintenance.get("provider") if isinstance(maintenance, Mapping) else None,
            "maintenance_base_url": maintenance.get("base_url") if isinstance(maintenance, Mapping) else None,
            "auth_mode": effective.get("auth_mode"),
            "otel_enabled": effective.get("otel", {}).get("enabled") if isinstance(effective.get("otel"), Mapping) else None,
        }
        pending_changes = sorted(
            key for key, value in desired.items()
            if key in _DESIRED_KEYS and value != effective_values.get(key, effective.get("multimodal", {}).get("enabled") if key == "multimodal_enabled" and isinstance(effective.get("multimodal"), Mapping) else None)
        )
        restart_required = bool(restart_keys.intersection(pending_changes))
        multimodal = effective.get("multimodal", {})
        current_enabled = multimodal.get("enabled") if isinstance(multimodal, Mapping) else None
        profile_keys = {"embedding_provider", "embedding_model", "embedding_dimension", "embedding_metric"}
        reembedding_required = bool(profile_keys.intersection(pending_changes))
        warnings: list[str] = []
        if restart_required:
            warnings.append("Model/provider changes are staged and require a graceful service restart.")
        if reembedding_required:
            warnings.append("Embedding profile changes require an isolated projection and re-embedding.")
        return {"pending_changes": pending_changes, "restart_required": restart_required, "reembedding_required": reembedding_required, "warnings": warnings}
