"""Build a small deterministic held-out text retrieval screening set.

This is deliberately not a calibration corpus. It is a cheap text-only gate for
embedding drift and ranking regressions; multimodal cases are added separately
when stable local media fixtures are available.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


TOPICS = [
    ("ordinary prose", "A quiet coastal town prepared for the winter storm.", "Residents secured boats, stocked supplies, and monitored the approaching storm."),
    ("code", "How does a Python dictionary lookup work?", "A dictionary uses a hash table to map keys to values and usually provides constant average-time lookup."),
    ("structured text", "What fields are required for an HTTP request?", "An HTTP request has a method, target URL, headers, and may include a body."),
    ("documents", "Summarize the purpose of an invoice.", "An invoice records goods or services supplied and the amount a customer owes."),
    ("science", "Why do objects fall toward Earth?", "Earth's gravitational attraction accelerates objects toward its center."),
    ("medicine", "What is the purpose of a vaccine?", "A vaccine trains the immune system to recognize a pathogen and respond more effectively."),
    ("finance", "What does diversification mean in investing?", "Diversification spreads investments across assets to reduce exposure to a single risk."),
    ("geography", "Which process creates river deltas?", "A river delta forms when sediment is deposited as flowing water enters a slower body of water."),
    ("history", "Why were ancient trade routes important?", "Trade routes moved goods, technologies, languages, and cultural practices between regions."),
    ("multilingual", "What is the French word for the English word 'house'?", "The French translation of 'house' is 'maison'."),
    ("software operations", "What does a container image contain?", "A container image packages an application, its dependencies, and filesystem layers for reproducible execution."),
    ("mathematics", "What is the derivative of x squared?", "The derivative of x squared with respect to x is 2x."),
]


def build() -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for index, (category, query, positive) in enumerate(TOPICS):
        for variant in range(5):
            negatives = [
                TOPICS[(index + offset) % len(TOPICS)][2]
                for offset in (1, 2, 3, 4, 5, 6, 7, 8, 9)
            ]
            records.append(
                {
                    "id": f"text-{index:02d}-{variant:02d}",
                    "modality": "text-text",
                    "category": category,
                    "query": query if variant == 0 else f"In other words, {query[0].lower() + query[1:]}",
                    "candidates": [positive, *negatives],
                    "positive_index": 0,
                    "source": "deterministic-held-out-screening-v1",
                }
            )
    return records


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    records = build()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(records, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({"records": len(records), "modalities": {"text-text": len(records)}}))


if __name__ == "__main__":
    main()
