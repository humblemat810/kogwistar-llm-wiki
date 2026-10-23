"""Profile the durable LLM-Wiki maintenance path against disposable PostgreSQL.

This intentionally uses deterministic embeddings and a fake observation critic:
it measures graph persistence, queue/lease handling, bounded context reads, audit
publication, and acknowledgement without attributing model inference to storage.
The runner creates a unique PostgreSQL schema and drops only that schema.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import tempfile
import time
import uuid
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from kogwistar.engine_core.models import Document, Grounding, Node, Span

from kogwistar_llm_wiki.configuration.workspace import WorkspaceNamespaces
from kogwistar_llm_wiki.ingest_pipeline import build_postgres_namespace_engines
from kogwistar_llm_wiki.maintenance.observation_evidence import (
    build_authoritative_source_evidence,
)
from kogwistar_llm_wiki.utils import _temporary_namespace
from kogwistar_llm_wiki.worker import MaintenanceWorker


class DeterministicEmbedding:
    """Fast, fixed-width encoder used only to isolate service/storage work."""

    def __call__(self, values: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for value in values:
            digest = hashlib.sha256(str(value).encode("utf-8")).digest()
            vectors.append(
                [float(int.from_bytes(digest[:4], "big")), float(int.from_bytes(digest[4:8], "big"))]
            )
        return vectors


def _percentile(values: list[float], percent: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percent
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _summary(values: list[float]) -> dict[str, float | None]:
    return {
        "count": float(len(values)),
        "mean_ms": statistics.fmean(values) if values else None,
        "p50_ms": _percentile(values, 0.50),
        "p95_ms": _percentile(values, 0.95),
        "p99_ms": _percentile(values, 0.99),
        "max_ms": max(values) if values else None,
    }


def _close_engines(engines: object) -> None:
    for name in ("conversation", "workflow", "kg", "wisdom", "derived_knowledge"):
        engine = getattr(engines, name, None)
        if engine is None:
            continue
        close = getattr(engine, "close", None)
        if callable(close):
            close()
        backend = getattr(engine, "backend", None)
        dispose = getattr(getattr(backend, "engine", None), "dispose", None)
        if callable(dispose):
            dispose()


def run_profile(*, dsn: str, items: int, critic_delay_ms: float) -> dict[str, Any]:
    from sqlalchemy import create_engine, text

    schema = f"profile_maintenance_{uuid.uuid4().hex[:16]}"
    admin = create_engine(dsn, pool_pre_ping=True)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    temp_root = Path(tempfile.mkdtemp(prefix="llmw-maintenance-profile-"))
    workspace_id = f"maintenance-profile-{uuid.uuid4().hex[:8]}"
    namespaces = WorkspaceNamespaces(workspace_id)
    traces: list[dict[str, object]] = []
    critic_times: list[float] = []
    seed_write_times: list[float] = []
    enqueue_times: list[float] = []
    source_evidence_roundtrip: str | None = None
    engines = None
    worker = None
    try:
        started = time.perf_counter()
        engines = build_postgres_namespace_engines(
            base_dir=temp_root,
            dsn=dsn,
            schema=schema,
            embedding_function=DeterministicEmbedding(),
            embedding_dimension=2,
            embedding_provider="benchmark",
            embedding_model="deterministic-sha256-2d",
        )
        engine_startup_ms = (time.perf_counter() - started) * 1000.0

        def critic(_frame: object, _ctx: object) -> Mapping[str, object]:
            call_started = time.perf_counter()
            if critic_delay_ms:
                time.sleep(critic_delay_ms / 1000.0)
            critic_times.append((time.perf_counter() - call_started) * 1000.0)
            return {"status": "succeeded", "findings": []}

        worker = MaintenanceWorker(
            engines,
            worker_id="maintenance-profile-worker",
            observation_critic=critic,
            trace_sink=lambda event: traces.append(dict(event)),
        )
        worker.lease_seconds = 300
        worker.lease_renew_interval_seconds = 60

        source_texts: list[tuple[str, str, str, str]] = []
        for index in range(items):
            content = f"Benchmark source {index}: workspace graph maintenance evidence."
            logical_id = f"profile-source-{index:05d}"
            revision_id = f"profile-revision-{index:05d}"
            revision_document_id = f"profile-revision-document-{index:05d}"
            digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
            source_texts.append((content, logical_id, revision_id, revision_document_id))

            started = time.perf_counter()
            with _temporary_namespace(engines.kg, namespaces.source_space):
                engines.kg.write.add_document(
                    Document(
                        id=revision_document_id,
                        content=content,
                        type="text",
                        metadata={
                            "workspace_id": workspace_id,
                            "logical_source_document_id": logical_id,
                            "source_revision_id": revision_id,
                            "revision_document_id": revision_document_id,
                            "source_digest": digest,
                        },
                    )
                )
            node_id = f"ws:{workspace_id}:maintenance-subject:{index:05d}"
            with _temporary_namespace(engines.kg, namespaces.curated_kg_space):
                engines.kg.write.add_node(
                    Node(
                        id=node_id,
                        label=f"Benchmark concept {index}",
                        type="entity",
                        summary=f"A bounded maintenance subject grounded in source {index}.",
                        doc_id=revision_document_id,
                        mentions=[
                            Grounding(
                                spans=[
                                    Span(
                                        doc_id=revision_document_id,
                                        start_char=0,
                                        end_char=len(content),
                                        excerpt=content,
                                        document_page_url="",
                                        collection_page_url="",
                                        insertion_method="profile-benchmark",
                                    )
                                ]
                            )
                        ],
                        metadata={
                            "workspace_id": workspace_id,
                            "source_document_id": logical_id,
                            "source_revision_id": revision_id,
                            "revision_document_id": revision_document_id,
                            "source_digest": digest,
                        },
                    )
                )
                if index == 0:
                    persisted_nodes = list(engines.kg.read.get_nodes(ids=[node_id], limit=1))
                    if persisted_nodes:
                        evidence = build_authoritative_source_evidence(
                            engines.kg,
                            persisted_nodes[0],
                            workspace_id=workspace_id,
                            source_namespace=namespaces.source_space,
                            expected_source_document_id=logical_id,
                            expected_revision_id=revision_id,
                            expected_revision_document_id=revision_document_id,
                            expected_source_digest=digest,
                        )
                        source_evidence_roundtrip = str(
                            evidence.get("source_evidence_status") or "missing_status"
                        )
                    else:
                        source_evidence_roundtrip = "persisted_node_not_readable"
            seed_write_times.append((time.perf_counter() - started) * 1000.0)

            job_id = f"maintenance-profile-job-{index:05d}"
            job_payload = {
                "workspace_id": workspace_id,
                "request_node_id": job_id,
                "source_document_id": logical_id,
                "source_revision_id": revision_id,
                "source_digest": digest,
                "revision_document_id": revision_document_id,
                "maintenance_kind": "review_maintenance_subject",
                "mode": "background",
                "observation_subject": {
                    "kind": "node",
                    "subject_id": node_id,
                    "namespace": namespaces.curated_kg_space,
                    "source_document_id": logical_id,
                    "revision_id": revision_id,
                    "revision_document_id": revision_document_id,
                    "source_digest": digest,
                },
                "observation_neighborhood_count": 8,
                "observation_token_budget": 1200,
                "budgets": {"max_llm_calls": 1, "max_tokens": 1200, "max_steps": 2},
            }
            started = time.perf_counter()
            engines.conversation.jobs.enqueue(
                job_id=job_id,
                namespace=namespaces.maintenance_jobs,
                entity_kind="maintenance_job",
                entity_id=node_id,
                job_kind="maintenance_job:review_maintenance_subject",
                payload=job_payload,
            )
            enqueue_times.append((time.perf_counter() - started) * 1000.0)

        process_started = time.perf_counter()
        worker.process_pending_jobs(workspace_id)
        processing_ms = (time.perf_counter() - process_started) * 1000.0

        dispatch_ms = [
            float(event["duration_ms"])
            for event in traces
            if event.get("event") == "maintenance_job_dispatch_complete"
            and isinstance(event.get("duration_ms"), (int, float))
        ]
        completed = sum(
            event.get("event") == "maintenance_job_acknowledged"
            for event in traces
        )
        provider_success = sum(
            event.get("event") == "maintenance_observation_completed"
            and isinstance(event.get("assessment"), Mapping)
            and event["assessment"].get("critic_status") == "succeeded"
            for event in traces
        )
        trace_counts = dict(Counter(str(event.get("event") or "unknown") for event in traces))
        critic_skip_reasons = [
            {
                "event": str(event.get("event")),
                "source_evidence_status": str(event.get("source_evidence_status") or ""),
            }
            for event in traces
            if event.get("event") == "maintenance_observation_critic_skipped_no_verified_source"
        ]
        queue_rows = engines.conversation.meta_sqlite.list_index_jobs(
            namespace=namespaces.maintenance_jobs,
            limit=items + 10,
        )
        status_counts: dict[str, int] = {}
        for row in queue_rows:
            status = str(getattr(row, "status", "unknown"))
            status_counts[status] = status_counts.get(status, 0) + 1

        with admin.connect() as connection:
            table_bytes = int(
                connection.execute(
                    text(
                        "SELECT COALESCE(sum(pg_total_relation_size(quote_ident(schemaname) || '.' || quote_ident(tablename))), 0) "
                        "FROM pg_tables WHERE schemaname = :schema"
                    ),
                    {"schema": schema},
                ).scalar_one()
            )

        return {
            "schema_version": 1,
            "workload": "durable-review-maintenance-postgres",
            "backend": "Kogwistar PostgreSQL graph storage plus durable maintenance queue",
            "items": items,
            "workspace_id": workspace_id,
            "embedding": "deterministic 2D SHA-256 projection; not a quality embedding",
            "critic": "fake structured observation critic; model inference excluded",
            "critic_delay_ms": critic_delay_ms,
            "telemetry_export_enabled": os.getenv("LLM_WIKI_OTEL_ENABLED", "unset"),
            "timings": {
                "engine_startup_ms": engine_startup_ms,
                "per_subject_source_and_node_write": _summary(seed_write_times),
                "per_job_enqueue": _summary(enqueue_times),
                "per_fake_critic_call": _summary(critic_times),
                "per_job_worker_dispatch_trace": _summary(dispatch_ms),
                "queue_drain_total_ms": processing_ms,
                "jobs_per_second": completed / (processing_ms / 1000.0) if processing_ms else 0.0,
            },
            "correctness": {
                "source_evidence_roundtrip": source_evidence_roundtrip,
                "jobs_enqueued": items,
                "jobs_acknowledged": completed,
                "critic_successful_assessments": provider_success,
                "queue_status_counts": status_counts,
                "trace_events": len(traces),
                "trace_event_counts": trace_counts,
                "critic_skip_reasons": critic_skip_reasons,
                "all_jobs_completed": completed == items and provider_success == items,
            },
            "postgres_schema_table_bytes": table_bytes,
            "measurement_limits": [
                "No production embedding or maintenance model was invoked.",
                "No real Grafana/OTel exporter or multi-worker contention was included.",
                "This run is a PostgreSQL-backed maintenance-path baseline, not a deployment-capacity claim.",
            ],
        }
    finally:
        if worker is not None:
            worker.close()
        if engines is not None:
            _close_engines(engines)
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dsn", default=os.getenv("KOGWISTAR_POSTGRES_DSN"))
    parser.add_argument("--items", type=int, default=100)
    parser.add_argument("--critic-delay-ms", type=float, default=0.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.dsn:
        parser.error("--dsn or KOGWISTAR_POSTGRES_DSN is required")
    if args.items < 1 or args.items > 10_000:
        parser.error("--items must be between 1 and 10000")
    if args.critic_delay_ms < 0 or args.critic_delay_ms > 60_000:
        parser.error("--critic-delay-ms must be between 0 and 60000")
    result = run_profile(dsn=args.dsn, items=args.items, critic_delay_ms=args.critic_delay_ms)
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0 if result["correctness"]["all_jobs_completed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
