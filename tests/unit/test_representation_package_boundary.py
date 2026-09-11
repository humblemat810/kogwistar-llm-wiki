from __future__ import annotations

import subprocess
import sys
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_standalone_service_imports_without_application_package() -> None:
    code = """
import sys
import llm_wiki_representation_contract
import llm_wiki_representation_service
assert 'kogwistar_llm_wiki' not in sys.modules
assert 'kogwistar' not in sys.modules
assert 'kg_doc_parser' not in sys.modules
assert 'kogwistar_obsidian_sink' not in sys.modules
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(ROOT / "src")
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
