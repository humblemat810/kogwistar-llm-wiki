from __future__ import annotations

import asyncio
import time

import pytest

from kogwistar_llm_wiki.embeddings.multimodal_projection import MultimodalSearchHit
from kogwistar_llm_wiki.embeddings.retrieval_experiment import (
    FeedState,
    MultimodalRetrievalSidecar,
    synchronous_multimodal_recall,
)


def _hit(view_id: str, *, score: float = 0.9) -> MultimodalSearchHit:
    return MultimodalSearchHit(
        view_id=view_id,
        score=score,
        source_id=f"source-{view_id}",
        source_revision_id="revision-1",
        modality="image",
        locator={"kind": "whole_image"},
        metadata={"fixture": True},
    )


@pytest.mark.anyio
async def test_synchronous_recall_waits_for_native_retrieval() -> None:
    async def retrieve(query: str, scope: frozenset[str] | None):
        assert query == "parallel architecture"
        assert scope is None
        await asyncio.sleep(0.02)
        return (_hit("architecture"),)

    result = await synchronous_multimodal_recall(retrieve, "parallel architecture")

    assert [item.view_id for item in result.hits] == ["architecture"]
    assert result.timing.total_ms >= 15


@pytest.mark.anyio
async def test_sidecar_overlaps_main_progress_and_assimilates_at_checkpoint() -> None:
    started = time.perf_counter()
    main_steps: list[str] = []

    async def retrieve(query: str, scope: frozenset[str] | None):
        await asyncio.sleep(0.05)
        return (_hit("late-diagram"),)

    sidecar = MultimodalRetrievalSidecar(retrieve)
    subscription = sidecar.start(
        run_id="run-1",
        subscription_id="sub-1",
        workspace_id="workspace-1",
        profile_fingerprint="profile-image-v1",
        query="parallel architecture",
    )
    await asyncio.sleep(0.01)
    main_steps.append("graph-step-a")
    await asyncio.sleep(0.01)
    main_steps.append("text-search")
    assert subscription.state is FeedState.RUNNING
    await subscription.wait()
    events = await subscription.drain()

    assert main_steps == ["graph-step-a", "text-search"]
    assert events[0].view_id == "late-diagram"
    assert (time.perf_counter() - started) < 0.10


@pytest.mark.anyio
async def test_close_feed_rejects_results_from_inflight_retrieval() -> None:
    release = asyncio.Event()

    async def retrieve(query: str, scope: frozenset[str] | None):
        await release.wait()
        return (_hit("closed-result"),)

    subscription = MultimodalRetrievalSidecar(retrieve).start(
        run_id="run-2",
        subscription_id="sub-2",
        workspace_id="workspace-1",
        profile_fingerprint="profile-image-v1",
        query="diagram",
    )
    await asyncio.sleep(0)
    subscription.close_feed()
    release.set()
    await subscription.wait()

    assert subscription.state is FeedState.CLOSED
    assert await subscription.drain() == ()
    assert subscription.dropped_count == 1


@pytest.mark.anyio
async def test_cancel_is_logical_immediately_and_best_effort_physically() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    async def retrieve(query: str, scope: frozenset[str] | None):
        started.set()
        await release.wait()
        return (_hit("cancelled-result"),)

    subscription = MultimodalRetrievalSidecar(retrieve).start(
        run_id="run-3",
        subscription_id="sub-3",
        workspace_id="workspace-1",
        profile_fingerprint="profile-image-v1",
        query="diagram",
    )
    await started.wait()
    subscription.cancel()
    await subscription.wait()

    assert subscription.state is FeedState.CANCELLED
    assert await subscription.drain() == ()


@pytest.mark.anyio
async def test_duplicate_hits_are_deduplicated_and_late_epoch_is_preserved() -> None:
    release = asyncio.Event()
    scopes: list[frozenset[str] | None] = []

    async def retrieve(query: str, scope: frozenset[str] | None):
        scopes.append(scope)
        await release.wait()
        if len(scopes) > 1:
            return (_hit("same", score=0.6),)
        return (_hit("same", score=0.8), _hit("same", score=0.7), _hit("other"))

    subscription = MultimodalRetrievalSidecar(retrieve).start(
        run_id="run-4",
        subscription_id="sub-4",
        workspace_id="workspace-1",
        profile_fingerprint="profile-image-v1",
        query="diagram",
    )
    await asyncio.sleep(0)
    subscription.refine(source_scope={"source-allowed"})
    release.set()
    await subscription.wait()
    events = await subscription.drain()

    assert [event.view_id for event in events] == ["same", "other"]
    assert all(event.origin_epoch == 0 for event in events)
    assert subscription.dropped_count == 2
    assert scopes == [None, frozenset({"source-allowed"})]


@pytest.mark.anyio
async def test_late_relevant_evidence_can_refine_current_conclusion() -> None:
    async def retrieve(query: str, scope: frozenset[str] | None):
        await asyncio.sleep(0.01)
        return (_hit("architecture-image"),)

    subscription = MultimodalRetrievalSidecar(retrieve).start(
        run_id="run-5",
        subscription_id="sub-5",
        workspace_id="workspace-1",
        profile_fingerprint="profile-image-v1",
        query="architecture",
    )
    provisional_conclusion = "synchronous"
    await subscription.wait()
    events = await subscription.drain()
    if any(event.view_id == "architecture-image" for event in events):
        provisional_conclusion = "asynchronous"

    assert provisional_conclusion == "asynchronous"
    assert events[0].origin_epoch == 0


@pytest.mark.anyio
async def test_host_validation_rejects_unauthorized_hits_before_admission() -> None:
    async def retrieve(query: str, scope: frozenset[str] | None):
        return (_hit("unauthorized"), _hit("authorized"))

    def validate(hit: MultimodalSearchHit) -> None:
        if hit.view_id == "unauthorized":
            raise PermissionError("outside workspace ACL")

    subscription = MultimodalRetrievalSidecar(retrieve, validate_hit=validate).start(
        run_id="run-6",
        subscription_id="sub-6",
        workspace_id="workspace-1",
        profile_fingerprint="profile-image-v1",
        query="architecture",
    )
    await subscription.wait()

    assert [event.view_id for event in await subscription.drain()] == ["authorized"]
    assert subscription.dropped_count == 1


@pytest.mark.anyio
async def test_unavailable_sidecar_becomes_observable_without_failing_main_worker() -> None:
    async def retrieve(query: str, scope: frozenset[str] | None):
        raise ConnectionError("embedding service unavailable")

    subscription = MultimodalRetrievalSidecar(retrieve, timeout_seconds=0.1).start(
        run_id="run-7",
        subscription_id="sub-7",
        workspace_id="workspace-1",
        profile_fingerprint="profile-image-v1",
        query="architecture",
    )
    await subscription.wait()

    assert subscription.state is FeedState.FINISHED
    assert isinstance(subscription.error, ConnectionError)
    assert await subscription.drain() == ()
