"""Measure concurrent long-context embedding requests."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import time
from pathlib import Path
from urllib.request import Request, urlopen


def one(url: str, words: int, request_id: int) -> dict[str, object]:
    # Keep the exact one-token pattern used by the accepted single-request
    # capacity probe; suffixing the token changes tokenization and can exceed
    # max-model-len even when the word count is unchanged.
    text = " ".join(["context"] * words)
    body = json.dumps({"model": "/model", "input": text}).encode()
    request = Request(url.rstrip("/") + "/v1/embeddings", data=body, headers={"Content-Type": "application/json"})
    started = time.perf_counter()
    with urlopen(request, timeout=1200) as response:
        payload = json.load(response)
    elapsed = time.perf_counter() - started
    vector = payload["data"][0]["embedding"]
    norm = math.sqrt(sum(value * value for value in vector))
    return {
        "request_id": request_id,
        "words": words,
        "dimensions": len(vector),
        "seconds": elapsed,
        "finite": all(math.isfinite(value) for value in vector),
        "norm": norm,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("output", type=Path)
    parser.add_argument("--words", type=int, default=32000)
    parser.add_argument("--concurrency", type=int, default=2)
    args = parser.parse_args()
    started = time.perf_counter()
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = [executor.submit(one, args.url, args.words, index) for index in range(args.concurrency)]
        results = [future.result() for future in futures]
    output = {
        "url": args.url,
        "words": args.words,
        "concurrency": args.concurrency,
        "wall_seconds": time.perf_counter() - started,
        "results": sorted(results, key=lambda item: int(item["request_id"])),
    }
    args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
