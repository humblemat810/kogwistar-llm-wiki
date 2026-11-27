from __future__ import annotations

import json
import sys
from types import ModuleType

import pytest

from kogwistar_llm_wiki import native


@pytest.fixture(autouse=True)
def clear_facade_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(native.IMPLEMENTATION_ENV, raising=False)


def _install_fake_native(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType(native.NATIVE_MODULE_NAME)
    module.stable_id_json = lambda payload_json: "native-stable-id"
    monkeypatch.setitem(sys.modules, native.NATIVE_MODULE_NAME, module)


def test_implementation_mode_supports_native_python_and_auto() -> None:
    assert native.implementation_mode() == "auto"
    monkeypatch = pytest.MonkeyPatch()
    with monkeypatch.context() as context:
        context.setenv(native.IMPLEMENTATION_ENV, "wrong")
        with pytest.raises(native.RustFacadeError, match="must be one of"):
            native.implementation_mode()
    assert native.implementation_mode() == "auto"


def test_native_mode_requires_built_extension(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(native.IMPLEMENTATION_ENV, "native")
    with pytest.raises(native.RustFacadeError, match="unavailable"):
        native.native_module()


def test_auto_falls_back_to_python_facade(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_native(monkeypatch)
    assert native.implementation_name() == "native"
    assert native.stable_id_json(json.dumps(["kind", "demo"])) == "native-stable-id"

    monkeypatch.delitem(sys.modules, native.NATIVE_MODULE_NAME)
    assert native.implementation_name() == "python"
    expected = str(
        __import__("kogwistar").id_provider.stable_id("kind", "demo")
    )
    assert native.stable_id_json(json.dumps(["kind", "demo"])) == expected


def test_python_mode_never_loads_extension(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(native.IMPLEMENTATION_ENV, "python")
    _install_fake_native(monkeypatch)
    assert native.native_module() is None
    assert native.stable_id_json(json.dumps(["kind", "demo"])) != "native-stable-id"


def test_canonical_json_and_evidence_fallback_match_python() -> None:
    digest = {
        "node_ids": ["b", "a"],
        "edge_ids": ["e2", "e1"],
        "depth": "deep",
        "max_chars_per_item": 10,
        "max_total_chars": 20,
        "evidence_pack_hash": "ignored",
        "extra": "x",
    }
    assert native.canonical_json({"b": 2, "a": 1}) == '{"a":1,"b":2}'
    assert native.canonicalized_evidence_pack(digest)["node_ids"] == ["a", "b"]
    assert native.evidence_pack_digest(digest) == (
        "08e8f5a5272cffdf4b88ce7c12bf778380f335b3d7b8ae2ecf8c18e296f86d0d"
    )


def test_basic_sense_fallback_matches_python_contract() -> None:
    result = native.basic_sense_eval(
        {"nodes": [], "edges": []},
        {"fallback_reason": "missing"},
    )
    assert result["basic_sense_verdict"] == "weak"
    assert result["fallback_used"] is True

def test_graph_space_match_fallback_matches_python() -> None:
    assert native.graph_space_match(
        "ws:w1:g:source", {"workspace_id": "w1", "graph_space": "source"}
    ) is True
    assert native.graph_space_match(
        "ws:w1:g:source:lane:fg",
        {"workspace_id": "w1", "graph_space": "source", "graph_lane": "fg"},
    ) is True
    assert native.graph_space_match(
        "ws:w1:g:source", {"workspace_id": "w2", "graph_space": "source"}
    ) is False
    assert native.graph_space_match(
        "ws:w1:g:source", {"workspace_id": "w1", "graph_space": "review"}
    ) is False
    assert native.graph_space_match("not-a-namespace", {"workspace_id": "w1"}) is False
