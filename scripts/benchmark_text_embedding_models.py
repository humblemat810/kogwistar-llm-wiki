"""Fast, side-by-side text embedding benchmark for CPU or CUDA.

This is intentionally an optional benchmark tool. Install
``sentence-transformers`` in the benchmark environment, not in the LLM-Wiki
runtime image. Every result records both requested words and the tokenizer's
effective token count after the model's own maximum sequence length, because
short-context models otherwise look like long-context models while silently
truncating input.

Example::

    python scripts/benchmark_text_embedding_models.py \
      --device cpu --models all --lengths 128 512 2048 4096 \
      --batch-size 8 --warmups 1 --repeats 2 \
      --output test-results/text-embeddings-cpu.json

For CUDA, run the same command in a CUDA-enabled Python environment. Model
weights are loaded sequentially and released before the next model so the
matrix does not require all checkpoints to fit in VRAM simultaneously.
"""

from __future__ import annotations

import argparse
import gc
import json
import platform
import time
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from typing import Any


MODEL_IDS: dict[str, str] = {
    "all-minilm-l6-v2": "sentence-transformers/all-MiniLM-L6-v2",
    "multilingual-e5-small": "intfloat/multilingual-e5-small",
    "paraphrase-multilingual-minilm-l12-v2": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    "gte-multilingual-base": "Alibaba-NLP/gte-multilingual-base",
    "nomic-embed-text-v2-moe": "nomic-ai/nomic-embed-text-v2-moe",
    "bge-m3": "BAAI/bge-m3",
    "qwen3-embedding-0.6b": "Qwen/Qwen3-Embedding-0.6B",
}

# Revisions captured for this comparison on 2026-10-04. Pinning is important
# because model cards and custom remote-code files can change independently.
MODEL_REVISIONS: dict[str, str] = {
    "all-minilm-l6-v2": "1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
    "multilingual-e5-small": "614241f622f53c4eeff9890bdc4f31cfecc418b3",
    "paraphrase-multilingual-minilm-l12-v2": "e8f8c211226b894fcb81acc59f3b34ba3efd5f42",
    "gte-multilingual-base": "9bbca17d9273fd0d03d5725c7a4b0f6b45142062",
    "nomic-embed-text-v2-moe": "1066b6599d099fbb93dfcb64f9c37a7c9e503e85",
    "bge-m3": "5617a9f61b028005a4858fdac845db406aefb181",
    "qwen3-embedding-0.6b": "97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3",
}


@dataclass(frozen=True, slots=True)
class Result:
    model_key: str
    model_id: str
    device: str
    requested_words: int
    requested_chars: int
    requested_tokens: int
    effective_tokens: int
    max_seq_length: int
    batch_size: int
    warmups: int
    repeats: int
    median_ms: float
    items_per_second: float
    dimension: int
    resident_memory_mb: float | None
    gpu_memory_mb: float | None
    status: str
    error: str | None = None
    target_tokens: int | None = None


def _make_text(words: int) -> str:
    phrases = (
        "retrieval systems preserve source evidence and stable semantic links",
        "a knowledge graph separates canonical facts from embedding projections",
        "context windows affect latency memory and truncation behavior",
        "this deterministic paragraph is used only for throughput measurement",
    )
    tokens: list[str] = []
    index = 0
    while len(tokens) < words:
        tokens.extend(phrases[index % len(phrases)].split())
        index += 1
    return " ".join(tokens[:words])


def _memory_mb() -> float | None:
    try:
        import psutil

        return psutil.Process().memory_info().rss / (1024 * 1024)
    except ImportError:
        return None


def _gpu_memory_mb(torch: Any) -> float | None:
    if not torch.cuda.is_available():
        return None
    return float(torch.cuda.memory_allocated() / (1024 * 1024))


def _token_counts(model: Any, text: str) -> tuple[int, int, int]:
    tokenizer = model.tokenizer
    requested = _token_count(
        tokenizer(text, add_special_tokens=True, truncation=False)["input_ids"]
    )
    max_length = int(model.max_seq_length)
    effective = min(requested, max_length)
    return requested, effective, max_length


def _token_count(input_ids: Any) -> int:
    """Count single-text tokenizer output from flat or batch-shaped IDs."""
    shape = getattr(input_ids, "shape", ())
    if len(shape) > 1:
        return int(shape[-1])
    if input_ids and isinstance(input_ids[0], (list, tuple)):
        return len(input_ids[0])
    return len(input_ids)


def _make_text_for_token_target(model: Any, target_tokens: int) -> str:
    """Build the deterministic fixture closest to a tokenizer token target."""
    tokenizer = model.tokenizer

    def count(words: int) -> int:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            return _token_count(
                tokenizer(
                    _make_text(words),
                    add_special_tokens=True,
                    truncation=False,
                )["input_ids"]
            )

    low = 1
    high = max(256, target_tokens * 2)
    while count(high) < target_tokens:
        high *= 2
    while low < high:
        midpoint = (low + high + 1) // 2
        if count(midpoint) <= target_tokens:
            low = midpoint
        else:
            high = midpoint - 1
    return _make_text(low)


def _measure_model(
    *,
    model_key: str,
    model_id: str,
    device: str,
    lengths: tuple[int, ...],
    token_lengths: tuple[int, ...] | None,
    batch_size: int,
    warmups: int,
    repeats: int,
    trust_remote_code: bool,
) -> tuple[Result, ...]:
    import torch
    from sentence_transformers import SentenceTransformer

    load_started = time.perf_counter()
    model = SentenceTransformer(
        model_id,
        device=device,
        revision=MODEL_REVISIONS[model_key],
        trust_remote_code=trust_remote_code,
    )
    # The caller records model loading time; rows below are warm steady-state
    # measurements only.
    results: list[Result] = []
    if token_lengths is None:
        measurements = ((words, None, _make_text(words)) for words in lengths)
    else:
        measurements = (
            (
                len((text := _make_text_for_token_target(model, target)).split()),
                target,
                text,
            )
            for target in token_lengths
        )
    for words, target_tokens, text in measurements:
        requested_tokens, effective_tokens, max_seq_length = _token_counts(model, text)
        batch = [text] * batch_size
        for _ in range(warmups):
            model.encode(batch, batch_size=batch_size, convert_to_numpy=True, normalize_embeddings=True, show_progress_bar=False)
        if device.startswith("cuda"):
            torch.cuda.synchronize()
        timings: list[float] = []
        for _ in range(repeats):
            started = time.perf_counter()
            vectors = model.encode(
                batch,
                batch_size=batch_size,
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            if device.startswith("cuda"):
                torch.cuda.synchronize()
            elapsed = (time.perf_counter() - started) * 1000.0
            if len(vectors) != batch_size:
                raise RuntimeError(f"{model_id} returned {len(vectors)} vectors for {batch_size} inputs")
            timings.append(elapsed)
        median_ms = median(timings)
        dimension = len(vectors[0])
        results.append(
            Result(
                model_key=model_key,
                model_id=model_id,
                device=device,
                requested_words=words,
                requested_chars=len(text),
                requested_tokens=requested_tokens,
                effective_tokens=effective_tokens,
                max_seq_length=max_seq_length,
                batch_size=batch_size,
                warmups=warmups,
                repeats=repeats,
                median_ms=round(median_ms, 2),
                items_per_second=round(batch_size / (median_ms / 1000.0), 3),
                dimension=dimension,
                resident_memory_mb=round(_memory_mb() or 0.0, 2),
                gpu_memory_mb=round(_gpu_memory_mb(torch) or 0.0, 2),
                status="ok",
                target_tokens=target_tokens,
            )
        )
    del model
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    return tuple(results)


def run_benchmark(
    *,
    model_keys: tuple[str, ...],
    device: str,
    lengths: tuple[int, ...],
    token_lengths: tuple[int, ...] | None,
    batch_size: int,
    warmups: int,
    repeats: int,
    trust_remote_code: bool,
    fail_fast: bool,
) -> dict[str, object]:
    if token_lengths is not None:
        if not token_lengths or any(value <= 0 for value in token_lengths):
            raise ValueError("token_lengths must contain positive values")
        lengths = ()
    elif not lengths or any(value <= 0 for value in lengths):
        raise ValueError("lengths must contain positive values")
    if batch_size <= 0 or repeats <= 0 or warmups < 0:
        raise ValueError("batch size and repeats must be positive; warmups cannot be negative")
    results: list[dict[str, object]] = []
    models: list[dict[str, object]] = []
    for model_key in model_keys:
        model_id = MODEL_IDS[model_key]
        load_started = time.perf_counter()
        try:
            measured = _measure_model(
                model_key=model_key,
                model_id=model_id,
                device=device,
                lengths=lengths,
                token_lengths=token_lengths,
                batch_size=batch_size,
                warmups=warmups,
                repeats=repeats,
                trust_remote_code=trust_remote_code,
            )
            models.append({"model_key": model_key, "model_id": model_id, "revision": MODEL_REVISIONS[model_key], "load_seconds": round(time.perf_counter() - load_started, 2), "status": "ok"})
            results.extend(asdict(item) for item in measured)
        except Exception as exc:  # noqa: BLE001 - one unavailable model should not hide the matrix
            models.append({"model_key": model_key, "model_id": model_id, "revision": MODEL_REVISIONS[model_key], "load_seconds": round(time.perf_counter() - load_started, 2), "status": "error", "error": f"{type(exc).__name__}: {exc}"})
            if fail_fast:
                raise
    return {
        "schema_version": 1,
        "host": platform.platform(),
        "device": device,
        "model_keys": model_keys,
        "length_unit": "repeated words; requested/effective tokenizer tokens are reported separately",
        "lengths": lengths,
        "token_lengths": token_lengths,
        "batch_size": batch_size,
        "warmups": warmups,
        "repeats": repeats,
        "models": models,
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--models", nargs="+", choices=tuple(MODEL_IDS) + ("all",), default=["all"])
    parser.add_argument("--lengths", type=int, nargs="+", default=[128, 512, 2048, 4096])
    parser.add_argument("--token-lengths", type=int, nargs="+")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--warmups", type=int, default=1)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--trust-remote-code", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.token_lengths is not None and args.lengths != [128, 512, 2048, 4096]:
        parser.error("use either --lengths or --token-lengths, not both")
    selected = tuple(MODEL_IDS) if "all" in args.models else tuple(args.models)
    if args.device == "cuda":
        import torch

        if not torch.cuda.is_available():
            parser.error("CUDA was requested but torch.cuda.is_available() is false")
    report = run_benchmark(
        model_keys=selected,
        device=args.device,
        lengths=tuple(args.lengths),
        token_lengths=tuple(args.token_lengths) if args.token_lengths is not None else None,
        batch_size=args.batch_size,
        warmups=args.warmups,
        repeats=args.repeats,
        trust_remote_code=args.trust_remote_code,
        fail_fast=args.fail_fast,
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
