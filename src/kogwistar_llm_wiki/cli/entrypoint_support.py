"""Shared lifecycle helpers for the command-line entry point."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol, TypedDict

if TYPE_CHECKING:
    from ..models import NamespaceEngines


class PersistenceKwargs(TypedDict, total=False):
    """Optional keyword arguments shared by engine and pipeline builders."""

    conversation_persistence_mode: Literal["single_stage", "two_stage"]


class PersistenceKwargsFactory(Protocol):
    """Build optional persistence settings from parsed CLI arguments."""

    def __call__(self, args: argparse.Namespace, /) -> PersistenceKwargs: ...


class EngineBuilder(Protocol):
    """Construct the namespace engine bundle used by CLI commands."""

    def __call__(
        self,
        workspace_id: str,
        data_dir: str | None,
        backend: str,
        dsn: str | None,
        /,
        *,
        split_derived_knowledge: bool = False,
        conversation_persistence_mode: Literal["single_stage", "two_stage"] = "single_stage",
        embedding_profile_mode: Literal["enforce", "inspect", "adopt"] = "enforce",
        vector_backend: str | None = None,
    ) -> NamespaceEngines: ...


class DemoEngineBuilder(Protocol):
    """Construct the in-memory engine bundle used by the demo command."""

    def __call__(
        self,
        *,
        split_derived_knowledge: bool = False,
        conversation_persistence_mode: Literal["single_stage", "two_stage"] = "single_stage",
    ) -> NamespaceEngines: ...


class EngineCloser(Protocol):
    """Release the engine bundle owned by one CLI command."""

    def __call__(self, engines: NamespaceEngines, /) -> None: ...


class PersistentBuilderKwargs(TypedDict, total=False):
    """Typed optional arguments accepted by the public engine builders."""

    split_derived_knowledge: bool
    conversation_persistence_mode: Literal["single_stage", "two_stage"]
    embedding_profile_mode: Literal["enforce", "inspect", "adopt"]
    vector_backend: str


class PostgresBuilderKwargs(TypedDict, total=False):
    """Typed optional arguments accepted by the PostgreSQL builder."""

    split_derived_knowledge: bool
    conversation_persistence_mode: Literal["single_stage", "two_stage"]
    embedding_profile_mode: Literal["enforce", "inspect", "adopt"]
    postgres_embedding_layout: Literal["shared", "profile_isolated"]


class InMemoryBuilderKwargs(TypedDict, total=False):
    """Typed optional arguments accepted by the in-memory builder."""

    split_derived_knowledge: bool
    conversation_persistence_mode: Literal["single_stage", "two_stage"]


def _conversation_mode(value: str) -> Literal["single_stage", "two_stage"]:
    if value == "single_stage" or value == "two_stage":
        return value
    raise ValueError(f"unsupported conversation persistence mode: {value}")


def _embedding_profile_mode(value: str) -> Literal["enforce", "inspect", "adopt"]:
    if value == "enforce" or value == "inspect" or value == "adopt":
        return value
    raise ValueError(f"unsupported embedding profile mode: {value}")


def _postgres_layout(value: str) -> Literal["shared", "profile_isolated"]:
    if value == "shared" or value == "profile_isolated":
        return value
    raise ValueError(f"unsupported PostgreSQL embedding layout: {value}")


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


def conversation_persistence_kwargs(args: argparse.Namespace) -> PersistenceKwargs:
    """Forward optional engine persistence settings without changing defaults."""
    mode = _conversation_mode(
        str(getattr(args, "conversation_persistence_mode", "single_stage"))
    )
    if mode == "single_stage":
        return PersistenceKwargs()
    return PersistenceKwargs(conversation_persistence_mode=mode)


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
    conversation_persistence_mode: Literal["single_stage", "two_stage"] = "single_stage",
    embedding_profile_mode: Literal["enforce", "inspect", "adopt"] = "enforce",
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
    mode = _conversation_mode(conversation_persistence_mode)
    profile_mode = _embedding_profile_mode(embedding_profile_mode)
    builder_kwargs: PersistentBuilderKwargs = {
        "split_derived_knowledge": split_derived_knowledge,
    }
    if profile_mode != "enforce":
        builder_kwargs["embedding_profile_mode"] = profile_mode
    if mode != "single_stage":
        builder_kwargs["conversation_persistence_mode"] = mode
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
        postgres_layout = _postgres_layout(
            os.environ.get("KOGWISTAR_POSTGRES_EMBEDDING_LAYOUT", "shared")
            .strip()
            .lower()
        )
        if postgres_layout != "shared":
            postgres_kwargs: PostgresBuilderKwargs = {
                "split_derived_knowledge": split_derived_knowledge,
                "embedding_profile_mode": profile_mode,
                "postgres_embedding_layout": postgres_layout,
            }
            if mode != "single_stage":
                postgres_kwargs["conversation_persistence_mode"] = mode
        else:
            postgres_kwargs = {
                "split_derived_knowledge": split_derived_knowledge,
                "embedding_profile_mode": profile_mode,
            }
            if mode != "single_stage":
                postgres_kwargs["conversation_persistence_mode"] = mode
        return build_postgres_namespace_engines(
            base_dir=effective_data_dir,
            dsn=dsn,
            **postgres_kwargs,
        )
    raise ValueError(f"Unsupported backend: {selected_backend!r}")


def build_demo_engines(
    *,
    split_derived_knowledge: bool = False,
    conversation_persistence_mode: str = "single_stage",
) -> NamespaceEngines:
    """Construct the ephemeral in-memory engine bundle used by ``demo``."""
    from ..ingest_pipeline import build_in_memory_namespace_engines

    mode = _conversation_mode(conversation_persistence_mode)
    builder_kwargs: InMemoryBuilderKwargs = {
        "split_derived_knowledge": split_derived_knowledge,
    }
    if mode != "single_stage":
        builder_kwargs["conversation_persistence_mode"] = mode
    return build_in_memory_namespace_engines(**builder_kwargs)


__all__ = [
    "InMemoryBuilderKwargs",
    "PersistenceKwargs",
    "PersistenceKwargsFactory",
    "EngineCloser",
    "PersistentBuilderKwargs",
    "PostgresBuilderKwargs",
    "build_demo_engines",
    "build_engines",
    "close_engines",
    "conversation_persistence_kwargs",
    "load_env_file",
]
