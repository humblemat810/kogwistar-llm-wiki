from __future__ import annotations

import asyncio
import json
from pathlib import Path

from scripts.benchmark_multimodal_retrieval_modes import run_experiment


def test_multimodal_fixture_manifest_has_image_first_expectations() -> None:
    root = Path(__file__).parents[1] / "fixtures" / "multimodal_retrieval"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["fixture_only"] is True
    assert {unit["id"] for unit in manifest["units"]} >= {
        "text-prototype",
        "architecture-image",
        "benchmark-image",
        "distractor-image",
    }
    assert manifest["queries"][0]["expected_top_refs"] == ["architecture-image"]
    assert "FIXTURE DATA" in (root / "architecture_parallel.svg").read_text(encoding="utf-8")


def test_mode_comparison_proves_fast_path_overlap_and_late_evidence() -> None:
    report = asyncio.run(run_experiment(multimodal_delay_ms=80.0))
    synchronous, asynchronous = report["results"]

    assert synchronous["expected_target_retrieved"] is True
    assert asynchronous["expected_target_retrieved"] is True
    assert asynchronous["main_steps_before_multimodal"] == 3
    assert asynchronous["correction_success"] is True
    assert asynchronous["time_to_fast_path_ms"] < asynchronous["time_to_first_multimodal_ms"]
