from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from kogwistar.engine_core.engine import (
    GraphKnowledgeEngine,
    _NamespacedEngineProxy as _CoreNamespacedEngineProxy,
    scoped_namespace as _core_scoped_namespace,
)


# Re-export the core namespace proxy so app tests and runtime use the same
# implementation that ``kogwistar.engine_core.engine.scoped_namespace`` binds.
_NamespacedEngineProxy = _CoreNamespacedEngineProxy


@contextmanager
def _temporary_namespace(engine: GraphKnowledgeEngine, namespace: str) -> Iterator[None]:
    """Compatibility wrapper over the core namespace scoping primitive."""
    with _core_scoped_namespace(engine, namespace):
        yield
