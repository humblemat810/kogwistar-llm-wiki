"""Run one real-model, source-grounded cross-link lifecycle on disposable PostgreSQL.

This diagnostic uses the local Prism llama.cpp endpoint for both proposal and
critic calls, and local multilingual E5 for graph indexing. It creates and
drops only a unique PostgreSQL schema. The fixture is synthetic and must never
be pointed at a production database.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import tempfile
import threading
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import Any, Literal
from urllib.request import urlopen

from kogwistar.engine_core.async_compat import run_awaitable_blocking
from kogwistar.engine_core.models import Document, Grounding, Node, Span
from profile_maintenance_e2e import (
    LocalE5SmallEmbedding,
    _local_critic_provider_settings,
)
from sqlalchemy import create_engine, text

from kogwistar_llm_wiki.configuration.workspace import WorkspaceNamespaces
from kogwistar_llm_wiki.ingest_pipeline import build_postgres_namespace_engines
from kogwistar_llm_wiki.maintenance.crosslink_proposals import CrosslinkReviewDecision
from kogwistar_llm_wiki.maintenance.crosslink_reviews import CrosslinkGroupReviewService
from kogwistar_llm_wiki.maintenance.maintenance_guards import (
    readiness_id,
    source_digest,
)
from kogwistar_llm_wiki.parsing.parse_generation_store import ParseGenerationStore
from kogwistar_llm_wiki.parsing.parse_views import (
    ParseGeneration,
    ParseGenerationCommit,
    ParseGenerationMember,
    ParseGenerationStatus,
    ParseView,
    ParseViewSelection,
    ParseViewStore,
    SourceRegion,
    generation_member_id,
)
from kogwistar_llm_wiki.utils import _temporary_namespace
from kogwistar_llm_wiki.worker import MaintenanceWorker

SENTENCES = (
    "Training workload Alpha uses CUDA to train a neural network.\n\n"
    "CUDA is NVIDIA's platform for general-purpose GPU computing."
)


def _provider_runtime_label(provider_mode: Literal["live", "fixture-replay"]) -> str:
    if provider_mode == "live":
        return "custom Prism llama.cpp / Bonsai2"
    return "deterministic known-answer fixture replay; no LLM inference"


def _seed_source_and_parse_view(
    engines: Any, workspace_id: str
) -> tuple[list[str], str, str, str, str]:
    namespaces = WorkspaceNamespaces(workspace_id)
    logical_source_id = f"crosslink-source-{uuid.uuid4().hex[:8]}"
    revision_id = f"crosslink-revision-{uuid.uuid4().hex[:8]}"
    revision_document_id = f"crosslink-revision-document-{uuid.uuid4().hex[:8]}"
    digest = source_digest(SENTENCES)
    with _temporary_namespace(engines.kg, namespaces.source_space):
        engines.kg.write.add_document(
            Document(
                id=revision_document_id,
                content=SENTENCES,
                type="text",
                metadata={
                    "workspace_id": workspace_id,
                    "acl_mode": "public",
                    "logical_source_document_id": logical_source_id,
                    "source_revision_id": revision_id,
                    "revision_document_id": revision_document_id,
                    "source_digest": digest,
                },
            )
        )
        engines.kg.write.add_node(
            Node(
                id=revision_id,
                label="Synthetic source revision",
                type="entity",
                summary="Immutable source revision fence for the synthetic profile.",
                doc_id=revision_document_id,
                mentions=[
                    Grounding(
                        spans=[
                            Span(
                                collection_page_url="",
                                document_page_url="",
                                doc_id=revision_document_id,
                                start_char=0,
                                end_char=len(SENTENCES),
                                excerpt=SENTENCES,
                                insertion_method="crosslink-profile-fixture",
                            )
                        ]
                    )
                ],
                metadata={
                    "workspace_id": workspace_id,
                    "acl_mode": "public",
                    "artifact_kind": "source_revision",
                    "source_document_id": logical_source_id,
                    "source_revision_id": revision_id,
                    "source_digest": digest,
                    "revision_document_id": revision_document_id,
                    "revision_status": "current",
                },
            )
        )
        ready_id = readiness_id(
            source_document_id=logical_source_id,
            revision_id=revision_id,
            stage="parsed_graph_persisted",
        )
        engines.kg.write.add_node(
            Node(
                id=ready_id,
                label="Synthetic parsed source readiness",
                type="entity",
                summary="Synthetic source is ready for parsed graph maintenance.",
                doc_id=revision_document_id,
                mentions=[
                    Grounding(
                        spans=[
                            Span(
                                collection_page_url="",
                                document_page_url="",
                                doc_id=revision_document_id,
                                start_char=0,
                                end_char=len(SENTENCES),
                                excerpt=SENTENCES,
                                insertion_method="crosslink-profile-fixture",
                            )
                        ]
                    )
                ],
                metadata={
                    "workspace_id": workspace_id,
                    "acl_mode": "public",
                    "artifact_kind": "source_readiness",
                    "source_document_id": logical_source_id,
                    "source_revision_id": revision_id,
                    "source_digest": digest,
                    "revision_document_id": revision_document_id,
                    "readiness_stage": "parsed_graph_persisted",
                    "graph_status": "ready",
                },
            )
        )

    generation = ParseGeneration(
        generation_id=f"crosslink-generation-{uuid.uuid4().hex[:8]}",
        workspace_id=workspace_id,
        source_document_id=logical_source_id,
        source_revision_id=revision_id,
        source_digest=digest,
        revision_document_id=revision_document_id,
        parser_profile="profile-fixture",
        parser_version="profile-fixture-v1",
        status=ParseGenerationStatus.STABLE,
    )
    spans = [
        (0, len("Training workload Alpha uses CUDA to train a neural network.")),
        (SENTENCES.index("CUDA is NVIDIA"), len(SENTENCES)),
    ]
    member_ids = [
        generation_member_id(
            generation_id=generation.generation_id,
            commit_id="fixture-commit",
            ordinal=i,
        )
        for i in range(2)
    ]
    regions = [
        SourceRegion(
            source_document_id=revision_document_id, start_char=start, end_char=end
        )
        for start, end in spans
    ]
    members = [
        ParseGenerationMember(
            member_id=member_id,
            generation_id=generation.generation_id,
            workspace_id=workspace_id,
            source_document_id=logical_source_id,
            source_revision_id=revision_id,
            revision_document_id=revision_document_id,
            region=region,
            semantic_id=f"crosslink-concept-{index}",
            payload={"fixture": True},
        )
        for index, (member_id, region) in enumerate(
            zip(member_ids, regions, strict=True)
        )
    ]
    commit = ParseGenerationCommit(
        commit_id="fixture-commit",
        generation_id=generation.generation_id,
        workspace_id=workspace_id,
        source_document_id=logical_source_id,
        source_revision_id=revision_id,
        member_ids=tuple(member_ids),
    )
    generation_store = ParseGenerationStore(
        engines.conversation.meta_sqlite, workspace_id=workspace_id
    )
    generation_store.commit(generation, commit, members)
    view = ParseView(
        view_id=f"crosslink-view-{uuid.uuid4().hex[:8]}",
        view_version=1,
        workspace_id=workspace_id,
        source_document_id=logical_source_id,
        source_revision_id=revision_id,
        revision_document_id=revision_document_id,
        selections=tuple(
            ParseViewSelection(
                member_id=member.member_id,
                generation_id=generation.generation_id,
                region=member.region,
            )
            for member in members
        ),
    )
    ParseViewStore(
        engines.conversation.meta_sqlite, workspace_id=workspace_id
    ).activate(view, expected_view_version=None)

    labels = ("Training workload Alpha", "CUDA")
    node_ids: list[str] = []
    with _temporary_namespace(engines.kg, namespaces.curated_kg_space):
        for index, (label, (start, end), member_id) in enumerate(
            zip(labels, spans, member_ids, strict=True)
        ):
            node_id = f"ws:{workspace_id}:concept:{index}"
            node_ids.append(node_id)
            excerpt = SENTENCES[start:end]
            engines.kg.write.add_node(
                Node(
                    id=node_id,
                    label=label,
                    type="entity",
                    summary=excerpt,
                    doc_id=revision_document_id,
                    mentions=[
                        Grounding(
                            spans=[
                                Span(
                                    doc_id=revision_document_id,
                                    start_char=start,
                                    end_char=end,
                                    excerpt=excerpt,
                                    document_page_url="",
                                    collection_page_url="",
                                    insertion_method="crosslink-profile-fixture",
                                )
                            ]
                        )
                    ],
                    metadata={
                        "workspace_id": workspace_id,
                        "acl_mode": "public",
                        "source_document_id": logical_source_id,
                        "source_revision_id": revision_id,
                        "revision_document_id": revision_document_id,
                        "source_digest": digest,
                        "parse_generation_id": generation.generation_id,
                        "parse_generation_member_id": member_id,
                    },
                )
            )
    return node_ids, logical_source_id, revision_id, revision_document_id, digest


def _served_context_tokens(base_url: str) -> int:
    """Read Prism's loaded context size so benchmark metadata matches reality."""
    base = base_url.rstrip("/")
    models_url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
    with urlopen(models_url, timeout=10) as response:
        payload = json.load(response)
    models = payload.get("data")
    if not isinstance(models, list):
        raise TypeError("model endpoint response is missing the Prism data list")
    contexts = {
        int(meta["n_ctx"])
        for item in models
        if isinstance(item, dict)
        and isinstance((meta := item.get("meta")), dict)
        and isinstance(meta.get("n_ctx"), int)
        and not isinstance(meta.get("n_ctx"), bool)
    }
    if len(contexts) != 1:
        raise RuntimeError(
            "Prism /v1/models must report one unambiguous loaded meta.n_ctx value"
        )
    return contexts.pop()


def _measured_profile_audit(
    timings: list[dict[str, object]],
    label: str,
    callback: Any,
    *args: object,
) -> Any:
    """Run one diagnostic census and retain its wall-time separately."""
    started = time.perf_counter()
    value = callback(*args)
    timings.append(
        {"audit": label, "elapsed_ms": round((time.perf_counter() - started) * 1000, 3)}
    )
    return value


def _backend_entity_rows(
    engine: Any,
    entity_kind: Literal["node", "edge"],
    *,
    ids: list[str] | None,
    limit: int,
) -> tuple[list[str], list[dict[str, object]]]:
    """Read raw storage IDs/metadata for isolated synthetic integrity audits.

    This deliberately bypasses request-level ACL filtering to distinguish
    physical persistence from authorized visibility. Callers return aggregate
    counts only; this is not an application read path.
    """
    getter = getattr(engine.backend, f"{entity_kind}_get")
    result = run_awaitable_blocking(
        getter(ids=ids, include=["metadatas"], limit=limit)
    )
    if not isinstance(result, dict):
        raise TypeError("backend entity audit must return an object")
    raw_ids = result.get("ids")
    raw_metadata = result.get("metadatas")
    if not isinstance(raw_ids, list) or not isinstance(raw_metadata, list):
        raise TypeError("backend entity audit response is missing IDs or metadata")
    entity_ids = [str(value) for value in raw_ids]
    metadatas = [
        dict(value) if isinstance(value, dict) else {}
        for value in raw_metadata
    ]
    if len(entity_ids) != len(metadatas):
        raise ValueError("backend entity audit returned misaligned IDs and metadata")
    return entity_ids, metadatas


def _index_queue_summary(engine: Any, namespace: str) -> dict[str, object]:
    audit_started = time.perf_counter()
    jobs_started = audit_started
    rows = engine.meta_sqlite.list_index_jobs(namespace=namespace, limit=10_000)
    jobs_elapsed_ms = (time.perf_counter() - jobs_started) * 1000
    status_counts = Counter(str(getattr(row, "status", "unknown")) for row in rows)
    entity_job_counts: Counter[tuple[str, str]] = Counter()
    projection_job_counts: Counter[tuple[str, str, str]] = Counter()
    entity_ids_by_kind: dict[str, set[str]] = {}
    operation_counts = Counter(
        "::".join(
            str(getattr(row, field, "unknown"))
            for field in ("entity_kind", "index_kind", "op")
        )
        for row in rows
    )
    for row in rows:
        entity_kind = str(getattr(row, "entity_kind", "unknown"))
        entity_id = str(getattr(row, "entity_id", "unknown"))
        index_kind = str(getattr(row, "index_kind", "unknown"))
        entity_job_counts[(entity_kind, entity_id)] += 1
        projection_job_counts[(entity_kind, entity_id, index_kind)] += 1
        entity_ids_by_kind.setdefault(entity_kind, set()).add(entity_id)
    entities_by_kind: Counter[str] = Counter(kind for kind, _entity_id in entity_job_counts)
    projections_by_kind: Counter[str] = Counter(
        f"{kind}::{index_kind}"
        for kind, _entity_id, index_kind in projection_job_counts
    )
    per_entity_rows: dict[str, list[int]] = {}
    for (kind, _entity_id), count in entity_job_counts.items():
        per_entity_rows.setdefault(kind, []).append(count)
    source_presence: dict[str, dict[str, int]] = {}
    backend_present: dict[tuple[str, str], bool] = {}
    backend_lookup_started = time.perf_counter()
    with _temporary_namespace(engine, namespace):
        for kind, ids in sorted(entity_ids_by_kind.items()):
            if not ids or kind not in {"node", "edge"}:
                continue
            found_ids, found_metadata = _backend_entity_rows(
                engine,
                kind,
                ids=sorted(ids),
                limit=max(len(ids), 1),
            )
            states = Counter(
                "tombstoned"
                if str(metadata.get("lifecycle_status") or "active") == "tombstoned"
                else "active"
                for metadata in found_metadata
            )
            states["missing"] = max(0, len(ids) - len(found_ids))
            source_presence[kind] = dict(sorted(states.items()))
            for entity_id in ids & set(found_ids):
                backend_present[(kind, entity_id)] = True
    backend_lookup_elapsed_ms = (time.perf_counter() - backend_lookup_started) * 1000

    wanted_entities = set(entity_job_counts)
    latest_events_by_namespace: dict[
        str, dict[tuple[str, str], str]
    ] = {}
    event_scan_elapsed_ms_by_namespace: dict[str, float] = {}
    iter_events = getattr(engine.meta_sqlite, "iter_entity_events", None)
    if callable(iter_events) and wanted_entities:
        candidates = (
            ("requested", namespace),
            ("engine_base", str(getattr(engine, "namespace", ""))),
            ("default", "default"),
        )
        seen_namespaces: set[str] = set()
        for label, event_namespace in candidates:
            if not event_namespace or event_namespace in seen_namespaces:
                continue
            seen_namespaces.add(event_namespace)
            event_scan_started = time.perf_counter()
            latest_events: dict[tuple[str, str], str] = {}
            for event in iter_events(namespace=event_namespace, from_seq=1):
                if len(event) < 5:
                    continue
                entity_kind = str(event[1])
                entity_id = str(event[2])
                key = (entity_kind, entity_id)
                if key not in wanted_entities:
                    continue
                operation = str(event[3]).upper()
                payload = json.loads(str(event[4]))
                if not isinstance(payload, dict):
                    raise TypeError("canonical entity event payload must be an object")
                if operation in {"DELETE", "TOMBSTONE"}:
                    state = "deleted"
                elif operation in {"ADD", "REPLACE"}:
                    patch = payload.get("lifecycle_patch")
                    state = (
                        "deleted"
                        if isinstance(patch, dict)
                        and patch.get("lifecycle_status") == "tombstoned"
                        else "active"
                    )
                else:
                    state = "unknown"
                latest_events[key] = state
            latest_events_by_namespace[label] = latest_events
            event_scan_elapsed_ms_by_namespace[label] = round(
                (time.perf_counter() - event_scan_started) * 1000, 3
            )

    canonical_event_counts_by_namespace: dict[str, dict[str, dict[str, int]]] = {}
    active_event_missing_backend_by_namespace: dict[str, dict[str, int]] = {}
    for label, latest_events in latest_events_by_namespace.items():
        canonical_event_counts: dict[str, dict[str, int]] = {}
        active_event_missing_backend: Counter[str] = Counter()
        for kind, ids in sorted(entity_ids_by_kind.items()):
            counts = Counter(
                latest_events.get((kind, entity_id), "no_event") for entity_id in ids
            )
            canonical_event_counts[kind] = dict(sorted(counts.items()))
            active_event_missing_backend[kind] = sum(
                latest_events.get((kind, entity_id)) == "active"
                and not backend_present.get((kind, entity_id), False)
                for entity_id in ids
            )
        canonical_event_counts_by_namespace[label] = canonical_event_counts
        active_event_missing_backend_by_namespace[label] = dict(
            sorted(active_event_missing_backend.items())
        )
    requested_events = latest_events_by_namespace.get("requested", {})
    canonical_event_counts = {
        kind: dict(
            sorted(
                Counter(
                    requested_events.get((kind, entity_id), "no_event")
                    for entity_id in ids
                ).items()
            )
        )
        for kind, ids in sorted(entity_ids_by_kind.items())
    }
    active_event_missing_backend = {
        kind: sum(
            requested_events.get((kind, entity_id)) == "active"
            and not backend_present.get((kind, entity_id), False)
            for entity_id in ids
        )
        for kind, ids in sorted(entity_ids_by_kind.items())
    }
    return {
        "total": len(rows),
        "status_counts": dict(sorted(status_counts.items())),
        "operation_counts": dict(sorted(operation_counts.items())),
        "distinct_entities_by_kind": dict(sorted(entities_by_kind.items())),
        "backend_entity_presence_by_kind": source_presence,
        "canonical_event_state_by_kind": canonical_event_counts,
        "canonical_event_state_by_namespace_candidate": canonical_event_counts_by_namespace,
        "active_event_missing_backend_by_kind": dict(
            sorted(active_event_missing_backend.items())
        ),
        "active_event_missing_backend_by_namespace_candidate": (
            active_event_missing_backend_by_namespace
        ),
        "distinct_projection_keys_by_kind": dict(sorted(projections_by_kind.items())),
        "duplicate_projection_job_rows": len(rows) - len(projection_job_counts),
        "jobs_per_entity": {
            kind: {
                "entity_count": len(counts),
                "median_job_rows": statistics.median(counts),
                "max_job_rows": max(counts),
            }
            for kind, counts in sorted(per_entity_rows.items())
        },
        "diagnostic_timing_ms": {
            "job_listing": round(jobs_elapsed_ms, 3),
            "backend_entity_reads": round(backend_lookup_elapsed_ms, 3),
            "event_scan_by_namespace": event_scan_elapsed_ms_by_namespace,
            "total": round((time.perf_counter() - audit_started) * 1000, 3),
        },
    }


def _conversation_artifact_summary(engine: Any, namespace: str) -> dict[str, object]:
    """Count conversation artifacts by type without serializing their contents."""
    audit_started = time.perf_counter()
    with _temporary_namespace(engine, namespace):
        node_read_started = time.perf_counter()
        node_ids, node_metadata = _backend_entity_rows(
            engine, "node", ids=None, limit=10_000
        )
        node_read_elapsed_ms = (time.perf_counter() - node_read_started) * 1000
        edge_read_started = time.perf_counter()
        edge_ids, edge_metadata = _backend_entity_rows(
            engine, "edge", ids=None, limit=10_000
        )
        edge_read_elapsed_ms = (time.perf_counter() - edge_read_started) * 1000

    def count_kinds(items: list[dict[str, object]]) -> dict[str, int]:
        counts: Counter[str] = Counter()
        for metadata in items:
            counts[str(metadata.get("artifact_kind") or "unclassified")] += 1
        return dict(sorted(counts.items()))

    return {
        "node_count": len(node_ids),
        "node_artifact_kinds": count_kinds(node_metadata),
        "edge_count": len(edge_ids),
        "edge_artifact_kinds": count_kinds(edge_metadata),
        "diagnostic_timing_ms": {
            "node_read": round(node_read_elapsed_ms, 3),
            "edge_read": round(edge_read_elapsed_ms, 3),
            "total": round((time.perf_counter() - audit_started) * 1000, 3),
        },
    }


def _embedding_metrics_delta(
    before: dict[str, object], after: dict[str, object]
) -> dict[str, object]:
    """Return cumulative encoder-call and duration deltas between checkpoints."""
    before_calls = int(before.get("invocation_count") or 0)
    after_calls = int(after.get("invocation_count") or 0)
    before_items = int(before.get("item_count") or 0)
    after_items = int(after.get("item_count") or 0)
    before_ms = float(before.get("total_time_ms") or 0.0)
    after_ms = float(after.get("total_time_ms") or 0.0)
    if after_calls < before_calls or after_items < before_items or after_ms < before_ms:
        raise ValueError("embedding metrics checkpoints must be monotonic")
    return {
        "invocation_count": after_calls - before_calls,
        "item_count": after_items - before_items,
        "total_time_ms": round(after_ms - before_ms, 3),
    }


def _instrument_embedding_caches(engines: Any) -> dict[str, Any]:
    """Count lookups against Kogwistar's joblib-backed embedding caches."""
    counters: dict[str, dict[str, int]] = {}
    locks: dict[str, threading.Lock] = {}
    for space in ("conversation", "workflow", "kg", "wisdom"):
        engine = getattr(engines, space)
        cached_embed = getattr(engine, "cached_embed", None)
        if cached_embed is None:
            raise RuntimeError(f"embedding cache was not enabled for {space}")
        check_call_in_cache = getattr(cached_embed, "check_call_in_cache", None)
        if not callable(check_call_in_cache):
            raise TypeError(f"cannot inspect embedding cache for {space}")
        counters[space] = {"lookups": 0, "hits": 0, "misses": 0}
        locks[space] = threading.Lock()

        def measured_embed(
            query: str,
            *,
            _space: str = space,
            _original: Any = cached_embed,
            _checker: Any = check_call_in_cache,
        ) -> object:
            hit = bool(_checker(query))
            with locks[_space]:
                counters[_space]["lookups"] += 1
                counters[_space]["hits" if hit else "misses"] += 1
            return _original(query)

        engine.cached_embed = measured_embed

    def snapshot() -> dict[str, dict[str, int]]:
        result: dict[str, dict[str, int]] = {}
        for space, values in counters.items():
            with locks[space]:
                result[space] = dict(values)
        return result

    return {"snapshot": snapshot}


def _cache_metrics_delta(
    before: dict[str, dict[str, int]], after: dict[str, dict[str, int]]
) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for space, after_counts in after.items():
        before_counts = before.get(space, {})
        delta = {
            key: after_counts[key] - int(before_counts.get(key, 0))
            for key in ("lookups", "hits", "misses")
        }
        if any(value < 0 for value in delta.values()):
            raise ValueError("embedding cache counters must be monotonic")
        result[space] = delta
    return result


def _known_answer_proposal(
    context: list[dict[str, object]], _ctx: object
) -> dict[str, object]:
    """Replay a fixed known-answer relation over the supplied fixture evidence."""
    evidence = sorted(context, key=lambda item: str(item.get("node_id") or ""))
    if len(evidence) < 2:
        return {"groups": []}
    left_id = str(evidence[0].get("evidence_id") or "")
    right_id = str(evidence[1].get("evidence_id") or "")
    if not left_id or not right_id:
        return {"groups": []}
    return {
        "groups": [
            {
                "group_id": "known-answer-cuda-uses-v1",
                "rationale": "Replay fixture: the training workload uses CUDA.",
                "operations": [
                    {
                        "left_evidence_id": left_id,
                        "right_evidence_id": right_id,
                        "relation": "uses",
                        "rationale": "Replay fixture expected relation: uses.",
                    }
                ],
            }
        ]
    }


def _known_answer_critic(
    proposal: dict[str, object], _ctx: object
) -> dict[str, object]:
    evidence = proposal.get("evidence")
    evidence_ids = [
        str(item["evidence_id"])
        for item in evidence
        if isinstance(item, dict) and item.get("evidence_id")
    ] if isinstance(evidence, list) else []
    return {
        "verdict": "approve",
        "explanation": "Deterministic fixture replay approves its known-answer relation.",
        "evidence_ids": evidence_ids,
    }


def _edge_profile_audit(edge: object) -> dict[str, object]:
    metadata = getattr(edge, "metadata", None) or {}
    properties = getattr(edge, "properties", None) or {}
    spans: list[dict[str, object]] = []
    iter_spans = getattr(edge, "iter_span", None)
    if callable(iter_spans):
        for span in iter_spans():
            start = getattr(span, "start_char", None)
            end = getattr(span, "end_char", None)
            if isinstance(start, int) and isinstance(end, int):
                spans.append(
                    {
                        "doc_id": str(getattr(span, "doc_id", "") or ""),
                        "start_char": start,
                        "end_char": end,
                        "span_chars": max(0, end - start),
                    }
                )
    return {
        "id": str(getattr(edge, "id", "") or ""),
        "source_ids": sorted(
            str(item) for item in (getattr(edge, "source_ids", ()) or ())
        ),
        "target_ids": sorted(
            str(item) for item in (getattr(edge, "target_ids", ()) or ())
        ),
        "relation": str(getattr(edge, "relation", "") or ""),
        "crosslink_status": str(
            (metadata.get("crosslink_status") if isinstance(metadata, dict) else None)
            or (
                properties.get("crosslink_status")
                if isinstance(properties, dict)
                else None
            )
            or ""
        ),
        "workspace_id": str(metadata.get("workspace_id") or "")
        if isinstance(metadata, dict)
        else "",
        "evidence_spans": sorted(
            spans,
            key=lambda item: (
                str(item["doc_id"]),
                int(item["start_char"]),
                int(item["end_char"]),
            ),
        ),
    }


def _semantic_relation_matches(
    edge_audit: dict[str, object],
    *,
    expected_source_id: str,
    expected_target_id: str,
    expected_relation: str,
) -> bool:
    return (
        edge_audit.get("source_ids") == [expected_source_id]
        and edge_audit.get("target_ids") == [expected_target_id]
        and str(edge_audit.get("relation") or "").strip().casefold()
        == expected_relation.casefold()
    )


def _profile_failure_code(
    *,
    pending_group_count: int,
    stage_outcomes: list[dict[str, object]],
    provider_errors: list[dict[str, object]],
) -> str:
    if any(
        error.get("error_type") == "LengthFinishReasonError"
        for error in provider_errors
    ):
        return "provider_output_limit"
    if any(
        outcome.get("stage") == "critic"
        and outcome.get("outcome") in {"reject", "rejected"}
        for outcome in stage_outcomes
    ):
        return "critic_rejected"
    if pending_group_count != 1:
        return "unexpected_pending_group_count"
    return "crosslink_profile_gate_failed"


def _acl_policy_signature(record: object | None) -> tuple[object, ...] | None:
    if record is None:
        return None
    return (
        getattr(record, "mode", None),
        getattr(record, "owner_id", None),
        getattr(record, "security_scope", None),
        tuple(getattr(record, "shared_with_principals", ()) or ()),
        tuple(getattr(record, "shared_with_groups", ()) or ()),
    )


def _edge_acl_matches_endpoint_policy(engine: object, edge: object) -> bool:
    """Require persisted edge/grounding ACL policy to match both endpoints."""
    if not bool(getattr(engine, "acl_enabled", False)):
        return False
    acl_graph = getattr(engine, "acl_graph", None)
    latest_record = getattr(acl_graph, "latest_record", None)
    if not callable(latest_record):
        return False
    truth_graph = str(getattr(engine, "kg_graph_type", "") or "")
    edge_id = str(getattr(edge, "id", "") or "")
    if not truth_graph or not edge_id:
        return False

    def policy(
        grain: str, entity_id: str, target_item_id: str | None = None
    ) -> tuple[object, ...] | None:
        record = latest_record(
            grain=grain,
            truth_graph=truth_graph,
            entity_id=entity_id,
            target_item_id=target_item_id,
        )
        return _acl_policy_signature(record)

    endpoint_ids = sorted(
        {
            str(item)
            for item in (
                *(getattr(edge, "source_ids", ()) or ()),
                *(getattr(edge, "target_ids", ()) or ()),
            )
        }
    )
    endpoint_policies = [policy("node", entity_id) for entity_id in endpoint_ids]
    edge_policy = policy("edge", edge_id)
    if not endpoint_policies or edge_policy is None or any(
        item is None or item != endpoint_policies[0] for item in endpoint_policies
    ):
        return False
    if edge_policy != endpoint_policies[0]:
        return False

    grounding_policy = policy("grounding", edge_id, f"{edge_id}::mention::0")
    if grounding_policy != edge_policy:
        return False
    spans = list(getattr(edge, "iter_span", lambda: ())())
    if not spans:
        return False
    return all(
        policy("span", edge_id, f"{edge_id}::mention::0::span::{index}")
        == edge_policy
        for index, _span in enumerate(spans)
    )


def _summarize_profile_samples(
    samples: list[dict[str, object]],
) -> dict[str, object]:
    timing_summary: dict[str, dict[str, object]] = {}
    for metric in ("proposal_and_review", "approval_revalidation_apply"):
        values: list[float] = []
        for sample in samples:
            timings = sample.get("timings_seconds")
            if isinstance(timings, dict):
                value = timings.get(metric)
                if isinstance(value, (int, float)):
                    values.append(float(value))
        if values:
            timing_summary[metric] = {
                "median": statistics.median(values),
                "minimum": min(values),
                "maximum": max(values),
                "samples": values,
            }
    critic_verdicts: Counter[str] = Counter()
    failure_codes: Counter[str] = Counter()
    provider_error_types: Counter[str] = Counter()
    failed_samples = 0
    all_index_jobs_done = True
    all_semantic_retrieval_passed = True
    all_expected_semantic_relations_matched = True
    all_acl_policy_audits_verified = True
    for sample in samples:
        if sample.get("status") == "failed":
            failed_samples += 1
        result = sample.get("result")
        if isinstance(result, dict):
            failure_code = result.get("failure_code")
            if isinstance(failure_code, str):
                failure_codes[failure_code] += 1
            for error in result.get("provider_errors", []):
                if isinstance(error, dict):
                    provider_error_types[str(error.get("error_type") or "unknown")] += 1
        if not isinstance(result, dict):
            all_index_jobs_done = False
            all_semantic_retrieval_passed = False
            all_expected_semantic_relations_matched = False
            all_acl_policy_audits_verified = False
            continue
        for review in result.get("group_review_summaries", []):
            if isinstance(review, dict):
                critic_verdicts[str(review.get("critic_verdict") or "unknown")] += 1
        statuses = result.get("index_job_status_counts")
        if not isinstance(statuses, dict) or any(
            int(statuses.get(status, 0) or 0)
            for status in ("PENDING", "DOING", "FAILED")
        ):
            all_index_jobs_done = False
        all_semantic_retrieval_passed = all_semantic_retrieval_passed and (
            result.get("semantic_self_retrieval") is True
        )
        all_expected_semantic_relations_matched = (
            all_expected_semantic_relations_matched
            and result.get("expected_semantic_relation_matched") is True
        )
        all_acl_policy_audits_verified = all_acl_policy_audits_verified and (
            result.get("acl_policy_audit") == "verified"
        )
    return {
        "sample_count": len(samples),
        "failed_sample_count": failed_samples,
        "all_samples_passed": failed_samples == 0
        and all(isinstance(sample.get("result"), dict) for sample in samples),
        "accepted_crosslink_edges": sum(
            int((sample.get("result") or {}).get("accepted_crosslink_edges", 0))
            for sample in samples
            if isinstance(sample.get("result"), dict)
        ),
        "critic_verdict_counts": dict(sorted(critic_verdicts.items())),
        "failure_code_counts": dict(sorted(failure_codes.items())),
        "provider_error_type_counts": dict(sorted(provider_error_types.items())),
        "all_index_jobs_terminal_without_failures": all_index_jobs_done,
        "all_semantic_self_retrieval_passed": all_semantic_retrieval_passed,
        "all_expected_semantic_relations_matched": all_expected_semantic_relations_matched,
        "all_acl_policy_audits_verified": all_acl_policy_audits_verified,
        "timings_seconds": timing_summary,
    }


def run(
    *,
    dsn: str,
    base_url: str,
    model: str,
    output_tokens: int,
    output: Path | None,
    acl_enabled: bool = False,
    provider_mode: Literal["live", "fixture-replay"] = "live",
    embedding_device: Literal["cpu", "cuda"] = "cuda",
    embedding_cache_dir: Path | None = None,
) -> dict[str, object]:
    profile_started = time.perf_counter()
    context_tokens = _served_context_tokens(base_url) if provider_mode == "live" else None
    admin = create_engine(dsn, pool_pre_ping=True)
    schema = f"profile_crosslink_{uuid.uuid4().hex[:16]}"
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    temp_root = Path(tempfile.mkdtemp(prefix="llmw-crosslink-profile-"))
    workspace_id = f"crosslink-profile-{uuid.uuid4().hex[:8]}"
    namespaces = WorkspaceNamespaces(workspace_id)
    engines = None
    worker = None
    try:
        # Match the intended GPU-backed profile by default. CPU is an explicit
        # diagnostic mode and must never be selected as an implicit fallback.
        embedding = LocalE5SmallEmbedding(device=embedding_device)
        engines = build_postgres_namespace_engines(
            base_dir=temp_root,
            dsn=dsn,
            schema=schema,
            conversation_persistence_mode="single_stage",
            embedding_function=embedding,
            embedding_dimension=embedding.dimension,
            # The engine resolver currently has no local/custom provider enum;
            # this label is metadata only because the callable is injected.
            embedding_provider="fake",
            embedding_model="intfloat/multilingual-e5-small",
            acl_enabled=acl_enabled,
            embedding_cache_dir=embedding_cache_dir,
        )
        embedding_cache = (
            _instrument_embedding_caches(engines)
            if embedding_cache_dir is not None
            else None
        )
        settings = _local_critic_provider_settings(
            provider="llama_cpp",
            model=model,
            base_url=base_url,
            timeout_seconds=300.0,
            context_tokens=context_tokens or 4096,
            output_tokens=output_tokens,
        )
        events: list[dict[str, object]] = []
        trace_lock = threading.Lock()
        started_monotonic = time.monotonic()
        last_event_monotonic = started_monotonic
        last_stage = "setup"

        def record_trace(event: dict[str, object]) -> None:
            nonlocal last_event_monotonic, last_stage
            safe = {"event": str(event.get("event") or "unknown")}
            for key in (
                "workflow_stage",
                "outcome",
                "critic_verdict",
                "policy",
                "operation_count",
                "patch_status",
                "applied_count",
                "skipped_count",
                "failed_count",
                "validation_issue_codes",
                "maintenance_kind",
                "duration_ms",
                "error_type",
                "validation_errors",
                "reason",
                "status",
            ):
                value = event.get(key)
                if value is not None and (
                    isinstance(value, (str, int, float, bool))
                    or key in {"validation_errors", "validation_issue_codes"}
                    and isinstance(value, list)
                ):
                    safe[key] = value
            with trace_lock:
                events.append(safe)
                last_event_monotonic = time.monotonic()
                if safe.get("workflow_stage"):
                    last_stage = str(safe["workflow_stage"])
            print(
                "TRACE " + json.dumps(safe, sort_keys=True, separators=(",", ":")),
                flush=True,
            )

        progress_stop = threading.Event()
        diagnostic_audit_timings: list[dict[str, object]] = []

        def report_progress() -> None:
            while not progress_stop.wait(5.0):
                with trace_lock:
                    event_count = len(events)
                    event_age = time.monotonic() - last_event_monotonic
                    stage = last_stage
                print(
                    "PROGRESS "
                    + json.dumps(
                        {
                            "elapsed_seconds": round(
                                time.monotonic() - started_monotonic, 1
                            ),
                            "trace_events": event_count,
                            "last_stage": stage,
                            "seconds_since_last_trace": round(event_age, 1),
                            "active_provider": _provider_runtime_label(provider_mode),
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    flush=True,
                )

        replay_calls = Counter()

        def replay_proposer(
            context: list[dict[str, object]], ctx: object
        ) -> dict[str, object]:
            replay_calls["proposal"] += 1
            return _known_answer_proposal(context, ctx)

        def replay_critic(
            proposal: dict[str, object], ctx: object
        ) -> dict[str, object]:
            replay_calls["critic"] += 1
            return _known_answer_critic(proposal, ctx)

        worker = MaintenanceWorker(
            engines,
            worker_id="crosslink-profile-worker",
            provider_settings=settings,
            trace_sink=record_trace,
            crosslink_proposer=(replay_proposer if provider_mode == "fixture-replay" else None),
            crosslink_critic=(replay_critic if provider_mode == "fixture-replay" else None),
        )
        worker.lease_seconds = 300
        worker.lease_renew_interval_seconds = 30
        embedding_before_fixture_seed = embedding.metrics()
        cache_before_fixture_seed = (
            embedding_cache["snapshot"]() if embedding_cache is not None else None
        )
        (
            node_ids,
            logical_source_id,
            source_revision_id,
            revision_document_id,
            source_digest_value,
        ) = _seed_source_and_parse_view(engines, workspace_id)
        embedding_after_fixture_seed = embedding.metrics()
        cache_after_fixture_seed = (
            embedding_cache["snapshot"]() if embedding_cache is not None else None
        )
        embedding_before_proposal = embedding.metrics()
        cache_before_proposal = (
            embedding_cache["snapshot"]() if embedding_cache is not None else None
        )
        index_counts_before_proposal = _measured_profile_audit(
            diagnostic_audit_timings,
            "index_queue_before_proposal",
            _index_queue_summary,
            engines.conversation,
            namespaces.conv_bg,
        )
        conversation_artifacts_before_proposal = _measured_profile_audit(
            diagnostic_audit_timings,
            "conversation_artifacts_before_proposal",
            _conversation_artifact_summary,
            engines.conversation,
            namespaces.conv_bg,
        )
        job_id = f"crosslink-proposal-{uuid.uuid4().hex[:12]}"
        payload = {
            "workspace_id": workspace_id,
            "request_node_id": job_id,
            "source_document_id": logical_source_id,
            "source_revision_id": source_revision_id,
            "revision_document_id": revision_document_id,
            "source_digest": source_digest_value,
            "maintenance_kind": "document_propose_crosslinks",
            "mode": "background",
            "maintenance_candidates": [
                {"candidate_id": node_id} for node_id in node_ids
            ],
            "parse_quality_status": "parsed",
            "crosslink_approval_mode": "human",
            "crosslink_context_budget": {
                "max_nodes": 4,
                "max_edges": 4,
                "max_characters": 4000,
            },
            "budgets": {"max_llm_calls": 2, "max_tokens": 4096, "max_steps": 8},
        }
        engines.conversation.jobs.enqueue(
            job_id=job_id,
            namespace=namespaces.maintenance_jobs,
            entity_kind="maintenance_job",
            entity_id=node_ids[0],
            job_kind="maintenance_job:document_propose_crosslinks",
            payload=payload,
        )
        started = time.perf_counter()
        progress_thread = threading.Thread(
            target=report_progress,
            name="crosslink-profile-progress",
            daemon=True,
        )
        progress_thread.start()
        try:
            worker.process_pending_jobs(workspace_id)
        except Exception as exc:
            print(
                "FAILURE "
                + json.dumps(
                    {
                        "phase": last_stage,
                        "error_type": type(exc).__name__,
                        "trace_events": len(events),
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                flush=True,
            )
            raise
        finally:
            progress_stop.set()
            progress_thread.join(timeout=6.0)
        proposal_elapsed = time.perf_counter() - started
        embedding_after_proposal = embedding.metrics()
        cache_after_proposal = (
            embedding_cache["snapshot"]() if embedding_cache is not None else None
        )

        review_service = CrosslinkGroupReviewService(engines)
        pending = review_service.list_pending(workspace_id=workspace_id)
        index_counts_after_proposal = _measured_profile_audit(
            diagnostic_audit_timings,
            "index_queue_after_proposal",
            _index_queue_summary,
            engines.conversation,
            namespaces.conv_bg,
        )
        conversation_artifacts_after_proposal = _measured_profile_audit(
            diagnostic_audit_timings,
            "conversation_artifacts_after_proposal",
            _conversation_artifact_summary,
            engines.conversation,
            namespaces.conv_bg,
        )
        status_before_decision = [
            str((item.metadata or {}).get("review_status") or "unknown")
            for item in pending
        ]
        if len(pending) != 1:
            stage_outcomes = [
                {"stage": event.get("workflow_stage"), "outcome": event.get("outcome")}
                for event in events
                if event.get("event") == "maintenance_crosslink_workflow_stage"
            ]
            quality_events = [
                {
                    "event": event.get("event"),
                    "critic_verdict": event.get("critic_verdict"),
                    "policy": event.get("policy"),
                    "operation_count": event.get("operation_count"),
                }
                for event in events
                if event.get("event") == "maintenance_crosslink_group_reviewed"
            ]
            provider_errors = [
                {
                    "event": event.get("event"),
                    "error_type": event.get("error_type"),
                }
                for event in events
                if isinstance(event.get("error_type"), str)
            ]
            failure_code = _profile_failure_code(
                pending_group_count=len(pending),
                stage_outcomes=stage_outcomes,
                provider_errors=provider_errors,
            )
            failure: dict[str, object] = {
                "schema_version": 1,
                "workload": "real-provider-grounded-crosslink-lifecycle",
                "synthetic_data": True,
                "status": "failed",
                "provider": {
                    "runtime": (
                        "custom Prism llama.cpp OpenAI-compatible endpoint"
                        if provider_mode == "live"
                        else _provider_runtime_label(provider_mode)
                    ),
                    "mode": provider_mode,
                    "model": model if provider_mode == "live" else "known-answer-cuda-uses-v1",
                    "context_tokens": context_tokens,
                    "output_tokens": output_tokens,
                    "embedding": f"intfloat/multilingual-e5-small on {embedding_device.upper()}",
                    "acl_enabled": acl_enabled,
                },
                "result": {
                    "status": "failed",
                    "failure_stage": "human_review_pending_persistence",
                    "failure_code": failure_code,
                    "pending_groups": len(pending),
                    "pending_status_before_decision": status_before_decision,
                    "accepted_crosslink_edges": 0,
                    "stage_outcomes": stage_outcomes,
                    "group_review_summaries": quality_events,
                    "provider_errors": provider_errors,
                },
                "timings_seconds": {
                    "proposal_and_review": round(proposal_elapsed, 3),
                },
                "trace_event_count": len(events),
            }
            if output is not None:
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(
                    json.dumps(failure, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
            print(json.dumps(failure, indent=2, sort_keys=True), flush=True)
            raise RuntimeError(
                f"expected exactly one valid human-pending review group, found {len(pending)}"
            )
        artifact = pending[0]
        decision = review_service.decide_batch(
            workspace_id=workspace_id,
            decisions=[
                CrosslinkReviewDecision(
                    artifact_id=str(artifact.id),
                    decision="approve",
                    expected_version=int(
                        (artifact.metadata or {}).get("decision_version") or 1
                    ),
                )
            ],
            actor_id="synthetic-profile-reviewer",
        )
        if not decision or decision[0].get("status") != "decided":
            raise RuntimeError(f"fixture approval did not commit: {decision!r}")
        embedding_after_approval_decision = embedding.metrics()
        cache_after_approval_decision = (
            embedding_cache["snapshot"]() if embedding_cache is not None else None
        )
        index_counts_after_approval_decision = _measured_profile_audit(
            diagnostic_audit_timings,
            "index_queue_after_approval_decision",
            _index_queue_summary,
            engines.conversation,
            namespaces.conv_bg,
        )
        apply_started = time.perf_counter()
        worker.process_pending_jobs(workspace_id)
        approval_apply_elapsed = time.perf_counter() - apply_started
        embedding_after_apply = embedding.metrics()
        cache_after_apply = (
            embedding_cache["snapshot"]() if embedding_cache is not None else None
        )
        index_counts_after_apply_worker = _measured_profile_audit(
            diagnostic_audit_timings,
            "index_queue_after_apply_worker",
            _index_queue_summary,
            engines.conversation,
            namespaces.conv_bg,
        )
        conversation_artifacts_after_apply = _measured_profile_audit(
            diagnostic_audit_timings,
            "conversation_artifacts_after_apply_worker",
            _conversation_artifact_summary,
            engines.conversation,
            namespaces.conv_bg,
        )

        with _temporary_namespace(engines.kg, namespaces.curated_kg_space):
            edges = list(
                engines.kg.read.get_edges(
                    where={
                        "workspace_id": workspace_id,
                        "crosslink_status": "accepted",
                    },
                    limit=100,
                )
            )
        edge_audit = [_edge_profile_audit(edge) for edge in edges]
        accepted_edges = [
            edge
            for edge in edges
            if (
                (getattr(edge, "metadata", None) or {}).get("crosslink_status")
                or (getattr(edge, "properties", None) or {}).get("crosslink_status")
            )
            == "accepted"
            and set(getattr(edge, "source_ids", ()) or ())
            | set(getattr(edge, "target_ids", ()) or ())
            == set(node_ids)
        ]
        index_reconciliation_started = time.perf_counter()
        materialized_index_jobs = engines.conversation.reconcile_indexes(
            max_jobs=10_000,
            namespace=namespaces.conv_bg,
        )
        index_reconciliation_elapsed = time.perf_counter() - index_reconciliation_started
        index_queue_after_reconciliation = _measured_profile_audit(
            diagnostic_audit_timings,
            "index_queue_after_reconciliation",
            _index_queue_summary,
            engines.conversation,
            namespaces.conv_bg,
        )
        embedding_after_reconciliation = embedding.metrics()
        cache_after_reconciliation = (
            embedding_cache["snapshot"]() if embedding_cache is not None else None
        )
        index_status_counts = index_queue_after_reconciliation["status_counts"]
        semantic_retrieval_started = time.perf_counter()
        semantic_self_hit = False
        with _temporary_namespace(engines.conversation, namespaces.conv_bg):
            conversation_nodes = engines.conversation.read.get_nodes(limit=100)
            indexed_node = next(
                (
                    node
                    for node in conversation_nodes
                    if getattr(node, "embedding", None)
                ),
                None,
            )
            if indexed_node is not None:
                result_batches = engines.conversation.read.query_nodes(
                    query_embeddings=indexed_node.embedding,
                    n_results=5,
                )
                semantic_self_hit = any(
                    hit.id == indexed_node.id
                    for batch in result_batches
                    for hit in batch
                )
        semantic_retrieval_elapsed = time.perf_counter() - semantic_retrieval_started
        embedding_after_semantic_retrieval = embedding.metrics()
        cache_after_semantic_retrieval = (
            embedding_cache["snapshot"]() if embedding_cache is not None else None
        )
        stage_outcomes = [
            {"stage": event.get("workflow_stage"), "outcome": event.get("outcome")}
            for event in events
            if event.get("event") == "maintenance_crosslink_workflow_stage"
        ]
        quality_events = [
            {
                "event": event.get("event"),
                "critic_verdict": event.get("critic_verdict"),
                "policy": event.get("policy"),
                "operation_count": event.get("operation_count"),
            }
            for event in events
            if event.get("event") == "maintenance_crosslink_group_reviewed"
        ]
        required_stage_outcomes = {
            ("validate", "passed"),
            ("critic", "approve"),
            ("pending", "persisted"),
            ("outcome", "pending"),
        }
        observed_stage_outcomes = {
            (str(item.get("stage") or ""), str(item.get("outcome") or ""))
            for item in stage_outcomes
        }
        if required_stage_outcomes - observed_stage_outcomes:
            raise RuntimeError(
                "required proposal, validation, critic, or pending-review stage was absent"
            )
        if (
            status_before_decision != ["pending"]
            or len(quality_events) != 1
            or quality_events[0].get("critic_verdict") != "approve"
            or quality_events[0].get("policy") != "human"
        ):
            raise RuntimeError(
                "critic approval or human-pending lifecycle evidence was inconsistent"
            )
        accepted_edge_audit = (
            _edge_profile_audit(accepted_edges[0]) if len(accepted_edges) == 1 else None
        )
        with _temporary_namespace(engines.kg, namespaces.curated_kg_space):
            acl_enabled = bool(getattr(engines.kg, "acl_enabled", False))
            acl_policy_matches_endpoints = (
                _edge_acl_matches_endpoint_policy(engines.kg, accepted_edges[0])
                if acl_enabled and len(accepted_edges) == 1
                else False
            )
        if accepted_edge_audit is not None:
            expected_spans = {
                (
                    revision_document_id,
                    0,
                    len("Training workload Alpha uses CUDA to train a neural network."),
                ),
                (
                    revision_document_id,
                    SENTENCES.index("CUDA is NVIDIA"),
                    len(SENTENCES),
                ),
            }
            observed_spans = {
                (
                    str(span.get("doc_id") or ""),
                    int(span.get("start_char") or 0),
                    int(span.get("end_char") or 0),
                )
                for span in accepted_edge_audit["evidence_spans"]
            }
            if (
                len(accepted_edge_audit["source_ids"]) != 1
                or len(accepted_edge_audit["target_ids"]) != 1
                or set(
                    accepted_edge_audit["source_ids"]
                    + accepted_edge_audit["target_ids"]
                )
                != set(node_ids)
                or not accepted_edge_audit["relation"]
                or observed_spans != expected_spans
                or (acl_enabled and not acl_policy_matches_endpoints)
            ):
                raise RuntimeError(
                    "persisted edge endpoints, relation, source spans, or ACL policy failed audit"
                )
        expected_semantic_relation_matched = (
            accepted_edge_audit is not None
            and _semantic_relation_matches(
                accepted_edge_audit,
                expected_source_id=node_ids[0],
                expected_target_id=node_ids[1],
                expected_relation="uses",
            )
        )
        result: dict[str, object] = {
            "schema_version": 1,
            "workload": "real-provider-grounded-crosslink-lifecycle",
            "synthetic_data": True,
            "database": "PostgreSQL/pgvector; unique schema dropped after run",
            "provider": {
                "runtime": (
                    "custom Prism llama.cpp OpenAI-compatible endpoint"
                    if provider_mode == "live"
                    else "deterministic known-answer fixture replay; no LLM inference"
                ),
                "mode": provider_mode,
                "model": model if provider_mode == "live" else "known-answer-cuda-uses-v1",
                "base_url": base_url,
                "context_tokens": context_tokens,
                "output_tokens": output_tokens,
                "embedding": f"intfloat/multilingual-e5-small on {embedding_device.upper()}",
                "acl_enabled": acl_enabled,
                "embedding_binding": (
                    "local Hugging Face E5 callable injected into the engine"
                ),
            },
            "result": {
                "status": "passed",
                "pending_groups": len(pending),
                "pending_status_before_decision": status_before_decision,
                "synthetic_fixture_decision": decision[0].get("status"),
                "accepted_crosslink_edges": len(accepted_edges),
                "accepted_edge_audit": accepted_edge_audit,
                "expected_relation": "uses",
                "expected_semantic_relation_matched": expected_semantic_relation_matched,
                "acl_policy_audit": (
                    "verified"
                    if acl_enabled and acl_policy_matches_endpoints
                    else "failed"
                    if acl_enabled
                    else "disabled"
                ),
                "index_job_status_counts": index_status_counts,
                "index_job_status_counts_by_phase": {
                    "before_proposal": index_counts_before_proposal,
                    "after_proposal": index_counts_after_proposal,
                    "after_approval_decision": index_counts_after_approval_decision,
                    "after_apply_worker": index_counts_after_apply_worker,
                    "after_reconciliation": index_queue_after_reconciliation,
                },
                "conversation_artifact_counts_by_phase": {
                    "before_proposal": conversation_artifacts_before_proposal,
                    "after_proposal": conversation_artifacts_after_proposal,
                    "after_apply_worker": conversation_artifacts_after_apply,
                },
                "materialized_index_jobs": materialized_index_jobs,
                "semantic_self_retrieval": semantic_self_hit,
                "embedding_metrics": embedding.metrics(),
                "embedding_result_cache_enabled": embedding_cache_dir is not None,
                "embedding_metrics_by_phase": {
                    "fixture_seed": _embedding_metrics_delta(
                        embedding_before_fixture_seed, embedding_after_fixture_seed
                    ),
                    "proposal_and_review": _embedding_metrics_delta(
                        embedding_before_proposal, embedding_after_proposal
                    ),
                    "approval_decision": _embedding_metrics_delta(
                        embedding_after_proposal, embedding_after_approval_decision
                    ),
                    "approval_apply": _embedding_metrics_delta(
                        embedding_after_approval_decision, embedding_after_apply
                    ),
                    "index_reconciliation": _embedding_metrics_delta(
                        embedding_after_apply, embedding_after_reconciliation
                    ),
                    "semantic_self_retrieval": _embedding_metrics_delta(
                        embedding_after_reconciliation, embedding_after_semantic_retrieval
                    ),
                },
                "embedding_result_cache_metrics_by_phase": (
                    {
                        "fixture_seed": _cache_metrics_delta(
                            cache_before_fixture_seed, cache_after_fixture_seed
                        ),
                        "proposal_and_review": _cache_metrics_delta(
                            cache_before_proposal, cache_after_proposal
                        ),
                        "approval_decision": _cache_metrics_delta(
                            cache_after_proposal, cache_after_approval_decision
                        ),
                        "approval_apply": _cache_metrics_delta(
                            cache_after_approval_decision, cache_after_apply
                        ),
                        "index_reconciliation": _cache_metrics_delta(
                            cache_after_apply, cache_after_reconciliation
                        ),
                        "semantic_self_retrieval": _cache_metrics_delta(
                            cache_after_reconciliation, cache_after_semantic_retrieval
                        ),
                    }
                    if embedding_cache is not None
                    else None
                ),
                "provider_call_counts": (
                    dict(replay_calls)
                    if provider_mode == "fixture-replay"
                    else dict(
                        Counter(
                            str(event.get("reason") or "unknown")
                            for event in events
                            if event.get("event") == "maintenance_crosslink_provider_call_reserved"
                        )
                    )
                ),
                "stage_outcomes": stage_outcomes,
                "group_review_summaries": quality_events,
                "diagnostic_audit_timings": diagnostic_audit_timings,
            },
            "timings_seconds": {
                "proposal_and_review": round(proposal_elapsed, 3),
                "approval_revalidation_apply": round(approval_apply_elapsed, 3),
                "index_reconciliation": round(index_reconciliation_elapsed, 3),
                "semantic_self_retrieval": round(semantic_retrieval_elapsed, 3),
                "diagnostic_audits": round(
                    sum(float(item["elapsed_ms"]) for item in diagnostic_audit_timings)
                    / 1000,
                    3,
                ),
                "profile_wall_before_cleanup": round(
                    time.perf_counter() - profile_started, 3
                ),
            },
            "trace_event_count": len(events),
        }
        if len(accepted_edges) != 1:
            audit = {
                "schema_version": 1,
                "workload": "real-provider-grounded-crosslink-lifecycle",
                "synthetic_data": True,
                "provider_model": model,
                "result": {
                    "expected_node_ids": node_ids,
                    "edge_scan_count": len(edges),
                    "edge_audit": edge_audit,
                    "accepted_crosslink_edges": len(accepted_edges),
                    "trace_events": events,
                },
            }
            if output is not None:
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(
                    json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8"
                )
            print(
                "FINAL_GRAPH_AUDIT "
                + json.dumps(audit["result"], sort_keys=True, separators=(",", ":")),
                flush=True,
            )
            raise RuntimeError(
                f"expected one accepted persisted edge after approval, found {len(accepted_edges)}"
            )
        if (
            index_status_counts.get("PENDING", 0)
            or index_status_counts.get("DOING", 0)
            or not semantic_self_hit
        ):
            raise RuntimeError(
                f"conversation index work is not terminal: {index_status_counts!r}"
            )
        if output is not None:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
        print(json.dumps(result, indent=2, sort_keys=True), flush=True)
        if not expected_semantic_relation_matched:
            raise RuntimeError(
                "persisted cross-link did not match the fixture's expected direction and relation"
            )
        return result
    finally:
        if worker is not None:
            worker.close()
        if engines is not None:
            engines.close()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()
        shutil.rmtree(temp_root, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default=os.getenv("KOGWISTAR_POSTGRES_DSN"))
    parser.add_argument("--base-url", default="http://127.0.0.1:8181")
    parser.add_argument("--model", default="Ternary-Bonsai-2-27B-PTQ1_0")
    parser.add_argument(
        "--provider-mode",
        choices=("live", "fixture-replay"),
        default="live",
        help="use Bonsai/Prism or a deterministic known-answer provider replay",
    )
    parser.add_argument(
        "--embedding-device",
        choices=("cuda", "cpu"),
        default="cuda",
        help="local E5 inference device; CUDA is the intended profile default",
    )
    parser.add_argument(
        "--embedding-cache-dir",
        type=Path,
        help="enable Kogwistar's persistent result cache at this path; omit for cache-off",
    )
    parser.add_argument(
        "--output-tokens",
        type=int,
        default=1024,
        help="per-provider-call completion limit; the maintenance budget remains separately capped",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--acl-enabled",
        action="store_true",
        help="construct all graph-space engines with core ACL enforcement enabled",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=1,
        help="independent fresh-schema runs; the default single-run behavior is unchanged",
    )
    args = parser.parse_args()
    if not args.dsn:
        parser.error("--dsn or KOGWISTAR_POSTGRES_DSN is required")
    if not 1 <= args.samples <= 20:
        parser.error("--samples must be between 1 and 20")
    if not 128 <= args.output_tokens <= 4096:
        parser.error("--output-tokens must be between 128 and 4096")
    if args.samples == 1:
        result = run(
            dsn=args.dsn,
            base_url=args.base_url,
            model=args.model,
            output_tokens=args.output_tokens,
            output=args.output,
            acl_enabled=args.acl_enabled,
            provider_mode=args.provider_mode,
            embedding_device=args.embedding_device,
            embedding_cache_dir=args.embedding_cache_dir,
        )
        return 0 if result["result"]["accepted_crosslink_edges"] == 1 else 1  # type: ignore[index]

    results: list[dict[str, object]] = []
    for sample_index in range(1, args.samples + 1):
        sample_output = (
            args.output.with_name(
                f"{args.output.stem}.sample-{sample_index:02d}{args.output.suffix}"
            )
            if args.output is not None
            else None
        )
        try:
            result = run(
                dsn=args.dsn,
                base_url=args.base_url,
                model=args.model,
                output_tokens=args.output_tokens,
                output=sample_output,
                acl_enabled=args.acl_enabled,
                provider_mode=args.provider_mode,
                embedding_device=args.embedding_device,
                embedding_cache_dir=args.embedding_cache_dir,
            )
            result["status"] = "passed"
            results.append(result)
        except Exception as exc:  # noqa: BLE001 - isolate and record each profile sample
            failure: dict[str, object] | None = None
            if sample_output is not None and sample_output.is_file():
                try:
                    loaded = json.loads(sample_output.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        failure = loaded
                except (OSError, json.JSONDecodeError):
                    failure = None
            if failure is None:
                failure = {
                    "schema_version": 1,
                    "workload": "real-provider-grounded-crosslink-lifecycle",
                    "status": "failed",
                    "failure": {"error_type": type(exc).__name__},
                }
                if sample_output is not None:
                    sample_output.parent.mkdir(parents=True, exist_ok=True)
                    sample_output.write_text(
                        json.dumps(failure, indent=2, sort_keys=True) + "\n",
                        encoding="utf-8",
                    )
            failure["status"] = "failed"
            failure["sample_index"] = sample_index
            results.append(failure)
    report = {
        "schema_version": 1,
        "workload": "real-provider-grounded-crosslink-lifecycle",
        "samples": results,
        "summary": _summarize_profile_samples(results),
    }
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    print(json.dumps(report, indent=2, sort_keys=True), flush=True)
    return 0 if report["summary"]["all_samples_passed"] else 1  # type: ignore[index]


if __name__ == "__main__":
    raise SystemExit(main())
