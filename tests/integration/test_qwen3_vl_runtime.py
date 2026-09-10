"""Opt-in smoke coverage for a real local Qwen3-VL checkpoint."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from scripts.benchmark_multimodal import run_multimodal_benchmark


pytestmark = [pytest.mark.manual, pytest.mark.slow]


def test_local_qwen3_vl_checkpoint_produces_verified_dense_benchmark() -> None:
    model_dir = os.getenv("LLM_WIKI_QWEN3_VL_MODEL_DIR", "").strip()
    if not model_dir:
        pytest.skip("set LLM_WIKI_QWEN3_VL_MODEL_DIR for the real checkpoint smoke test")
    path = Path(model_dir)
    if not path.is_dir():
        pytest.fail(f"configured Qwen3-VL model directory does not exist: {path}")
    report = run_multimodal_benchmark(
        backend="qwen3-vl",
        model_dir=str(path),
        device=os.getenv("LLM_WIKI_QWEN3_VL_DEVICE") or None,
        dimension=int(os.getenv("LLM_WIKI_QWEN3_VL_DIMENSION", "1024")),
        items=2,
        batch_size=1,
        repeats=1,
        warmup=1,
    )
    assert report["profile"]["model"].endswith("Qwen3-VL-Embedding-2B") or report["profile"]["model"] == str(path)
    assert report["profile"]["representation"] == "dense"
    assert all(case["item_count"] > 0 for case in report["cases"])
