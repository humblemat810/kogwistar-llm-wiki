"""Lazy construction of optional Kogwistar vector backends.

The application keeps the backend selection here instead of importing vendor
SDKs from the normal engine path.  This keeps the default image small and
ensures an unused provider cannot affect startup or dependency resolution.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from importlib import import_module
from typing import Protocol, cast

from kogwistar.engine_core import GraphKnowledgeEngine, StorageBackendFactory
from kogwistar.engine_core.storage_backend import StorageBackend

SUPPORTED_BACKENDS = ("chroma", "postgres", "pinecone", "qdrant")


class _PineconeProvider(Protocol):
    @classmethod
    def from_env(
        cls,
        *,
        index_host: str | None,
        dimension: int,
        prefix: str,
        engine: GraphKnowledgeEngine,
    ) -> StorageBackend: ...


class _QdrantProvider(Protocol):
    @classmethod
    def remote(
        cls,
        url: str,
        *,
        prefix: str,
        dimension: int,
        engine: GraphKnowledgeEngine,
    ) -> StorageBackend: ...

    @classmethod
    def local(
        cls,
        path: str | None,
        *,
        prefix: str,
        dimension: int,
        engine: GraphKnowledgeEngine,
    ) -> StorageBackend: ...


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
) -> StorageBackendFactory | None:
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
) -> StorageBackendFactory:
    def factory(engine: GraphKnowledgeEngine) -> StorageBackend:
        try:
            provider = import_module("kogwistar_pinecone")
            backend = cast(_PineconeProvider, provider.PineconeBackend)
        except ImportError as exc:
            raise RuntimeError(
                "Pinecone backend is selected but kogwistar-pinecone is not "
                "installed; install the pinecone optional dependency"
            ) from exc
        return backend.from_env(
            index_host=settings.pinecone_index_host,
            dimension=settings.dimension,
            prefix=settings.prefix,
            engine=engine,
        )

    return factory


def _qdrant_factory(
    settings: VectorBackendSettings,
) -> StorageBackendFactory:
    def factory(engine: GraphKnowledgeEngine) -> StorageBackend:
        try:
            provider = import_module("kogwistar_qdrant")
            backend = cast(_QdrantProvider, provider.QdrantBackend)
        except ImportError as exc:
            raise RuntimeError(
                "Qdrant backend is selected but kogwistar-qdrant is not "
                "installed; install the qdrant optional dependency"
            ) from exc
        kwargs = {"prefix": settings.prefix, "dimension": settings.dimension, "engine": engine}
        if settings.qdrant_url:
            return backend.remote(settings.qdrant_url, **kwargs)
        return backend.local(settings.qdrant_path, **kwargs)

    return factory


__all__ = ["SUPPORTED_BACKENDS", "VectorBackendSettings", "build_backend_factory"]
