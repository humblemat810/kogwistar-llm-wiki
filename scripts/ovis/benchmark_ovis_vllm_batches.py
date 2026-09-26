"""Measure short-input batch scaling for an OpenAI-compatible Ovis endpoint."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import statistics
import time
from pathlib import Path
from urllib.request import Request, urlopen


def request_embeddings(url: str, batch_size: int, text: str) -> tuple[float, int, float, bool]:
    body = json.dumps({"model": "/model", "input": [text] * batch_size}).encode()
    request = Request(
        url.rstrip("/") + "/v1/embeddings",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    started = time.perf_counter()
    with urlopen(request, timeout=600) as response:
        payload = json.load(response)
    elapsed = time.perf_counter() - started
    vectors = [item["embedding"] for item in sorted(payload["data"], key=lambda item: item["index"])]
    norms = [math.sqrt(sum(value * value for value in vector)) for vector in vectors]
    finite = all(math.isfinite(value) for vector in vectors for value in vector)
    return elapsed, len(vectors[0]), statistics.mean(norms), finite


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("output", type=Path)
    parser.add_argument("--batch-sizes", type=int, nargs="+", default=[1, 2, 4, 8])
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--concurrency", type=int, default=1)
    args = parser.parse_args()
    text = "A compact retrieval benchmark sentence about a database migration and its rollback plan."
    rows: list[dict[str, object]] = []
    for batch_size in args.batch_sizes:
        # Warm up each batch shape before recording timings.
        request_embeddings(args.url, batch_size, text)
        samples: list[float] = []
        dimensions = 0
        norm = 0.0
        finite = True
        started = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            futures = [executor.submit(request_embeddings, args.url, batch_size, text) for _ in range(args.repeats)]
            for future in futures:
                elapsed, dimensions, norm, sample_finite = future.result()
                samples.append(elapsed)
                finite = finite and sample_finite
        wall = time.perf_counter() - started
        rows.append(
            {
                "batch_size": batch_size,
                "repeats": args.repeats,
                "concurrency": args.concurrency,
                "median_latency_seconds": statistics.median(samples),
                "min_latency_seconds": min(samples),
                "max_latency_seconds": max(samples),
                "wall_seconds": wall,
                "requests_per_second": args.repeats / wall,
                "items_per_second": args.repeats * batch_size / wall,
                "dimensions": dimensions,
                "mean_norm": norm,
                "finite": finite,
            }
        )
    output = {"url": args.url, "rows": rows}
    args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
