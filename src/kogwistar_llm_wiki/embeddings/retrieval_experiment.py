"""Bounded synchronous and asynchronous multimodal retrieval experiments.

This module deliberately stays above the projection store.  It models the
runtime choice between waiting for native multimodal recall and allowing a
main worker to make progress while the same recall runs in the background.
Events carry references only; they never become canonical graph truth.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from .multimodal_projection import MultimodalSearchHit


class FeedState(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    CLOSED = "closed"
    CANCELLED = "cancelled"
    FINISHED = "finished"


@dataclass(frozen=True, slots=True)
class MultimodalRecallTiming:
    """Timing breakdown for a synchronous native multimodal recall."""

    query_embedding_ms: float
    vector_search_ms: float
    total_ms: float


@dataclass(frozen=True, slots=True)
class SynchronousMultimodalRecall:
    hits: tuple[MultimodalSearchHit, ...]
    timing: MultimodalRecallTiming


@dataclass(frozen=True, slots=True)
class EvidenceEvent:
    """Reference-first evidence emitted by an asynchronous retrieval feed."""

    run_id: str
    subscription_id: str
    origin_epoch: int
    seq: int
    workspace_id: str
    view_id: str
    source_id: str
    source_revision_id: str
    modality: str
    profile_fingerprint: str
    retrieval_score: float
    emitted_at: float
    locator: Mapping[str, object] = field(default_factory=dict)
    metadata: Mapping[str, object] = field(default_factory=dict)

    @property
    def evidence_key(self) -> tuple[str, str, str, str]:
        return (
            self.profile_fingerprint,
            self.view_id,
            self.source_id,
            self.source_revision_id,
        )


Retrieve = Callable[[str, frozenset[str] | None], Awaitable[Iterable[MultimodalSearchHit]]]
HitValidator = Callable[[MultimodalSearchHit], None]


def _clock_ms() -> float:
    return time.perf_counter() * 1000.0


async def synchronous_multimodal_recall(
    retrieve: Retrieve,
    query: str,
    *,
    source_scope: frozenset[str] | None = None,
) -> SynchronousMultimodalRecall:
    """Run the native multimodal recall path and wait for all results."""

    started = _clock_ms()
    hits = tuple(await retrieve(query, source_scope))
    finished = _clock_ms()
    # The callback owns model/search internals.  The total is authoritative;
    # the split is intentionally explicit but unavailable without instrumentation.
    return SynchronousMultimodalRecall(
        hits=hits,
        timing=MultimodalRecallTiming(
            query_embedding_ms=0.0,
            vector_search_ms=0.0,
            total_ms=finished - started,
        ),
    )


class EvidenceSubscription:
    """A bounded, explicitly controlled evidence stream.

    ``drain`` is the safe assimilation boundary.  Retrieval tasks may finish
    after ``close_feed`` or ``cancel``; their results are then rejected and
    never enter the active stream.
    """

    def __init__(
        self,
        *,
        run_id: str,
        subscription_id: str,
        workspace_id: str,
        profile_fingerprint: str,
        retrieve: Retrieve,
        query: str,
        max_events: int = 32,
        validate_hit: HitValidator | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        if not run_id or not subscription_id or not workspace_id or not profile_fingerprint:
            raise ValueError("evidence subscriptions require stable identity fields")
        if max_events <= 0:
            raise ValueError("max_events must be positive")
        if timeout_seconds is not None and timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive when supplied")
        self.run_id = run_id
        self.subscription_id = subscription_id
        self.workspace_id = workspace_id
        self.profile_fingerprint = profile_fingerprint
        self._retrieve = retrieve
        self._query = query
        self._max_events = max_events
        self._validate_hit = validate_hit
        self._timeout_seconds = timeout_seconds
        self._state = FeedState.CREATED
        self._epoch = 0
        self._next_seq = 0
        self._queue: deque[EvidenceEvent] = deque()
        self._seen: set[tuple[str, str, str, str]] = set()
        self._task: asyncio.Task[None] | None = None
        self._scope: frozenset[str] | None = None
        self._refine_requested = False
        self._dropped = 0
        self._error: Exception | None = None

    @property
    def state(self) -> FeedState:
        return self._state

    @property
    def dropped_count(self) -> int:
        return self._dropped

    @property
    def epoch(self) -> int:
        return self._epoch

    @property
    def error(self) -> Exception | None:
        """Provider failure, if the feed degraded instead of producing events."""

        return self._error

    def start(self) -> None:
        if self._state is not FeedState.CREATED:
            raise RuntimeError(f"cannot start feed in state {self._state}")
        self._state = FeedState.RUNNING
        self._task = asyncio.create_task(self._produce())

    def refine(self, *, source_scope: Iterable[str]) -> None:
        if self._state is not FeedState.RUNNING:
            raise RuntimeError(f"cannot refine feed in state {self._state}")
        self._epoch += 1
        self._scope = frozenset(str(item) for item in source_scope)
        self._refine_requested = True

    def close_feed(self) -> None:
        """Stop logical admission; an in-flight provider call may finish."""

        if self._state is FeedState.RUNNING:
            self._state = FeedState.CLOSED

    def cancel(self) -> None:
        """Close admission and best-effort cancel the provider task."""

        if self._state in {FeedState.CREATED, FeedState.RUNNING}:
            self._state = FeedState.CANCELLED
            if self._task is not None:
                self._task.cancel()

    async def wait(self) -> None:
        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                if self._state is not FeedState.CANCELLED:
                    raise

    async def drain(self, *, limit: int | None = None) -> tuple[EvidenceEvent, ...]:
        """Consume pending evidence at an explicit main-worker checkpoint."""

        if limit is not None and limit < 0:
            raise ValueError("limit cannot be negative")
        items: list[EvidenceEvent] = []
        while self._queue and (limit is None or len(items) < limit):
            items.append(self._queue.popleft())
        return tuple(items)

    async def _produce(self) -> None:
        try:
            while self._state is FeedState.RUNNING:
                origin_epoch = self._epoch
                source_scope = self._scope
                self._refine_requested = False
                retrieval = self._retrieve(self._query, source_scope)
                if self._timeout_seconds is None:
                    hits = await retrieval
                else:
                    hits = await asyncio.wait_for(retrieval, timeout=self._timeout_seconds)
                for hit in hits:
                    if self._state is not FeedState.RUNNING:
                        self._dropped += 1
                        continue
                    self._emit(hit, origin_epoch=origin_epoch)
                if not self._refine_requested and self._state is FeedState.RUNNING:
                    self._state = FeedState.FINISHED
        except asyncio.CancelledError:
            if self._state is not FeedState.CANCELLED:
                raise
        except (ConnectionError, OSError, RuntimeError, TimeoutError, ValueError) as exc:
            # A failed sidecar must not fail the main worker.  Its terminal
            # state is observable and the caller can report the provider error.
            self._error = exc
            self._state = FeedState.FINISHED
            return

    def _emit(self, hit: MultimodalSearchHit, *, origin_epoch: int) -> None:
        key = (
            self.profile_fingerprint,
            hit.view_id,
            hit.source_id,
            hit.source_revision_id,
        )
        if self._validate_hit is not None:
            try:
                self._validate_hit(hit)
            except (PermissionError, ValueError):
                self._dropped += 1
                return
        if key in self._seen:
            self._dropped += 1
            return
        if len(self._queue) >= self._max_events:
            self._queue.popleft()
            self._dropped += 1
        self._seen.add(key)
        self._queue.append(
            EvidenceEvent(
                run_id=self.run_id,
                subscription_id=self.subscription_id,
                origin_epoch=origin_epoch,
                seq=self._next_seq,
                workspace_id=self.workspace_id,
                view_id=hit.view_id,
                source_id=hit.source_id,
                source_revision_id=hit.source_revision_id,
                modality=hit.modality,
                profile_fingerprint=self.profile_fingerprint,
                retrieval_score=float(hit.score),
                emitted_at=time.time(),
                locator=dict(hit.locator),
                metadata=dict(hit.metadata),
            )
        )
        self._next_seq += 1


class MultimodalRetrievalSidecar:
    """Small application-local sidecar factory for the experiment."""

    def __init__(
        self,
        retrieve: Retrieve,
        *,
        max_events: int = 32,
        validate_hit: HitValidator | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self._retrieve = retrieve
        self._max_events = max_events
        self._validate_hit = validate_hit
        self._timeout_seconds = timeout_seconds

    def start(
        self,
        *,
        run_id: str,
        subscription_id: str,
        workspace_id: str,
        profile_fingerprint: str,
        query: str,
    ) -> EvidenceSubscription:
        subscription = EvidenceSubscription(
            run_id=run_id,
            subscription_id=subscription_id,
            workspace_id=workspace_id,
            profile_fingerprint=profile_fingerprint,
            retrieve=self._retrieve,
            query=query,
            max_events=self._max_events,
            validate_hit=self._validate_hit,
            timeout_seconds=self._timeout_seconds,
        )
        subscription.start()
        return subscription
