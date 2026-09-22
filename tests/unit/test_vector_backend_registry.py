from __future__ import annotations

import sys
import types

import pytest

from kogwistar_llm_wiki.backends import (
    SUPPORTED_BACKENDS,
    VectorBackendSettings,
    build_backend_factory,
)


def test_registry_does_not_import_optional_provider_when_not_selected() -> None:
    assert SUPPORTED_BACKENDS == ("chroma", "postgres", "pinecone", "qdrant")
    assert build_backend_factory(VectorBackendSettings("chroma", 1024)) is None
    assert "kogwistar_pinecone" not in sys.modules
    assert "kogwistar_qdrant" not in sys.modules


def test_pinecone_provider_is_loaded_only_when_factory_is_called(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    class FakeBackend:
        @classmethod
        def from_env(cls, **kwargs: object) -> object:
            calls.append(kwargs)
            return cls()

    monkeypatch.setitem(sys.modules, "kogwistar_pinecone", types.SimpleNamespace(PineconeBackend=FakeBackend))
    factory = build_backend_factory(
        VectorBackendSettings("pinecone", 768, pinecone_index_host="https://index.example")
    )
    assert calls == []
    engine = object()
    assert isinstance(factory, object)
    assert factory is not None
    assert isinstance(factory(engine), FakeBackend)
    assert calls == [
        {"index_host": "https://index.example", "dimension": 768, "prefix": "kogwistar", "engine": engine}
    ]


def test_missing_qdrant_provider_has_actionable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delitem(sys.modules, "kogwistar_qdrant", raising=False)
    factory = build_backend_factory(VectorBackendSettings("qdrant", 1024, qdrant_path="/tmp/qdrant"))
    assert factory is not None
    with pytest.raises(RuntimeError, match="kogwistar-qdrant is not installed"):
        factory(object())


def test_qdrant_requires_a_persistent_endpoint_or_path() -> None:
    with pytest.raises(ValueError, match="QDRANT_URL or QDRANT_PATH"):
        build_backend_factory(VectorBackendSettings("qdrant", 1024))


def test_unknown_backend_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported vector backend"):
        build_backend_factory(VectorBackendSettings("unknown", 1024))
