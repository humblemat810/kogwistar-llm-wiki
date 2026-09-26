"""Compare a quantized endpoint with a BF16/reference endpoint on one fixture.

The fixture is sent identically to both OpenAI-compatible servers.  The output
is an auditable artifact containing per-vector cosine fidelity, drift summary,
nearest-neighbor agreement, and retrieval metrics for both endpoints.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from pathlib import Path
from urllib.request import Request, urlopen


def embed(url: str, texts: list[str]) -> list[list[float]]:
    body = json.dumps({"model": "/model", "input": texts}).encode()
    request = Request(
        url.rstrip("/") + "/v1/embeddings",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=1200) as response:
        payload = json.load(response)
    return [item["embedding"] for item in sorted(payload["data"], key=lambda item: item["index"])]


def load_reference(source: str, texts: list[str]) -> tuple[list[list[float]], float | None, str]:
    path = Path(source)
    if not path.is_file():
        started = time.perf_counter()
        return embed(source, texts), time.perf_counter() - started, source
    payload = json.loads(path.read_text(encoding="utf-8"))
    vectors = payload.get("vectors")
    if not isinstance(vectors, list) or len(vectors) != len(texts):
        raise ValueError(
            f"persisted reference has {len(vectors or [])} vectors; fixture requires {len(texts)}"
        )
    if payload.get("text_count", len(vectors)) != len(texts):
        raise ValueError("persisted reference text_count does not match the fixture")
    return vectors, payload.get("seconds"), str(path)


def cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(a * a for a in right))
    return dot / (left_norm * right_norm)


def retrieval(vectors: list[list[float]], records: list[dict[str, object]]) -> tuple[dict[str, float], list[dict[str, object]]]:
    results = []
    cursor = 0
    for record in records:
        query = vectors[cursor]
        count = len(record["candidates"])
        candidates = vectors[cursor + 1 : cursor + 1 + count]
        scores = [cosine(query, candidate) for candidate in candidates]
        ranking = sorted(range(count), key=lambda index: scores[index], reverse=True)
        positive_rank = ranking.index(int(record["positive_index"])) + 1
        results.append({"id": record["id"], "ranking": ranking, "positive_rank": positive_rank})
        cursor += 1 + count
    metrics = {
        "recall_at_1": sum(item["positive_rank"] <= 1 for item in results) / len(results),
        "recall_at_5": sum(item["positive_rank"] <= 5 for item in results) / len(results),
        "recall_at_10": sum(item["positive_rank"] <= 10 for item in results) / len(results),
        "ndcg": statistics.mean(1.0 / math.log2(item["positive_rank"] + 1) for item in results),
    }
    return metrics, results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("benchmark", type=Path)
    parser.add_argument("reference_url")
    parser.add_argument("candidate_url")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    records = json.loads(args.benchmark.read_text(encoding="utf-8"))
    texts = [text for record in records for text in [record["query"], *record["candidates"]]]

    reference, reference_seconds, reference_source = load_reference(args.reference_url, texts)
    started = time.perf_counter()
    candidate = embed(args.candidate_url, texts)
    candidate_seconds = time.perf_counter() - started
    if len(reference) != len(candidate) or not reference:
        raise ValueError("reference and candidate returned different vector counts")
    dimensions = {len(vector) for vector in reference + candidate}
    if len(dimensions) != 1:
        raise ValueError(f"dimension mismatch: {sorted(dimensions)}")

    similarities = [cosine(left, right) for left, right in zip(reference, candidate)]
    reference_metrics, reference_results = retrieval(reference, records)
    candidate_metrics, candidate_results = retrieval(candidate, records)
    agreements = [
        left["ranking"][0] == right["ranking"][0]
        for left, right in zip(reference_results, candidate_results)
    ]
    output = {
        "benchmark": str(args.benchmark),
        "reference_url": reference_source,
        "candidate_url": args.candidate_url,
        "records": len(records),
        "vectors": len(reference),
        "dimensions": dimensions.pop(),
        "reference_seconds": reference_seconds,
        "candidate_seconds": candidate_seconds,
        "fidelity": {
            "mean_cosine": statistics.mean(similarities),
            "min_cosine": min(similarities),
            "p05_cosine": sorted(similarities)[max(0, math.ceil(len(similarities) * 0.05) - 1)],
            "mean_cosine_drift": 1.0 - statistics.mean(similarities),
            "max_cosine_drift": 1.0 - min(similarities),
            "nearest_neighbor_agreement": sum(agreements) / len(agreements),
        },
        "reference_retrieval": reference_metrics,
        "candidate_retrieval": candidate_metrics,
        "per_record": [
            {
                "id": left["id"],
                "reference_positive_rank": left["positive_rank"],
                "candidate_positive_rank": right["positive_rank"],
                "top1_agreement": left["ranking"][0] == right["ranking"][0],
            }
            for left, right in zip(reference_results, candidate_results)
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: output[key] for key in ("records", "vectors", "dimensions", "fidelity", "reference_retrieval", "candidate_retrieval")}))


if __name__ == "__main__":
    main()
