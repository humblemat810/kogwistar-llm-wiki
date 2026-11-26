"""Root Rust facade loading and Python fallback contracts.

The extension is optional.  The stable seam is this module: callers choose
``native``, ``python``, or the default ``auto`` implementation mode without
having to know whether a build artifact is present.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from types import ModuleType
from typing import Any, Mapping

from kogwistar.id_provider import stable_id
from kogwistar.provenance import canonicalize_evidence_pack_digest, evidence_pack_digest_hash

from .parsing.parse_quality import basic_sense_eval_from_graph_payload

NATIVE_MODULE_NAME = "_kogwistar_llm_wiki_rust"
IMPLEMENTATION_ENV = "KOGWISTAR_LLM_WIKI_RUST"
_IMPLEMENTATION_MODES = {"auto", "native", "python"}


class RustFacadeError(RuntimeError):
    """Raised when a required Rust facade is unavailable or misconfigured."""


def implementation_mode() -> str:
    """Return the requested implementation mode after validating its value."""

    mode = os.getenv(IMPLEMENTATION_ENV, "auto").strip().lower() or "auto"
    if mode not in _IMPLEMENTATION_MODES:
        raise RustFacadeError(
            f"{IMPLEMENTATION_ENV} must be one of: auto, native, python"
        )
    return mode


def native_module() -> ModuleType | None:
    """Load the optional root extension, returning ``None`` when unavailable."""

    if implementation_mode() == "python":
        return None
    try:
        return importlib.import_module(NATIVE_MODULE_NAME)
    except ImportError as exc:
        if implementation_mode() == "native":
            raise RustFacadeError(
                f"{NATIVE_MODULE_NAME} is unavailable; build the root Rust facade"
            ) from exc
        return None


def require_native_module() -> ModuleType:
    """Load the root extension or explain that the package must be built."""

    module = native_module()
    if module is None:
        raise ImportError(
            f"{NATIVE_MODULE_NAME} is unavailable; build the root Rust facade"
        )
    return module


def implementation_name() -> str:
    """Return whether the current JSON facade would use native or Python."""

    return "native" if native_module() is not None else "python"


def stable_id_json(payload_json: str) -> str:
    """Return one stable UUID from the versioned JSON array contract."""

    module = native_module()
    if module is not None and hasattr(module, "stable_id_json"):
        return str(module.stable_id_json(payload_json))
    try:
        values = json.loads(payload_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("stable ID payload must be a JSON array") from exc
    if not isinstance(values, list) or not values or not isinstance(values[0], str):
        raise ValueError("stable ID payload must start with a string kind")
    parts = [str(value) for value in values[1:]]
    return str(stable_id(values[0], *parts))


def canonical_json(value: Any) -> str:
    """Return compact, sorted, ASCII-escaped canonical JSON."""

    module = native_module()
    if module is not None and hasattr(module, "canonical_json"):
        return str(module.canonical_json(json.dumps(value, ensure_ascii=False, separators=(",", ":"))))
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def evidence_pack_digest(digest: Mapping[str, Any]) -> str:
    """Return the deterministic evidence-pack hash for a mapping or model."""

    module = native_module()
    if module is not None and hasattr(module, "evidence_pack_digest_hash"):
        return str(module.evidence_pack_digest_hash(json.dumps(dict(digest), ensure_ascii=False, separators=(",", ":"))))
    return evidence_pack_digest_hash(dict(digest))


def basic_sense_eval(graph_payload: Mapping[str, Any], diagnostics: Mapping[str, Any]) -> dict[str, Any]:
    """Return basic structural quality evaluation using native or Python code."""

    module = native_module()
    if module is not None and hasattr(module, "basic_sense_eval"):
        payload_json = json.dumps(
            {"graph_payload": dict(graph_payload), "diagnostics": dict(diagnostics)},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return dict(json.loads(str(module.basic_sense_eval(payload_json))))
    return basic_sense_eval_from_graph_payload(
        graph_payload=dict(graph_payload),
        diagnostics=dict(diagnostics),
    )


def canonicalized_evidence_pack(digest: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize evidence-pack identifiers without choosing a hash backend."""

    return dict(canonicalize_evidence_pack_digest(dict(digest)))


__all__ = [
    "IMPLEMENTATION_ENV",
    "NATIVE_MODULE_NAME",
    "RustFacadeError",
    "basic_sense_eval",
    "canonical_json",
    "canonicalized_evidence_pack",
    "evidence_pack_digest",
    "implementation_mode",
    "implementation_name",
    "native_module",
    "require_native_module",
    "stable_id_json",
    "sys",
]
