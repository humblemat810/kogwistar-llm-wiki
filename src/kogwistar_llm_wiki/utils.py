from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from kogwistar.engine_core.engine import (
    GraphKnowledgeEngine,
)
from kogwistar.engine_core.engine import (
    _NamespacedEngineProxy as _CoreNamespacedEngineProxy,
)
from kogwistar.engine_core.engine import (
    scoped_namespace as _core_scoped_namespace,
)

from .configuration.identity import durable_storage_namespace_context

# Re-export the core namespace proxy so app tests and runtime use the same
# implementation that ``kogwistar.engine_core.engine.scoped_namespace`` binds.
_NamespacedEngineProxy = _CoreNamespacedEngineProxy


@contextmanager
def _temporary_namespace(engine: GraphKnowledgeEngine, namespace: str) -> Iterator[None]:
    """Compatibility wrapper over the core namespace scoping primitive."""
    with _core_scoped_namespace(engine, namespace):
        yield


@contextmanager
def _background_namespace(engine: GraphKnowledgeEngine, namespace: str) -> Iterator[None]:
    """Scope graph operations and lane-message projection to one background namespace."""

    with durable_storage_namespace_context(namespace), _temporary_namespace(engine, namespace):
        yield
