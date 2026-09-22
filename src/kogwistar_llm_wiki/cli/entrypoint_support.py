"""Shared lifecycle helpers for the command-line entry point."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models import NamespaceEngines


def load_env_file(path: Path) -> None:
    """Load simple dotenv assignments without overriding exported values."""
    if not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise SystemExit(f"cannot read env file {path}: {exc}") from exc
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[7:].lstrip()
        name, separator, value = stripped.partition("=")
        if not separator or not name.strip() or name.strip() in os.environ:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[name.strip()] = value


def conversation_persistence_kwargs(args: argparse.Namespace) -> dict[str, str]:
    """Forward optional engine persistence settings without changing defaults."""
    mode = str(getattr(args, "conversation_persistence_mode", "single_stage"))
    return {} if mode == "single_stage" else {"conversation_persistence_mode": mode}


def close_engines(engines: NamespaceEngines) -> None:
    """Close real engine bundles while remaining compatible with test doubles."""
    close = getattr(engines, "close", None)
    if callable(close):
        close()


def build_engines(
    workspace_id: str,
    data_dir: str | None,
    backend: str,
    dsn: str | None,
    *,
    split_derived_knowledge: bool = False,
    conversation_persistence_mode: str = "single_stage",
    embedding_profile_mode: str = "enforce",
    vector_backend: str | None = None,
) -> NamespaceEngines:
    """Construct a persistent namespace-engine bundle from CLI settings."""
    from ..ingest_pipeline import (
        build_persistent_namespace_engines,
        build_postgres_namespace_engines,
    )

    del workspace_id
    selected_backend = vector_backend or backend
    effective_data_dir = data_dir or os.environ.get("KOGWISTAR_DATA_DIR")
    if not effective_data_dir:
        raise ValueError("persistent commands require --data-dir or KOGWISTAR_DATA_DIR")
    builder_kwargs: dict[str, object] = {
        "split_derived_knowledge": split_derived_knowledge,
    }
    if embedding_profile_mode != "enforce":
        builder_kwargs["embedding_profile_mode"] = embedding_profile_mode
    if conversation_persistence_mode != "single_stage":
        builder_kwargs["conversation_persistence_mode"] = conversation_persistence_mode
    if selected_backend in {"chroma", "pinecone", "qdrant"}:
        if selected_backend != "chroma":
            builder_kwargs["vector_backend"] = selected_backend
        return build_persistent_namespace_engines(
            base_dir=effective_data_dir,
            **builder_kwargs,
        )
    if selected_backend == "postgres":
        if not dsn:
            raise ValueError("--dsn is required when --backend postgres is selected")
        return build_postgres_namespace_engines(
            base_dir=effective_data_dir,
            dsn=dsn,
            **builder_kwargs,
        )
    raise ValueError(f"Unsupported backend: {selected_backend!r}")


def build_demo_engines(
    *,
    split_derived_knowledge: bool = False,
    conversation_persistence_mode: str = "single_stage",
) -> NamespaceEngines:
    """Construct the ephemeral in-memory engine bundle used by ``demo``."""
    from ..ingest_pipeline import build_in_memory_namespace_engines

    builder_kwargs: dict[str, object] = {
        "split_derived_knowledge": split_derived_knowledge,
    }
    if conversation_persistence_mode != "single_stage":
        builder_kwargs["conversation_persistence_mode"] = conversation_persistence_mode
    return build_in_memory_namespace_engines(**builder_kwargs)


__all__ = [
    "build_demo_engines",
    "build_engines",
    "close_engines",
    "conversation_persistence_kwargs",
    "load_env_file",
]
