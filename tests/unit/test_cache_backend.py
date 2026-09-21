from __future__ import annotations

from types import SimpleNamespace

import pytest
from kogwistar.utils import cache_backend


def test_none_cache_provider_preserves_memory_shape(tmp_path) -> None:
    memory = cache_backend.CacheMemory(tmp_path, backend="none")
    calls = 0

    @memory.cache
    def compute(value: int) -> int:
        nonlocal calls
        calls += 1
        return value + 1

    assert compute(1) == 2
    assert compute(1) == 2
    assert calls == 2
    assert memory.backend == "none"


def test_auto_selects_diskcache_for_pypy(monkeypatch, tmp_path) -> None:
    class FakeCache:
        def __init__(self, location: str) -> None:
            self.location = location

        def memoize(self, *, ignore=()):
            def decorate(function):
                return function

            return decorate

    monkeypatch.setattr(
        cache_backend,
        "sys",
        SimpleNamespace(implementation=SimpleNamespace(name="pypy")),
    )
    monkeypatch.setitem(
        __import__("sys").modules,
        "diskcache",
        SimpleNamespace(Cache=FakeCache),
    )

    memory = cache_backend.CacheMemory(tmp_path)

    assert memory.backend == "diskcache"


def test_invalid_cache_provider_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("KOGWISTAR_CACHE_BACKEND", "invalid")

    try:
        cache_backend.CacheMemory()
    except ValueError as exc:
        assert "KOGWISTAR_CACHE_BACKEND" in str(exc)
    else:
        raise AssertionError("invalid cache provider was accepted")


def test_diskcache_persists_and_honors_ignored_arguments(tmp_path) -> None:
    pytest.importorskip("diskcache")
    memory = cache_backend.CacheMemory(tmp_path, backend="diskcache")
    calls = 0

    @memory.cache(ignore=["agent"])
    def compute(agent: object, value: int) -> int:
        nonlocal calls
        calls += 1
        return value + 1

    assert compute(object(), 1) == 2
    assert compute(object(), 1) == 2
    assert calls == 1
    assert cache_backend.CacheMemory(tmp_path, backend="diskcache").backend == "diskcache"


def test_diskcache_clear_removes_cached_value(tmp_path) -> None:
    pytest.importorskip("diskcache")
    memory = cache_backend.CacheMemory(tmp_path, backend="diskcache")
    calls = 0

    @memory.cache
    def compute(value: int) -> int:
        nonlocal calls
        calls += 1
        return calls + value

    assert compute(1) == 2
    assert compute(1) == 2
    memory.clear()
    assert compute(1) == 3
    memory.close()
