"""Measure conversation graph admission before and after two-stage projection.

The benchmark intentionally uses a deterministic, delayed embedding provider.
It measures the hot-path improvement from deferring embeddings and reports the
later batch-promotion cost separately, so it never mislabels deferred work as
free work.  It is safe to run without an LLM, network access, or a vector DB.
"""

from __future__ import annotations

import argparse
import json
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from kogwistar.engine_core import GraphKnowledgeEngine
from kogwistar.engine_core.in_memory_backend import build_in_memory_backend
from kogwistar.engine_core.models import Grounding, MentionVerification, Node, Span


class DelayedMockEmbeddingProvider:
    """A deterministic provider that makes request-count savings observable."""

    def __init__(self, *, request_delay_ms: float) -> None:
        self.request_delay_ms = float(request_delay_ms)
        self.calls: list[int] = []

    def __call__(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(len(texts))
        time.sleep(self.request_delay_ms / 1000.0)
        return [[float(len(text) + 1), float(index + 1)] for index, text in enumerate(texts)]


@dataclass(frozen=True)
class ConversationMaterializationBenchmark:
    entity_count: int
    provider_delay_ms: float
    single_stage_admission_ms: float
    single_stage_provider_calls: int
    two_stage_admission_ms: float
    two_stage_provider_calls_before_drain: int
    two_stage_promotion_ms: float
    two_stage_total_ms: float
    two_stage_provider_calls_total: int
    promoted_entities: int

    @property
    def admission_speedup(self) -> float:
        if self.two_stage_admission_ms <= 0:
            return float("inf")
        return self.single_stage_admission_ms / self.two_stage_admission_ms


def _node(index: int) -> Node:
    excerpt = f"Mock conversation turn {index}"
    span = Span(
        doc_id="mock-conversation",
        chunk_id=None,
        source_cluster_id=None,
        verification=MentionVerification(
            method="human",
            is_verified=True,
            score=1.0,
            notes="deterministic benchmark fixture",
        ),
        collection_page_url="benchmark://conversation",
        document_page_url="benchmark://conversation",
        insertion_method="benchmark_fixture",
        page_number=1,
        start_char=0,
        end_char=len(excerpt),
        excerpt=excerpt,
        context_before="",
        context_after="",
    )
    return Node(
        id=f"conversation-turn-{index:03d}",
        label="conversation_turn",
        type="entity",
        summary=f"Mock conversation turn {index}: grounded user and assistant content.",
        doc_id="mock-conversation",
        mentions=[Grounding(spans=[span])],
        level_from_root=0,
        metadata={"artifact_type": "conversation_turn", "turn_index": index},
    )


def _engine(root: Path, *, provider: DelayedMockEmbeddingProvider, persistence_mode: str) -> GraphKnowledgeEngine:
    return GraphKnowledgeEngine(
        persist_directory=str(root),
        kg_graph_type="conversation",
        namespace="conversation",
        embedding_function=provider,
        backend_factory=build_in_memory_backend,
        persistence_mode=persistence_mode,
    )


def run_conversation_materialization_benchmark(
    *,
    entity_count: int = 12,
    provider_delay_ms: float = 10.0,
    base_dir: str | Path | None = None,
) -> ConversationMaterializationBenchmark:
    """Compare synchronous embedding with deferred, minibatched promotion."""
    if entity_count <= 1:
        raise ValueError("entity_count must be greater than one to demonstrate batching")
    root = Path(base_dir) if base_dir is not None else Path(tempfile.mkdtemp(prefix="llm-wiki-two-stage-benchmark-"))
    root.mkdir(parents=True, exist_ok=True)

    single_provider = DelayedMockEmbeddingProvider(request_delay_ms=provider_delay_ms)
    single = _engine(root / "single", provider=single_provider, persistence_mode="single_stage")
    started = time.perf_counter()
    for index in range(entity_count):
        single.write.add_node(_node(index))
    single_admission_ms = (time.perf_counter() - started) * 1000.0

    two_stage_provider = DelayedMockEmbeddingProvider(request_delay_ms=provider_delay_ms)
    two_stage = _engine(root / "two-stage", provider=two_stage_provider, persistence_mode="two_stage")
    started = time.perf_counter()
    for index in range(entity_count):
        two_stage.write.add_node(_node(index))
    two_stage_admission_ms = (time.perf_counter() - started) * 1000.0
    calls_before_drain = len(two_stage_provider.calls)

    started = time.perf_counter()
    metrics = two_stage.indexing.make_index_job_worker(
        batch_size=entity_count,
        max_inflight=1,
        max_jobs_per_tick=entity_count,
    ).tick()
    promotion_ms = (time.perf_counter() - started) * 1000.0
    promoted = sum(
        1
        for index in range(entity_count)
        if two_stage.backend.node_get(ids=[_node(index).safe_get_id()], include=["embeddings"])["embeddings"][0]
        is not None
    )
    if metrics.done < entity_count or promoted != entity_count:
        raise RuntimeError("mock two-stage benchmark did not promote every conversation node")

    return ConversationMaterializationBenchmark(
        entity_count=entity_count,
        provider_delay_ms=provider_delay_ms,
        single_stage_admission_ms=single_admission_ms,
        single_stage_provider_calls=len(single_provider.calls),
        two_stage_admission_ms=two_stage_admission_ms,
        two_stage_provider_calls_before_drain=calls_before_drain,
        two_stage_promotion_ms=promotion_ms,
        two_stage_total_ms=two_stage_admission_ms + promotion_ms,
        two_stage_provider_calls_total=len(two_stage_provider.calls),
        promoted_entities=promoted,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entities", type=int, default=12)
    parser.add_argument("--provider-delay-ms", type=float, default=10.0)
    parser.add_argument(
        "--work-dir",
        type=Path,
        help="Directory for deterministic in-memory benchmark state. Defaults to a temporary directory.",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = run_conversation_materialization_benchmark(
        entity_count=args.entities,
        provider_delay_ms=args.provider_delay_ms,
        base_dir=args.work_dir,
    )
    payload: dict[str, Any] = asdict(report)
    payload["admission_speedup"] = report.admission_speedup
    rendered = json.dumps(payload, indent=2, sort_keys=True)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
