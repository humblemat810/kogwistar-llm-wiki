from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any


# ---------------------------------------------------------------------------
# CoW namespace proxy
# ---------------------------------------------------------------------------

class _NamespacedEngineProxy:
    """Copy-on-Write namespace view over a ``GraphKnowledgeEngine``.

    Intercepts ``.namespace`` reads and returns the scoped value.
    All other attribute access and mutation is delegated transparently to the
    real engine. The real engine's ``.namespace`` attribute is **never touched**.

    This is the building block for :func:`_temporary_namespace`.
    """

    def __init__(self, real_engine: Any, namespace: str) -> None:
        # Bypass our own __setattr__ to avoid recursion.
        object.__setattr__(self, "_real", real_engine)
        object.__setattr__(self, "_ns", namespace)

    def __getattribute__(self, name: str) -> Any:
        # Short-circuit our own private attrs.
        if name in ("_real", "_ns"):
            return object.__getattribute__(self, name)
        # Intercept: return the scoped namespace.
        if name == "namespace":
            return object.__getattribute__(self, "_ns")
        # Delegate everything else to the real engine.
        return getattr(object.__getattribute__(self, "_real"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "namespace":
            object.__setattr__(self, "_ns", value)
        elif name in ("_real", "_ns"):
            object.__setattr__(self, name, value)
        else:
            setattr(object.__getattribute__(self, "_real"), name, value)

    def __repr__(self) -> str:
        ns = object.__getattribute__(self, "_ns")
        real = object.__getattribute__(self, "_real")
        return f"<NamespacedEngineProxy ns={ns!r} over {type(real).__name__}>"


# Per-engine reentrant lock — allows safe nesting by the same thread while
# blocking a second thread from switching namespace mid-operation.
_engine_ns_locks: dict[int, threading.RLock] = {}
_engine_ns_locks_meta = threading.Lock()


def _get_engine_lock(engine: Any) -> threading.RLock:
    eid = id(engine)
    with _engine_ns_locks_meta:
        if eid not in _engine_ns_locks:
            _engine_ns_locks[eid] = threading.RLock()
        return _engine_ns_locks[eid]


# Subsystems whose engine ref is stored as ``self._e`` (NamespaceProxy base).
_SUBSYSTEMS_WITH_E = (
    "read", "write", "extract", "persist",
    "rollback", "adjudicate", "ingest", "embed", "lifecycle",
)


@contextmanager
def _temporary_namespace(engine: Any, namespace: str):
    """CoW namespace scope for a ``GraphKnowledgeEngine``.

    Creates a :class:`_NamespacedEngineProxy` that shadows ``.namespace`` and
    temporarily rebinds **all subsystem engine references** to the proxy:

    * ``subsystem._e``  — for every ``NamespaceProxy`` subclass
      (``read``, ``write``, ``extract``, ``persist``, ``rollback``,
      ``adjudicate``, ``ingest``, ``embed``, ``lifecycle``)
    * ``indexing.engine`` — ``IndexingSubsystem`` is a dataclass using a
      different field name

    The real engine's ``.namespace`` attribute is **never mutated**.

    **Backend semantics** — for both the in-memory and ChromaDB backends
    ``engine.namespace`` is only used to tag entity events in the SQLite
    meta-store.  Data storage and retrieval are isolated entirely through
    ``where``-clause metadata filters (``workspace_id``, ``artifact_kind``…).

    **Thread-safety** — a per-engine ``threading.RLock`` serialises namespace
    switches so concurrent callers sharing one engine cannot clobber each
    other's scoped namespace.  Reentrant: the same thread may safely nest
    ``_temporary_namespace`` calls.
    """
    proxy = _NamespacedEngineProxy(engine, namespace)

    # Collect (object, attribute_name, old_value) triples for rollback.
    rebindings: list[tuple[Any, str, Any]] = []
    for sub_name in _SUBSYSTEMS_WITH_E:
        sub = getattr(engine, sub_name, None)
        if sub is not None and hasattr(sub, "_e"):
            rebindings.append((sub, "_e", sub._e))

    # IndexingSubsystem is a @dataclass — its field is called ``engine``.
    indexing = getattr(engine, "indexing", None)
    if indexing is not None and hasattr(indexing, "engine"):
        rebindings.append((indexing, "engine", indexing.engine))

    lock = _get_engine_lock(engine)
    with lock:
        for obj, attr, _ in rebindings:
            setattr(obj, attr, proxy)
        try:
            yield
        finally:
            for obj, attr, old_val in rebindings:
                setattr(obj, attr, old_val)
