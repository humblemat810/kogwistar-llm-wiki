"""Compare synchronous multimodal recall with a bounded async evidence feed.

The default backend is deterministic and intentionally small.  It measures
orchestration behavior, not model quality.  A production encoder can be
inserted through the same ``retrieve`` callback used by the experiment.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from kogwistar_llm_wiki.embeddings.multimodal_projection import MultimodalSearchHit
from kogwistar_llm_wiki.embeddings.retrieval_experiment import (
    MultimodalRetrievalSidecar,
    synchronous_multimodal_recall,
)


@dataclass(frozen=True, slots=True)
class RetrievalModeResult:
    mode: str
    total_ms: float
    time_to_fast_path_ms: float
    time_to_first_multimodal_ms: float
    main_steps_before_multimodal: int
    expected_target_retrieved: bool
    correction_success: bool
    emitted_events: int
    admitted_events: int
    dropped_events: int


def _hit(view_id: str, score: float = 0.9) -> MultimodalSearchHit:
    return MultimodalSearchHit(
        view_id=view_id,
        score=score,
        source_id=f"fixture-source-{view_id}",
        source_revision_id="fixture-revision-1",
        modality="image",
        locator={"kind": "whole_image", "fixture": True},
        metadata={"fixture_only": True},
    )


async def run_experiment(*, multimodal_delay_ms: float = 80.0) -> dict[str, object]:
    """Run equal synchronous and asynchronous scenarios over the fixture."""

    async def retrieve(query: str, scope: frozenset[str] | None):
        await asyncio.sleep(multimodal_delay_ms / 1000.0)
        if "bottleneck" in query:
            return (_hit("benchmark-image"),)
        return (_hit("architecture-image"),)

    async def main_steps() -> tuple[int, float]:
        started = time.perf_counter()
        steps = 0
        for _ in range(3):
            await asyncio.sleep(0.015)
            steps += 1
        return steps, (time.perf_counter() - started) * 1000.0

    sync_started = time.perf_counter()
    sync_result = await synchronous_multimodal_recall(
        retrieve, "parallel architecture", source_scope=None
    )
    _sync_steps, sync_fast_ms = await main_steps()
    sync_total_ms = (time.perf_counter() - sync_started) * 1000.0
    sync_target = any(hit.view_id == "architecture-image" for hit in sync_result.hits)
    sync_metrics = RetrievalModeResult(
        mode="synchronous",
        total_ms=sync_total_ms,
        time_to_fast_path_ms=sync_fast_ms + sync_result.timing.total_ms,
        time_to_first_multimodal_ms=sync_result.timing.total_ms,
        main_steps_before_multimodal=0,
        expected_target_retrieved=sync_target,
        correction_success=sync_target,
        emitted_events=0,
        admitted_events=0,
        dropped_events=0,
    )

    async_started = time.perf_counter()
    async_started_wall = time.time()
    sidecar = MultimodalRetrievalSidecar(retrieve)
    subscription = sidecar.start(
        run_id="fixture-run",
        subscription_id="fixture-subscription",
        workspace_id="fixture-workspace",
        profile_fingerprint="fixture-native-mm-v1",
        query="parallel architecture",
    )
    async_steps, fast_ms = await main_steps()
    await subscription.wait()
    events = await subscription.drain()
    async_total_ms = (time.perf_counter() - async_started) * 1000.0
    async_target = any(event.view_id == "architecture-image" for event in events)
    async_metrics = RetrievalModeResult(
        mode="asynchronous_sidecar",
        total_ms=async_total_ms,
        time_to_fast_path_ms=fast_ms,
        time_to_first_multimodal_ms=(
            (events[0].emitted_at - async_started_wall) * 1000.0 if async_target else 0.0
        ),
        main_steps_before_multimodal=async_steps if async_target else 0,
        expected_target_retrieved=async_target,
        correction_success=async_target,
        emitted_events=len(events) + subscription.dropped_count,
        admitted_events=len(events),
        dropped_events=subscription.dropped_count,
    )
    return {
        "backend": "deterministic_fixture_retriever",
        "multimodal_delay_ms": multimodal_delay_ms,
        "results": [asdict(sync_metrics), asdict(async_metrics)],
        "notes": [
            "Fixture retrieval is deterministic and does not measure model inference quality.",
            "The asynchronous timing demonstrates overlap and safe late evidence admission.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delay-ms", type=float, default=80.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = asyncio.run(run_experiment(multimodal_delay_ms=args.delay_ms))
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
