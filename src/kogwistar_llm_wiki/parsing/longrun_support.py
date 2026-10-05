"""Small persistence and diagnostics helpers for the long-run parser worker."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast


def now_ms() -> int:
    return int(time.time() * 1000)


def write_json_file(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def append_trace_line(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{now_ms()} | {message}\n")


def object_mapping(value: object) -> dict[str, object]:
    """Narrow a decoded JSON object at an external process boundary."""

    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in value.items()}


def payload_path(payload: Mapping[str, object], key: str) -> Path:
    value = payload.get(key)
    if not isinstance(value, (str, Path)):
        raise ValueError(f"parser child payload field {key!r} must be a path")
    return Path(value)


def payload_int(payload: Mapping[str, object], key: str, *, default: int) -> int:
    value = payload.get(key)
    if value is None:
        return default
    if isinstance(value, bool):
        raise ValueError(f"parser child payload field {key!r} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError as exc:
            raise ValueError(f"parser child payload field {key!r} must be an integer") from exc
    raise ValueError(f"parser child payload field {key!r} must be an integer")


def close_resources_quietly(*resources: object) -> None:
    """Close child-owned backend resources without masking the parse result."""

    for resource in resources:
        close = getattr(resource, "close", None)
        if not callable(close):
            continue
        try:
            close()
        except Exception:  # noqa: BLE001, S112 - cleanup must not mask the original failure
            continue


def dump_model(value: object) -> object:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        model_dump_fn = cast(Callable[..., object], model_dump)
        try:
            return model_dump_fn(field_mode="backend", dump_format="json")
        except TypeError:
            return model_dump_fn()
    if isinstance(value, dict):
        return {str(key): dump_model(item) for key, item in value.items()}
    if isinstance(value, list):
        return [dump_model(item) for item in value]
    return value


def proposal_mode_summary(final_state: dict[str, object]) -> dict[str, object]:
    current_layer_result = object_mapping(final_state.get("current_layer_result"))
    metadata = object_mapping(current_layer_result.get("metadata"))
    if not metadata:
        return {}
    summary: dict[str, object] = {
        "proposal_mode": metadata.get("proposal_mode"),
        "proposal_source": metadata.get("proposal_source"),
        "proposal_failure_reason": metadata.get("proposal_failure_reason"),
        "boundary_proposed_count": metadata.get("boundary_proposed_count"),
        "boundary_dropped_count": metadata.get("boundary_dropped_count"),
        "boundary_accepted_count": metadata.get("boundary_accepted_count"),
        "boundary_shifted_count": metadata.get("boundary_shifted_count"),
        "boundary_rejected_count": metadata.get("boundary_rejected_count"),
        "boundary_refinement_count": metadata.get("boundary_refinement_count"),
        "boundary_refinement_attempts": metadata.get("boundary_refinement_attempts"),
        "boundary_summary_count": metadata.get("boundary_summary_count"),
        "unresolved_interval_count": metadata.get("unresolved_interval_count"),
        "provider_child_count": metadata.get("provider_child_count"),
    }
    return {key: value for key, value in summary.items() if value is not None}
