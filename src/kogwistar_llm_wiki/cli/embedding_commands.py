"""Embedding profile inspection commands for the LLM-Wiki CLI."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from typing import Protocol, runtime_checkable

from ..models import NamespaceEngines
from .entrypoint_support import EngineBuilder


@runtime_checkable
class EmbeddingProfileReportingEngine(Protocol):
    """Minimal engine capability required by profile inspection commands."""

    @property
    def embedding_profile_report(self) -> object:
        """Return the backend's serializable embedding profile report."""


def _profile_report(engine: object) -> object:
    """Read the optional report capability without widening engine models."""
    if not isinstance(engine, EmbeddingProfileReportingEngine):
        raise TypeError("engine does not expose embedding_profile_report")
    return engine.embedding_profile_report


def namespace_engine_items(
    engines: NamespaceEngines,
) -> tuple[tuple[str, object | None], ...]:
    return (
        ("conversation", engines.conversation),
        ("workflow", engines.workflow),
        ("knowledge", engines.kg),
        ("wisdom", engines.wisdom),
        ("derived_knowledge", engines.derived_knowledge),
    )


def embeddings_inspect(
    args: argparse.Namespace,
    *,
    build_engines: EngineBuilder,
    close_engines: Callable[[NamespaceEngines], None],
) -> None:
    engines = build_engines(
        args.workspace,
        args.data_dir,
        args.backend,
        args.dsn,
        split_derived_knowledge=args.split_derived_knowledge,
        conversation_persistence_mode=args.conversation_persistence_mode,
        embedding_profile_mode="inspect",
    )
    try:
        print(
            json.dumps(
                {
                    label: _profile_report(engine)
                    for label, engine in namespace_engine_items(engines)
                    if engine is not None
                },
                indent=2,
                sort_keys=True,
            )
        )
    finally:
        close_engines(engines)


def embeddings_adopt_legacy(
    args: argparse.Namespace,
    *,
    build_engines: EngineBuilder,
    close_engines: Callable[[NamespaceEngines], None],
) -> None:
    if not args.acknowledge_legacy_vectors:
        raise ValueError(
            "legacy profile adoption is unsafe without --acknowledge-legacy-vectors; "
            "verify the previous provider, model, dimension, endpoint, and metric first"
        )
    engines = build_engines(
        args.workspace,
        args.data_dir,
        args.backend,
        args.dsn,
        split_derived_knowledge=args.split_derived_knowledge,
        conversation_persistence_mode=args.conversation_persistence_mode,
        embedding_profile_mode="adopt",
    )
    try:
        print(
            json.dumps(
                {
                    label: _profile_report(engine)
                    for label, engine in namespace_engine_items(engines)
                    if engine is not None
                },
                indent=2,
                sort_keys=True,
            )
        )
    finally:
        close_engines(engines)
