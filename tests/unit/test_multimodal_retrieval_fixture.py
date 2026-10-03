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
    image_query = next(
        query for query in manifest["queries"] if query["id"] == "image_to_related_text"
    )
    assert image_query["query_kind"] == "image"
    assert image_query["expected_top_refs"] == ["text-prototype"]
    assert "FIXTURE DATA" in (root / "architecture_parallel.svg").read_text(
        encoding="utf-8"
    )


def test_mode_comparison_proves_fast_path_overlap_and_late_evidence() -> None:
    report = asyncio.run(run_experiment(delay_ms=80.0, repeats=2))
    architecture = next(
        item
        for item in report["scenarios"]
        if item["query_id"] == "architecture_parallel"
    )
    synchronous, asynchronous = architecture["results"]

    assert "fixture vectors" in report["warning"]
    assert synchronous["expected_target_retrieved"] is True
    assert asynchronous["expected_target_retrieved"] is True
    assert synchronous["recall_at_k"] == asynchronous["recall_at_k"] == 1.0
    assert asynchronous["main_steps_before_multimodal"] == 3
    assert asynchronous["late_correction"] is True
    assert asynchronous["fast_path_ms"] < asynchronous["time_to_first_multimodal_ms"]
    assert asynchronous["overlapped_work_ms"] > 0
    assert synchronous["main_steps_before_multimodal"] == 0
    assert synchronous["late_correction"] is False
    for scenario in report["scenarios"]:
        assert all(result["recall_at_k"] == 1.0 for result in scenario["results"])
        if scenario["query_id"] != "architecture_parallel":
            sidecar = scenario["results"][1]
            assert sidecar["late_correction"] is False
