"""Measure Ovis embedding latency and validity at context-length stress points."""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from urllib.request import Request, urlopen


def measure(url: str, words: int) -> dict[str, object]:
    text = " ".join(["context"] * words)
    body = json.dumps({"model": "/model", "input": text}).encode()
    request = Request(url.rstrip("/") + "/v1/embeddings", data=body, headers={"Content-Type": "application/json"})
    started = time.perf_counter()
    with urlopen(request, timeout=1200) as response:
        payload = json.load(response)
    vector = payload["data"][0]["embedding"]
    return {
        "words": words,
        "seconds": time.perf_counter() - started,
        "dimensions": len(vector),
        "finite": all(math.isfinite(value) for value in vector),
        "norm": math.sqrt(sum(value * value for value in vector)),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("output", type=Path)
    parser.add_argument("--words", type=int, nargs="+", default=[8000, 16000, 32000])
    args = parser.parse_args()
    output = {"url": args.url, "results": [measure(args.url, words) for words in args.words]}
    args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
