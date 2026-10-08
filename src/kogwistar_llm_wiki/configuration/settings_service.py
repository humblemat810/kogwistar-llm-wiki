"""Operator settings inspection and staged desired configuration."""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, cast

from kogwistar.json_types import JsonValue

from ..embeddings.multimodal_projection import MultimodalEncoder
from ..embeddings.multimodal_runtime import (
    configured_embedding_crop_token_budget,
    configured_embedding_max_model_len,
    configured_embedding_service_url,
    configured_multimodal_backend,
    configured_multimodal_dimension,
    configured_multimodal_model,
    configured_vllm_url,
)
from ..maintenance import (
    configured_maintenance_budget,
    configured_maintenance_enabled,
    configured_maintenance_profile,
    configured_prices,
    configured_profile_ladder,
    configured_token_budget_rate,
    normalize_profile_ladder,
    resolve_profile,
)
from ..maintenance.maintenance_control import (
    MaintenanceControl,
    MaintenanceControlState,
    configured_default_request_max_rounds,
)
from ..models import NamespaceEngines
from ..otel import _trace_exporter_endpoint
from ..providers.model_catalog import _safe_endpoint
from ..providers.role_config import (
    resolve_maintenance_provider_settings,
    resolve_parser_provider_settings,
)
from .identity import auth_mode

_DESIRED_KEYS = frozenset({
    "auth_mode",
    "multimodal_enabled",
    "otel_enabled",
    "parser_model",
    "maintenance_model",
    "parser_provider",
    "maintenance_provider",
    "maintenance_provider_chain",
    "parser_base_url",
    "maintenance_base_url",
    "embedding_max_model_len",
    "embedding_crop_token_budget",
    "codex_memory_enabled",
    "codex_memory_max_records_per_capture",
    "codex_memory_max_recall_records",
    "maintenance_default_request_max_rounds",
    "maintenance_enabled",
    "maintenance_profile",
    "maintenance_profile_ladder",
    "maintenance_token_budget_rate",
    "maintenance_model_class",
    "maintenance_budget",
})
_SECRET_WORDS = ("token", "secret", "password", "api_key", "credential")
_WORKER_PROVIDERS = frozenset({"fake", "ollama", "gemini", "openai", "azure", "azure_openai", "vertex", "router", "llm_router", "codex"})
JsonObject = dict[str, JsonValue]


class SettingsPipelineLike(Protocol):
    """Product pipeline surface required for settings inspection and updates."""

    @property
    def engines(self) -> NamespaceEngines: ...

    @property
    def multimodal_encoder(self) -> MultimodalEncoder | None: ...

    @property
    def telemetry(self) -> object: ...


class SettingsMemoryLike(Protocol):
    """Mutable memory settings exposed through the operator settings surface."""

    enabled: bool
    max_records_per_capture: int
    max_recall_records: int


def _redact(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): ("[redacted]" if any(word in str(key).lower() for word in _SECRET_WORDS) else _redact(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _object_mapping(value: object) -> Mapping[str, JsonValue]:
    return cast(Mapping[str, JsonValue], value) if isinstance(value, Mapping) else {}


def _setting_int(value: object, *, name: str) -> int:
    if not isinstance(value, (int, float, str)):
        raise ValueError(f"{name} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc


class SettingsService:
    """Expose safe operational state without becoming a second runtime config system."""

    __slots__ = (
        "_runtime_multimodal_enabled",
        "_runtime_otel_enabled",
        "codex_memory",
        "path",
        "pipeline",
    )

    def __init__(
        self,
        pipeline: SettingsPipelineLike,
        *,
        path: str | Path | None = None,
        codex_memory: SettingsMemoryLike | None = None,
    ) -> None:
        self.pipeline = pipeline
        self.codex_memory = codex_memory
        self._runtime_multimodal_enabled: bool | None = None
        self._runtime_otel_enabled: bool | None = None
        configured_path = path or os.getenv("LLM_WIKI_SETTINGS_PATH")
        data_dir = os.getenv("KOGWISTAR_DATA_DIR")
        self.path = Path(configured_path) if configured_path else (
            Path(data_dir) / "settings" / "desired.json" if data_dir else None
        )

    def _load_desired(self) -> JsonObject:
        if self.path is None or not self.path.exists():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"cannot read desired settings: {exc}") from exc
        if not isinstance(payload, dict):
            raise TypeError("desired settings must be a JSON object")
        values = payload.get("settings", payload)
        if not isinstance(values, dict):
            raise TypeError("desired settings.settings must be a JSON object")
        return cast(JsonObject, {key: value for key, value in values.items() if key in _DESIRED_KEYS})

    def _save_desired(self, desired: Mapping[str, JsonValue]) -> None:
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

    def _effective_embeddings(self) -> JsonObject:
        result: JsonObject = {}
        for space in ("conversation", "workflow", "kg", "wisdom", "derived_knowledge"):
            engine = getattr(self.pipeline.engines, space, None)
            if engine is None:
                continue
            report = getattr(engine, "embedding_profile_report", None)
            if callable(report):
                report = report()
            if not isinstance(report, Mapping):
                profile = getattr(engine, "embedding_profile", None)
                to_dict = getattr(profile, "to_dict", None)
                report = to_dict() if callable(to_dict) else {}
            report_mapping = _object_mapping(report)
            profile_payload = (
                report_mapping.get("registered")
                or report_mapping.get("configured")
                or report_mapping
            )
            backend = getattr(engine, "backend", None)
            result[space] = {
                "backend": type(backend).__name__ if backend is not None else "unknown",
                "profile": _redact(dict(profile_payload)) if isinstance(profile_payload, Mapping) else {},
                "profile_locked": bool(report.get("registered")) if isinstance(report, Mapping) else bool(report),
            }
        return result

    def snapshot(self, *, workspace_id: str = "default") -> JsonObject:
        parser = resolve_parser_provider_settings().parser
        maintenance = resolve_maintenance_provider_settings().parser
        data_dir = os.getenv("KOGWISTAR_DATA_DIR")
        maintenance_state = MaintenanceControl(data_dir).get() if data_dir else MaintenanceControlState(
            enabled=configured_maintenance_enabled(),
            profile=configured_maintenance_profile(),
            budget=configured_maintenance_budget(),
        )
        configured_budget = maintenance_state.budget or configured_maintenance_budget()
        profile_status = resolve_profile(
            enabled=maintenance_state.enabled,
            requested=maintenance_state.profile,
            budget=configured_budget,
        )
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
            "maintenance": {
                "provider": maintenance.provider,
                "provider_chain": [maintenance.provider, *[item.provider for item in getattr(maintenance, "fallback_specs", [])]],
                "model": maintenance.model,
                "base_url": _safe_endpoint(maintenance.base_url or ""),
                "temperature": maintenance.temperature,
                "default_request_max_rounds": configured_default_request_max_rounds(),
                "enabled": maintenance_state.enabled,
                "requested_profile": maintenance_state.profile,
                "profile_ladder": maintenance_state.profile_ladder if maintenance_state.profile_ladder_configured else [
                    {
                        "name": level.name,
                        "provider": level.provider,
                        "model": level.model,
                        "profile": level.profile,
                        "budget": level.budget,
                    }
                    for level in configured_profile_ladder()
                ],
                "effective_profile": profile_status.effective,
                "profile_reason": profile_status.reason,
                "budget": configured_budget,
                "spend": maintenance_state.spend,
                "token_budget_rate": configured_token_budget_rate(),
                "budget_window_mode": os.getenv("LLM_WIKI_MAINTENANCE_BUDGET_WINDOW_MODE", "rolling"),
                "prices_per_1m": configured_prices(),
            },
            "embeddings": self._effective_embeddings(),
            "multimodal": {
                "enabled": multimodal_enabled,
                "configured": bool(
                    multimodal or configured_embedding_service_url() or configured_vllm_url()
                ),
                "backend": configured_multimodal_backend(),
                "model": getattr(getattr(multimodal, "profile", None), "model", configured_multimodal_model()),
                "dimension": getattr(getattr(multimodal, "profile", None), "dimension", configured_multimodal_dimension()),
                "max_model_len": configured_embedding_max_model_len(),
                "crop_token_budget": configured_embedding_crop_token_budget(),
                "service_url": configured_embedding_service_url(),
                "vllm_url": configured_vllm_url(),
            },
            "auth_mode": auth_mode(),
            "otel": {
                "enabled": otel_enabled,
                "configured": _trace_exporter_endpoint() is not None,
                "endpoint": _trace_exporter_endpoint(),
                "service_name": os.getenv("LLM_WIKI_OTEL_SERVICE_NAME", "kogwistar-llm-wiki"),
                "packages_available": telemetry is not None and getattr(telemetry, "packages_available", True),
            },
            "codex_memory": {
                "enabled": bool(
                    getattr(self.codex_memory, "enabled", False)
                    if self.codex_memory is not None
                    else os.getenv("LLM_WIKI_CODEX_MEMORY_ENABLED", "").strip().lower()
                    in {"1", "true", "yes", "on"}
                ),
                "max_records_per_capture": getattr(self.codex_memory, "max_records_per_capture", 8),
                "max_recall_records": getattr(self.codex_memory, "max_recall_records", 12),
                "policy": "grounded_plus_labeled_inference",
                "raw_transcripts": False,
            },
        }
        impact = self._impact(effective, desired)
        return cast(JsonObject, _redact({
            "version": 1,
            "effective": effective,
            "desired": desired,
            "components": self._components(effective),
            **impact,
        }))

    def health(self, *, workspace_id: str = "default", readiness: Mapping[str, JsonValue] | None = None) -> JsonObject:
        readiness = dict(readiness or self.pipeline_ready())
        multimodal = getattr(self.pipeline, "multimodal_encoder", None)
        embedding: dict[str, object] = {"state": "disabled"}
        if multimodal is not None:
            try:
                probe = multimodal.readiness() if hasattr(multimodal, "readiness") else {"ready": True}
                embedding = {"state": "up" if probe.get("ready") else "degraded", **probe}
            except Exception as exc:  # noqa: BLE001
                embedding = {"state": "unavailable", "reason": str(exc)}
        return {"version": 1, "workspace_id": workspace_id, "state": "up" if readiness.get("ready") else "degraded", "readiness": readiness, "embedding_service": _redact(embedding), "checked_at_ms": int(time.time() * 1000)}

    def update_desired(self, changes: Mapping[str, JsonValue], *, workspace_id: str = "default") -> JsonObject:
        unknown = sorted(set(changes) - _DESIRED_KEYS)
        if unknown:
            raise ValueError(f"unsupported settings: {', '.join(unknown)}")
        current = self._load_desired()
        next_desired = {**current, **dict(changes)}
        if "multimodal_enabled" in next_desired and not isinstance(next_desired["multimodal_enabled"], bool):
            raise ValueError("multimodal_enabled must be boolean")
        if "auth_mode" in next_desired and next_desired["auth_mode"] not in {"disabled", "static_token", "kogwistar_jwt"}:
            raise ValueError("auth_mode must be disabled, static_token, or kogwistar_jwt")
        for key in ("embedding_max_model_len", "embedding_crop_token_budget"):
            if key in next_desired:
                value = _setting_int(next_desired[key], name=key)
                next_desired[key] = value
                if value <= 0:
                    raise ValueError(f"{key} must be positive")
        if "codex_memory_enabled" in next_desired and not isinstance(next_desired["codex_memory_enabled"], bool):
            raise ValueError("codex_memory_enabled must be boolean")
        for key, upper in (
            ("codex_memory_max_records_per_capture", 32),
            ("codex_memory_max_recall_records", 100),
        ):
            if key in next_desired:
                value = _setting_int(next_desired[key], name=key)
                next_desired[key] = value
                if not 1 <= value <= upper:
                    raise ValueError(f"{key} must be between 1 and {upper}")
        if "maintenance_default_request_max_rounds" in next_desired:
            value = _setting_int(
                next_desired["maintenance_default_request_max_rounds"],
                name="maintenance_default_request_max_rounds",
            )
            next_desired["maintenance_default_request_max_rounds"] = value
            if not 1 <= value <= 100:
                raise ValueError("maintenance_default_request_max_rounds must be between 1 and 100")
        if "maintenance_enabled" in next_desired and not isinstance(next_desired["maintenance_enabled"], bool):
            raise ValueError("maintenance_enabled must be boolean")
        if "maintenance_profile" in next_desired and next_desired["maintenance_profile"] not in {"high", "balanced", "budgeted", "lite"}:
            raise ValueError("maintenance_profile must be high, balanced, budgeted, or lite")
        if "maintenance_profile_ladder" in next_desired:
            try:
                levels = normalize_profile_ladder(next_desired["maintenance_profile_ladder"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid maintenance_profile_ladder: {exc}") from exc
            next_desired["maintenance_profile_ladder"] = [
                {
                    "name": level.name,
                    "provider": level.provider,
                    "model": level.model,
                    "profile": level.profile,
                    "budget": level.budget,
                }
                for level in levels
            ]
        if "maintenance_token_budget_rate" in next_desired:
            value = _setting_int(
                next_desired["maintenance_token_budget_rate"],
                name="maintenance_token_budget_rate",
            )
            next_desired["maintenance_token_budget_rate"] = value
            if value <= 0:
                raise ValueError("maintenance_token_budget_rate must be positive")
        if "maintenance_budget" in next_desired:
            if not isinstance(next_desired["maintenance_budget"], Mapping):
                raise ValueError("maintenance_budget must be an object")
            from ..maintenance.maintenance_profiles import normalize_budget

            next_desired["maintenance_budget"] = normalize_budget(next_desired["maintenance_budget"])
        if (
            "embedding_max_model_len" in next_desired
            and "embedding_crop_token_budget" in next_desired
            and isinstance(next_desired["embedding_crop_token_budget"], (int, float, str))
            and isinstance(next_desired["embedding_max_model_len"], (int, float, str))
            and int(next_desired["embedding_crop_token_budget"]) > int(next_desired["embedding_max_model_len"])
        ):
            raise ValueError("embedding_crop_token_budget cannot exceed embedding_max_model_len")
        for key in ("parser_provider", "maintenance_provider"):
            if key in next_desired and str(next_desired[key]).strip().lower() not in _WORKER_PROVIDERS:
                raise ValueError(f"{key} must be a configured structured provider or router")
        if "maintenance_provider_chain" in next_desired:
            names = [part.strip().lower() for part in str(next_desired["maintenance_provider_chain"]).split(",") if part.strip()]
            if not names or any(name not in _WORKER_PROVIDERS for name in names):
                raise ValueError("maintenance_provider_chain must contain configured structured providers")
        self._save_desired(next_desired)
        return self.snapshot(workspace_id=workspace_id)

    def apply(self, *, workspace_id: str, confirmed: bool) -> JsonObject:
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
                return {"status": "rejected", "reason": "multimodal_embedding_service_not_configured", **snapshot}
            self._runtime_multimodal_enabled = desired["multimodal_enabled"]
            snapshot = self.snapshot(workspace_id=workspace_id)
        if self.codex_memory is not None and isinstance(desired, Mapping):
            if isinstance(desired.get("codex_memory_enabled"), bool):
                self.codex_memory.enabled = desired["codex_memory_enabled"]
            for key, attribute in (
                ("codex_memory_max_records_per_capture", "max_records_per_capture"),
                ("codex_memory_max_recall_records", "max_recall_records"),
            ):
                if key in desired:
                    setattr(self.codex_memory, attribute, _setting_int(desired[key], name=key))
            snapshot = self.snapshot(workspace_id=workspace_id)
        pending_changes = snapshot.get("pending_changes", [])
        if {"maintenance_enabled", "maintenance_profile", "maintenance_budget"}.intersection(
            pending_changes if isinstance(pending_changes, (list, tuple, set)) else ()
        ):
            return {"status": "staged", "reason": "maintenance_control_is_local_only", **snapshot}
        if snapshot["restart_required"] or snapshot["reembedding_required"]:
            return {"status": "staged", "reason": "restart_and_or_reembedding_required", **snapshot}
        return {"status": "applied", "applied_live": ["multimodal_enabled"] if self._runtime_multimodal_enabled is not None else [], **snapshot}

    def pipeline_ready(self) -> JsonObject:
        engines = self.pipeline.engines
        if getattr(engines, "_closed", False):
            return {"ready": False, "reason": "engines_closed"}
        return {"ready": True}

    def _backend_name(self) -> str:
        backend = getattr(self.pipeline.engines, "kg", None)
        backend = getattr(backend, "backend", None)
        return type(backend).__name__ if backend is not None else "unknown"

    def _components(self, effective: Mapping[str, JsonValue]) -> JsonObject:
        multimodal = effective["multimodal"]
        assert isinstance(multimodal, Mapping)
        otel = effective.get("otel", {})
        assert isinstance(otel, Mapping)
        return {
            "knowledge_text_embedding": {"state": "up", "toggleable": False, "description": "Local text embedding used by the configured graph backend."},
            "multimodal_embedding": {"state": "up" if multimodal["enabled"] else "disabled", "toggleable": True, "description": "Qwen3-VL remote projection route."},
            "parser": {"state": "up", "toggleable": False},
            "maintenance": {
                "state": "up" if _object_mapping(effective.get("maintenance")).get("enabled") else "paused",
                "toggleable": True,
                "profile": _object_mapping(effective.get("maintenance")).get("effective_profile"),
                "description": "Durable request maintenance plus locally controlled background profile work.",
            },
            "otel_sink": {"state": "up" if otel.get("enabled") else "disabled", "toggleable": True, "description": "Optional OpenTelemetry trace sink for the configured collector."},
            "codex_memory": {"state": "up" if _object_mapping(effective.get("codex_memory")).get("enabled") else "disabled", "toggleable": True, "description": "Project-scoped evidence-backed memory artifacts; canonical graph edits still require propose and confirm."},
        }

    @staticmethod
    def _impact(effective: Mapping[str, JsonValue], desired: Mapping[str, JsonValue]) -> JsonObject:
        restart_keys = {"auth_mode", "parser_model", "maintenance_model", "parser_provider", "maintenance_provider", "maintenance_provider_chain", "maintenance_profile_ladder", "parser_base_url", "maintenance_base_url", "embedding_max_model_len", "embedding_crop_token_budget", "maintenance_default_request_max_rounds"}
        parser = _object_mapping(effective.get("parser"))
        maintenance = _object_mapping(effective.get("maintenance"))
        otel = _object_mapping(effective.get("otel"))
        multimodal = _object_mapping(effective.get("multimodal"))
        codex_memory = _object_mapping(effective.get("codex_memory"))
        effective_values = {
            "parser_model": parser.get("model"),
            "parser_provider": parser.get("provider"),
            "parser_base_url": parser.get("base_url"),
            "maintenance_model": maintenance.get("model"),
            "maintenance_provider": maintenance.get("provider"),
            "maintenance_provider_chain": maintenance.get("provider_chain"),
            "maintenance_base_url": maintenance.get("base_url"),
            "auth_mode": effective.get("auth_mode"),
            "otel_enabled": otel.get("enabled"),
            "embedding_max_model_len": multimodal.get("max_model_len"),
            "embedding_crop_token_budget": multimodal.get("crop_token_budget"),
            "codex_memory_enabled": codex_memory.get("enabled"),
            "codex_memory_max_records_per_capture": codex_memory.get("max_records_per_capture"),
            "codex_memory_max_recall_records": codex_memory.get("max_recall_records"),
            "maintenance_default_request_max_rounds": maintenance.get("default_request_max_rounds"),
            "maintenance_enabled": maintenance.get("enabled"),
            "maintenance_profile": maintenance.get("requested_profile"),
            "maintenance_profile_ladder": maintenance.get("profile_ladder"),
            "maintenance_token_budget_rate": maintenance.get("token_budget_rate"),
            "maintenance_model_class": os.getenv("LLM_WIKI_MAINTENANCE_MODEL_CLASS", "unknown"),
            "maintenance_budget": maintenance.get("budget"),
        }
        pending_changes = sorted(
            key for key, value in desired.items()
            if key in _DESIRED_KEYS and value != effective_values.get(key, multimodal.get("enabled") if key == "multimodal_enabled" else None)
        )
        restart_required = bool(restart_keys.intersection(pending_changes))
        profile_keys = {"embedding_provider", "embedding_model", "embedding_dimension", "embedding_metric", "embedding_max_model_len", "embedding_crop_token_budget"}
        reembedding_required = bool(profile_keys.intersection(pending_changes))
        warnings: list[str] = []
        if restart_required:
            warnings.append("Model/provider changes are staged and require a graceful service restart.")
        if reembedding_required:
            warnings.append("Embedding profile changes require an isolated projection and re-embedding.")
        if {"maintenance_enabled", "maintenance_profile", "maintenance_profile_ladder", "maintenance_budget"}.intersection(pending_changes):
            warnings.append("Maintenance profile changes are applied through the local docker-exec control command.")
        return {"pending_changes": pending_changes, "restart_required": restart_required, "reembedding_required": reembedding_required, "warnings": warnings}
