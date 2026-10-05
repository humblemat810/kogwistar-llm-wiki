"""Compare synchronous multimodal recall with a concurrent retrieval sidecar.

The fixture uses hand-authored vectors in the real in-memory projection store.
Only the retrieval delay is injected; fixture graph/text work is performed
directly and is identical in both modes. This is orchestration evidence, not a
real-encoder latency or semantic-quality benchmark.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import statistics
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar, cast

from kogwistar.engine_core import GraphKnowledgeEngine
from kogwistar.engine_core.in_memory_backend import build_in_memory_backend
from kogwistar.engine_core.models import Edge, Grounding, Node, Span

from kogwistar_llm_wiki.embeddings.multimodal_projection import (
    InMemoryMultimodalProjectionStore,
    MultimodalEmbeddingProfile,
    MultimodalSourceUnit,
)
from kogwistar_llm_wiki.embeddings.retrieval_experiment import (
    EvidenceCandidate,
    EvidenceEvent,
    MultimodalRetrievalSidecar,
    RetrievalBatch,
    Retrieve,
    pipeline_multimodal_retriever,
    synchronous_multimodal_recall,
)

FIXTURE_ROOT = (
    Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "multimodal_retrieval"
)
WORKSPACE_ID = "fixture-workspace"


@dataclass(frozen=True, slots=True)
class FastPathResult:
    text_hits: tuple[str, ...]
    graph_refs: tuple[str, ...]
    conclusion: str
    elapsed_ms: float
    step_completion_ms: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class ModeResult:
    mode: str
    total_ms: float
    fast_path_ms: float
    time_to_first_text_graph_evidence_ms: float
    time_to_first_multimodal_ms: float | None
    overlapped_work_ms: float
    main_steps_before_multimodal: int
    expected_target_retrieved: bool
    expected_target_rank: int | None
    recall_at_k: float
    initial_conclusion: str
    final_conclusion: str
    late_correction: bool
    emitted_events: int
    admitted_events: int
    dropped_events: int
    payload_bytes: int
    query_embedding_ms: float
    vector_search_ms: float
    reference_resolution_ms: float


class FixtureSharedSpaceEncoder:
    """Small deterministic encoder for exercising the shared-space contract."""

    _documents: ClassVar[dict[str, tuple[tuple[float, ...], ...]]] = {
        "text-prototype": ((0.75, 0.66, 0.0),),
        "architecture-image": ((1.0, 0.0, 0.0),),
        "benchmark-image": ((0.0, 1.0, 0.0),),
        "provenance-memo": ((0.0, 0.0, 1.0),),
        "distractor-image": ((-1.0, 0.0, 0.0),),
    }
    _queries: ClassVar[dict[str, tuple[tuple[float, ...], ...]]] = {
        "architecture_parallel": ((1.0, 0.0, 0.0),),
        "bottleneck_visual": ((0.0, 1.0, 0.0),),
        "image_to_related_text": ((0.75, 0.66, 0.0),),
    }

    def __init__(self, profile: MultimodalEmbeddingProfile) -> None:
        self.profile = profile

    def encode_documents(self, units, *, batch_size=None, resolver=None):
        del batch_size, resolver
        return [self._documents[unit.view_id] for unit in units]

    def encode_queries(self, queries, *, batch_size=None):
        del batch_size
        return [self._queries[self._scenario(query)] for query in queries]

    def encode_image_queries(self, images, *, batch_size=None):
        del batch_size
        return [self._queries[str(image)] for image in images]

    @classmethod
    def _scenario(cls, query: str) -> str:
        return next(key for key in cls._queries if key in query)


class FixtureProjection:
    """Frozen source units, graph links, and profile-scoped vector projection."""

    def __init__(self) -> None:
        self.manifest = json.loads(
            (FIXTURE_ROOT / "manifest.json").read_text(encoding="utf-8")
        )
        self.profile = MultimodalEmbeddingProfile(
            provider="fixture",
            model="hand-authored-ranking-vectors-v1",
            embedding="dense",
            dimension=3,
            metric="cosine",
            model_revision="fixture-v1",
        )
        self.store = InMemoryMultimodalProjectionStore(
            scope=f"{WORKSPACE_ID}:retrieval-experiment", profile=self.profile
        )
        self.units: dict[str, MultimodalSourceUnit] = {}
        self._graph_directory = tempfile.TemporaryDirectory(
            prefix="multimodal-retrieval-"
        )
        self.graph = GraphKnowledgeEngine(
            persist_directory=self._graph_directory.name,
            kg_graph_type="knowledge",
            embedding_function=FixtureGraphEmbeddingFunction(),
            backend_factory=build_in_memory_backend,
            namespace="multimodal-retrieval-fixture",
        )
        self.encoder = FixtureSharedSpaceEncoder(self.profile)
        for raw in self.manifest["units"]:
            unit_id = str(raw["id"])
            source_bytes = (FIXTURE_ROOT / str(raw["path"])).read_bytes()
            content = source_bytes.decode("utf-8")
            is_text = raw["modality"] == "text"
            unit = MultimodalSourceUnit(
                view_id=unit_id,
                workspace_id=WORKSPACE_ID,
                source_id=f"fixture-source-{unit_id}",
                source_revision_id="fixture-revision-1",
                modality="text" if is_text else "image",
                locator=(
                    {"kind": "text_span", "start_char": 0, "end_char": len(content)}
                    if is_text
                    else {"kind": "whole_image", "fixture_path": raw["path"]}
                ),
                text=content if is_text else None,
                content_ref=None if is_text else str(FIXTURE_ROOT / str(raw["path"])),
                asset_sha256=hashlib.sha256(source_bytes).hexdigest(),
                metadata={"fixture_only": True, "role": raw["role"]},
            )
            self.units[unit_id] = unit
            self.store.capture(unit)
            self.store.upsert_embedding(
                unit, self.encoder.encode_documents((unit,))[0], profile=self.profile
            )
            span = Span(
                collection_page_url=f"fixture://collection/{unit_id}",
                document_page_url=f"fixture://document/{unit_id}",
                doc_id=f"fixture-document-{unit_id}",
                insertion_method="manual",
                page_number=1,
                start_char=0,
                end_char=max(1, min(len(content), 160)),
                excerpt=content[:160] or unit_id,
                context_before="",
                context_after=content[160:240],
            )
            self.graph.write.add_node(
                Node(
                    id=unit_id,
                    label=str(raw["role"]),
                    type="entity",
                    summary=str(raw["role"]),
                    doc_id=f"fixture-document-{unit_id}",
                    mentions=[Grounding(spans=[span])],
                    metadata={"fixture_only": True, "workspace_id": WORKSPACE_ID},
                )
            )
        edge_span = Span(
            collection_page_url="fixture://collection/text-prototype",
            document_page_url="fixture://document/text-prototype",
            doc_id="fixture-document-text-prototype",
            insertion_method="manual",
            page_number=1,
            start_char=0,
            end_char=29,
            excerpt="The first prototype performs",
            context_before="",
            context_after=" synchronous multimodal lookup",
        )
        self.graph.write.add_edge(
            Edge(
                id="fixture-edge-prototype-provenance",
                label="prototype provenance context",
                type="relationship",
                summary="Prototype is connected to its provenance memo",
                source_ids=["text-prototype"],
                target_ids=["provenance-memo"],
                relation="has_provenance_context",
                source_edge_ids=None,
                target_edge_ids=None,
                doc_id="fixture-document-text-prototype",
                mentions=[Grounding(spans=[edge_span])],
            )
        )
        self.pipeline = SimpleNamespace(
            multimodal_projection_store=self.store,
            multimodal_encoder=self.encoder,
        )

    def authorize(self, unit: MultimodalSourceUnit) -> None:
        if unit.workspace_id != WORKSPACE_ID:
            raise PermissionError("source is outside the fixture workspace")

    def validate(self, candidate: EvidenceCandidate) -> None:
        unit = self.store.get(candidate.hit.view_id, profile=self.profile)
        if unit is None or candidate.workspace_id != WORKSPACE_ID:
            raise PermissionError(
                "candidate does not resolve in the authorized projection"
            )
        if candidate.profile_fingerprint != self.profile.fingerprint:
            raise ValueError("candidate embedding profile mismatch")
        if (candidate.hit.source_id, candidate.hit.source_revision_id) != (
            unit.source_id,
            unit.source_revision_id,
        ):
            raise ValueError("candidate source revision is stale")

    def fast_path(self, query: str, started: float) -> FastPathResult:
        """Run a small BM25-style text rank followed by bounded graph expansion."""

        tokens = _tokens(query)
        documents = {
            unit_id: _tokens(unit.text)
            for unit_id, unit in self.units.items()
            if unit.text is not None
        }
        avg_length = sum(map(len, documents.values())) / max(len(documents), 1)
        ranked: list[tuple[float, str]] = []
        for unit_id, unit in self.units.items():
            if unit.text is None:
                continue
            doc_tokens = documents[unit_id]
            score = 0.0
            for token in tokens:
                frequency = sum(token in terms for terms in documents.values())
                if not frequency or token not in doc_tokens:
                    continue
                idf = 1.0 + math.log(
                    1.0 + (len(documents) - frequency + 0.5) / (frequency + 0.5)
                )
                term_frequency = sum(
                    word == token for word in unit.text.lower().split()
                )
                norm = term_frequency + 1.2 * (
                    1.0 - 0.75 + 0.75 * len(doc_tokens) / max(avg_length, 1.0)
                )
                score += idf * term_frequency * 2.2 / norm
            if score:
                ranked.append((score, unit_id))
        ranked.sort(key=lambda pair: (-pair[0], pair[1]))
        text_hits = tuple(unit_id for _, unit_id in ranked[:3])
        text_done = _elapsed_ms(started)

        graph_edges = self.graph.read.get_edges(limit=100)
        graph_refs = tuple(
            target_id
            for edge in graph_edges
            if set(edge.source_ids).intersection(text_hits)
            for target_id in edge.target_ids
        )
        graph_done = _elapsed_ms(started)
        # The retrieved text says the first prototype waits for lookup; only
        # the grounded architecture image corrects this provisional conclusion.
        top_text = self.units[text_hits[0]].text if text_hits else None
        initial = (
            "synchronous design"
            if top_text
            and "synchronous multimodal lookup before continuing" in top_text
            else "undetermined"
        )
        conclusion_done = _elapsed_ms(started)
        return FastPathResult(
            text_hits=text_hits,
            graph_refs=graph_refs,
            conclusion=initial,
            elapsed_ms=conclusion_done,
            step_completion_ms=(text_done, graph_done, conclusion_done),
        )

    def retrieve(self, query_spec: dict[str, object], *, limit: int = 5) -> Retrieve:
        image_query = (
            str(query_spec["id"]) if query_spec.get("query_kind") == "image" else None
        )
        return pipeline_multimodal_retriever(
            self.pipeline,
            workspace_id=WORKSPACE_ID,
            authorize_source=self.authorize,
            limit=limit,
            image_query=image_query,
        )

    def close(self) -> None:
        self.graph.close()
        self._graph_directory.cleanup()


class FixtureGraphEmbeddingFunction:
    """Deterministic, separate CPU graph-index embedding for fixture nodes."""

    _name = "multimodal-retrieval-fixture-graph"

    def name(self) -> str:
        return self._name

    def __call__(self, values):
        return [
            [float(len(str(value)) + 1), float(sum(map(ord, str(value))) % 97 + 1)]
            for value in values
        ]


def _tokens(text: str) -> set[str]:
    return {
        token.strip(".,:;()[]{}!?\"'").lower()
        for token in text.split()
        if len(token.strip(".,:;()[]{}!?\"'")) > 2
    }


def _elapsed_ms(started: float) -> float:
    return (time.perf_counter() - started) * 1000.0


def _target_metrics(
    refs: list[str], expected: set[str]
) -> tuple[bool, int | None, float]:
    rank = next((index + 1 for index, ref in enumerate(refs) if ref in expected), None)
    return (
        bool(expected.intersection(refs)),
        rank,
        (len(expected.intersection(refs)) / len(expected) if expected else 1.0),
    )


def _payload_size(events: tuple[EvidenceEvent, ...]) -> int:
    return sum(
        len(
            json.dumps(
                {
                    "view_id": event.view_id,
                    "source_id": event.source_id,
                    "source_revision_id": event.source_revision_id,
                    "modality": event.modality,
                    "profile_fingerprint": event.profile_fingerprint,
                    "embedding_reference_id": event.embedding_reference_id,
                    "dereference_status": event.dereference_status,
                    "multimodal_span": (
                        event.multimodal_span.model_dump(mode="json")
                        if event.multimodal_span is not None
                        else None
                    ),
                    "locator": event.locator,
                    "metadata": event.metadata,
                },
                separators=(",", ":"),
            ).encode("utf-8")
        )
        for event in events
    )


def _hit_payload_size(hits, profile_fingerprint: str) -> int:
    return sum(
        len(
            json.dumps(
                {
                    "view_id": hit.view_id,
                    "source_id": hit.source_id,
                    "source_revision_id": hit.source_revision_id,
                    "modality": hit.modality,
                    "profile_fingerprint": profile_fingerprint,
                    "embedding_reference_id": hit.embedding_reference_id,
                    "dereference_status": hit.dereference_status,
                    "multimodal_span": (
                        hit.multimodal_span.model_dump(mode="json")
                        if hit.multimodal_span is not None
                        else None
                    ),
                    "locator": hit.locator,
                    "metadata": hit.metadata,
                },
                separators=(",", ":"),
            ).encode("utf-8")
        )
        for hit in hits
    )


def _delayed_retriever(
    retrieve: Retrieve, batches: list[RetrievalBatch], delay_ms: float
) -> Retrieve:
    """Bind one scenario's provider delay and timing collector."""

    def invoke(query: str, scope: frozenset[str] | None):
        time.sleep(delay_ms / 1000.0)
        result = retrieve(query, scope)
        if isinstance(result, RetrievalBatch):
            batches.append(result)
        return result

    return cast(Retrieve, invoke)


async def _run_scenario(
    projection: FixtureProjection,
    spec: dict[str, object],
    retrieve: Retrieve,
    batches: list[RetrievalBatch],
    *,
    sidecar_first: bool,
) -> tuple[ModeResult, ModeResult]:
    if sidecar_first:
        sidecar_result = await _run_sidecar_mode(projection, spec, retrieve, batches)
        sync_result = await _run_synchronous_mode(projection, spec, retrieve)
    else:
        sync_result = await _run_synchronous_mode(projection, spec, retrieve)
        sidecar_result = await _run_sidecar_mode(projection, spec, retrieve, batches)
    return sync_result, sidecar_result


async def _run_synchronous_mode(
    projection: FixtureProjection, spec: dict[str, object], retrieve: Retrieve
) -> ModeResult:
    query_id = str(spec["id"])
    query = f"{query_id}: {spec['query']}"
    expected = set(cast(list[str], spec["expected_top_refs"]))
    sync_started = time.perf_counter()
    sync_recall = await synchronous_multimodal_recall(
        retrieve,
        query,
        workspace_id=WORKSPACE_ID,
        profile_fingerprint=projection.profile.fingerprint,
        validate_hit=projection.validate,
    )
    sync_mm_ms = _elapsed_ms(sync_started)
    sync_fast_started = time.perf_counter()
    sync_fast = projection.fast_path(query, sync_fast_started)
    sync_refs = [hit.view_id for hit in sync_recall.hits]
    sync_found, sync_rank, sync_recall_at_k = _target_metrics(sync_refs, expected)
    sync_final = (
        "parallel design"
        if query_id == "architecture_parallel" and sync_found
        else sync_fast.conclusion
    )
    sync_total = _elapsed_ms(sync_started)
    return ModeResult(
        mode="synchronous_baseline",
        total_ms=sync_total,
        fast_path_ms=_elapsed_ms(sync_fast_started),
        time_to_first_text_graph_evidence_ms=sync_mm_ms
        + sync_fast.step_completion_ms[0],
        time_to_first_multimodal_ms=sync_mm_ms,
        overlapped_work_ms=0.0,
        main_steps_before_multimodal=0,
        expected_target_retrieved=sync_found,
        expected_target_rank=sync_rank,
        recall_at_k=sync_recall_at_k,
        initial_conclusion="awaiting_multimodal_recall",
        final_conclusion=sync_final,
        late_correction=False,
        emitted_events=0,
        admitted_events=0,
        dropped_events=0,
        payload_bytes=_hit_payload_size(
            sync_recall.hits, projection.profile.fingerprint
        ),
        query_embedding_ms=sync_recall.timing.query_embedding_ms,
        vector_search_ms=sync_recall.timing.vector_search_ms,
        reference_resolution_ms=sync_recall.timing.reference_resolution_ms,
    )


async def _run_sidecar_mode(
    projection: FixtureProjection,
    spec: dict[str, object],
    retrieve: Retrieve,
    batches: list[RetrievalBatch],
) -> ModeResult:
    query_id = str(spec["id"])
    expected = set(cast(list[str], spec["expected_top_refs"]))
    async_started = time.perf_counter()
    async_query = f"{query_id}: {spec['query']}"
    sidecar = MultimodalRetrievalSidecar(
        retrieve,
        validate_hit=projection.validate,
        timeout_seconds=30.0,
    )
    sub = sidecar.start(
        run_id=f"run-{query_id}",
        subscription_id=f"subscription-{query_id}",
        workspace_id=WORKSPACE_ID,
        profile_fingerprint=projection.profile.fingerprint,
        query=async_query,
    )
    # Let the sidecar task enter its provider call before running synchronous
    # fixture work; otherwise create_task alone would defer it until a later
    # checkpoint and falsely claim overlap.
    await asyncio.sleep(0)
    initial_fast = projection.fast_path(async_query, async_started)
    conclusion = initial_fast.conclusion
    assimilated: list[EvidenceEvent] = []
    step_counts_before_mm = 0
    first_mm_ms: float | None = None
    for step_ms in initial_fast.step_completion_ms:
        # Yield to the offloaded retrieval, then assimilate only at this safe
        # checkpoint; no polling loop can starve either task.
        await asyncio.sleep(0)
        fresh = await sub.drain()
        assimilated.extend(fresh)
        if fresh and first_mm_ms is None:
            first_mm_ms = (fresh[0].emitted_monotonic - async_started) * 1000.0
        if first_mm_ms is None or step_ms < first_mm_ms:
            step_counts_before_mm += 1
        if query_id == "architecture_parallel" and any(
            event.view_id == "architecture-image" for event in fresh
        ):
            conclusion = "parallel design"

    # If retrieval is still outstanding after fast-path completion, await its
    # update once and assimilate before finalizing, without periodic polling.
    if sub.state.value == "running":
        await sub.wait_for_update(timeout=30.0)
    late = await sub.drain()
    assimilated.extend(late)
    if late and first_mm_ms is None:
        first_mm_ms = (late[0].emitted_monotonic - async_started) * 1000.0
    if query_id == "architecture_parallel" and any(
        event.view_id == "architecture-image" for event in late
    ):
        conclusion = "parallel design"
    await sub.wait()
    final = await sub.drain()
    assimilated.extend(final)
    if query_id == "architecture_parallel" and any(
        event.view_id == "architecture-image" for event in final
    ):
        conclusion = "parallel design"

    async_ids = [event.view_id for event in assimilated]
    async_found, async_rank, async_recall_at_k = _target_metrics(async_ids, expected)
    # Batch timings are retained by the callback for attribution to encoder,
    # index query, and source resolution separately.
    batch = batches[-1] if batches else None
    async_elapsed = _elapsed_ms(async_started)
    return ModeResult(
        mode="asynchronous_memory_sidecar",
        total_ms=async_elapsed,
        fast_path_ms=initial_fast.elapsed_ms,
        time_to_first_text_graph_evidence_ms=initial_fast.step_completion_ms[0],
        time_to_first_multimodal_ms=first_mm_ms,
        overlapped_work_ms=min(initial_fast.elapsed_ms, first_mm_ms or 0.0),
        main_steps_before_multimodal=step_counts_before_mm,
        expected_target_retrieved=async_found,
        expected_target_rank=async_rank,
        recall_at_k=async_recall_at_k,
        initial_conclusion=initial_fast.conclusion,
        final_conclusion=conclusion,
        late_correction=(
            query_id == "architecture_parallel"
            and initial_fast.conclusion != conclusion
            and conclusion == "parallel design"
        ),
        emitted_events=sub.emitted_count,
        admitted_events=len(assimilated),
        dropped_events=sub.dropped_count,
        payload_bytes=_payload_size(tuple(assimilated)),
        query_embedding_ms=batch.query_embedding_ms if batch else 0.0,
        vector_search_ms=batch.vector_search_ms if batch else 0.0,
        reference_resolution_ms=batch.reference_resolution_ms if batch else 0.0,
    )


async def run_experiment(
    *, delay_ms: float = 80.0, repeats: int = 5
) -> dict[str, object]:
    if delay_ms < 0 or repeats <= 0:
        raise ValueError("delay_ms must be non-negative and repeats must be positive")
    projection = FixtureProjection()
    try:
        return await _collect_results(projection, delay_ms=delay_ms, repeats=repeats)
    finally:
        projection.close()


async def _collect_results(
    projection: FixtureProjection, *, delay_ms: float, repeats: int
) -> dict[str, object]:
    scenarios: list[dict[str, object]] = []
    for spec in projection.manifest["queries"]:
        projection.fast_path(
            f"{spec['id']}: {spec['query']}", time.perf_counter()
        )  # warm the graph read path before paired measurements
        paired: list[tuple[ModeResult, ModeResult]] = []
        for repeat_index in range(repeats):
            batches: list[RetrievalBatch] = []
            retrieve = _delayed_retriever(projection.retrieve(spec), batches, delay_ms)
            paired.append(
                await _run_scenario(
                    projection,
                    spec,
                    retrieve,
                    batches,
                    sidecar_first=repeat_index % 2 == 1,
                )
            )
        result_rows: list[dict[str, object]] = []
        for index, mode in enumerate(
            ("synchronous_baseline", "asynchronous_memory_sidecar")
        ):
            values = [pair[index] for pair in paired]
            fields: dict[str, object] = asdict(values[0])
            for name in (
                "total_ms",
                "fast_path_ms",
                "time_to_first_text_graph_evidence_ms",
                "overlapped_work_ms",
                "payload_bytes",
                "query_embedding_ms",
                "vector_search_ms",
                "reference_resolution_ms",
            ):
                fields[name] = statistics.median(
                    getattr(value, name) for value in values
                )
            non_null_times = [
                value.time_to_first_multimodal_ms
                for value in values
                if value.time_to_first_multimodal_ms is not None
            ]
            fields["time_to_first_multimodal_ms"] = (
                statistics.median(non_null_times) if non_null_times else None
            )
            result_rows.append(fields)
        scenarios.append(
            {
                "query_id": spec["id"],
                "expected_top_refs": spec["expected_top_refs"],
                "results": result_rows,
            }
        )
    return {
        "fixture_manifest": str(FIXTURE_ROOT / "manifest.json"),
        "backend": "in_memory_multimodal_projection_with_hand_authored_vectors",
        "quality_evidence": {
            "status": "synthetic_fixture_only",
            "encoder": "hand-authored-ranking-vectors-v1",
            "semantic_quality_valid": False,
            "latency_valid": False,
        },
        "graph_backend": "Kogwistar GraphKnowledgeEngine with in-memory backend",
        "text_retrieval": "fixture BM25-style lexical rank",
        "profile_fingerprint": projection.profile.fingerprint,
        "retrieval_delay_ms": delay_ms,
        "repeats": repeats,
        "fast_path_warmup": True,
        "warning": "Injected retrieval delay and fixture vectors do not measure real Qwen latency or semantic quality.",
        "scenarios": scenarios,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--delay-ms", type=float, default=80.0)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = asyncio.run(run_experiment(delay_ms=args.delay_ms, repeats=args.repeats))
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
