from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    not os.getenv("KOGWISTAR_DEV_IMPORT_SMOKE"),
    reason="dev-only import smoke test; set KOGWISTAR_DEV_IMPORT_SMOKE=1 to run it locally",
)


def test_kogwistar_import_resolves_to_root_checkout() -> None:
    import kogwistar

    module_path = Path(kogwistar.__file__).resolve()
    repo_root = Path(__file__).resolve().parents[1]
    root_checkout = repo_root / "kogwistar"
    nested_vendor = repo_root / "kg-doc-parser" / "kogwistar"

    assert module_path.is_relative_to(root_checkout)
    assert not module_path.is_relative_to(nested_vendor)
