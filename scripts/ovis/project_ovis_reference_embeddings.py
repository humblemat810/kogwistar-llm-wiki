"""Derive a persisted lower-dimensional reference from saved BF16 vectors."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--dimension", type=int, required=True)
    args = parser.parse_args()
    payload = json.loads(args.source.read_text(encoding="utf-8"))
    vectors = []
    for vector in payload["vectors"]:
        prefix = vector[: args.dimension]
        norm = math.sqrt(sum(value * value for value in prefix))
        vectors.append([value / norm for value in prefix])
    payload["source_reference"] = str(args.source)
    payload["dimension_projection"] = f"prefix-{args.dimension}-renormalized"
    payload["dimensions"] = args.dimension
    payload["vectors"] = vectors
    args.output.write_text(json.dumps(payload) + "\n", encoding="utf-8")
    print(json.dumps({"vectors": len(vectors), "dimensions": args.dimension}))


if __name__ == "__main__":
    main()
