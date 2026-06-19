"""Unit tests for utils._temporary_namespace (CoW proxy semantics)."""
from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from kogwistar_llm_wiki.utils import _temporary_namespace, _NamespacedEngineProxy


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine(namespace: str = "default"):
    """Minimal engine mock with subsystem structure."""
    engine = MagicMock()
    engine.namespace = namespace

    # Simulate subsystems with _e pointing back to engine
    for sub_name in ("read", "write", "extract", "persist",
                     "rollback", "adjudicate", "ingest", "embed", "lifecycle"):
        sub = MagicMock()
        sub._e = engine
        setattr(engine, sub_name, sub)

    # Simulate IndexingSubsystem (dataclass — uses .engine not ._e)
    indexing = MagicMock()
    indexing.engine = engine
    engine.indexing = indexing

    return engine


# ---------------------------------------------------------------------------
# Proxy tests
# ---------------------------------------------------------------------------

class TestNamespacedEngineProxy:
    def test_namespace_intercepted(self):
        engine = _make_engine("original")
        proxy = _NamespacedEngineProxy(engine, "scoped")
        assert proxy.namespace == "scoped"

    def test_real_engine_namespace_unchanged(self):
        engine = _make_engine("original")
        proxy = _NamespacedEngineProxy(engine, "scoped")
        _ = proxy.namespace
        assert engine.namespace == "original"

    def test_other_attrs_delegated(self):
        engine = _make_engine()
        engine.kg_graph_type = "conversation"
        proxy = _NamespacedEngineProxy(engine, "scoped")
        assert proxy.kg_graph_type == "conversation"

    def test_setattr_on_proxy_namespace_stays_local(self):
        engine = _make_engine("original")
        proxy = _NamespacedEngineProxy(engine, "scoped")
        proxy.namespace = "updated"
        assert proxy.namespace == "updated"
        assert engine.namespace == "original"   # real engine untouched

    def test_setattr_other_delegates_to_engine(self):
        engine = _make_engine()
        proxy = _NamespacedEngineProxy(engine, "scoped")
        proxy.some_attr = 42
        assert engine.some_attr == 42


# ---------------------------------------------------------------------------
# _temporary_namespace tests
# ---------------------------------------------------------------------------

class TestTemporaryNamespace:
    def test_real_engine_namespace_never_mutated(self):
        engine = _make_engine("original")
        with _temporary_namespace(engine, "scoped"):
            assert engine.namespace == "original"  # CoW: untouched
        assert engine.namespace == "original"

    def test_subsystem_e_rebound_inside_block(self):
        engine = _make_engine("original")
        with _temporary_namespace(engine, "scoped"):
            # Inside the block, engine.write._e should be the proxy
            proxy = engine.write._e
            assert isinstance(proxy, _NamespacedEngineProxy)
            assert proxy.namespace == "scoped"

    def test_subsystem_e_restored_after_block(self):
        engine = _make_engine("original")
        with _temporary_namespace(engine, "scoped"):
            pass
        assert engine.write._e is engine

    def test_indexing_engine_rebound_and_restored(self):
        engine = _make_engine("original")
        with _temporary_namespace(engine, "scoped"):
            assert isinstance(engine.indexing.engine, _NamespacedEngineProxy)
            assert engine.indexing.engine.namespace == "scoped"
        assert engine.indexing.engine is engine

    def test_restores_on_exception(self):
        engine = _make_engine("original")
        try:
            with _temporary_namespace(engine, "scoped"):
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        assert engine.write._e is engine
        assert engine.indexing.engine is engine
        assert engine.namespace == "original"

    def test_nesting_same_thread(self):
        """Same thread may safely nest _temporary_namespace (RLock is reentrant)."""
        engine = _make_engine("original")
        with _temporary_namespace(engine, "outer"):
            with _temporary_namespace(engine, "inner"):
                assert engine.write._e.namespace == "inner"
            # inner restored
            assert engine.write._e.namespace == "outer"
        assert engine.write._e is engine

    def test_thread_safety_concurrent_callers(self):
        """Two threads sharing one engine must not see each other's namespace."""
        engine = _make_engine("default")
        observed: dict[str, str] = {}
        errors: list[Exception] = []

        def worker(ns: str):
            try:
                with _temporary_namespace(engine, ns):
                    # The proxy seen by this thread's subsystems should carry its namespace.
                    seen = engine.write._e.namespace
                    observed[ns] = seen
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=worker, args=("ns_alpha",))
        t2 = threading.Thread(target=worker, args=("ns_beta",))
        t1.start(); t2.start()
        t1.join(); t2.join()

        assert not errors, errors
        # Each thread must have seen exactly its own namespace.
        assert observed.get("ns_alpha") == "ns_alpha"
        assert observed.get("ns_beta") == "ns_beta"
