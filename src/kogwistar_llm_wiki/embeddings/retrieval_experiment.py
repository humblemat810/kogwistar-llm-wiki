"""Bounded synchronous and asynchronous multimodal retrieval experiments.

This module deliberately stays above the projection store.  It models the
runtime choice between waiting for native multimodal recall and allowing a
main worker to make progress while the same recall runs in the background.
Events carry references only; they never become canonical graph truth.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from collections import OrderedDict, deque
from collections.abc import AsyncIterable, Awaitable, Iterable, Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Protocol

from kogwistar.engine_core import MultimodalSpan

from .multimodal_dereference import EmbeddingReferenceDereferencer
from .multimodal_projection import (
    MultimodalEncoder,
    MultimodalImageQueryEncoder,
    MultimodalProjectionStore,
    MultimodalSearchHit,
    MultimodalSourceUnit,
)


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
    reference_resolution_ms: float
    total_ms: float


@dataclass(frozen=True, slots=True)
class SynchronousMultimodalRecall:
    hits: tuple[MultimodalSearchHit, ...]
    timing: MultimodalRecallTiming


@dataclass(frozen=True, slots=True)
class EvidenceCandidate:
    """A projection hit with identity verified by the host retrieval adapter."""

    hit: MultimodalSearchHit
    workspace_id: str
    profile_fingerprint: str


@dataclass(frozen=True, slots=True)
class RetrievalBatch:
    candidates: tuple[EvidenceCandidate, ...]
    query_embedding_ms: float
    vector_search_ms: float
    reference_resolution_ms: float = 0.0


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
    emitted_monotonic: float
    embedding_reference_id: str | None = None
    dereference_status: str = "unresolved"
    multimodal_span: MultimodalSpan | None = None
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


RetrieveResult = (
    RetrievalBatch | Iterable[EvidenceCandidate] | AsyncIterable[EvidenceCandidate]
)


class MultimodalPipelineLike(Protocol):
    """Minimal host pipeline surface required by the retrieval adapter."""

    @property
    def multimodal_encoder(self) -> MultimodalEncoder | None: ...

    @property
    def multimodal_projection_store(self) -> MultimodalProjectionStore | None: ...
class Retrieve(Protocol):
    """Retrieve bounded evidence synchronously or asynchronously."""

    def __call__(
        self,
        query: str,
        source_scope: frozenset[str] | None,
        /,
    ) -> RetrieveResult | Awaitable[RetrieveResult]: ...


class HitValidator(Protocol):
    """Validate one candidate before it becomes a recall result."""

    def __call__(self, candidate: EvidenceCandidate, /) -> None: ...


class SourceAuthorizer(Protocol):
    """Authorize access to one resolved multimodal source unit."""

    def __call__(self, source: MultimodalSourceUnit, /) -> None: ...


def _clock_ms() -> float:
    return time.perf_counter() * 1000.0


def _bounded_reference_fields(
    values: Mapping[str, object], *, allowed: frozenset[str], max_bytes: int = 2048
) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in values.items():
        if key not in allowed or not isinstance(key, str):
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            if isinstance(value, str) and len(value) > 512:
                continue
            result[key] = value
        if len(json.dumps(result, separators=(",", ":")).encode("utf-8")) > max_bytes:
            result.pop(key, None)
            break
    return result


async def synchronous_multimodal_recall(
    retrieve: Retrieve,
    query: str,
    *,
    workspace_id: str,
    profile_fingerprint: str,
    source_scope: frozenset[str] | None = None,
    validate_hit: HitValidator,
) -> SynchronousMultimodalRecall:
    """Run the native multimodal recall path and wait for all results."""

    started = _clock_ms()
    raw_result = await _retrieve_result(retrieve, query, source_scope)
    candidates: list[EvidenceCandidate] = []
    query_embedding_ms = 0.0
    vector_search_ms = 0.0
    reference_resolution_ms = 0.0
    if isinstance(raw_result, RetrievalBatch):
        query_embedding_ms = raw_result.query_embedding_ms
        vector_search_ms = raw_result.vector_search_ms
        reference_resolution_ms = raw_result.reference_resolution_ms
        source: Iterable[EvidenceCandidate] | AsyncIterable[EvidenceCandidate] = (
            raw_result.candidates
        )
    else:
        source = raw_result
    if isinstance(source, AsyncIterable):
        async for candidate in source:
            _validate_candidate(
                candidate,
                workspace_id=workspace_id,
                profile_fingerprint=profile_fingerprint,
                validator=validate_hit,
            )
            candidates.append(candidate)
    else:
        for candidate in source:
            _validate_candidate(
                candidate,
                workspace_id=workspace_id,
                profile_fingerprint=profile_fingerprint,
                validator=validate_hit,
            )
            candidates.append(candidate)
    finished = _clock_ms()
    return SynchronousMultimodalRecall(
        hits=tuple(item.hit for item in candidates),
        timing=MultimodalRecallTiming(
            query_embedding_ms=query_embedding_ms,
            vector_search_ms=vector_search_ms,
            reference_resolution_ms=reference_resolution_ms,
            total_ms=finished - started,
        ),
    )


async def _retrieve_result(
    retrieve: Retrieve, query: str, scope: frozenset[str] | None
) -> RetrieveResult:
    """Keep synchronous model/store calls off the main worker's event loop."""

    if inspect.iscoroutinefunction(retrieve):
        result = await retrieve(query, scope)
    else:
        result = await asyncio.to_thread(retrieve, query, scope)
    if inspect.isawaitable(result):
        result = await result
    if isinstance(result, (RetrievalBatch, AsyncIterable)):
        return result
    return await asyncio.to_thread(tuple, result)


async def _await_retrieval(
    retrieve: Retrieve,
    query: str,
    scope: frozenset[str] | None,
    timeout_seconds: float | None,
) -> RetrieveResult:
    operation = _retrieve_result(retrieve, query, scope)
    if timeout_seconds is None:
        return await operation
    return await asyncio.wait_for(operation, timeout=timeout_seconds)


def _validate_candidate(
    candidate: EvidenceCandidate,
    *,
    workspace_id: str,
    profile_fingerprint: str,
    validator: HitValidator,
) -> None:
    if candidate.workspace_id != workspace_id:
        raise PermissionError("retrieval candidate belongs to a different workspace")
    if candidate.profile_fingerprint != profile_fingerprint:
        raise ValueError("retrieval candidate belongs to a different embedding profile")
    validator(candidate)


def pipeline_multimodal_retriever(
    pipeline: MultimodalPipelineLike,
    *,
    workspace_id: str,
    authorize_source: SourceAuthorizer,
    limit: int = 10,
    overfetch_factor: int = 4,
    max_candidate_scan: int = 4096,
    image_query: object | None = None,
    dereferencer: EmbeddingReferenceDereferencer | None = None,
    allowed_namespaces: Iterable[str] | None = None,
) -> Retrieve:
    """Adapt the existing pipeline encoder/store into a scoped sync recall.

    The adapter resolves every hit back to the projection's source unit and
    rejects workspace, revision, source-scope, or authorization mismatches.
    Authorization remains a required host callback.
    """

    if (
        not workspace_id
        or limit <= 0
        or overfetch_factor <= 0
        or max_candidate_scan < limit
    ):
        raise ValueError(
            "workspace_id, result limit, overfetch factor, and candidate scan bound "
            "must be valid"
        )
    encoder = pipeline.multimodal_encoder
    store = pipeline.multimodal_projection_store
    if encoder is None or store is None:
        raise RuntimeError(
            "pipeline multimodal encoder and projection store are required"
        )
    if not isinstance(store, MultimodalProjectionStore):
        raise TypeError(
            "pipeline projection store does not implement the multimodal store contract"
        )
    namespaces = frozenset(str(item) for item in (allowed_namespaces or (workspace_id,)))
    if workspace_id not in namespaces:
        raise ValueError("workspace_id must be included in allowed_namespaces")

    def retrieve(query: str, source_scope: frozenset[str] | None) -> RetrievalBatch:
        started = _clock_ms()
        if image_query is None:
            query_vectors = encoder.encode_queries([query])
        else:
            if not isinstance(encoder, MultimodalImageQueryEncoder):
                raise TypeError(
                    "configured multimodal encoder does not support image queries"
                )
            query_vectors = encoder.encode_image_queries([image_query])
        embedded_at = _clock_ms()
        if len(query_vectors) != 1:
            raise ValueError(
                "multimodal query encoder returned an invalid result count"
            )
        # ACL filtering happens after vector ranking because the projection
        # store must remain independent of application authorization. Use a
        # bounded safety overfetch so a dense unauthorized prefix cannot
        # starve the authorized result set.
        fetch_limit = max_candidate_scan
        hits = store.search(
            query_vectors[0],
            profile=encoder.profile,
            limit=fetch_limit,
            workspace_id=workspace_id,
        )
        searched_at = _clock_ms()
        candidates: list[EvidenceCandidate] = []
        for hit in hits:
            unit = store.get(
                hit.view_id,
                profile=encoder.profile,
                workspace_id=workspace_id,
            )
            if unit is None:
                continue
            if (
                unit.workspace_id != workspace_id
                or hit.source_id != unit.source_id
                or hit.source_revision_id != unit.source_revision_id
            ):
                continue
            source_namespace = unit.source_namespace or unit.workspace_id
            if source_namespace not in namespaces:
                continue
            if (
                hit.multimodal_span is not None
                and hit.multimodal_span.source_namespace != source_namespace
            ):
                continue
            if source_scope is not None and unit.source_id not in source_scope:
                continue
            try:
                authorize_source(unit)
            except PermissionError:
                # Retrieval is overfetched specifically so an unauthorized
                # hit does not abort the whole authorized result set.
                continue
            if unit.embedding_reference is not None:
                if unit.embedding_reference.profile_fingerprint != encoder.profile.fingerprint:
                    continue
                if dereferencer is None:
                    hit = replace(hit, dereference_status="unresolved")
                else:
                    result = dereferencer.resolve(
                        unit.embedding_reference,
                        workspace_id=workspace_id,
                        allowed_namespaces=tuple(namespaces),
                    )
                    if result.status == "unauthorized":
                        # Do not leak a hit whose source map or target is not
                        # visible to this request, even as a ranked result.
                        continue
                    hit = replace(hit, dereference_status=result.status)
            else:
                hit = replace(hit, dereference_status="unresolved")
            candidates.append(
                EvidenceCandidate(
                    hit=hit,
                    workspace_id=unit.workspace_id,
                    profile_fingerprint=encoder.profile.fingerprint,
                )
            )
            if len(candidates) >= limit:
                break
        resolved_at = _clock_ms()
        return RetrievalBatch(
            candidates=tuple(candidates),
            query_embedding_ms=embedded_at - started,
            vector_search_ms=searched_at - embedded_at,
            reference_resolution_ms=resolved_at - searched_at,
        )

    return retrieve


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
        validate_hit: HitValidator,
        revalidate_hit: HitValidator | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        if (
            not run_id
            or not subscription_id
            or not workspace_id
            or not profile_fingerprint
        ):
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
        self._revalidate_hit = revalidate_hit or validate_hit
        self._timeout_seconds = timeout_seconds
        self._state = FeedState.CREATED
        self._epoch = 0
        self._next_seq = 0
        self._queue: deque[tuple[EvidenceEvent, EvidenceCandidate]] = deque()
        self._updated = asyncio.Event()
        self._seen: OrderedDict[tuple[str, str, str, str], None] = OrderedDict()
        self._task: asyncio.Task[None] | None = None
        self._scope: frozenset[str] | None = None
        self._refine_requested = False
        self._dropped = 0
        self._error: Exception | None = None
        self._query_count = 0
        self._emitted_count = 0
        self._stale_epoch_dropped = 0

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

    @property
    def query_count(self) -> int:
        return self._query_count

    @property
    def emitted_count(self) -> int:
        return self._emitted_count

    @property
    def stale_epoch_dropped_count(self) -> int:
        return self._stale_epoch_dropped

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
            self._updated.set()

    def cancel(self) -> None:
        """Close admission and best-effort cancel the provider task."""

        if self._state in {FeedState.CREATED, FeedState.RUNNING}:
            self._state = FeedState.CANCELLED
            self._updated.set()
            if self._task is not None:
                self._task.cancel()

    async def wait(self) -> None:
        if self._task is not None:
            try:
                await self._task
            except asyncio.CancelledError:
                if self._state is not FeedState.CANCELLED:
                    raise

    async def drain(
        self,
        *,
        limit: int | None = None,
        allow_stale_epochs: bool = False,
    ) -> tuple[EvidenceEvent, ...]:
        """Consume evidence only after current authorization is rechecked.

        A refinement creates a new retrieval epoch.  Results from older epochs
        are discarded by default, unless a caller explicitly opts into late
        evidence.  Both epoch policy and authorization are enforced at this
        assimilation boundary, not only when the sidecar emits a hit.
        """

        if limit is not None and limit < 0:
            raise ValueError("limit cannot be negative")
        items: list[EvidenceEvent] = []
        while self._queue and (limit is None or len(items) < limit):
            event, candidate = self._queue.popleft()
            if not allow_stale_epochs and event.origin_epoch != self._epoch:
                self._stale_epoch_dropped += 1
                self._dropped += 1
                continue
            try:
                self._revalidate_hit(candidate)
            except (PermissionError, ValueError):
                self._dropped += 1
                continue
            items.append(event)
        if not self._queue:
            self._updated.clear()
        return tuple(items)

    async def wait_for_update(self, *, timeout: float | None = None) -> bool:
        """Wait until evidence or a terminal state is available.

        Returns ``True`` when evidence is queued, otherwise ``False`` on
        timeout or terminal completion. Callers still use ``drain`` as the
        explicit assimilation boundary.
        """

        if self._queue:
            return True
        if self._state is not FeedState.RUNNING:
            return False
        self._updated.clear()
        if self._queue or self._state is not FeedState.RUNNING:
            return bool(self._queue)
        try:
            if timeout is None:
                await self._updated.wait()
            else:
                await asyncio.wait_for(self._updated.wait(), timeout=timeout)
        except TimeoutError:
            return False
        return bool(self._queue)

    async def _produce(self) -> None:
        try:
            while self._state is FeedState.RUNNING:
                deadline = (
                    asyncio.get_running_loop().time() + self._timeout_seconds
                    if self._timeout_seconds is not None
                    else None
                )
                origin_epoch = self._epoch
                source_scope = self._scope
                self._refine_requested = False
                self._query_count += 1
                result = await _await_retrieval(
                    self._retrieve,
                    self._query,
                    source_scope,
                    self._timeout_seconds,
                )
                if isinstance(result, RetrievalBatch):
                    candidates: (
                        Iterable[EvidenceCandidate] | AsyncIterable[EvidenceCandidate]
                    ) = result.candidates
                else:
                    candidates = result
                if isinstance(candidates, AsyncIterable):
                    remaining = (
                        max(0.0, deadline - asyncio.get_running_loop().time())
                        if deadline is not None
                        else None
                    )
                    if remaining is None:
                        async for candidate in candidates:
                            self._accept(candidate, origin_epoch=origin_epoch)
                    else:
                        async with asyncio.timeout(remaining):
                            async for candidate in candidates:
                                self._accept(candidate, origin_epoch=origin_epoch)
                else:
                    for candidate in candidates:
                        self._accept(candidate, origin_epoch=origin_epoch)
                if not self._refine_requested and self._state is FeedState.RUNNING:
                    self._state = FeedState.FINISHED
                    self._updated.set()
        except asyncio.CancelledError:
            if self._state is not FeedState.CANCELLED:
                raise
        except Exception as exc:  # noqa: BLE001 - provider exceptions are external boundary failures
            # A failed sidecar must not fail the main worker.  Its terminal
            # state is observable and the caller can report the provider error.
            self._error = exc
            self._state = FeedState.FINISHED
            self._updated.set()
            return

    def _accept(self, candidate: EvidenceCandidate, *, origin_epoch: int) -> None:
        if self._state is not FeedState.RUNNING:
            self._dropped += 1
            return
        try:
            _validate_candidate(
                candidate,
                workspace_id=self.workspace_id,
                profile_fingerprint=self.profile_fingerprint,
                validator=self._validate_hit,
            )
        except (PermissionError, ValueError):
            self._dropped += 1
            return
        self._emit(candidate.hit, origin_epoch=origin_epoch)

    def _emit(self, hit: MultimodalSearchHit, *, origin_epoch: int) -> None:
        key = (
            self.profile_fingerprint,
            hit.view_id,
            hit.source_id,
            hit.source_revision_id,
        )
        if key in self._seen:
            self._dropped += 1
            return
        if len(self._queue) >= self._max_events:
            weakest_index = min(
                range(len(self._queue)),
                key=lambda index: self._queue[index][0].retrieval_score,
            )
            if hit.score <= self._queue[weakest_index][0].retrieval_score:
                self._dropped += 1
                return
            del self._queue[weakest_index]
            self._dropped += 1
        self._seen[key] = None
        self._seen.move_to_end(key)
        while len(self._seen) > self._max_events * 4:
            self._seen.popitem(last=False)
        event = EvidenceEvent(
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
            emitted_monotonic=time.perf_counter(),
            embedding_reference_id=hit.embedding_reference_id,
            dereference_status=hit.dereference_status,
            multimodal_span=hit.multimodal_span,
            locator=_bounded_reference_fields(
                hit.locator,
                allowed=frozenset(
                    {
                        "kind",
                        "start_char",
                        "end_char",
                        "page_number",
                        "x",
                        "y",
                        "width",
                        "height",
                        "coordinate_system",
                        "frame_index",
                        "timestamp_ms",
                        "start_ms",
                        "end_ms",
                        "track_manifest_ref",
                        "track_manifest_sha256",
                        "source_map_ref",
                    }
                ),
            ),
            metadata=_bounded_reference_fields(
                hit.metadata,
                allowed=frozenset(
                    {
                        "embedding_reference_id",
                        "dereference_status",
                        "source_map_ref",
                        "evidence_key",
                        "namespace",
                        "role",
                        "fixture_only",
                    }
                ),
            ),
        )
        event_payload = {
            "run_id": event.run_id,
            "subscription_id": event.subscription_id,
            "workspace_id": event.workspace_id,
            "view_id": event.view_id,
            "source_id": event.source_id,
            "source_revision_id": event.source_revision_id,
            "profile_fingerprint": event.profile_fingerprint,
            "embedding_reference_id": event.embedding_reference_id,
            "locator": event.locator,
            "metadata": event.metadata,
            "multimodal_span": (
                event.multimodal_span.model_dump(mode="json")
                if event.multimodal_span is not None
                else None
            ),
        }
        if len(json.dumps(event_payload, separators=(",", ":")).encode("utf-8")) > 8192:
            self._dropped += 1
            return
        self._queue.append((event, EvidenceCandidate(
            hit=hit,
            workspace_id=self.workspace_id,
            profile_fingerprint=self.profile_fingerprint,
        )))
        self._next_seq += 1
        self._emitted_count += 1
        self._updated.set()


class MultimodalRetrievalSidecar:
    """Small application-local sidecar factory for the experiment."""

    def __init__(
        self,
        retrieve: Retrieve,
        *,
        max_events: int = 32,
        validate_hit: HitValidator | None = None,
        revalidate_hit: HitValidator | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self._retrieve = retrieve
        self._max_events = max_events
        self._validate_hit = validate_hit
        self._revalidate_hit = revalidate_hit
        self._timeout_seconds = timeout_seconds

    def start(
        self,
        *,
        run_id: str,
        subscription_id: str,
        workspace_id: str,
        profile_fingerprint: str,
        query: str,
        validate_hit: HitValidator | None = None,
        revalidate_hit: HitValidator | None = None,
    ) -> EvidenceSubscription:
        host_validator = validate_hit or self._validate_hit
        if host_validator is None:
            raise ValueError(
                "a host hit validator is required for every sidecar subscription"
            )
        subscription = EvidenceSubscription(
            run_id=run_id,
            subscription_id=subscription_id,
            workspace_id=workspace_id,
            profile_fingerprint=profile_fingerprint,
            retrieve=self._retrieve,
            query=query,
            max_events=self._max_events,
            validate_hit=host_validator,
            revalidate_hit=revalidate_hit or self._revalidate_hit or host_validator,
            timeout_seconds=self._timeout_seconds,
        )
        subscription.start()
        return subscription
