from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pytest

from kogwistar_llm_wiki.app_contracts import (
    WorkbenchExtension,
    WorkbenchExtensionResponse,
    load_workbench_extensions,
    workbench_extensions,
)


@dataclass
class _EntryPoint:
    name: str
    factory: Callable[[object], WorkbenchExtension]
    loaded: bool = False

    def load(self) -> Callable[[object], WorkbenchExtension]:
        self.loaded = True
        return self.factory


def _extension(extension_id: str) -> WorkbenchExtension:
    return WorkbenchExtension(extension_id, ())


def _mail_factory(_host: object) -> WorkbenchExtension:
    return _extension("mail")


def test_installed_extensions_are_not_loaded_without_explicit_allowlist(monkeypatch):
    def unexpected_entry_point_discovery(**_kwargs):
        pytest.fail("empty allowlist must not inspect or load installed code")

    monkeypatch.setattr(workbench_extensions, "entry_points", unexpected_entry_point_discovery)
    assert load_workbench_extensions(object(), ()) == ()


def test_loader_initializes_only_explicitly_enabled_extension(monkeypatch):
    enabled = _EntryPoint("mail", _mail_factory)
    unselected = _EntryPoint("other", lambda _host: _extension("other"))
    monkeypatch.setattr(
        workbench_extensions,
        "entry_points",
        lambda **_kwargs: (enabled, unselected),
    )
    host = object()
    loaded = load_workbench_extensions(host, ("mail",))
    assert tuple(extension.extension_id for extension in loaded) == ("mail",)
    assert enabled.loaded is True
    assert unselected.loaded is False


@pytest.mark.parametrize("enabled_ids", [("mail", "mail"), ("../mail",)])
def test_loader_rejects_invalid_allowlist_before_discovery(monkeypatch, enabled_ids):
    def unexpected_entry_point_discovery(**_kwargs):
        pytest.fail("invalid allowlist must fail before plugin discovery")

    monkeypatch.setattr(workbench_extensions, "entry_points", unexpected_entry_point_discovery)
    with pytest.raises(ValueError, match="allowlist"):
        load_workbench_extensions(object(), enabled_ids)


def test_loader_fails_closed_when_enabled_extension_is_not_installed(monkeypatch):
    monkeypatch.setattr(workbench_extensions, "entry_points", lambda **_kwargs: ())
    with pytest.raises(RuntimeError, match="not installed: mail"):
        load_workbench_extensions(object(), ("mail",))


def test_loader_rejects_factory_identity_mismatch(monkeypatch):
    monkeypatch.setattr(
        workbench_extensions,
        "entry_points",
        lambda **_kwargs: (_EntryPoint("mail", lambda _host: _extension("other")),),
    )
    with pytest.raises(ValueError, match="id mismatch"):
        load_workbench_extensions(object(), ("mail",))


def test_extension_response_preserves_explicit_status_and_content_type():
    response = WorkbenchExtensionResponse("<main>ok</main>", 202, "text/html")
    assert response.status == 202
    assert response.content_type == "text/html"

    with pytest.raises(TypeError, match="string body"):
        WorkbenchExtensionResponse({"html": True}, content_type="text/html")
