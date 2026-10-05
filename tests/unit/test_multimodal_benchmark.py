from __future__ import annotations

import asyncio

import pytest

from scripts.benchmark_multimodal import run_multimodal_benchmark
from scripts.benchmark_multimodal_retrieval_modes import run_experiment


def test_fake_multimodal_benchmark_reports_requested_workload_shapes() -> None:
    report = run_multimodal_benchmark(items=3, repeats=2, warmup=0, batch_size=2)

    assert report["backend"] == "fake"
    assert report["batch_size"] == 2
    assert [case["name"] for case in report["cases"]] == [
        "single_image",
        "image_batch",
        "single_text",
        "text_batch",
        "single_text_image",
        "text_image_batch",
        "mixed_image_text_batch",
    ]
    assert [case["item_count"] for case in report["cases"]] == [1, 3, 1, 3, 1, 3, 6]
    assert all(case["samples"] == 2 for case in report["cases"])
    assert all(case["warmup_count"] == 0 for case in report["cases"])
    assert all(case["warmup_ms"] >= 0 for case in report["cases"])
    assert all(case["median_ms"] >= 0 for case in report["cases"])
    assert report["profile"]["embedding"] == "late_interaction"


def test_retrieval_mode_benchmark_does_not_claim_real_model_quality() -> None:
    report = asyncio.run(run_experiment(delay_ms=0, repeats=1))
    assert report["quality_evidence"]["status"] == "synthetic_fixture_only"
    assert report["quality_evidence"]["semantic_quality_valid"] is False
    assert report["quality_evidence"]["latency_valid"] is False


def test_multimodal_benchmark_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError, match="positive"):
        run_multimodal_benchmark(items=0)
    with pytest.raises(ValueError, match="model-dir"):
        run_multimodal_benchmark(backend="colqwen")
    with pytest.raises(ValueError, match="service-url"):
        run_multimodal_benchmark(backend="remote")
    with pytest.raises(ValueError, match="allowed-host"):
        run_multimodal_benchmark(backend="remote", service_url="http://embedding:8790")
    with pytest.raises(ValueError, match="service-batch-size"):
        run_multimodal_benchmark(
            backend="remote",
            service_url="http://embedding:8790",
            service_allowed_hosts=("embedding",),
        )
