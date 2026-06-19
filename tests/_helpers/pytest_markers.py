from __future__ import annotations

from collections.abc import Iterable

import pytest

_DEFAULT_CI_BLOCKERS = {"ci_full", "manual", "longrun", "requires_ollama"}


def is_default_ci_item(item: pytest.Item) -> bool:
    """Return True when a test should be part of the default CI marker set."""

    return not any(marker in item.keywords for marker in _DEFAULT_CI_BLOCKERS)


def mark_default_ci_items(items: Iterable[pytest.Item]) -> None:
    """Add the ``ci`` marker to tests that are safe for default PR CI."""

    for item in items:
        if is_default_ci_item(item) and "ci" not in item.keywords:
            item.add_marker(pytest.mark.ci)
