from __future__ import annotations

import sys

import pytest

from tests._helpers.fake_email_plugin import plugin_module


@pytest.fixture
def install_fake_email_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep host tests independent from the optional private plugin package."""

    monkeypatch.setitem(sys.modules, "kogwistar_email_plugin", plugin_module())
