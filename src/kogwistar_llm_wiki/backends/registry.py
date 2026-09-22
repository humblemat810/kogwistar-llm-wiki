"""Lazy construction of optional Kogwistar vector backends.

The application keeps the backend selection here instead of importing vendor
SDKs from the normal engine path.  This keeps the default image small and
ensures an unused provider cannot affect startup or dependency resolution.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from kogwistar.engine_core import GraphKnowledgeEngine

SUPPORTED_BACKENDS = ("chroma", "postgres", "pinecone", "qdrant")


@dataclass(frozen=True)
class VectorBackendSettings:
    """Settings needed to construct one external vector backend."""

    name: str
    dimension: int
    prefix: str = "kogwistar"
    pinecone_index_host: str | None = None
    qdrant_url: str | None = None
    qdrant_path: str | None = None

    @classmethod
    def from_env(cls, name: str, *, dimension: int) -> VectorBackendSettings:
        return cls(
            name=name,
            dimension=dimension,
            prefix=os.environ.get("KOGWISTAR_VECTOR_PREFIX", "kogwistar"),
            pinecone_index_host=os.environ.get("PINECONE_INDEX_HOST"),
            qdrant_url=os.environ.get("QDRANT_URL"),
            qdrant_path=os.environ.get("QDRANT_PATH"),
        )


def build_backend_factory(
    settings: VectorBackendSettings,
) -> Callable[[GraphKnowledgeEngine], Any] | None:
    """Return a lazy Kogwistar backend factory for an optional provider.

    ``None`` means the built-in Chroma path remains responsible for creating
    the backend. PostgreSQL is deliberately handled by its existing builder
    because it also owns the relational metadata and transaction setup.
    """

    if settings.name in {"chroma", "postgres"}:
        return None
    if settings.name == "pinecone":
        return _pinecone_factory(settings)
    if settings.name == "qdrant":
        if not settings.qdrant_url and not settings.qdrant_path:
            raise ValueError(
                "Qdrant backend requires QDRANT_URL or QDRANT_PATH; "
                "refusing an unconfigured in-memory store"
            )
        return _qdrant_factory(settings)
    raise ValueError(
        f"Unsupported vector backend {settings.name!r}; "
        f"choose one of {', '.join(SUPPORTED_BACKENDS)}"
    )


def _pinecone_factory(
    settings: VectorBackendSettings,
) -> Callable[[GraphKnowledgeEngine], Any]:
    def factory(engine: GraphKnowledgeEngine) -> Any:
        try:
            from kogwistar_pinecone import PineconeBackend
        except ImportError as exc:
            raise RuntimeError(
                "Pinecone backend is selected but kogwistar-pinecone is not "
                "installed; install the pinecone optional dependency"
            ) from exc
        return PineconeBackend.from_env(
            index_host=settings.pinecone_index_host,
            dimension=settings.dimension,
            prefix=settings.prefix,
            engine=engine,
        )

    return factory


def _qdrant_factory(
    settings: VectorBackendSettings,
) -> Callable[[GraphKnowledgeEngine], Any]:
    def factory(engine: GraphKnowledgeEngine) -> Any:
        try:
            from kogwistar_qdrant import QdrantBackend
        except ImportError as exc:
            raise RuntimeError(
                "Qdrant backend is selected but kogwistar-qdrant is not "
                "installed; install the qdrant optional dependency"
            ) from exc
        kwargs = {"prefix": settings.prefix, "dimension": settings.dimension, "engine": engine}
        if settings.qdrant_url:
            return QdrantBackend.remote(settings.qdrant_url, **kwargs)
        return QdrantBackend.local(settings.qdrant_path, **kwargs)

    return factory


__all__ = ["SUPPORTED_BACKENDS", "VectorBackendSettings", "build_backend_factory"]
