"""Compare local GPU embedding inference with Kogwistar's result cache.

This isolates repeated-text embedding cost from graph, database, and LLM work.
Inputs are deterministic synthetic strings; only aggregate metrics are emitted.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from pathlib import Path
from typing import Any


def _sync_cuda(torch: Any) -> None:
    if torch.cuda.is_available():
        torch.cuda.synchronize()


def _summarize(samples: list[float]) -> dict[str, float]:
    ordered = sorted(samples)
    if not ordered:
        return {"count": 0.0, "median_ms": 0.0, "p95_ms": 0.0}
    p95_index = min(len(ordered) - 1, int(0.95 * (len(ordered) - 1)))
    middle = len(ordered) // 2
    median = (
        ordered[middle]
        if len(ordered) % 2
        else (ordered[middle - 1] + ordered[middle]) / 2
    )
    return {
        "count": float(len(ordered)),
        "median_ms": median,
        "p95_ms": ordered[p95_index],
    }


def run_benchmark(
    *,
    device: str = "cuda",
    unique_items: int = 24,
    repeat_rounds: int = 3,
    warmup_items: int = 3,
    cache_dir: Path | None = None,
) -> dict[str, object]:
    if min(unique_items, repeat_rounds, warmup_items) < 1:
        raise ValueError("unique-items, repeat-rounds, and warmup-items must be positive")

    import torch

    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA requested but unavailable; refusing a silent CPU run")

    from kogwistar.engine_core.engine import GraphKnowledgeEngine
    from kogwistar.engine_core.in_memory_backend import build_in_memory_backend
    from profile_maintenance_e2e import LocalE5SmallEmbedding

    encoder = LocalE5SmallEmbedding(device=device)
    texts = [
        f"Benchmark document {index:04d}: a stable synthetic passage for semantic retrieval."
        for index in range(unique_items)
    ]
    warmups = [f"Warm-up passage {index}: embedding model initialization." for index in range(warmup_items)]
    for text in warmups:
        encoder([text])
    _sync_cuda(torch)

    temporary_cache: tempfile.TemporaryDirectory[str] | None = None
    if cache_dir is None:
        temporary_cache = tempfile.TemporaryDirectory(prefix="kogwistar-embedding-cache-")
        effective_cache_dir = Path(temporary_cache.name) / "cache"
    else:
        effective_cache_dir = cache_dir
        effective_cache_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="kogwistar-embedding-graph-") as graph_dir:
        uncached_engine = GraphKnowledgeEngine(
            persist_directory=str(Path(graph_dir) / "uncached"),
            embedding_function=encoder,
            backend_factory=build_in_memory_backend,
        )
        engine = GraphKnowledgeEngine(
            persist_directory=str(Path(graph_dir) / "cached"),
            embedding_function=encoder,
            embedding_cache_path=str(effective_cache_dir),
            backend_factory=build_in_memory_backend,
        )
        try:
            cache_fn = engine.cached_embed
            if cache_fn is None:
                raise RuntimeError("Kogwistar did not initialize its requested embedding cache")
            cache_check = getattr(cache_fn, "check_call_in_cache", None)
            if not callable(cache_check):
                raise TypeError("Kogwistar cache hit inspection is unavailable")

            uncached_latency_ms: list[float] = []
            uncached_vectors: dict[str, list[float]] = {}
            for _ in range(repeat_rounds):
                for text in texts:
                    _sync_cuda(torch)
                    started = time.perf_counter()
                    vector = uncached_engine.embed.iterative_defensive_emb(text)
                    _sync_cuda(torch)
                    uncached_latency_ms.append((time.perf_counter() - started) * 1000)
                    uncached_vectors[text] = vector

            def run_cached_pass() -> tuple[list[float], list[bool], float]:
                outputs: list[list[float]] = []
                was_cached: list[bool] = []
                _sync_cuda(torch)
                started = time.perf_counter()
                for text in texts:
                    was_cached.append(bool(cache_check(text)))
                    outputs.append(engine.embed.iterative_defensive_emb(text))
                _sync_cuda(torch)
                return outputs, was_cached, (time.perf_counter() - started) * 1000

            cold_outputs, cold_hit_flags, cold_elapsed = run_cached_pass()
            warm_samples_ms: list[float] = []
            warm_hit_flags: list[bool] = []
            warm_outputs: list[list[float]] = []
            for _ in range(repeat_rounds - 1):
                outputs, hit_flags, elapsed_ms = run_cached_pass()
                warm_outputs = outputs
                warm_hit_flags.extend(hit_flags)
                warm_samples_ms.append(elapsed_ms)

            vector_errors = [
                abs(expected - actual)
                for text, actual_vector in zip(texts, warm_outputs or cold_outputs)
                for expected, actual in zip(uncached_vectors[text], actual_vector)
            ]
            if any(
                len(vector) != encoder.dimension
                for vector in cold_outputs + warm_outputs
            ):
                raise RuntimeError("embedding dimension changed between cache modes")
            max_absolute_vector_error = max(vector_errors, default=0.0)
            if max_absolute_vector_error > 1e-6:
                raise RuntimeError(
                    "cached and uncached vectors differ beyond tolerance: "
                    f"{max_absolute_vector_error}"
                )

            return {
                "schema_version": 1,
                "workload": "repeated-stable-text-local-embedding-cache",
                "synthetic_data": True,
                "provider": {
                    "model": "intfloat/multilingual-e5-small",
                    "model_identity": encoder.name(),
                    "device": device,
                    "dimension": encoder.dimension,
                    "max_sequence_length": 512,
                    "model_load_ms": encoder.metrics()["model_load_ms"],
                },
                "configuration": {
                    "unique_items": unique_items,
                    "repeat_rounds": repeat_rounds,
                    "warmup_items": warmup_items,
                    "cache_enabled": True,
                },
                "results": {
                    "uncached_inference": {
                        "logical_embedding_calls": len(uncached_latency_ms),
                        "total_engine_path_ms": sum(uncached_latency_ms),
                        "per_item_latency": _summarize(uncached_latency_ms),
                    },
                    "cache_cold_pass": {
                        "items": unique_items,
                        "elapsed_ms": cold_elapsed,
                        "hits": sum(cold_hit_flags),
                        "misses": len(cold_hit_flags) - sum(cold_hit_flags),
                    },
                    "cache_warm_passes": {
                        "pass_count": len(warm_samples_ms),
                        "total_elapsed_ms": sum(warm_samples_ms),
                        "per_pass_ms": warm_samples_ms,
                        "hits": sum(warm_hit_flags),
                        "misses": len(warm_hit_flags) - sum(warm_hit_flags),
                    },
                    "cache_repeated_hit_rate": (
                        sum(warm_hit_flags) / len(warm_hit_flags)
                        if warm_hit_flags
                        else None
                    ),
                    "max_absolute_vector_error": max_absolute_vector_error,
                    "vector_equivalence": "passed",
                    "encoder_metrics": encoder.metrics(),
                },
            }
        finally:
            engine.close()
            uncached_engine.close()
            if temporary_cache is not None:
                temporary_cache.cleanup()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    parser.add_argument("--unique-items", type=int, default=24)
    parser.add_argument("--repeat-rounds", type=int, default=3)
    parser.add_argument("--warmup-items", type=int, default=3)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run_benchmark(
        device=args.device,
        unique_items=args.unique_items,
        repeat_rounds=args.repeat_rounds,
        warmup_items=args.warmup_items,
        cache_dir=args.cache_dir,
    )
    serialized = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + "\n", encoding="utf-8")
    print(serialized)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
