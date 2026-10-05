from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import replace

import pytest
from kogwistar.engine_core import (
    EmbeddingReference,
    MultimodalSpan,
    PinnedLogicalRef,
    SpatialRegionLocator,
)
from kogwistar.logical_refs import LogicalRef

from kogwistar_llm_wiki.embeddings.multimodal_dereference import (
    EmbeddingReferenceDereferencer,
)
from kogwistar_llm_wiki.embeddings.multimodal_projection import (
    InMemoryMultimodalProjectionStore,
    MultimodalEmbeddingProfile,
    MultimodalSearchHit,
    MultimodalSourceUnit,
)
from kogwistar_llm_wiki.embeddings.retrieval_experiment import (
    EvidenceCandidate,
    FeedState,
    MultimodalRetrievalSidecar,
    pipeline_multimodal_retriever,
    synchronous_multimodal_recall,
)
from kogwistar_llm_wiki.maintenance.maintenance_designs import (
    build_multimodal_retrieval_design,
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
        multimodal_span=MultimodalSpan(
            source_namespace="workspace-1",
            resource_id=f"source-{view_id}",
            resource_revision_id="revision-1",
            content_sha256="0" * 64,
            modality="image",
            locator=SpatialRegionLocator(x=0, y=0, width=1, height=1),
        ),
    )


def _candidate(view_id: str, *, score: float = 0.9) -> EvidenceCandidate:
    return EvidenceCandidate(
        hit=_hit(view_id, score=score),
        workspace_id="workspace-1",
        profile_fingerprint="profile-image-v1",
    )


def _candidate_with_identity(
    view_id: str,
    *,
    workspace_id: str = "workspace-1",
    profile_fingerprint: str = "profile-image-v1",
) -> EvidenceCandidate:
    return EvidenceCandidate(
        hit=_hit(view_id),
        workspace_id=workspace_id,
        profile_fingerprint=profile_fingerprint,
    )


def _validate(_: EvidenceCandidate) -> None:
    return None


@pytest.mark.anyio
async def test_synchronous_recall_waits_for_native_retrieval() -> None:
    async def retrieve(query: str, scope: frozenset[str] | None):
        assert query == "parallel architecture"
        assert scope is None
        await asyncio.sleep(0.02)
        return (_candidate("architecture"),)

    result = await synchronous_multimodal_recall(
        retrieve,
        "parallel architecture",
        workspace_id="workspace-1",
        profile_fingerprint="profile-image-v1",
        validate_hit=_validate,
    )

    assert [item.view_id for item in result.hits] == ["architecture"]
    assert result.timing.total_ms >= 15


@pytest.mark.anyio
async def test_sidecar_overlaps_main_progress_and_assimilates_at_checkpoint() -> None:
    started = time.perf_counter()
    main_steps: list[str] = []

    async def retrieve(query: str, scope: frozenset[str] | None):
        await asyncio.sleep(0.05)
        return (_candidate("late-diagram"),)

    sidecar = MultimodalRetrievalSidecar(retrieve)
    subscription = sidecar.start(
        run_id="run-1",
        subscription_id="sub-1",
        workspace_id="workspace-1",
        profile_fingerprint="profile-image-v1",
        query="parallel architecture",
        validate_hit=_validate,
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
async def test_progressive_async_results_wake_checkpoint_without_polling() -> None:
    async def retrieve(query: str, scope: frozenset[str] | None):
        async def candidates():
            await asyncio.sleep(0.01)
            yield _candidate("first")
            await asyncio.sleep(0.02)
            yield _candidate("second")

        return candidates()

    subscription = MultimodalRetrievalSidecar(retrieve, validate_hit=_validate).start(
        run_id="run-progressive",
        subscription_id="sub-progressive",
        workspace_id="workspace-1",
        profile_fingerprint="profile-image-v1",
        query="diagram",
    )

    assert await subscription.wait_for_update(timeout=0.2)
    first = await subscription.drain()
    assert [event.view_id for event in first] == ["first"]
    assert first[0].multimodal_span is not None
    assert await subscription.wait_for_update(timeout=0.2)
    second = await subscription.drain()
    assert [event.view_id for event in second] == ["second"]
    await subscription.wait()
    assert subscription.state is FeedState.FINISHED


@pytest.mark.anyio
async def test_sync_retriever_runs_off_event_loop_and_rejects_profile_mismatch() -> (
    None
):
    retrieval_started = asyncio.Event()

    def retrieve(query: str, scope: frozenset[str] | None):
        retrieval_started_loop.call_soon_threadsafe(retrieval_started.set)
        time.sleep(0.04)
        return (
            _candidate_with_identity(
                "wrong-profile", profile_fingerprint="other-profile"
            ),
            _candidate("valid"),
        )

    retrieval_started_loop = asyncio.get_running_loop()
    subscription = MultimodalRetrievalSidecar(
        retrieve,
        validate_hit=_validate,
        timeout_seconds=1.0,
    ).start(
        run_id="run-thread",
        subscription_id="sub-thread",
        workspace_id="workspace-1",
        profile_fingerprint="profile-image-v1",
        query="diagram",
    )
    await retrieval_started.wait()
    progress_finished = asyncio.Event()
    asyncio.get_running_loop().call_later(0.005, progress_finished.set)
    await progress_finished.wait()
    await subscription.wait()

    assert [event.view_id for event in await subscription.drain()] == ["valid"]
    assert subscription.dropped_count == 1


@pytest.mark.anyio
async def test_feed_queue_keeps_best_ranked_reference_and_bounds_event_metadata() -> (
    None
):
    async def retrieve(query: str, scope: frozenset[str] | None):
        huge = _hit("low", score=0.1)
        huge = replace(
            huge,
            metadata={"raw_text": "x" * 10000, "fixture_only": True},
            locator={"kind": "whole_image", "payload": "x" * 10000},
        )
        high = replace(
            _hit("high", score=0.95),
            metadata={"raw_text": "y" * 10000, "fixture_only": True},
            locator={"kind": "whole_image", "payload": "y" * 10000},
        )
        return (
            EvidenceCandidate(huge, "workspace-1", "profile-image-v1"),
            EvidenceCandidate(high, "workspace-1", "profile-image-v1"),
        )

    subscription = MultimodalRetrievalSidecar(
        retrieve, max_events=1, validate_hit=_validate
    ).start(
        run_id="run-bounded",
        subscription_id="sub-bounded",
        workspace_id="workspace-1",
        profile_fingerprint="profile-image-v1",
        query="diagram",
    )
    await subscription.wait()
    events = await subscription.drain()

    assert [event.view_id for event in events] == ["high"]
    assert events[0].metadata == {"fixture_only": True}
    assert events[0].locator == {"kind": "whole_image"}
    assert subscription.dropped_count == 1


@pytest.mark.anyio
async def test_close_feed_rejects_results_from_inflight_retrieval() -> None:
    release = asyncio.Event()

    async def retrieve(query: str, scope: frozenset[str] | None):
        await release.wait()
        return (_candidate("closed-result"),)

    subscription = MultimodalRetrievalSidecar(retrieve).start(
        run_id="run-2",
        subscription_id="sub-2",
        workspace_id="workspace-1",
        profile_fingerprint="profile-image-v1",
        query="diagram",
        validate_hit=_validate,
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
        return (_candidate("cancelled-result"),)

    subscription = MultimodalRetrievalSidecar(retrieve).start(
        run_id="run-3",
        subscription_id="sub-3",
        workspace_id="workspace-1",
        profile_fingerprint="profile-image-v1",
        query="diagram",
        validate_hit=_validate,
    )
    await started.wait()
    subscription.cancel()
    await subscription.wait()

    assert subscription.state is FeedState.CANCELLED
    assert await subscription.drain() == ()


@pytest.mark.anyio
async def test_cancelled_blocking_provider_result_is_never_admitted() -> None:
    provider_started = threading.Event()
    release_provider = threading.Event()
    provider_finished = threading.Event()

    def retrieve(query: str, scope: frozenset[str] | None):
        provider_started.set()
        release_provider.wait(timeout=1.0)
        provider_finished.set()
        return (_candidate("completed-after-cancel"),)

    subscription = MultimodalRetrievalSidecar(
        retrieve, validate_hit=_validate, timeout_seconds=2.0
    ).start(
        run_id="run-cancel-thread",
        subscription_id="sub-cancel-thread",
        workspace_id="workspace-1",
        profile_fingerprint="profile-image-v1",
        query="diagram",
    )
    assert await asyncio.to_thread(provider_started.wait, 1.0)
    subscription.cancel()
    await subscription.wait()
    release_provider.set()
    assert await asyncio.to_thread(provider_finished.wait, 1.0)
    assert await subscription.drain() == ()


@pytest.mark.anyio
async def test_empty_sidecar_result_finishes_without_blocking_main_path() -> None:
    release = asyncio.Event()

    async def retrieve(query: str, scope: frozenset[str] | None):
        await release.wait()
        return ()

    subscription = MultimodalRetrievalSidecar(retrieve, validate_hit=_validate).start(
        run_id="run-empty",
        subscription_id="sub-empty",
        workspace_id="workspace-1",
        profile_fingerprint="profile-image-v1",
        query="diagram",
    )
    main_path_completed = asyncio.Event()

    async def main_path() -> str:
        await asyncio.sleep(0)
        main_path_completed.set()
        return "text/graph path completed"

    main_result = await main_path()
    assert main_path_completed.is_set()
    assert main_result == "text/graph path completed"
    release.set()
    await subscription.wait()

    assert subscription.error is None
    assert await subscription.drain() == ()


@pytest.mark.anyio
async def test_duplicate_hits_are_deduplicated_and_late_epoch_is_preserved() -> None:
    release = asyncio.Event()
    scopes: list[frozenset[str] | None] = []

    async def retrieve(query: str, scope: frozenset[str] | None):
        scopes.append(scope)
        await release.wait()
        if len(scopes) > 1:
            return (_candidate("same", score=0.6),)
        return (
            _candidate("same", score=0.8),
            _candidate("same", score=0.7),
            _candidate("other"),
        )

    subscription = MultimodalRetrievalSidecar(retrieve).start(
        run_id="run-4",
        subscription_id="sub-4",
        workspace_id="workspace-1",
        profile_fingerprint="profile-image-v1",
        query="diagram",
        validate_hit=_validate,
    )
    await asyncio.sleep(0)
    subscription.refine(source_scope={"source-allowed"})
    release.set()
    await subscription.wait()
    events = await subscription.drain()

    assert events == ()
    assert subscription.stale_epoch_dropped_count == 2
    assert subscription.dropped_count == 4
    assert scopes == [None, frozenset({"source-allowed"})]


@pytest.mark.anyio
async def test_late_relevant_evidence_can_refine_current_conclusion() -> None:
    async def retrieve(query: str, scope: frozenset[str] | None):
        await asyncio.sleep(0.01)
        return (_candidate("architecture-image"),)

    subscription = MultimodalRetrievalSidecar(retrieve).start(
        run_id="run-5",
        subscription_id="sub-5",
        workspace_id="workspace-1",
        profile_fingerprint="profile-image-v1",
        query="architecture",
        validate_hit=_validate,
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
        return (_candidate("unauthorized"), _candidate("authorized"))

    def validate(candidate: EvidenceCandidate) -> None:
        if candidate.hit.view_id == "unauthorized":
            raise PermissionError("outside workspace ACL")

    subscription = MultimodalRetrievalSidecar(retrieve, validate_hit=validate).start(
        run_id="run-6",
        subscription_id="sub-6",
        workspace_id="workspace-1",
        profile_fingerprint="profile-image-v1",
        query="architecture",
        validate_hit=validate,
    )
    await subscription.wait()

    assert [event.view_id for event in await subscription.drain()] == ["authorized"]
    assert subscription.dropped_count == 1


@pytest.mark.anyio
async def test_unavailable_sidecar_becomes_observable_without_failing_main_worker() -> (
    None
):
    async def retrieve(query: str, scope: frozenset[str] | None):
        raise ConnectionError("embedding service unavailable")

    subscription = MultimodalRetrievalSidecar(retrieve, timeout_seconds=0.1).start(
        run_id="run-7",
        subscription_id="sub-7",
        workspace_id="workspace-1",
        profile_fingerprint="profile-image-v1",
        query="architecture",
        validate_hit=_validate,
    )
    await subscription.wait()

    assert subscription.state is FeedState.FINISHED
    assert isinstance(subscription.error, ConnectionError)
    assert await subscription.drain() == ()


@pytest.mark.anyio
async def test_drain_rechecks_current_acl_after_evidence_was_queued() -> None:
    revoked = False

    def revalidate(candidate: EvidenceCandidate) -> None:
        if revoked:
            raise PermissionError("source ACL was revoked after retrieval")

    async def retrieve(query: str, scope: frozenset[str] | None):
        return (_candidate("revocable"),)

    subscription = MultimodalRetrievalSidecar(
        retrieve, validate_hit=_validate, revalidate_hit=revalidate
    ).start(
        run_id="run-revocation",
        subscription_id="sub-revocation",
        workspace_id="workspace-1",
        profile_fingerprint="profile-image-v1",
        query="diagram",
    )
    await subscription.wait()
    revoked = True

    assert await subscription.drain() == ()
    assert subscription.dropped_count == 1


@pytest.mark.anyio
async def test_async_stream_timeout_covers_iteration_between_yields() -> None:
    release = asyncio.Event()

    async def retrieve(query: str, scope: frozenset[str] | None):
        async def candidates():
            yield _candidate("first-before-timeout")
            await release.wait()
            yield _candidate("never-reached")

        return candidates()

    subscription = MultimodalRetrievalSidecar(
        retrieve, validate_hit=_validate, timeout_seconds=0.01
    ).start(
        run_id="run-stream-timeout",
        subscription_id="sub-stream-timeout",
        workspace_id="workspace-1",
        profile_fingerprint="profile-image-v1",
        query="diagram",
    )
    await subscription.wait()

    assert isinstance(subscription.error, TimeoutError)
    assert [event.view_id for event in await subscription.drain()] == [
        "first-before-timeout"
    ]


def test_pipeline_skips_one_unauthorized_hit_and_keeps_later_authorized_hits() -> None:
    profile = MultimodalEmbeddingProfile(
        provider="fixture", model="retrieval", embedding="dense", dimension=2
    )
    encoder = type(
        "Encoder",
        (),
        {
            "profile": profile,
            "encode_queries": lambda self, queries: [((1.0, 0.0),) for _ in queries],
        },
    )()
    store = InMemoryMultimodalProjectionStore(scope="workspace-1", profile=profile)
    units = tuple(
        MultimodalSourceUnit(
            view_id=f"blocked-{index}",
            workspace_id="workspace-1",
            source_id=f"blocked-source-{index}",
            source_revision_id="rev-1",
            modality="text",
            locator={"kind": "text_span", "start_char": 0, "end_char": 4},
            text="text",
        )
        for index in range(40)
    ) + (
        MultimodalSourceUnit(
            view_id="allowed",
            workspace_id="workspace-1",
            source_id="allowed-source",
            source_revision_id="rev-1",
            modality="text",
            locator={"kind": "text_span", "start_char": 0, "end_char": 4},
            text="text",
        ),
    )
    for unit in units:
        vector = ((1.0, 0.0),) if unit.source_id.startswith("blocked") else ((0.9, 0.1),)
        store.capture(unit)
        store.upsert_embedding(unit, vector, profile=profile)

    class Pipeline:
        multimodal_encoder = encoder
        multimodal_projection_store = store

    def authorize(unit: MultimodalSourceUnit) -> None:
        if unit.source_id.startswith("blocked-source"):
            raise PermissionError("ACL denied")

    batch = pipeline_multimodal_retriever(
        Pipeline(), workspace_id="workspace-1", authorize_source=authorize, limit=1
    )("text", None)
    assert [candidate.hit.view_id for candidate in batch.candidates] == ["allowed"]


def test_pipeline_marks_reference_available_only_after_authorized_dereference() -> None:
    profile = MultimodalEmbeddingProfile(
        provider="fixture", model="retrieval", embedding="dense", dimension=2
    )
    unit = MultimodalSourceUnit(
        view_id="referenced",
        workspace_id="workspace-1",
        source_namespace="workspace-1",
        source_id="source-1",
        source_revision_id="rev-1",
        modality="text",
        locator={"kind": "text_span", "start_char": 0, "end_char": 4},
        text="text",
    )
    reference = EmbeddingReference(
        source_namespace="workspace-1",
        profile_fingerprint=profile.fingerprint,
        embedding_set_id=unit.view_id,
        span=unit.to_multimodal_span(),
        targets=(
            PinnedLogicalRef(
                logical_ref=LogicalRef("workspace-1", "artifact", "source-map:source-1"),
                role="source_map",
                revision_id="rev-1",
            ),
        ),
    )
    unit = replace(unit, embedding_reference=reference)
    store = InMemoryMultimodalProjectionStore(scope="workspace-1", profile=profile)
    store.upsert_embedding(unit, ((1.0, 0.0),), profile=profile)

    class Encoder:
        def __init__(self, profile):
            self.profile = profile

        def encode_queries(self, queries):
            return [((1.0, 0.0),) for _ in queries]

    class Pipeline:
        multimodal_encoder = Encoder(profile)
        multimodal_projection_store = store

    class Resolver:
        def resolve_source_map(self, reference):
            return True

        def authorize_target(self, reference):
            return True

    batch = pipeline_multimodal_retriever(
        Pipeline(),
        workspace_id="workspace-1",
        authorize_source=lambda unit: None,
        dereferencer=EmbeddingReferenceDereferencer(Resolver()),
    )("text", None)
    assert batch.candidates[0].hit.dereference_status == "available"


def test_multimodal_sidecar_has_a_graph_native_workflow_design() -> None:
    design = build_multimodal_retrieval_design()
    operations = {str(node.metadata["wf_op"]) for node in design.nodes}
    assert design.start_node_id in {str(node.id) for node in design.nodes}
    assert {"multimodal_dispatch", "multimodal_sidecar", "multimodal_authorize"} <= operations
    assert any(
        edge.metadata.get("wf_predicate") is None
        for edge in design.edges
    )
