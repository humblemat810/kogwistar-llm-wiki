"""Profile the durable LLM-Wiki maintenance path against disposable PostgreSQL.

By default this uses deterministic embeddings and a fake observation critic to
isolate graph persistence, queue/lease handling, bounded context reads, audit
publication, and acknowledgement. Optional local model modes include real
embedding inference and Ollama or OpenAI-compatible llama.cpp critics. The
runner creates a unique PostgreSQL schema and drops only that schema.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import os
import re
import shutil
import statistics
import tempfile
import threading
import time
import uuid
from collections import Counter
from collections.abc import Mapping
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from kg_doc_parser.workflow_ingest.providers import (
    ProviderEndpointConfig,
    WorkflowProviderSettings,
)
from kogwistar.engine_core.engine import GraphKnowledgeEngine
from kogwistar.engine_core.models import Document, Grounding, Node, Span
from sqlalchemy.exc import SQLAlchemyError

from kogwistar_llm_wiki.configuration.workspace import WorkspaceNamespaces
from kogwistar_llm_wiki.ingest_pipeline import build_postgres_namespace_engines
from kogwistar_llm_wiki.maintenance.observation_critic import build_observation_critic
from kogwistar_llm_wiki.maintenance.observation_evidence import (
    build_authoritative_source_evidence,
)
from kogwistar_llm_wiki.utils import _background_namespace, _temporary_namespace
from kogwistar_llm_wiki.worker import MaintenanceWorker

_LOCAL_OPENAI_API_KEY_ENV = "LLM_WIKI_PROFILE_LOCAL_OPENAI_API_KEY"


def _local_critic_provider_settings(
    *,
    provider: str,
    model: str,
    base_url: str,
    timeout_seconds: float,
    context_tokens: int,
    output_tokens: int,
) -> WorkflowProviderSettings:
    if provider not in {"ollama", "llama_cpp"}:
        raise ValueError(f"unsupported local critic provider: {provider}")
    provider_name = "ollama" if provider == "ollama" else "openai"
    configured_url = base_url
    api_key_env = None
    if provider == "llama_cpp":
        configured_url = base_url.rstrip("/")
        if not configured_url.endswith("/v1"):
            configured_url += "/v1"
        api_key_env = _LOCAL_OPENAI_API_KEY_ENV
        os.environ.setdefault(api_key_env, "local-no-auth")
    return WorkflowProviderSettings(
        parser=ProviderEndpointConfig(
            provider=provider_name,
            model=model,
            base_url=configured_url,
            api_key_env=api_key_env,
            timeout_seconds=timeout_seconds,
            max_output_tokens=output_tokens,
            ollama_num_ctx=context_tokens if provider == "ollama" else None,
            max_retries=0,
            temperature=0.0,
        )
    )


class DeterministicEmbedding:
    """Fast, fixed-width encoder used only to isolate service/storage work."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._calls = 0
        self._items = 0
        self._latencies_ms: list[float] = []
        self._invocations: list[dict[str, float | int]] = []

    def __call__(self, values: list[str]) -> list[list[float]]:
        started = time.perf_counter()
        vectors: list[list[float]] = []
        for value in values:
            digest = hashlib.sha256(str(value).encode("utf-8")).digest()
            vectors.append(
                [float(int.from_bytes(digest[:4], "big")), float(int.from_bytes(digest[4:8], "big"))]
            )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        with self._lock:
            self._calls += 1
            self._items += len(values)
            self._latencies_ms.append(elapsed_ms)
            self._invocations.append({"batch_items": len(values), "duration_ms": elapsed_ms})
        return vectors

    def metrics(self) -> dict[str, object]:
        with self._lock:
            latencies = list(self._latencies_ms)
            call_count = self._calls
            item_count = self._items
        return {
            "invocation_count": call_count,
            "item_count": item_count,
            "total_time_ms": sum(latencies),
            "per_invocation_ms": _summary(latencies),
            "invocations": self._invocations[:100],
            "invocations_truncated": len(self._invocations) > 100,
            "meaning": "deterministic synthetic embedding only; not model inference latency",
        }


class LocalE5SmallEmbedding:
    """Local-only multilingual-e5-small encoder for production-shaped profiles."""

    def __init__(self, *, device: str = "cpu") -> None:
        started = time.perf_counter()
        import torch
        from huggingface_hub import snapshot_download
        from transformers import AutoModel, AutoTokenizer

        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA was requested for the local embedding model but is unavailable")
        model_dir = snapshot_download(
            repo_id="intfloat/multilingual-e5-small", local_files_only=True
        )
        self._snapshot_revision = Path(model_dir).name
        self._torch = torch
        self._device = torch.device(device)
        self._tokenizer = AutoTokenizer.from_pretrained(
            model_dir, local_files_only=True
        )
        self._model = AutoModel.from_pretrained(
            model_dir, local_files_only=True
        ).to(self._device)
        self._model.eval()
        self.dimension = int(self._model.config.hidden_size)
        self._load_ms = (time.perf_counter() - started) * 1000.0
        self._lock = threading.Lock()
        self._calls = 0
        self._items = 0
        self._latencies_ms: list[float] = []

    def name(self) -> str:
        """Return cache identity including weights and pooling semantics."""
        return (
            "intfloat/multilingual-e5-small:"
            f"{self._snapshot_revision}:mean-pool-l2-v1:max512"
        )

    def __call__(self, values: list[str]) -> list[list[float]]:
        started = time.perf_counter()
        inputs = self._tokenizer(
            [f"passage: {value}" for value in values],
            padding=True,
            truncation=True,
            max_length=512,
            return_tensors="pt",
        )
        inputs = {key: value.to(self._device) for key, value in inputs.items()}
        with self._torch.inference_mode():
            hidden = self._model(**inputs).last_hidden_state
            mask = inputs["attention_mask"].unsqueeze(-1).to(hidden.dtype)
            pooled = (hidden * mask).sum(dim=1) / mask.sum(dim=1).clamp(min=1)
            vectors = self._torch.nn.functional.normalize(pooled, p=2, dim=1)
        result = vectors.cpu().tolist()
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        with self._lock:
            self._calls += 1
            self._items += len(values)
            self._latencies_ms.append(elapsed_ms)
        return result

    def metrics(self) -> dict[str, object]:
        with self._lock:
            latencies = list(self._latencies_ms)
            call_count = self._calls
            item_count = self._items
        return {
            "model": "intfloat/multilingual-e5-small",
            "model_snapshot": self._snapshot_revision,
            "device": str(self._device),
            "dimension": self.dimension,
            "max_sequence_length": 512,
            "model_load_ms": self._load_ms,
            "invocation_count": call_count,
            "item_count": item_count,
            "total_time_ms": sum(latencies),
            "per_invocation_ms": _summary(latencies),
            "meaning": "local Hugging Face model; snapshot resolution is local-files-only",
        }


class ConversationSemanticVisibilityProbe:
    """Verify semantic self-retrieval after synchronous lane-message sends."""

    def __init__(
        self,
        engine: GraphKnowledgeEngine,
        namespace: str,
    ) -> None:
        self._engine = engine
        self._namespace = namespace
        self._send_latencies_ms: list[float] = []
        self._verification_latencies_ms: list[float] = []
        self._visible_after_send = 0
        self._not_visible_after_send = 0
        self._lock = threading.Lock()
        self._errors = 0

    def record_send_result(self, message_id: str, send_latency_ms: float) -> None:
        """Measure the full send call, then verify visibility without a poller.

        ``send_lane_message`` may flush indexing before returning. A concurrent
        observer would contend on the engine's namespace-scoped lock and distort
        both the write latency and the visibility measurement.
        """
        verification_started = time.perf_counter()
        visible = False
        error = False
        try:
            visible = self._is_semantically_visible(str(message_id))
        except (AttributeError, RuntimeError, TypeError, ValueError, SQLAlchemyError):
            error = True
        verification_latency_ms = (time.perf_counter() - verification_started) * 1000.0
        with self._lock:
            self._send_latencies_ms.append(max(0.0, send_latency_ms))
            self._verification_latencies_ms.append(verification_latency_ms)
            self._visible_after_send += int(visible)
            self._not_visible_after_send += int(not visible)
            self._errors += int(error)

    def metrics(self) -> dict[str, object]:
        with self._lock:
            send_latencies = list(self._send_latencies_ms)
            verification_latencies = list(self._verification_latencies_ms)
            visible_after_send = self._visible_after_send
            not_visible_after_send = self._not_visible_after_send
            errors = self._errors
        return {
            "enabled": True,
            "messages_sent": len(send_latencies),
            "messages_semantically_visible_after_send": visible_after_send,
            "messages_not_visible_after_send": not_visible_after_send,
            "send_call_latency_ms": _summary(send_latencies),
            "post_send_semantic_query_latency_ms": _summary(verification_latencies),
            "measurement": (
                "synchronous post-return verification; send latency includes any "
                "index reconciliation performed before send_lane_message returns"
            ),
            "probe_errors": errors,
            "message_ids_persisted": False,
        }

    def _is_semantically_visible(self, message_id: str) -> bool:
        with _temporary_namespace(self._engine, self._namespace):
            nodes = list(self._engine.read.get_nodes(ids=[message_id], limit=1))
            if not nodes:
                return False
            embedding = getattr(nodes[0], "embedding", None)
            if embedding is None:
                return False
            result_batches = self._engine.read.query_nodes(
                query_embeddings=embedding,
                n_results=10,
            )
        return any(
            str(hit.id) == message_id
            for batch in result_batches
            for hit in batch
        )


_STREAMED_TRACE_FIELDS = (
    "event",
    "workflow_stage",
    "stage",
    "outcome",
    "status",
    "reason",
    "duration_ms",
    "job_id",
    "request_node_id",
    "mode",
    "claimed_count",
    "error_type",
    "error_code",
    "source_evidence_status",
)


def _trace_summary_line(event: Mapping[str, object], sequence: int) -> dict[str, object]:
    """Project worker traces to bounded operational fields, excluding payloads."""
    result: dict[str, object] = {
        "trace_sequence": sequence,
        "observed_at_utc": datetime.now(UTC).isoformat(timespec="milliseconds"),
    }
    for field in _STREAMED_TRACE_FIELDS:
        value = event.get(field)
        if isinstance(value, (str, int, float, bool)):
            result[field] = value
    assessment = event.get("assessment")
    if isinstance(assessment, Mapping):
        critic_status = assessment.get("critic_status")
        if isinstance(critic_status, str):
            result["critic_status"] = critic_status
        verdict = assessment.get("verdict")
        if verdict in {
            "adequate",
            "too_coarse",
            "too_fine",
            "coverage_gap",
            "overlap",
            "duplicate",
            "misgrouped",
            "weak_label",
            "ungrounded",
            "relation_unsupported",
            "relation_misplaced",
            "contradictory",
            "quality_unknown",
            "review_required",
        }:
            result["assessment_verdict"] = verdict
        recommended_action = assessment.get("recommended_action")
        if recommended_action in {
            "none",
            "review_parent",
            "expand_children",
            "retry_same_strategy",
            "switch_to_excerpt",
            "switch_to_boundary",
            "reparse_region",
            "propose_relation_patch",
            "validate_crosslinks",
            "request_human_review",
        }:
            result["recommended_action"] = recommended_action
        findings = assessment.get("findings")
        if isinstance(findings, (list, tuple)):
            result["finding_count"] = len(findings)
            severities = Counter(
                str(finding.get("severity"))
                for finding in findings
                if isinstance(finding, Mapping)
                and finding.get("severity") in {"info", "warning", "error"}
            )
            result["finding_severity_counts"] = dict(sorted(severities.items()))
    return result


def _assessment_quality_summary(traces: list[Mapping[str, object]]) -> dict[str, object]:
    """Aggregate allowlisted assessment labels without retaining finding content."""
    verdicts = {
        "adequate",
        "too_coarse",
        "too_fine",
        "coverage_gap",
        "overlap",
        "duplicate",
        "misgrouped",
        "weak_label",
        "ungrounded",
        "relation_unsupported",
        "relation_misplaced",
        "contradictory",
        "quality_unknown",
        "review_required",
    }
    actions = {
        "none",
        "review_parent",
        "expand_children",
        "retry_same_strategy",
        "switch_to_excerpt",
        "switch_to_boundary",
        "reparse_region",
        "propose_relation_patch",
        "validate_crosslinks",
        "request_human_review",
    }
    assessment_events = [
        event
        for event in traces
        if event.get("event") == "maintenance_observation_completed"
        and isinstance(event.get("assessment"), Mapping)
    ]
    verdict_counts: Counter[str] = Counter()
    action_counts: Counter[str] = Counter()
    severity_counts: Counter[str] = Counter()
    finding_verdict_counts: Counter[str] = Counter()
    finding_count = 0
    for event in assessment_events:
        assessment = event["assessment"]
        verdict = assessment.get("verdict")
        if verdict in verdicts:
            verdict_counts[str(verdict)] += 1
        action = assessment.get("recommended_action")
        if action in actions:
            action_counts[str(action)] += 1
        findings = assessment.get("findings")
        if not isinstance(findings, (list, tuple)):
            continue
        finding_count += len(findings)
        for finding in findings:
            if not isinstance(finding, Mapping):
                continue
            severity = finding.get("severity")
            if severity in {"info", "warning", "error"}:
                severity_counts[str(severity)] += 1
            finding_verdict = finding.get("verdict")
            if finding_verdict in verdicts:
                finding_verdict_counts[str(finding_verdict)] += 1
    return {
        "assessment_count": len(assessment_events),
        "verdict_counts": dict(sorted(verdict_counts.items())),
        "recommended_action_counts": dict(sorted(action_counts.items())),
        "finding_count": finding_count,
        "finding_severity_counts": dict(sorted(severity_counts.items())),
        "finding_verdict_counts": dict(sorted(finding_verdict_counts.items())),
        "finding_text_persisted": False,
    }


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
        self.templates: dict[str, dict[str, float | int]] = {}
        self._lock = threading.Lock()

        def before_execute(connection: object, _cursor: object, statement: str, _params: object,
                           context: object, _executemany: bool) -> None:
            if getattr(connection, "engine", None) is self._admin_engine:
                return
            operation = statement.lstrip().split(maxsplit=1)[0].upper() if statement.strip() else "EMPTY"
            template = re.sub(r"\s+", " ", statement).strip()
            template = re.sub(r"'(?:''|[^'])*'", "?", template)
            template = re.sub(
                r"profile_maintenance_[a-z0-9_]+",
                "profile_maintenance_<schema>",
                template,
                flags=re.IGNORECASE,
            )
            with self._lock:
                self.counts[operation] += 1
            context._profile_started_at = time.perf_counter()  # type: ignore[attr-defined]
            context._profile_sql_template = template  # type: ignore[attr-defined]

        def after_execute(connection: object, _cursor: object, _statement: str, _params: object,
                          context: object, _executemany: bool) -> None:
            if getattr(connection, "engine", None) is self._admin_engine:
                return
            started = getattr(context, "_profile_started_at", None)
            if isinstance(started, float):
                elapsed_ms = (time.perf_counter() - started) * 1000.0
                template = str(getattr(context, "_profile_sql_template", "<unknown>"))
                with self._lock:
                    self.latencies_ms.append(elapsed_ms)
                    metrics = self.templates.setdefault(
                        template,
                        {"count": 0, "total_ms": 0.0, "max_ms": 0.0},
                    )
                    metrics["count"] = int(metrics["count"]) + 1
                    metrics["total_ms"] = float(metrics["total_ms"]) + elapsed_ms
                    metrics["max_ms"] = max(float(metrics["max_ms"]), elapsed_ms)

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
            top_templates = sorted(
                (
                    {
                        "template": template[:600],
                        "count": int(metrics["count"]),
                        "total_ms": round(float(metrics["total_ms"]), 3),
                        "max_ms": round(float(metrics["max_ms"]), 3),
                    }
                    for template, metrics in self.templates.items()
                ),
                key=lambda item: float(item["total_ms"]),
                reverse=True,
            )[:25]
            return {
                "statement_count": sum(self.counts.values()),
                "statement_counts_by_verb": dict(sorted(self.counts.items())),
                "statement_latency_ms": _summary(list(self.latencies_ms)),
                "top_statement_templates_by_total_latency": top_templates,
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


_LANE_PROCESS_STATE: dict[str, Any] | None = None


def _initialize_lane_process_worker(
    config: tuple[str, str, str, str, int],
    ready_barrier: Any,
    work_barrier: Any,
) -> None:
    """Construct one process-local engine before the timed drain phase."""

    from sqlalchemy import create_engine

    global _LANE_PROCESS_STATE
    dsn, schema, temp_root, workspace_id, conversation_persistence_mode = config
    worker_index = os.getpid()
    worker_base_dir = Path(temp_root) / f"process-worker-{worker_index}"
    started = time.perf_counter()
    rss_before = _process_rss_bytes()
    engines = build_postgres_namespace_engines(
        base_dir=worker_base_dir,
        dsn=dsn,
        schema=schema,
        conversation_persistence_mode=conversation_persistence_mode,
        embedding_function=DeterministicEmbedding(),
        embedding_dimension=2,
        embedding_provider="benchmark",
        embedding_model="deterministic-sha256-2d",
    )
    engine_startup_ms = (time.perf_counter() - started) * 1000.0
    admin = create_engine(dsn, pool_pre_ping=True)
    sql_metrics = _SqlStatementMetrics(admin)
    _LANE_PROCESS_STATE = {
        "dsn": dsn,
        "workspace_id": workspace_id,
        "worker_index": worker_index,
        "engines": engines,
        "admin": admin,
        "sql_metrics": sql_metrics,
        "engine_startup_ms": engine_startup_ms,
        "rss_before_bytes": rss_before,
        "work_barrier": work_barrier,
    }
    ready_barrier.wait(timeout=180)


def _lane_process_worker_ready(_worker_index: int) -> dict[str, float | int]:
    if _LANE_PROCESS_STATE is None:
        raise RuntimeError("lane process worker was not initialized")
    return {
        "pid": os.getpid(),
        "engine_startup_ms": float(_LANE_PROCESS_STATE["engine_startup_ms"]),
    }


def _run_lane_inbox_process_worker(_worker_index: int) -> dict[str, Any]:
    """Claim and acknowledge a shared inbox in an already-started process."""

    from kogwistar.server.auth_middleware import claims_ctx

    state = _LANE_PROCESS_STATE
    if state is None:
        raise RuntimeError("lane process worker was not initialized")
    engines = state["engines"]
    conversation = engines.conversation
    namespace = WorkspaceNamespaces(str(state["workspace_id"])).conv_bg
    worker_index = int(state["worker_index"])
    sql_metrics = state["sql_metrics"]
    rss_before = state["rss_before_bytes"]
    claim_batch_times: list[float] = []
    ack_times: list[float] = []
    scope_wait_times: list[float] = []
    scope_hold_times: list[float] = []
    claimed_count = 0
    state["work_barrier"].wait(timeout=120)
    claims_token = claims_ctx.set({"storage_ns": namespace})
    try:
        scope_started = time.perf_counter()
        with _background_namespace(conversation, namespace):
            scope_acquired = time.perf_counter()
            scope_wait_times.append((scope_acquired - scope_started) * 1000.0)
            try:
                while True:
                    started = time.perf_counter()
                    rows = conversation.claim_projected_lane_messages(
                        inbox_id="inbox:maintenance-profile",
                        claimed_by=f"lane-profile-process-{worker_index}",
                        limit=32,
                        lease_seconds=300,
                    )
                    claim_batch_times.append((time.perf_counter() - started) * 1000.0)
                    if not rows:
                        break
                    for row in rows:
                        started = time.perf_counter()
                        conversation.ack_projected_lane_message(
                            message_id=str(row.message_id),
                            claimed_by=f"lane-profile-process-{worker_index}",
                        )
                        ack_times.append((time.perf_counter() - started) * 1000.0)
                        claimed_count += 1
            finally:
                scope_hold_times.append((time.perf_counter() - scope_acquired) * 1000.0)
    finally:
        claims_ctx.reset(claims_token)
    statement_metrics = sql_metrics.result()
    rss_after = _process_rss_bytes()
    return {
        "claimed_count": claimed_count,
        "engine_startup_ms": float(state["engine_startup_ms"]),
        "claim_batch_ms": claim_batch_times,
        "ack_ms": ack_times,
        "scope_wait_ms": scope_wait_times,
        "scope_hold_ms": scope_hold_times,
        "statement_count": statement_metrics["statement_count"],
        "statement_counts_by_verb": statement_metrics["statement_counts_by_verb"],
        "statement_latencies_ms": list(sql_metrics.latencies_ms),
        "rss_delta_bytes": (
            rss_after - rss_before
            if rss_before is not None and rss_after is not None
            else None
        ),
    }


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
    stream_trace: bool = False,
    progress_interval_seconds: float = 5.0,
    critic_provider: str = "fake",
    critic_model: str = "bonsai-parser-8k:latest",
    critic_base_url: str = "http://127.0.0.1:11434",
    critic_timeout_seconds: float = 300.0,
    critic_context_tokens: int = 2_048,
    critic_output_tokens: int = 256,
    embedding_provider: str = "deterministic",
    embedding_device: str = "cpu",
    measure_semantic_visibility: bool = False,
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
    visibility_probe: ConversationSemanticVisibilityProbe | None = None
    visibility_metrics: dict[str, object] | None = None
    index_backlog_before_reconcile: dict[str, object] | None = None
    otel_provider: object | None = None
    otel_exporter: object | None = None
    runtime_persistence_times: dict[str, list[float]] = {}
    job_queue_times: dict[str, list[float]] = {}
    phase_timings: dict[str, float] = {}
    trace_state_lock = threading.Lock()
    last_trace: dict[str, object] = {}
    last_trace_at = 0.0
    acknowledged_jobs = 0
    rss_before = _process_rss_bytes()
    try:
        embedding_load_started = time.perf_counter()
        if embedding_provider == "deterministic":
            embedding = DeterministicEmbedding()
            embedding_dimension = 2
            embedding_model = "deterministic-sha256-2d"
        elif embedding_provider == "multilingual-e5-small":
            embedding = LocalE5SmallEmbedding(device=embedding_device)
            embedding_dimension = embedding.dimension
            embedding_model = "intfloat/multilingual-e5-small"
        else:
            raise ValueError(f"unsupported profile embedding provider: {embedding_provider}")
        embedding_model_load_ms = (
            getattr(embedding, "_load_ms", 0.0)
            or (time.perf_counter() - embedding_load_started) * 1000.0
        )
        started = time.perf_counter()
        engines = build_postgres_namespace_engines(
            base_dir=temp_root,
            dsn=dsn,
            schema=schema,
            conversation_persistence_mode=conversation_persistence_mode,
            embedding_function=embedding,
            embedding_dimension=embedding_dimension,
            embedding_provider="fake",
            embedding_model=embedding_model,
        )
        engine_startup_ms = (time.perf_counter() - started) * 1000.0
        sql_metrics.reset()

        if measure_semantic_visibility:
            visibility_probe = ConversationSemanticVisibilityProbe(
                engines.conversation,
                namespaces.conv_bg,
            )
            send_message = engines.conversation.send_lane_message

            def track_message_admission(*args: object, **kwargs: object) -> object:
                admission_started = time.perf_counter()
                result = send_message(*args, **kwargs)
                message_id = getattr(result, "message_id", None)
                if message_id:
                    visibility_probe.record_send_result(
                        str(message_id),
                        (time.perf_counter() - admission_started) * 1000.0,
                    )
                return result

            engines.conversation.send_lane_message = track_message_admission

        if critic_provider in {"ollama", "llama_cpp"}:
            provider_settings = _local_critic_provider_settings(
                provider=critic_provider,
                model=critic_model,
                base_url=critic_base_url,
                timeout_seconds=critic_timeout_seconds,
                context_tokens=critic_context_tokens,
                output_tokens=critic_output_tokens,
            )
            provider_critic = build_observation_critic(provider_settings)
        elif critic_provider == "fake":
            provider_critic = None
        else:
            raise ValueError(f"unsupported profile critic provider: {critic_provider}")

        def critic(_frame: object, _ctx: object) -> Mapping[str, object]:
            call_started = time.perf_counter()
            try:
                if provider_critic is not None:
                    return provider_critic(_frame, _ctx)
                if critic_delay_ms:
                    time.sleep(critic_delay_ms / 1000.0)
                return {"status": "succeeded", "findings": []}
            finally:
                critic_times.append((time.perf_counter() - call_started) * 1000.0)

        if telemetry_mode == "enabled":
            otel_provider, otel_exporter = _install_counting_otel_exporter()

        worker = MaintenanceWorker(
            engines,
            worker_id="maintenance-profile-worker",
            observation_critic=critic,
            trace_sink=None,
        )
        def record_trace(event: Mapping[str, object]) -> None:
            nonlocal acknowledged_jobs, last_trace_at
            traces.append(dict(event))
            with trace_state_lock:
                last_trace.clear()
                last_trace.update(_trace_summary_line(event, len(traces)))
                last_trace_at = time.monotonic()
                if event.get("event") == "maintenance_job_acknowledged":
                    acknowledged_jobs += 1
            if stream_trace:
                print(
                    "TRACE "
                    + json.dumps(
                        _trace_summary_line(event, len(traces)),
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    flush=True,
                )

        worker.trace_sink = record_trace
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
        seed_phase_started = time.perf_counter()
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
        phase_timings["seed_and_enqueue_ms"] = (time.perf_counter() - seed_phase_started) * 1000.0

        process_started = time.perf_counter()
        progress_stop = threading.Event()
        progress_started = time.monotonic()
        process_cpu_started = time.process_time()

        def report_progress() -> None:
            while not progress_stop.wait(progress_interval_seconds):
                with trace_state_lock:
                    trace_snapshot = dict(last_trace)
                    event_age_seconds = (
                        round(time.monotonic() - last_trace_at, 1) if last_trace_at else None
                    )
                    completed_snapshot = acknowledged_jobs
                embedding_snapshot = embedding.metrics()
                print(
                    "PROGRESS "
                    + json.dumps(
                        {
                            "elapsed_seconds": round(time.monotonic() - progress_started, 1),
                            "process_cpu_seconds": round(time.process_time() - process_cpu_started, 1),
                            "requested_jobs": items,
                            "acknowledged_jobs": completed_snapshot,
                            "last_trace": trace_snapshot,
                            "seconds_since_last_trace": event_age_seconds,
                            "embedding_model": embedding_model,
                            "embedding_device": embedding_device,
                            "embedding_invocations": embedding_snapshot["invocation_count"],
                            "embedding_items": embedding_snapshot["item_count"],
                            "embedding_model_total_ms": round(
                                float(embedding_snapshot["total_time_ms"]), 3
                            ),
                            "rss_bytes": _process_rss_bytes(),
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    flush=True,
                )

        progress_thread: threading.Thread | None = None
        if stream_trace:
            progress_thread = threading.Thread(
                target=report_progress,
                name="maintenance-profile-progress",
                daemon=True,
            )
            progress_thread.start()
        try:
            worker.process_pending_jobs(workspace_id)
        finally:
            progress_stop.set()
            if progress_thread is not None:
                progress_thread.join(timeout=max(1.0, progress_interval_seconds + 1.0))
        processing_ms = (time.perf_counter() - process_started) * 1000.0
        phase_timings["worker_processing_ms"] = processing_ms

        runtime_otel_sink = getattr(worker.runtime, "_otel_sink", None)
        worker.close()
        worker = None

        backlog_rows = engines.conversation.meta_sqlite.list_index_jobs(
            namespace=namespaces.conv_bg,
            limit=max(100_000, items * 40),
        )
        pre_reconcile_status_counts: dict[str, int] = {}
        pending_embedding_ages_seconds: list[float] = []
        now_epoch = time.time()
        for row in backlog_rows:
            status = str(getattr(row, "status", "unknown"))
            index_kind = str(getattr(row, "index_kind", "unknown"))
            pre_reconcile_status_counts[status] = (
                pre_reconcile_status_counts.get(status, 0) + 1
            )
            if status == "PENDING" and index_kind == "node_embedding":
                try:
                    created_at = int(getattr(row, "created_at", 0))
                except (TypeError, ValueError):
                    continue
                if created_at > 0:
                    pending_embedding_ages_seconds.append(
                        max(0.0, now_epoch - created_at)
                    )
        index_backlog_before_reconcile = {
            "index_job_status_counts": pre_reconcile_status_counts,
            "pending_embedding_jobs": len(pending_embedding_ages_seconds),
            "oldest_pending_embedding_age_seconds": max(
                pending_embedding_ages_seconds,
                default=None,
            ),
        }
        materialization_started = time.perf_counter()
        materialized_jobs = engines.conversation.reconcile_indexes(
            max_jobs=100_000,
            namespace=namespaces.conv_bg,
        )
        materialization_ms = (time.perf_counter() - materialization_started) * 1000.0
        phase_timings["conversation_materialization_ms"] = materialization_ms
        if visibility_probe is not None:
            visibility_metrics = visibility_probe.metrics()
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
        maintenance_status_counts: dict[str, int] = {}
        for row in queue_rows:
            status = str(getattr(row, "status", "unknown"))
            maintenance_status_counts[status] = maintenance_status_counts.get(status, 0) + 1
        index_rows = engines.conversation.meta_sqlite.list_index_jobs(
            namespace=namespaces.conv_bg,
            limit=max(100_000, items * (2 if include_runtime_workflow else 1) * 40),
        )
        index_status_counts: dict[str, int] = {}
        index_kind_counts: dict[str, int] = {}
        for row in index_rows:
            status = str(getattr(row, "status", "unknown"))
            index_kind = str(getattr(row, "index_kind", "unknown"))
            index_status_counts[status] = index_status_counts.get(status, 0) + 1
            index_kind_counts[index_kind] = index_kind_counts.get(index_kind, 0) + 1

        semantic_visibility_verified = False
        with _temporary_namespace(engines.conversation, namespaces.conv_bg):
            conversation_nodes = engines.conversation.read.get_nodes(limit=100)
            query_node = next(
                (node for node in conversation_nodes if getattr(node, "embedding", None)),
                None,
            )
            if query_node is not None:
                vector_hits = engines.conversation.read.query_nodes(
                    query_embeddings=query_node.embedding,
                    n_results=5,
                )
                semantic_visibility_verified = any(
                    hit.id == query_node.id
                    for result_batch in vector_hits
                    for hit in result_batch
                )
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
            "embedding": embedding_model,
            "embedding_dimension": embedding_dimension,
            "embedding_model_load_ms": embedding_model_load_ms,
            "embedding_metrics": embedding.metrics(),
            "trace_streamed": stream_trace,
            "critic": (
                f"Ollama structured critic: {critic_model} at {critic_base_url}"
                if critic_provider == "ollama"
                else (
                    f"llama.cpp OpenAI-compatible JSON-schema critic: {critic_model} "
                    f"at {critic_base_url}"
                    if critic_provider == "llama_cpp"
                    else "fake structured observation critic; model inference excluded"
                )
            ),
            "critic_provider": critic_provider,
            "critic_model": critic_model if critic_provider != "fake" else None,
            "critic_structured_output": (
                "Pydantic ObservationCriticOutput via ChatOpenAI json_schema mode"
                if critic_provider == "llama_cpp"
                else "Pydantic ObservationCriticOutput via provider structured output"
            ),
            "critic_context_tokens": (
                critic_context_tokens if critic_provider in {"ollama", "llama_cpp"} else None
            ),
            "critic_output_tokens": (
                critic_output_tokens if critic_provider in {"ollama", "llama_cpp"} else None
            ),
            "critic_timeout_seconds": (
                critic_timeout_seconds if critic_provider in {"ollama", "llama_cpp"} else None
            ),
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
                "phases": phase_timings,
                "per_subject_source_and_node_write": _summary(seed_write_times),
                "per_job_enqueue": _summary(enqueue_times),
                "per_critic_call": _summary(critic_times),
                "per_job_worker_dispatch_trace": _summary(dispatch_ms),
                "queue_drain_total_ms": processing_ms,
                "conversation_index_materialization_ms": materialization_ms,
                "conversation_semantic_visibility": visibility_metrics,
                "index_backlog_before_reconcile": index_backlog_before_reconcile,
                "conversation_index_jobs_materialized": materialized_jobs,
                "conversation_index_job_status_counts": index_status_counts,
                "conversation_index_job_kind_counts": index_kind_counts,
                "semantic_visibility_verified": semantic_visibility_verified,
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
                "critic_quality_summary": _assessment_quality_summary(traces),
                "maintenance_queue_status_counts": maintenance_status_counts,
                "conversation_index_job_status_counts": index_status_counts,
                "conversation_index_job_kind_counts": index_kind_counts,
                "conversation_index_jobs_materialized": materialized_jobs,
                "semantic_visibility_verified": semantic_visibility_verified,
                "conversation_semantic_visibility": visibility_metrics,
                "index_backlog_before_reconcile": index_backlog_before_reconcile,
                "all_messages_semantically_visible_after_send": (
                    visibility_metrics["messages_not_visible_after_send"] == 0
                    and visibility_metrics["probe_errors"] == 0
                    if visibility_metrics is not None
                    else None
                ),
                "all_conversation_index_jobs_completed": (
                    not index_status_counts.get("PENDING", 0)
                    and not index_status_counts.get("DOING", 0)
                    and not index_status_counts.get("FAILED", 0)
                ),
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
                *(
                    ["Synthetic embeddings were used; embedding quality and model latency are excluded."]
                    if embedding_provider == "deterministic"
                    else ["Embedding model is local-only; results are specific to this model/device."]
                ),
                *(
                    ["A fake critic was used; maintenance model inference is excluded."]
                    if critic_provider == "fake"
                    else [
                        f"The local {critic_provider} critic was used; results are specific to its model/runtime."
                    ]
                ),
                "OTel enabled runs use a local counting exporter, not Grafana/OTLP network transport.",
                "Maintenance jobs are processed by one worker; lane inbox concurrency is a separate workload.",
                "This is a bounded PostgreSQL maintenance-path profile, not a deployment-capacity claim.",
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
        shutil.rmtree(temp_root, ignore_errors=True)


def run_lane_inbox_profile(
    *,
    dsn: str,
    items: int,
    workers: int,
    conversation_persistence_mode: str,
    worker_engine_topology: str,
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
    independent_worker_engines: list[object] = []
    worker_engine_startup_ms: list[float] = []
    worker_process_results: list[dict[str, Any]] = []
    worker_process_ids: list[int] = []
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
        if worker_engine_topology == "independent":
            for worker_index in range(workers):
                started = time.perf_counter()
                independent_worker_engines.append(
                    build_postgres_namespace_engines(
                        base_dir=temp_root / f"worker-{worker_index}",
                        dsn=dsn,
                        schema=schema,
                        conversation_persistence_mode=conversation_persistence_mode,
                        embedding_function=DeterministicEmbedding(),
                        embedding_dimension=2,
                        embedding_provider="benchmark",
                        embedding_model="deterministic-sha256-2d",
                    )
                )
                worker_engine_startup_ms.append((time.perf_counter() - started) * 1000.0)
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
            worker_engines = (
                independent_worker_engines
                if worker_engine_topology == "independent"
                else [engines] * workers
            )
            worker_engine = worker_engines[worker_index]
            worker_conversation = worker_engine.conversation
            token = claims_ctx.set({"storage_ns": namespaces.conv_bg})
            claimed_count = 0
            try:
                scope_started = time.perf_counter()
                with _background_namespace(worker_conversation, namespaces.conv_bg):
                    scope_acquired = time.perf_counter()
                    with timings_lock:
                        namespace_scope_wait_times.append((scope_acquired - scope_started) * 1000.0)
                    try:
                        while True:
                            started = time.perf_counter()
                            rows = worker_conversation.claim_projected_lane_messages(
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
                                worker_conversation.ack_projected_lane_message(
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

        if worker_engine_topology == "process":
            process_context = multiprocessing.get_context("spawn")
            ready_barrier = process_context.Barrier(workers)
            work_barrier = process_context.Barrier(workers)
            process_config = (
                dsn,
                schema,
                str(temp_root),
                workspace_id,
                conversation_persistence_mode,
            )
            process_startup_started = time.perf_counter()
            with ProcessPoolExecutor(
                max_workers=workers,
                mp_context=process_context,
                initializer=_initialize_lane_process_worker,
                initargs=(process_config, ready_barrier, work_barrier),
            ) as executor:
                ready_results = list(executor.map(_lane_process_worker_ready, range(workers)))
                worker_engine_startup_ms.extend(
                    float(result["engine_startup_ms"]) for result in ready_results
                )
                worker_process_ids = [int(result["pid"]) for result in ready_results]
                process_pool_ready_ms = (time.perf_counter() - process_startup_started) * 1000.0
                drain_started = time.perf_counter()
                worker_process_results = list(
                    executor.map(_run_lane_inbox_process_worker, range(workers))
                )
                drain_ms = (time.perf_counter() - drain_started) * 1000.0
            results = [int(result["claimed_count"]) for result in worker_process_results]
            for result in worker_process_results:
                claim_batch_times.extend(result["claim_batch_ms"])
                ack_times.extend(result["ack_ms"])
                namespace_scope_wait_times.extend(result["scope_wait_ms"])
                namespace_scope_hold_times.extend(result["scope_hold_ms"])
        else:
            process_pool_ready_ms = None
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
        postgres_metrics = sql_metrics.result()
        worker_process_rss_deltas = [
            int(result["rss_delta_bytes"])
            for result in worker_process_results
            if isinstance(result.get("rss_delta_bytes"), int)
        ]
        if worker_process_results:
            statement_counts = Counter(postgres_metrics["statement_counts_by_verb"])
            statement_latencies = list(sql_metrics.latencies_ms)
            for result in worker_process_results:
                statement_counts.update(result["statement_counts_by_verb"])
                statement_latencies.extend(result["statement_latencies_ms"])
            postgres_metrics = {
                "statement_count": sum(statement_counts.values()),
                "statement_counts_by_verb": dict(sorted(statement_counts.items())),
                "statement_latency_ms": _summary(statement_latencies),
            }
        return {
            "schema_version": 1,
            "workload": "authoritative-lane-message-publication-and-inbox-drain",
            "backend": "Kogwistar PostgreSQL graph storage plus lane-message projection",
            "items": items,
            "warmup_messages": 1,
            "workers": workers,
            "worker_engine_topology": worker_engine_topology,
            "worker_process_count": len(set(worker_process_ids)),
            "claim_batch_size": 32,
            "conversation_persistence_mode": conversation_persistence_mode,
            "telemetry": {"requested": "disabled", "reason": "isolated lane workload"},
            "timings": {
                "independent_worker_engine_startup": _summary(worker_engine_startup_ms),
                "process_pool_ready_ms": process_pool_ready_ms,
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
                "postgres_statements": postgres_metrics,
            },
            "process_memory": {
                "rss_before_bytes": rss_before,
                "rss_after_bytes": rss_after,
                "rss_delta_bytes": (
                    rss_after - rss_before
                    if rss_before is not None and rss_after is not None
                    else None
                ),
                "worker_process_rss_delta_bytes": worker_process_rss_deltas,
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
                    and (
                        worker_engine_topology != "process"
                        or len(set(worker_process_ids)) == workers
                    )
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
        for worker_engine in independent_worker_engines:
            _close_engines(worker_engine)
        sql_metrics.close()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        admin.dispose()
        shutil.rmtree(temp_root, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workload", choices=("maintenance", "lane_inbox"), default="maintenance")
    parser.add_argument("--dsn", default=os.getenv("KOGWISTAR_POSTGRES_DSN"))
    parser.add_argument("--items", type=int, default=100)
    parser.add_argument("--workers", type=int, choices=(1, 4, 16), default=1)
    parser.add_argument(
        "--worker-engine-topology",
        choices=("shared", "independent", "process"),
        default="shared",
        help="share one engine, create per-thread engines, or run workers in independent processes",
    )
    parser.add_argument("--critic-delay-ms", type=float, default=0.0)
    parser.add_argument(
        "--critic-provider",
        choices=("fake", "ollama", "llama_cpp"),
        default="fake",
        help="use a synthetic, Ollama, or OpenAI-compatible llama.cpp structured-output critic",
    )
    parser.add_argument("--critic-model", default="bonsai-parser-8k:latest")
    parser.add_argument("--critic-base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--critic-timeout-seconds", type=float, default=300.0)
    parser.add_argument(
        "--critic-output-tokens",
        type=int,
        default=256,
        help="completion token cap passed to the local critic provider",
    )
    parser.add_argument(
        "--critic-context-tokens",
        type=int,
        default=2_048,
        help="reported local-model context size; for Ollama this also configures num_ctx",
    )
    parser.add_argument(
        "--embedding-provider",
        choices=("deterministic", "multilingual-e5-small"),
        default="deterministic",
        help="use synthetic vectors or cached local intfloat/multilingual-e5-small",
    )
    parser.add_argument("--embedding-device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument(
        "--measure-semantic-visibility",
        action="store_true",
        help="measure full lane-message send duration and verify semantic self-retrieval after return",
    )
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
    parser.add_argument(
        "--stream-trace",
        action="store_true",
        help="emit bounded worker trace summaries as they occur (maintenance workload only)",
    )
    parser.add_argument(
        "--progress-interval-seconds",
        type=float,
        default=5.0,
        help="heartbeat interval when --stream-trace is enabled",
    )
    args = parser.parse_args()
    if not args.dsn:
        parser.error("--dsn or KOGWISTAR_POSTGRES_DSN is required")
    if args.items < 1 or args.items > 10_000:
        parser.error("--items must be between 1 and 10000")
    if args.critic_delay_ms < 0 or args.critic_delay_ms > 60_000:
        parser.error("--critic-delay-ms must be between 0 and 60000")
    if args.critic_timeout_seconds <= 0 or args.critic_timeout_seconds > 3600:
        parser.error("--critic-timeout-seconds must be between 0 and 3600")
    if args.critic_output_tokens < 32 or args.critic_output_tokens > 16_384:
        parser.error("--critic-output-tokens must be between 32 and 16384")
    if args.critic_context_tokens < 256 or args.critic_context_tokens > 32_768:
        parser.error("--critic-context-tokens must be between 256 and 32768")
    if args.critic_provider != "fake" and args.critic_delay_ms:
        parser.error("--critic-delay-ms only applies to the fake critic")
    if args.workload == "lane_inbox" and (
        args.critic_provider != "fake"
        or args.embedding_provider != "deterministic"
    ):
        parser.error("local model options apply only to the maintenance workload")
    if args.token_budget < 256 or args.token_budget > 100_000:
        parser.error("--token-budget must be between 256 and 100000")
    if args.checkpoint_every_n_steps < 1 or args.checkpoint_every_n_steps > 100:
        parser.error("--checkpoint-every-n-steps must be between 1 and 100")
    if args.lease_renew_interval_seconds < 1 or args.lease_renew_interval_seconds > 300:
        parser.error("--lease-renew-interval-seconds must be between 1 and 300")
    if args.progress_interval_seconds < 0.2 or args.progress_interval_seconds > 300:
        parser.error("--progress-interval-seconds must be between 0.2 and 300")
    os.environ["LLM_WIKI_OTEL_ENABLED"] = "true" if args.otel == "enabled" else "false"
    if args.workload == "lane_inbox":
        result = run_lane_inbox_profile(
            dsn=args.dsn,
            items=args.items,
            workers=args.workers,
            conversation_persistence_mode=args.conversation_persistence_mode,
            worker_engine_topology=args.worker_engine_topology,
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
            stream_trace=args.stream_trace,
            progress_interval_seconds=args.progress_interval_seconds,
            critic_provider=args.critic_provider,
            critic_model=args.critic_model,
            critic_base_url=args.critic_base_url,
            critic_timeout_seconds=args.critic_timeout_seconds,
            critic_context_tokens=args.critic_context_tokens,
            critic_output_tokens=args.critic_output_tokens,
            embedding_provider=args.embedding_provider,
            embedding_device=args.embedding_device,
            measure_semantic_visibility=args.measure_semantic_visibility,
        )
    rendered = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    correctness_key = "all_jobs_completed" if args.workload == "maintenance" else "all_messages_completed_once"
    if args.workload == "maintenance":
        return 0 if (
            result["correctness"][correctness_key]
            and result["correctness"]["all_conversation_index_jobs_completed"]
            and result["correctness"]["semantic_visibility_verified"]
            and (
                not args.measure_semantic_visibility
                or result["correctness"]["all_messages_semantically_visible_after_send"]
            )
        ) else 1
    return 0 if result["correctness"][correctness_key] else 1


if __name__ == "__main__":
    raise SystemExit(main())
