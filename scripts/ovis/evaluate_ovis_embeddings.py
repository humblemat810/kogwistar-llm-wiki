"""Evaluate an OpenAI-compatible embedding endpoint on the screening set."""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from urllib.request import Request, urlopen


def embed(url: str, texts: list[str]) -> list[list[float]]:
    body = json.dumps({"model": "/model", "input": texts}).encode()
    request = Request(url.rstrip("/") + "/v1/embeddings", data=body, headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=600) as response:
        payload = json.load(response)
    return [item["embedding"] for item in sorted(payload["data"], key=lambda item: item["index"])]


def cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    return dot / (left_norm * right_norm)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("benchmark", type=Path)
    parser.add_argument("url")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    records = json.loads(args.benchmark.read_text(encoding="utf-8"))
    texts = [text for record in records for text in [record["query"], *record["candidates"]]]
    started = time.perf_counter()
    vectors = embed(args.url, texts)
    elapsed = time.perf_counter() - started
    results = []
    cursor = 0
    for record in records:
        query = vectors[cursor]
        candidates = vectors[cursor + 1 : cursor + 1 + len(record["candidates"])]
        scores = [cosine(query, candidate) for candidate in candidates]
        ranking = sorted(range(len(scores)), key=lambda index: scores[index], reverse=True)
        results.append({"id": record["id"], "scores": scores, "ranking": ranking, "positive_rank": ranking.index(record["positive_index"]) + 1})
        cursor += 1 + len(candidates)
    recall1 = sum(result["positive_rank"] == 1 for result in results) / len(results)
    recall5 = sum(result["positive_rank"] <= 5 for result in results) / len(results)
    recall10 = sum(result["positive_rank"] <= 10 for result in results) / len(results)
    ndcg = sum(1.0 / math.log2(result["positive_rank"] + 1) for result in results) / len(results)
    output = {"url": args.url, "records": len(records), "dimensions": len(vectors[0]), "seconds": elapsed, "recall_at_1": recall1, "recall_at_5": recall5, "recall_at_10": recall10, "ndcg": ndcg, "results": results}
    args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: output[key] for key in ("url", "records", "dimensions", "seconds", "recall_at_1", "recall_at_5")}))


if __name__ == "__main__":
    main()
