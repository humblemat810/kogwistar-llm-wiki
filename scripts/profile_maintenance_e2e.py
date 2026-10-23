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
import threading
import time
import uuid
from collections import Counter
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from kogwistar.engine_core.models import Document, Grounding, Node, Span

from kogwistar_llm_wiki.configuration.workspace import WorkspaceNamespaces
from kogwistar_llm_wiki.ingest_pipeline import build_postgres_namespace_engines
from kogwistar_llm_wiki.maintenance.observation_evidence import (
    build_authoritative_source_evidence,
)
from kogwistar_llm_wiki.utils import _background_namespace, _temporary_namespace
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


class _SqlStatementMetrics:
    """Count SQL issued by benchmark engines, excluding admin/setup queries."""

    def __init__(self, admin_engine: object) -> None:
        from sqlalchemy import event
        from sqlalchemy.engine import Engine

        self._event = event
        self._engine_type = Engine
        self._admin_engine = admin_engine
        self.counts: Counter[str] = Counter()
        self.latencies_ms: list[float] = []
        self._lock = threading.Lock()

        def before_execute(connection: object, _cursor: object, statement: str, _params: object,
                           context: object, _executemany: bool) -> None:
            if getattr(connection, "engine", None) is self._admin_engine:
                return
            operation = statement.lstrip().split(maxsplit=1)[0].upper() if statement.strip() else "EMPTY"
            with self._lock:
                self.counts[operation] += 1
            context._profile_started_at = time.perf_counter()  # type: ignore[attr-defined]

        def after_execute(connection: object, _cursor: object, _statement: str, _params: object,
                          context: object, _executemany: bool) -> None:
            if getattr(connection, "engine", None) is self._admin_engine:
                return
            started = getattr(context, "_profile_started_at", None)
            if isinstance(started, float):
                with self._lock:
                    self.latencies_ms.append((time.perf_counter() - started) * 1000.0)

        self._before = before_execute
        self._after = after_execute
        event.listen(Engine, "before_cursor_execute", before_execute)
        event.listen(Engine, "after_cursor_execute", after_execute)

    def close(self) -> None:
        self._event.remove(self._engine_type, "before_cursor_execute", self._before)
        self._event.remove(self._engine_type, "after_cursor_execute", self._after)

    def reset(self) -> None:
        with self._lock:
            self.counts.clear()
            self.latencies_ms.clear()

    def result(self) -> dict[str, object]:
        with self._lock:
            return {
                "statement_count": sum(self.counts.values()),
                "statement_counts_by_verb": dict(sorted(self.counts.items())),
                "statement_latency_ms": _summary(list(self.latencies_ms)),
            }


def _install_counting_otel_exporter() -> tuple[object, object]:
    """Install an in-process OTel exporter so enabled runs are not no-op runs."""

    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor,
        SpanExporter,
        SpanExportResult,
    )

    class CountingExporter(SpanExporter):
        def __init__(self) -> None:
            self.span_count = 0
            self._lock = threading.Lock()

        def export(self, spans: object) -> object:
            with self._lock:
                self.span_count += len(spans)  # type: ignore[arg-type]
            return SpanExportResult.SUCCESS

        def shutdown(self) -> None:
            return None

        def force_flush(self, timeout_millis: int = 30_000) -> bool:
            return True

    provider = TracerProvider()
    exporter = CountingExporter()
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    return provider, exporter


def _workflow_artifact_counts(engine: object, namespace: str) -> dict[str, int]:
    reader = getattr(engine, "read", None)
    get_nodes = getattr(reader, "get_nodes", None)
    if not callable(get_nodes):
        return {
            "workflow_run": 0,
            "workflow_step_exec": 0,
            "workflow_checkpoint": 0,
            "checkpoint_state_bytes": 0,
        }
    counts: dict[str, int] = {}
    with _temporary_namespace(engine, namespace):
        for entity_type in ("workflow_run", "workflow_step_exec", "workflow_checkpoint"):
            try:
                rows = get_nodes(where={"entity_type": entity_type}, limit=100_000)
                counts[entity_type] = len(rows)
                if entity_type == "workflow_checkpoint":
                    counts["checkpoint_state_bytes"] = sum(
                        len(str((getattr(row, "metadata", {}) or {}).get("state_json") or "").encode("utf-8"))
                        for row in rows
                    )
            except (AttributeError, TypeError, ValueError, NotImplementedError):
                try:
                    all_rows = get_nodes(limit=100_000)
                    rows = [
                        row
                        for row in all_rows
                        if str((getattr(row, "metadata", {}) or {}).get("entity_type") or "")
                        == entity_type
                    ]
                    counts[entity_type] = len(rows)
                    if entity_type == "workflow_checkpoint":
                        counts["checkpoint_state_bytes"] = sum(
                            len(str((getattr(row, "metadata", {}) or {}).get("state_json") or "").encode("utf-8"))
                            for row in rows
                        )
                except (AttributeError, TypeError, ValueError, NotImplementedError):
                    counts[entity_type] = -1
                if entity_type == "workflow_checkpoint":
                    counts.setdefault("checkpoint_state_bytes", -1)
    return counts


def _process_rss_bytes() -> int | None:
    try:
        import psutil
    except ImportError:
        return None
    return int(psutil.Process().memory_info().rss)


def _instrument_method_timings(
    target: object,
    method_names: tuple[str, ...],
) -> dict[str, list[float]]:
    """Measure selected synchronous calls without changing their results."""

    samples = {name: [] for name in method_names}
    lock = threading.Lock()
    for name in method_names:
        original = getattr(target, name, None)
        if not callable(original):
            continue

        def measured(*args: object, _name: str = name, _original: object = original,
                     **kwargs: object) -> object:
            started = time.perf_counter()
            try:
                return _original(*args, **kwargs)  # type: ignore[operator]
            finally:
                with lock:
                    samples[_name].append((time.perf_counter() - started) * 1000.0)

        setattr(target, name, measured)
    return samples


def run_profile(
    *,
    dsn: str,
    items: int,
    critic_delay_ms: float,
    token_budget: int,
    conversation_persistence_mode: str,
    checkpoint_every_n_steps: int,
    telemetry_mode: str,
    include_runtime_workflow: bool,
    lease_renew_interval_seconds: int,
) -> dict[str, Any]:
    from sqlalchemy import create_engine, text

    schema = f"profile_maintenance_{uuid.uuid4().hex[:16]}"
    admin = create_engine(dsn, pool_pre_ping=True)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    sql_metrics = _SqlStatementMetrics(admin)

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
    otel_provider: object | None = None
    otel_exporter: object | None = None
    runtime_persistence_times: dict[str, list[float]] = {}
    job_queue_times: dict[str, list[float]] = {}
    rss_before = _process_rss_bytes()
    try:
        started = time.perf_counter()
        engines = build_postgres_namespace_engines(
            base_dir=temp_root,
            dsn=dsn,
            schema=schema,
            conversation_persistence_mode=conversation_persistence_mode,
            embedding_function=DeterministicEmbedding(),
            embedding_dimension=2,
            embedding_provider="benchmark",
            embedding_model="deterministic-sha256-2d",
        )
        engine_startup_ms = (time.perf_counter() - started) * 1000.0
        sql_metrics.reset()

        def critic(_frame: object, _ctx: object) -> Mapping[str, object]:
            call_started = time.perf_counter()
            if critic_delay_ms:
                time.sleep(critic_delay_ms / 1000.0)
            critic_times.append((time.perf_counter() - call_started) * 1000.0)
            return {"status": "succeeded", "findings": []}

        if telemetry_mode == "enabled":
            otel_provider, otel_exporter = _install_counting_otel_exporter()

        worker = MaintenanceWorker(
            engines,
            worker_id="maintenance-profile-worker",
            observation_critic=critic,
            trace_sink=lambda event: traces.append(dict(event)),
        )
        worker.lease_seconds = 300
        worker.lease_renew_interval_seconds = lease_renew_interval_seconds
        worker.runtime.checkpoint_every_n_steps = checkpoint_every_n_steps
        runtime_persistence_times = _instrument_method_timings(
            worker.runtime,
            ("_persist_workflow_run", "_persist_step_exec", "_persist_checkpoint"),
        )
        job_queue_times = _instrument_method_timings(
            engines.conversation.jobs,
            ("claim", "mark_done", "renew_lease"),
        )

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
                "observation_token_budget": token_budget,
                "budgets": {"max_llm_calls": 1, "max_tokens": token_budget, "max_steps": 2},
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
            if include_runtime_workflow:
                runtime_job_id = f"maintenance-profile-distill-{index:05d}"
                runtime_payload = {
                    "workspace_id": workspace_id,
                    "request_node_id": runtime_job_id,
                    "source_document_id": logical_id,
                    "source_revision_id": revision_id,
                    "source_digest": digest,
                    "revision_document_id": revision_document_id,
                    "maintenance_kind": "distill",
                    "mode": "background",
                    "maintenance_candidates": [{"candidate_id": node_id}],
                    "budgets": {"max_llm_calls": 0, "max_tokens": 0, "max_steps": 4},
                }
                started = time.perf_counter()
                engines.conversation.jobs.enqueue(
                    job_id=runtime_job_id,
                    namespace=namespaces.maintenance_jobs,
                    entity_kind="maintenance_job",
                    entity_id=node_id,
                    job_kind="maintenance_job:distill",
                    payload=runtime_payload,
                )
                enqueue_times.append((time.perf_counter() - started) * 1000.0)

        process_started = time.perf_counter()
        worker.process_pending_jobs(workspace_id)
        processing_ms = (time.perf_counter() - process_started) * 1000.0

        runtime_otel_sink = getattr(worker.runtime, "_otel_sink", None)
        worker.close()
        worker = None

        materialization_started = time.perf_counter()
        materialized_jobs = engines.conversation.reconcile_indexes(max_jobs=100_000)
        materialization_ms = (time.perf_counter() - materialization_started) * 1000.0
        workflow_artifacts = _workflow_artifact_counts(engines.conversation, namespaces.conv_bg)
        if otel_provider is not None:
            force_flush = getattr(otel_provider, "force_flush", None)
            if callable(force_flush):
                force_flush()
        rss_after = _process_rss_bytes()

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
        observation_omitted_counts = [
            dict(event["omitted_counts"])
            for event in traces
            if event.get("event") == "maintenance_observation_completed"
            and isinstance(event.get("omitted_counts"), Mapping)
        ]
        queue_rows = engines.conversation.meta_sqlite.list_index_jobs(
            namespace=namespaces.maintenance_jobs,
            limit=(items * (2 if include_runtime_workflow else 1)) + 10,
        )
        status_counts: dict[str, int] = {}
        for row in queue_rows:
            status = str(getattr(row, "status", "unknown"))
            status_counts[status] = status_counts.get(status, 0) + 1
        expected_jobs = items * (2 if include_runtime_workflow else 1)

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
            "runtime_workflow_jobs_included": include_runtime_workflow,
            "lease_renew_interval_seconds": lease_renew_interval_seconds,
            "workspace_id": workspace_id,
            "embedding": "deterministic 2D SHA-256 projection; not a quality embedding",
            "critic": "fake structured observation critic; model inference excluded",
            "critic_delay_ms": critic_delay_ms,
            "observation_token_budget": token_budget,
            "conversation_persistence_mode": conversation_persistence_mode,
            "checkpoint_every_n_steps": checkpoint_every_n_steps,
            "checkpoint_frequency_note": (
                "non-default frequency is a test-only sensitivity comparison; "
                "production durability defaults remain unchanged"
            ),
            "telemetry": {
                "requested": telemetry_mode,
                "exporter": "in-process counting exporter" if otel_exporter is not None else "disabled",
                "runtime_sink_installed": runtime_otel_sink is not None,
                "spans_exported": int(getattr(otel_exporter, "span_count", 0)),
                "dropped_events": int(getattr(runtime_otel_sink, "dropped_events", 0)),
                "export_errors": int(getattr(runtime_otel_sink, "export_errors", 0)),
            },
            "timings": {
                "engine_startup_ms": engine_startup_ms,
                "per_subject_source_and_node_write": _summary(seed_write_times),
                "per_job_enqueue": _summary(enqueue_times),
                "per_fake_critic_call": _summary(critic_times),
                "per_job_worker_dispatch_trace": _summary(dispatch_ms),
                "queue_drain_total_ms": processing_ms,
                "conversation_index_materialization_ms": materialization_ms,
                "conversation_index_jobs_materialized": materialized_jobs,
                "postgres_statements": sql_metrics.result(),
                "durable_job_queue_operations": {
                    name: _summary(values) for name, values in job_queue_times.items()
                },
                "workflow_trace_and_checkpoint_persistence": {
                    name: _summary(values) for name, values in runtime_persistence_times.items()
                },
                "jobs_per_second": completed / (processing_ms / 1000.0) if processing_ms else 0.0,
            },
            "workflow_artifacts": workflow_artifacts,
            "process_memory": {
                "rss_before_bytes": rss_before,
                "rss_after_bytes": rss_after,
                "rss_delta_bytes": (
                    rss_after - rss_before
                    if rss_before is not None and rss_after is not None
                    else None
                ),
                "measurement": "psutil RSS samples; optional dependency",
            },
            "correctness": {
                "source_evidence_roundtrip": source_evidence_roundtrip,
                "jobs_enqueued": expected_jobs,
                "jobs_acknowledged": completed,
                "critic_successful_assessments": provider_success,
                "queue_status_counts": status_counts,
                "trace_events": len(traces),
                "trace_event_counts": trace_counts,
                "maintenance_lease_renewals": trace_counts.get("maintenance_lease_renewed", 0),
                "maintenance_lease_renewal_failures": trace_counts.get("maintenance_lease_renewal_failed", 0),
                "critic_skip_reasons": critic_skip_reasons,
                "observation_omitted_counts": observation_omitted_counts,
                "all_jobs_completed": completed == expected_jobs and provider_success == items,
            },
            "postgres_schema_table_bytes": table_bytes,
            "measurement_limits": [
                "No production embedding or maintenance model was invoked.",
                "OTel enabled runs use a local counting exporter, not Grafana/OTLP network transport.",
                "Maintenance jobs are processed by one worker; lane inbox concurrency is a separate workload.",
                "This run is a PostgreSQL-backed maintenance-path baseline, not a deployment-capacity claim.",
            ],
        }
    finally:
        if worker is not None:
            worker.close()
        if engines is not None:
            _close_engines(engines)
        sql_metrics.close()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()


def run_lane_inbox_profile(
    *,
    dsn: str,
    items: int,
    workers: int,
    conversation_persistence_mode: str,
) -> dict[str, Any]:
    """Measure authoritative lane-message publication separately from telemetry."""

    from kogwistar.server.auth_middleware import claims_ctx
    from sqlalchemy import create_engine, text

    schema = f"profile_lane_{uuid.uuid4().hex[:16]}"
    admin = create_engine(dsn, pool_pre_ping=True)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    sql_metrics = _SqlStatementMetrics(admin)
    temp_root = Path(tempfile.mkdtemp(prefix="llmw-lane-profile-"))
    workspace_id = f"lane-profile-{uuid.uuid4().hex[:8]}"
    namespaces = WorkspaceNamespaces(workspace_id)
    engines = None
    send_times: list[float] = []
    claim_batch_times: list[float] = []
    ack_times: list[float] = []
    namespace_scope_wait_times: list[float] = []
    namespace_scope_hold_times: list[float] = []
    timings_lock = threading.Lock()
    rss_before = _process_rss_bytes()
    try:
        engines = build_postgres_namespace_engines(
            base_dir=temp_root,
            dsn=dsn,
            schema=schema,
            conversation_persistence_mode=conversation_persistence_mode,
            embedding_function=DeterministicEmbedding(),
            embedding_dimension=2,
            embedding_provider="benchmark",
            embedding_model="deterministic-sha256-2d",
        )
        sql_metrics.reset()
        conversation = engines.conversation
        inbox_id = "inbox:maintenance-profile"
        message_ids: set[str] = set()
        send_claim = claims_ctx.set({"storage_ns": namespaces.conv_bg})
        try:
            with _temporary_namespace(conversation, namespaces.conv_bg):
                warmup_started = time.perf_counter()
                warmup_result = conversation.send_lane_message(
                    conversation_id=f"maintenance-profile:{workspace_id}",
                    inbox_id=inbox_id,
                    sender_id="lane:maintenance-profile-sender",
                    recipient_id="lane:maintenance-profile-worker",
                    msg_type="maintenance.profile.item",
                    purpose="maintenance",
                    payload={"workspace_id": workspace_id, "job_id": "warmup", "sequence": -1},
                    idempotency_key=f"lane-profile:{workspace_id}:warmup",
                )
                warmup_send_ms = (time.perf_counter() - warmup_started) * 1000.0
                message_ids.add(str(warmup_result.message_id))
                send_started = time.perf_counter()
                for index in range(items):
                    started = time.perf_counter()
                    result = conversation.send_lane_message(
                        conversation_id=f"maintenance-profile:{workspace_id}",
                        inbox_id=inbox_id,
                        sender_id="lane:maintenance-profile-sender",
                        recipient_id="lane:maintenance-profile-worker",
                        msg_type="maintenance.profile.item",
                        purpose="maintenance",
                        payload={
                            "workspace_id": workspace_id,
                            "job_id": f"lane-profile-{index:08d}",
                            "sequence": index,
                        },
                        idempotency_key=f"lane-profile:{workspace_id}:{index}",
                    )
                    message_ids.add(str(result.message_id))
                    send_times.append((time.perf_counter() - started) * 1000.0)
        finally:
            claims_ctx.reset(send_claim)
        send_total_ms = (time.perf_counter() - send_started) * 1000.0

        raw_projection_rows = conversation.meta_sqlite.list_projected_lane_messages(
            namespace=namespaces.conv_bg,
            inbox_id=inbox_id,
            limit=items + 2,
        )
        with _temporary_namespace(conversation, namespaces.conv_bg):
            visible_message_nodes = conversation.read.get_nodes(
                ids=[str(row.message_id) for row in raw_projection_rows],
                limit=items + 2,
            )

        def claim_and_ack(worker_index: int) -> int:
            token = claims_ctx.set({"storage_ns": namespaces.conv_bg})
            claimed_count = 0
            try:
                scope_started = time.perf_counter()
                with _background_namespace(conversation, namespaces.conv_bg):
                    scope_acquired = time.perf_counter()
                    with timings_lock:
                        namespace_scope_wait_times.append((scope_acquired - scope_started) * 1000.0)
                    try:
                        while True:
                            started = time.perf_counter()
                            rows = conversation.claim_projected_lane_messages(
                                inbox_id=inbox_id,
                                claimed_by=f"lane-profile-worker-{worker_index}",
                                limit=32,
                                lease_seconds=300,
                            )
                            elapsed_ms = (time.perf_counter() - started) * 1000.0
                            with timings_lock:
                                claim_batch_times.append(elapsed_ms)
                            if not rows:
                                return claimed_count
                            for row in rows:
                                started = time.perf_counter()
                                conversation.ack_projected_lane_message(
                                    message_id=str(row.message_id),
                                    claimed_by=f"lane-profile-worker-{worker_index}",
                                )
                                elapsed_ms = (time.perf_counter() - started) * 1000.0
                                with timings_lock:
                                    ack_times.append(elapsed_ms)
                                claimed_count += 1
                    finally:
                        with timings_lock:
                            namespace_scope_hold_times.append(
                                (time.perf_counter() - scope_acquired) * 1000.0
                            )
            finally:
                claims_ctx.reset(token)

        drain_started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as executor:
            results = list(executor.map(claim_and_ack, range(workers)))
        drain_ms = (time.perf_counter() - drain_started) * 1000.0
        token = claims_ctx.set({"storage_ns": namespaces.conv_bg})
        try:
            remaining = conversation.list_projected_lane_messages(
                inbox_id=inbox_id,
                limit=items + 1,
            )
        finally:
            claims_ctx.reset(token)
        completed_ids = {
            str(row.message_id)
            for row in remaining
            if str(getattr(row, "status", "")) == "completed"
        }
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
        rss_after = _process_rss_bytes()
        completed = sum(results)
        return {
            "schema_version": 1,
            "workload": "authoritative-lane-message-publication-and-inbox-drain",
            "backend": "Kogwistar PostgreSQL graph storage plus lane-message projection",
            "items": items,
            "warmup_messages": 1,
            "workers": workers,
            "claim_batch_size": 32,
            "conversation_persistence_mode": conversation_persistence_mode,
            "telemetry": {"requested": "disabled", "reason": "isolated lane workload"},
            "timings": {
                "message_publication_total_ms": send_total_ms,
                "warmup_message_publication_ms": warmup_send_ms,
                "per_message_publication": _summary(send_times),
                "inbox_drain_total_ms": drain_ms,
                "per_claim_batch": _summary(claim_batch_times),
                "per_acknowledgment": _summary(ack_times),
                "per_namespace_scope_acquisition_wait": _summary(namespace_scope_wait_times),
                "per_namespace_scope_hold": _summary(namespace_scope_hold_times),
                "messages_published_per_second": items / (send_total_ms / 1000.0) if send_total_ms else 0.0,
                "messages_claimed_and_acked_per_second": completed / (drain_ms / 1000.0) if drain_ms else 0.0,
                "postgres_statements": sql_metrics.result(),
            },
            "process_memory": {
                "rss_before_bytes": rss_before,
                "rss_after_bytes": rss_after,
                "rss_delta_bytes": (
                    rss_after - rss_before
                    if rss_before is not None and rss_after is not None
                    else None
                ),
                "measurement": "psutil RSS samples; optional dependency",
            },
            "lease_renewal": {
                "available_for_lane_messages": False,
                "note": "the current lane-message API supports claim, ack, requeue, and dead-letter; no explicit lease-renew operation was found",
            },
            "correctness": {
                "published_unique_ids_including_warmup": len(message_ids),
                "projection_rows_visible_before_claim": len(raw_projection_rows),
                "message_nodes_visible_before_claim": len(visible_message_nodes),
                "claimed_and_acknowledged": completed,
                "completed_projection_ids": len(completed_ids),
                "all_messages_completed_once": (
                    len(message_ids) == items + 1
                    and completed == items + 1
                    and completed_ids == message_ids
                ),
            },
            "postgres_schema_table_bytes": table_bytes,
            "measurement_limits": [
                "No LLM or embedding inference was used; lane-message publication is storage-path measurement.",
                "RSS is a before/after process sample, not a high-water mark.",
                "PostgreSQL lock-wait time is not directly instrumented; application namespace-scope wait is measured separately.",
            ],
        }
    finally:
        if engines is not None:
            _close_engines(engines)
        sql_metrics.close()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workload", choices=("maintenance", "lane_inbox"), default="maintenance")
    parser.add_argument("--dsn", default=os.getenv("KOGWISTAR_POSTGRES_DSN"))
    parser.add_argument("--items", type=int, default=100)
    parser.add_argument("--workers", type=int, choices=(1, 4, 16), default=1)
    parser.add_argument("--critic-delay-ms", type=float, default=0.0)
    parser.add_argument("--token-budget", type=int, default=4_000)
    parser.add_argument(
        "--conversation-persistence-mode",
        choices=("single_stage", "two_stage"),
        default="single_stage",
    )
    parser.add_argument("--checkpoint-every-n-steps", type=int, default=1)
    parser.add_argument("--lease-renew-interval-seconds", type=int, default=30)
    parser.add_argument(
        "--include-runtime-workflow",
        action="store_true",
        help="enqueue one no-LLM distillation workflow per subject to profile run/step/checkpoint persistence",
    )
    parser.add_argument("--otel", choices=("enabled", "disabled"), default="disabled")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.dsn:
        parser.error("--dsn or KOGWISTAR_POSTGRES_DSN is required")
    if args.items < 1 or args.items > 10_000:
        parser.error("--items must be between 1 and 10000")
    if args.critic_delay_ms < 0 or args.critic_delay_ms > 60_000:
        parser.error("--critic-delay-ms must be between 0 and 60000")
    if args.token_budget < 256 or args.token_budget > 100_000:
        parser.error("--token-budget must be between 256 and 100000")
    if args.checkpoint_every_n_steps < 1 or args.checkpoint_every_n_steps > 100:
        parser.error("--checkpoint-every-n-steps must be between 1 and 100")
    if args.lease_renew_interval_seconds < 1 or args.lease_renew_interval_seconds > 300:
        parser.error("--lease-renew-interval-seconds must be between 1 and 300")
    os.environ["LLM_WIKI_OTEL_ENABLED"] = "true" if args.otel == "enabled" else "false"
    if args.workload == "lane_inbox":
        result = run_lane_inbox_profile(
            dsn=args.dsn,
            items=args.items,
            workers=args.workers,
            conversation_persistence_mode=args.conversation_persistence_mode,
        )
    else:
        result = run_profile(
            dsn=args.dsn,
            items=args.items,
            critic_delay_ms=args.critic_delay_ms,
            token_budget=args.token_budget,
            conversation_persistence_mode=args.conversation_persistence_mode,
            checkpoint_every_n_steps=args.checkpoint_every_n_steps,
            telemetry_mode=args.otel,
            include_runtime_workflow=args.include_runtime_workflow,
            lease_renew_interval_seconds=args.lease_renew_interval_seconds,
        )
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    correctness_key = "all_jobs_completed" if args.workload == "maintenance" else "all_messages_completed_once"
    return 0 if result["correctness"][correctness_key] else 1


if __name__ == "__main__":
    raise SystemExit(main())
