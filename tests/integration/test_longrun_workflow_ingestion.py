from __future__ import annotations

import json
import os
import re
import shutil
import time
import zipfile
from collections import Counter, defaultdict
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from kg_doc_parser.workflow_ingest.page_index import parse_page_index_document
from kg_doc_parser.workflow_ingest.providers import (
    EmbeddingProviderConfig,
    ProviderEndpointConfig,
    WorkflowProviderSettings,
)

from kogwistar.id_provider import stable_id
from kogwistar.runtime import MappingStepResolver
from kogwistar.runtime.models import RunSuccess, WorkflowEdge, WorkflowNode
from kogwistar.runtime.runtime import WorkflowRuntime
from kogwistar.engine_core.models import Grounding, Span

from kogwistar_llm_wiki import IngestPipeline, IngestPipelineRequest
from kogwistar_llm_wiki.ingest_pipeline import build_in_memory_namespace_engines
from kogwistar_llm_wiki.maintenance_designs import materialize_maintenance_designs
from kogwistar_llm_wiki.namespaces import WorkspaceNamespaces
from kogwistar_llm_wiki.projection_worker import ProjectionWorker
from kogwistar_llm_wiki.utils import _temporary_namespace
from kogwistar_llm_wiki.worker import MaintenanceWorker


STATUSES = {
    "PENDING",
    "CLAIMED",
    "TOKEN_CHECKED",
    "PARSED",
    "PERSISTED",
    "MAINTENANCE_ENQUEUED",
    "MAINTENANCE_OBSERVED",
    "COMPLETED",
    "FAILED",
    "QUARANTINED",
}
TERMINAL_STATES = {"COMPLETED", "FAILED", "QUARANTINED"}
TOKENIZER_METHOD = "regex_non_whitespace_v1"
WORKFLOW_ID = "llm_wiki.longrun_ingestion.v1"
WORKFLOW_STEPS = [
    "claim_document",
    "token_check",
    "parse_document",
    "persist_document",
    "enqueue_background_maintenance",
    "observe_background_maintenance",
    "verify_document_artifacts",
    "move_completed",
]

RECOVERABLE_DOCUMENT_FAILURES = {
    "token_count_out_of_range",
    "document_parse_failed",
    "document_persist_failed_after_retries",
    "maintenance_artifact_missing_for_doc",
}
RECOVERABLE_LLM_QUALITY_FAILURES = {
    "llm_invalid_json",
    "llm_unsupported_citation",
    "llm_ungrounded_output",
    "llm_contradicts_source",
    "llm_empty_or_low_confidence_output",
}
SYSTEMIC_FAILURES = {
    "database_write_repeated_failure",
    "graph_invariant_violation",
    "projection_repair_failure",
    "runtime_worker_stuck",
    "ollama_unavailable_repeatedly",
    "same_error_repeated_across_unrelated_docs",
}


class LongRunDocumentError(RuntimeError):
    """Document-scoped failure raised from a workflow step."""

    def __init__(self, code: str, message: str, *, phase: str) -> None:
        super().__init__(message)
        self.code = code
        self.phase = phase


class LongRunSystemicError(RuntimeError):
    """Abort-class failure raised when infrastructure or invariants look broken."""

    def __init__(self, code: str, message: str, *, phase: str) -> None:
        super().__init__(message)
        self.code = code
        self.phase = phase


@dataclass(frozen=True)
class LongRunConfig:
    enabled: bool
    doc_count: int
    ollama_model: str
    ollama_base_url: str
    max_repeated_systemic_errors: int
    max_post_doc_maintenance_steps: int
    max_idle_loops: int = 25
    max_runtime_seconds: int = 3600
    token_min: int = 500
    token_max: int = 2000
    workspace_id: str = "longrun"

    @classmethod
    def from_env(cls) -> "LongRunConfig":
        doc_count = int(os.getenv("KOGWISTAR_LONGRUN_DOC_COUNT", "20"))
        if doc_count < 20 and os.getenv("KOGWISTAR_LONGRUN_ALLOW_SMALL") != "1":
            raise ValueError(
                "KOGWISTAR_LONGRUN_DOC_COUNT must be at least 20 unless "
                "KOGWISTAR_LONGRUN_ALLOW_SMALL=1 is set"
            )
        return cls(
            enabled=os.getenv("KOGWISTAR_LLM_WIKI_LONGRUN") == "1",
            doc_count=doc_count,
            ollama_model=os.getenv("KOGWISTAR_OLLAMA_MODEL", "gemma4:e2b"),
            ollama_base_url=os.getenv("KOGWISTAR_OLLAMA_BASE_URL", "http://localhost:11434"),
            max_repeated_systemic_errors=int(
                os.getenv("KOGWISTAR_LONGRUN_MAX_REPEATED_SYSTEMIC_ERRORS", "3")
            ),
            max_post_doc_maintenance_steps=int(
                os.getenv("KOGWISTAR_LONGRUN_MAX_POST_DOC_MAINTENANCE_STEPS", "100")
            ),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "doc_count": self.doc_count,
            "ollama_model": self.ollama_model,
            "ollama_base_url": self.ollama_base_url,
            "max_repeated_systemic_errors": self.max_repeated_systemic_errors,
            "max_post_doc_maintenance_steps": self.max_post_doc_maintenance_steps,
            "max_idle_loops": self.max_idle_loops,
            "max_runtime_seconds": self.max_runtime_seconds,
            "token_min": self.token_min,
            "token_max": self.token_max,
            "tokenizer_method": TOKENIZER_METHOD,
            "workspace_id": self.workspace_id,
        }


@dataclass
class DocumentRecord:
    doc_id: str
    title: str
    source_uri: str
    input_path: Path
    current_path: Path
    status: str = "PENDING"
    token_count: int | None = None
    run_id: str | None = None
    source_document_id: str | None = None
    maintenance_job_id: str | None = None
    candidate_link_id: str | None = None
    promotion_candidate_id: str | None = None
    promoted_entity_id: str | None = None
    parse_result: Any | None = field(default=None, repr=False)
    graph_extraction: Any | None = field(default=None, repr=False)
    llm_quality_failures: list[str] = field(default_factory=list)


@dataclass
class FailureRecord:
    run_id: str
    doc_id: str | None
    phase: str
    code: str
    scope: str
    message: str
    fingerprint: str
    timestamp_ms: int


class ErrorCircuitBreaker:
    """Tracks normalized systemic failures across unrelated documents."""

    def __init__(self, threshold: int) -> None:
        self.threshold = threshold
        self._docs_by_fingerprint: dict[str, set[str]] = defaultdict(set)
        self._counts: Counter[str] = Counter()

    def record(self, failure: FailureRecord) -> bool:
        if failure.scope != "systemic":
            return False
        self._counts[failure.fingerprint] += 1
        if failure.doc_id:
            self._docs_by_fingerprint[failure.fingerprint].add(failure.doc_id)
        unrelated_count = len(self._docs_by_fingerprint[failure.fingerprint])
        return unrelated_count > self.threshold or self._counts[failure.fingerprint] > self.threshold

    def as_dict(self) -> dict[str, Any]:
        return {
            "threshold": self.threshold,
            "counts": dict(self._counts),
            "documents_by_fingerprint": {
                key: sorted(value) for key, value in self._docs_by_fingerprint.items()
            },
        }


class DiagnosticDumper:
    """Writes an uploadable long-run diagnostic bundle from current harness state."""

    def __init__(self, run_dir: Path, harness: "LongRunHarness") -> None:
        self.run_dir = run_dir
        self.harness = harness
        self.dump_dir = run_dir / "dump"
        self.dump_dir.mkdir(parents=True, exist_ok=True)

    def dump(self, *, reason: str, final: bool = False) -> Path:
        self.dump_dir.mkdir(parents=True, exist_ok=True)
        self._write_json("run_config.json", self.harness.config.as_dict())
        self._write_jsonl("manifest.jsonl", [self.harness.manifest_row(record) for record in self.harness.records])
        self._write_jsonl("status_transitions.jsonl", self.harness.status_transitions)
        self._write_jsonl("failure_records.jsonl", [record.__dict__ for record in self.harness.failure_records])
        self._write_json("error_fingerprints.json", self.harness.circuit_breaker.as_dict())
        self._write_json("folder_inventory.json", self.harness.folder_inventory())
        self._write_json("graph_export.json", self.harness.graph_export())
        self._write_json("projection_summary.json", self.harness.projection_summary())
        self._write_json("maintenance_summary.json", self.harness.maintenance_summary())
        self._write_json("llm_calls_summary.json", self.harness.llm_summary())
        self._write_jsonl("sampled_prompts_and_responses.jsonl", [])
        self._copy_raw_documents()
        self._write_report(reason=reason)
        if final:
            zip_path = self.run_dir / "longrun-dump.zip"
            with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in self.dump_dir.rglob("*"):
                    if path.is_file():
                        archive.write(path, path.relative_to(self.dump_dir))
            self._write_json("dump_package.json", {"zip_path": str(zip_path)})
        return self.dump_dir

    def _write_json(self, name: str, payload: Any) -> None:
        (self.dump_dir / name).write_text(
            json.dumps(_jsonable(payload), indent=2, sort_keys=True),
            encoding="utf-8",
        )

    def _write_jsonl(self, name: str, rows: list[dict[str, Any]]) -> None:
        path = self.dump_dir / name
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(_jsonable(row), sort_keys=True) + "\n")

    def _copy_raw_documents(self) -> None:
        target = self.dump_dir / "raw_documents"
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        for record in self.harness.records:
            if record.current_path.exists():
                shutil.copy2(record.current_path, target / record.current_path.name)

    def _write_report(self, *, reason: str) -> None:
        counts = Counter(record.status for record in self.harness.records)
        lines = [
            "# Long-Run Workflow Diagnostic Report",
            "",
            f"- Reason: `{reason}`",
            f"- Run id: `{self.harness.run_id}`",
            f"- Workspace: `{self.harness.config.workspace_id}`",
            f"- Dump directory: `{self.dump_dir}`",
            f"- Dump zip: `{self.run_dir / 'longrun-dump.zip'}`",
            "",
            "## Document Counts",
            "",
        ]
        for status in sorted(STATUSES):
            lines.append(f"- {status}: {counts.get(status, 0)}")
        lines.extend(
            [
                "",
                "## Repeated Errors",
                "",
                f"```json\n{json.dumps(self.harness.circuit_breaker.as_dict(), indent=2, sort_keys=True)}\n```",
                "",
                "## LLM Quality Failures",
                "",
            ]
        )
        quality = [
            {"doc_id": record.doc_id, "failures": record.llm_quality_failures}
            for record in self.harness.records
            if record.llm_quality_failures
        ]
        lines.append(f"```json\n{json.dumps(quality, indent=2, sort_keys=True)}\n```")
        lines.extend(
            [
                "",
                "## Maintenance Summary",
                "",
                f"```json\n{json.dumps(_jsonable(self.harness.maintenance_summary()), indent=2, sort_keys=True)}\n```",
                "",
                "## Projection Summary",
                "",
                f"```json\n{json.dumps(_jsonable(self.harness.projection_summary()), indent=2, sort_keys=True)}\n```",
            ]
        )
        (self.dump_dir / "final_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


class LongRunHarness:
    """Single-daemon-style test harness around runtime workflow ingestion."""

    def __init__(self, *, run_dir: Path, config: LongRunConfig) -> None:
        self.run_dir = run_dir
        self.config = config
        self.run_id = f"longrun-{stable_id('llm_wiki.longrun', str(run_dir), config.doc_count)}"
        self.records: list[DocumentRecord] = []
        self.contexts: dict[str, DocumentRecord] = {}
        self.status_transitions: list[dict[str, Any]] = []
        self.failure_records: list[FailureRecord] = []
        self.circuit_breaker = ErrorCircuitBreaker(config.max_repeated_systemic_errors)
        self.maintenance_poll_count = 0
        self.projection_poll_count = 0
        self.aborted = False
        self.abort_reason: str | None = None
        self.engines = build_in_memory_namespace_engines(run_dir / "engines")
        self.pipeline = IngestPipeline(self.engines)
        self.pipeline.parser = self._build_parser()
        self.maintenance_worker = MaintenanceWorker(self.engines)
        self.projection_worker = ProjectionWorker(self.engines)
        self.dumper = DiagnosticDumper(run_dir, self)

    def prepare(self) -> None:
        for name in ("input", "processing", "completed", "failed", "quarantine", "dump"):
            (self.run_dir / name).mkdir(parents=True, exist_ok=True)
        materialize_maintenance_designs(self.engines.workflow)
        self._materialize_workflow_design()
        self._generate_corpus()
        self.dumper.dump(reason="prepared")

    def run(self) -> None:
        started = time.monotonic()
        idle_loops = 0
        self.dumper.dump(reason="run_started")
        for record in self.records:
            if self.aborted:
                break
            previous_state = self._state_signature()
            try:
                self._run_document_workflow(record)
            except Exception as exc:  # noqa: BLE001
                failure = self._classify_exception(exc, doc_id=record.doc_id, phase="runtime")
                self._record_failure(failure)
                if failure.scope == "systemic" and self.circuit_breaker.record(failure):
                    self._abort(f"circuit breaker tripped: {failure.fingerprint}")
                    break
                self._move_failed_or_quarantine(record, failure)
            self._poll_maintenance_once(phase="document_loop")
            if self._state_signature() == previous_state:
                idle_loops += 1
                if idle_loops > self.config.max_idle_loops:
                    self._abort("runtime_worker_stuck: no state changes while work remained")
                    break
            else:
                idle_loops = 0
            if time.monotonic() - started > self.config.max_runtime_seconds:
                self._abort("runtime_worker_stuck: max runtime exceeded")
                break

        if self.aborted:
            self.dumper.dump(reason="abort_snapshot")
            self._quarantine_processing_docs()
            self.dumper.dump(reason="abort_finalized", final=True)
            raise AssertionError(self.abort_reason or "long-run workflow aborted")

        self._drain_maintenance_after_documents()
        self._poll_projection_once()
        self._verify_run_invariants()
        self.dumper.dump(reason="success", final=True)

    def manifest_row(self, record: DocumentRecord) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "doc_id": record.doc_id,
            "title": record.title,
            "source_uri": record.source_uri,
            "current_path": str(record.current_path),
            "status": record.status,
            "token_count": record.token_count,
            "tokenizer_method": TOKENIZER_METHOD,
            "source_document_id": record.source_document_id,
            "maintenance_job_id": record.maintenance_job_id,
            "promoted_entity_id": record.promoted_entity_id,
            "llm_quality_failures": list(record.llm_quality_failures),
        }

    def folder_inventory(self) -> dict[str, list[str]]:
        return {
            name: sorted(path.name for path in (self.run_dir / name).glob("*") if path.is_file())
            for name in ("input", "processing", "completed", "failed", "quarantine", "dump")
        }

    def graph_export(self) -> dict[str, Any]:
        workspace_id = self.config.workspace_id
        ns = WorkspaceNamespaces(workspace_id)
        return {
            "conversation_fg": self._export_engine(
                self.engines.conversation,
                where={"workspace_id": workspace_id},
                namespace=ns.conv_fg,
            ),
            "conversation_bg": self._export_engine(
                self.engines.conversation,
                where={"workspace_id": workspace_id},
                namespace=ns.conv_bg,
            ),
            "kg": self._export_engine(
                self.engines.kg,
                where={"workspace_id": workspace_id},
                namespace=ns.kg,
            ),
            "derived_knowledge": self._export_engine(
                self.engines.derived_knowledge_engine(),
                where={"workspace_id": workspace_id},
                namespace=ns.derived_knowledge,
            ),
            "workflow_events": self._export_engine(
                self.engines.conversation,
                where={"workflow_id": WORKFLOW_ID},
                namespace=ns.conv_bg,
            ),
        }

    def maintenance_summary(self) -> dict[str, Any]:
        ns = WorkspaceNamespaces(self.config.workspace_id)
        jobs = self.engines.conversation.meta_sqlite.list_index_jobs(
            namespace=ns.maintenance_jobs,
            limit=10_000,
        )
        with _temporary_namespace(self.engines.conversation, ns.conv_bg):
            lane_rows = self.engines.conversation.list_projected_lane_messages(
                inbox_id="inbox:worker:maintenance"
            )
            replies = self.engines.conversation.list_projected_lane_messages(
                inbox_id="inbox:foreground"
            )
            steps = self.engines.conversation.read.get_nodes(
                where={"entity_type": "workflow_step_exec", "workspace_id": self.config.workspace_id},
                limit=10_000,
            )
        with _temporary_namespace(self.engines.derived_knowledge_engine(), ns.derived_knowledge):
            derived = self.engines.derived_knowledge_engine().read.get_nodes(
                where={"artifact_kind": "derived_knowledge", "workspace_id": self.config.workspace_id},
                limit=10_000,
            )
        return {
            "maintenance_poll_count": self.maintenance_poll_count,
            "job_status_counts": dict(Counter(str(job.status) for job in jobs)),
            "jobs": [_job_to_dict(job) for job in jobs],
            "maintenance_lane_messages": [_lane_row_to_dict(row) for row in lane_rows],
            "foreground_replies": [_lane_row_to_dict(row) for row in replies],
            "workflow_step_count": len(steps),
            "derived_artifact_count": len(derived),
            "derived_artifact_ids": [str(node.id) for node in derived],
        }

    def projection_summary(self) -> dict[str, Any]:
        try:
            snapshot = self.pipeline.build_projection_snapshot(self.config.workspace_id)
            entity_count = len(snapshot.entities)
            entity_ids = [entity.kg_id for entity in snapshot.entities]
            status = "ok"
            error = None
        except Exception as exc:  # noqa: BLE001
            entity_count = 0
            entity_ids = []
            status = "failed"
            error = f"{type(exc).__name__}: {exc}"
        ns = WorkspaceNamespaces(self.config.workspace_id)
        row = self.engines.conversation.meta_sqlite.get_named_projection(
            ns.projection_manifest,
            self.config.workspace_id,
        )
        return {
            "projection_poll_count": self.projection_poll_count,
            "snapshot_status": status,
            "snapshot_error": error,
            "entity_count": entity_count,
            "entity_ids": entity_ids,
            "manifest": row,
        }

    def llm_summary(self) -> dict[str, Any]:
        return {
            "provider": "ollama",
            "model": self.config.ollama_model,
            "base_url": self.config.ollama_base_url,
            "parser_mode": "ollama",
            "sampled_prompts_available": False,
            "quality_failures": [
                {"doc_id": record.doc_id, "failures": record.llm_quality_failures}
                for record in self.records
                if record.llm_quality_failures
            ],
        }

    def _run_document_workflow(self, record: DocumentRecord) -> None:
        resolver = self._build_resolver()
        runtime = WorkflowRuntime(
            workflow_engine=self.engines.workflow,
            conversation_engine=self.engines.conversation,
            step_resolver=resolver,
            predicate_registry={},
        )
        run_id = f"{self.run_id}:{record.doc_id}"
        record.run_id = run_id
        with _temporary_namespace(
            self.engines.conversation,
            WorkspaceNamespaces(self.config.workspace_id).conv_bg,
        ):
            result = runtime.run(
                workflow_id=WORKFLOW_ID,
                conversation_id=f"longrun:{self.config.workspace_id}",
                turn_node_id=record.doc_id,
                initial_state={
                    "workspace_id": self.config.workspace_id,
                    "doc_id": record.doc_id,
                    "_deps": {"harness": self},
                },
                run_id=run_id,
            )
        if result.status != "succeeded":
            raise LongRunDocumentError(
                "document_parse_failed",
                f"workflow returned {result.status}",
                phase="runtime",
            )

    def _build_resolver(self) -> MappingStepResolver:
        resolver = MappingStepResolver()

        @resolver.register("claim_document")
        def _claim(ctx):
            record = self.contexts[str(ctx.state_view["doc_id"])]
            target = self.run_dir / "processing" / record.input_path.name
            if record.current_path != target:
                shutil.move(str(record.current_path), str(target))
                record.current_path = target
            self._transition(record, "CLAIMED", phase="claim_document")
            return RunSuccess(state_update=[("u", {"claimed_path": str(target)})])

        @resolver.register("token_check")
        def _token_check(ctx):
            record = self.contexts[str(ctx.state_view["doc_id"])]
            text = record.current_path.read_text(encoding="utf-8")
            count = _count_tokens(text)
            record.token_count = count
            self._transition(record, "TOKEN_CHECKED", phase="token_check", token_count=count)
            if count < self.config.token_min or count > self.config.token_max:
                raise LongRunDocumentError(
                    "token_count_out_of_range",
                    f"token count {count} outside {self.config.token_min}-{self.config.token_max}",
                    phase="token_check",
                )
            return RunSuccess(state_update=[("u", {"token_count": count})])

        @resolver.register("parse_document")
        def _parse(ctx):
            record = self.contexts[str(ctx.state_view["doc_id"])]
            request = self._request_for(record)
            source_document_id = self.pipeline._source_document_id(request)
            try:
                record.parse_result = self.pipeline.parse_source(
                    request=request,
                    source_document_id=source_document_id,
                )
            except Exception as exc:  # noqa: BLE001
                code = classify_exception(exc, phase="parse_document")
                if code in RECOVERABLE_LLM_QUALITY_FAILURES:
                    code = "document_parse_failed"
                raise LongRunDocumentError(code, str(exc), phase="parse_document") from exc
            record.source_document_id = source_document_id
            self._transition(record, "PARSED", phase="parse_document")
            return RunSuccess(state_update=[("u", {"source_document_id": source_document_id})])

        @resolver.register("persist_document")
        def _persist(ctx):
            record = self.contexts[str(ctx.state_view["doc_id"])]
            request = self._request_for(record)
            source_document_id = str(record.source_document_id)
            ns = self.pipeline.namespaces_for(request.workspace_id)
            try:
                self.pipeline.register_source(
                    request=request,
                    source_document_id=source_document_id,
                    namespace=ns.conv_fg,
                )
                record.graph_extraction = self.pipeline.translate_parse_result(
                    parse_result=record.parse_result,
                    source_document_id=source_document_id,
                )
                self.pipeline.ingest_parse_result(
                    request=request,
                    source_document_id=source_document_id,
                    graph_extraction=record.graph_extraction,
                    namespace=ns.conv_fg,
                )
            except Exception as exc:  # noqa: BLE001
                raise LongRunDocumentError(
                    "document_persist_failed_after_retries",
                    str(exc),
                    phase="persist_document",
                ) from exc
            self._transition(record, "PERSISTED", phase="persist_document")
            return RunSuccess(state_update=[("u", {"persisted": True})])

        @resolver.register("enqueue_background_maintenance")
        def _enqueue(ctx):
            record = self.contexts[str(ctx.state_view["doc_id"])]
            request = self._request_for(record)
            ns = self.pipeline.namespaces_for(request.workspace_id)
            source_document_id = str(record.source_document_id)
            maintenance_job_id = self.pipeline.create_maintenance_request(
                request=request,
                source_document_id=source_document_id,
                namespace=ns.conv_bg,
            )
            candidate_link_id = self.pipeline.create_candidate_link(
                request=request,
                source_document_id=source_document_id,
                parse_result=record.parse_result,
                namespace=ns.conv_bg,
            )
            promotion_candidate_id = self.pipeline.create_promotion_candidate(
                request=request,
                source_document_id=source_document_id,
                candidate_link_id=candidate_link_id,
                namespace=ns.conv_bg,
            )
            promoted_entity_id = self.pipeline.promote_to_knowledge(
                request=request,
                source_document_id=source_document_id,
                promotion_candidate_id=promotion_candidate_id,
                namespace=ns.kg,
            )
            record.maintenance_job_id = maintenance_job_id
            record.candidate_link_id = candidate_link_id
            record.promotion_candidate_id = promotion_candidate_id
            record.promoted_entity_id = promoted_entity_id
            self._transition(record, "MAINTENANCE_ENQUEUED", phase="enqueue_background_maintenance")
            return RunSuccess(
                state_update=[
                    (
                        "u",
                        {
                            "maintenance_job_id": maintenance_job_id,
                            "promoted_entity_id": promoted_entity_id,
                        },
                    )
                ]
            )

        @resolver.register("observe_background_maintenance")
        def _observe(ctx):
            record = self.contexts[str(ctx.state_view["doc_id"])]
            try:
                self._poll_maintenance_once(phase="observe_background_maintenance")
            except Exception as exc:  # noqa: BLE001
                code = classify_exception(exc, phase="observe_background_maintenance")
                if code in RECOVERABLE_LLM_QUALITY_FAILURES:
                    record.llm_quality_failures.append(code)
                    failure = self._failure_record(
                        doc_id=record.doc_id,
                        phase="observe_background_maintenance",
                        code=code,
                        scope="llm_quality",
                        message=str(exc),
                    )
                    self._record_failure(failure)
                else:
                    raise
            self._transition(record, "MAINTENANCE_OBSERVED", phase="observe_background_maintenance")
            return RunSuccess(state_update=[("u", {"maintenance_observed": True})])

        @resolver.register("verify_document_artifacts")
        def _verify(ctx):
            record = self.contexts[str(ctx.state_view["doc_id"])]
            self._verify_document(record)
            return RunSuccess(state_update=[("u", {"verified": True})])

        @resolver.register("move_completed")
        def _move_completed(ctx):
            record = self.contexts[str(ctx.state_view["doc_id"])]
            target = self.run_dir / "completed" / record.current_path.name
            if record.current_path.exists() and record.current_path != target:
                shutil.move(str(record.current_path), str(target))
                record.current_path = target
            self._transition(record, "COMPLETED", phase="move_completed")
            return RunSuccess(state_update=[("u", {"completed_path": str(target)})])

        return resolver

    def _request_for(self, record: DocumentRecord) -> IngestPipelineRequest:
        return IngestPipelineRequest(
            workspace_id=self.config.workspace_id,
            source_uri=record.source_uri,
            title=record.title,
            raw_text=record.current_path.read_text(encoding="utf-8"),
            source_format="markdown",
            parser_mode="ollama",
            promotion_mode="sync",
            llm_provider="ollama",
            llm_model=self.config.ollama_model,
        )

    def _transition(self, record: DocumentRecord, status: str, *, phase: str, **extra: Any) -> None:
        if status not in STATUSES:
            raise ValueError(f"unknown long-run status {status!r}")
        record.status = status
        self.status_transitions.append(
            {
                "run_id": record.run_id or self.run_id,
                "doc_id": record.doc_id,
                "phase": phase,
                "status": status,
                "timestamp_ms": _now_ms(),
                **extra,
            }
        )

    def _record_failure(self, failure: FailureRecord) -> None:
        self.failure_records.append(failure)

    def _failure_record(
        self,
        *,
        doc_id: str | None,
        phase: str,
        code: str,
        scope: str,
        message: str,
    ) -> FailureRecord:
        return FailureRecord(
            run_id=self.run_id,
            doc_id=doc_id,
            phase=phase,
            code=code,
            scope=scope,
            message=message,
            fingerprint=normalized_fingerprint(code=code, phase=phase, message=message),
            timestamp_ms=_now_ms(),
        )

    def _classify_exception(self, exc: Exception, *, doc_id: str | None, phase: str) -> FailureRecord:
        code = getattr(exc, "code", None) or classify_exception(exc, phase=phase)
        if code in RECOVERABLE_LLM_QUALITY_FAILURES:
            scope = "llm_quality"
        elif code in RECOVERABLE_DOCUMENT_FAILURES:
            scope = "document"
        else:
            code = code if code in SYSTEMIC_FAILURES else "same_error_repeated_across_unrelated_docs"
            scope = "systemic"
        return self._failure_record(
            doc_id=doc_id,
            phase=getattr(exc, "phase", phase),
            code=str(code),
            scope=scope,
            message=f"{type(exc).__name__}: {exc}",
        )

    def _move_failed_or_quarantine(self, record: DocumentRecord, failure: FailureRecord) -> None:
        target_dir = "quarantine" if failure.scope == "systemic" else "failed"
        status = "QUARANTINED" if failure.scope == "systemic" else "FAILED"
        target = self.run_dir / target_dir / record.current_path.name
        if record.current_path.exists() and record.current_path != target:
            shutil.move(str(record.current_path), str(target))
            record.current_path = target
        self._transition(record, status, phase="move_failed_or_quarantine", failure_code=failure.code)

    def _abort(self, reason: str) -> None:
        self.aborted = True
        self.abort_reason = reason

    def _quarantine_processing_docs(self) -> None:
        for record in self.records:
            if record.status not in TERMINAL_STATES and record.current_path.exists():
                target = self.run_dir / "quarantine" / record.current_path.name
                shutil.move(str(record.current_path), str(target))
                record.current_path = target
                self._transition(record, "QUARANTINED", phase="abort_quarantine")

    def _poll_maintenance_once(self, *, phase: str) -> None:
        del phase
        self.maintenance_poll_count += 1
        self.maintenance_worker.process_pending_jobs(self.config.workspace_id)

    def _drain_maintenance_after_documents(self) -> None:
        ns = WorkspaceNamespaces(self.config.workspace_id)
        for _ in range(self.config.max_post_doc_maintenance_steps):
            pending_before = self.engines.conversation.meta_sqlite.list_index_jobs(
                namespace=ns.maintenance_jobs,
                status="PENDING",
                limit=10_000,
            )
            if not pending_before:
                break
            self._poll_maintenance_once(phase="post_doc_drain")

    def _poll_projection_once(self) -> None:
        self.projection_poll_count += 1
        vault_root = self.run_dir / "projection_vault"
        self.projection_worker.process_pending_projections(self.config.workspace_id, str(vault_root))

    def _verify_document(self, record: DocumentRecord) -> None:
        if not record.source_document_id:
            raise LongRunSystemicError(
                "graph_invariant_violation",
                "missing source_document_id",
                phase="verify_document_artifacts",
            )
        stored_document = self.engines.conversation.backend.document_get(
            ids=[record.source_document_id],
            include=["documents", "metadatas"],
        )
        if stored_document["ids"] != [record.source_document_id]:
            raise LongRunSystemicError(
                "graph_invariant_violation",
                f"source document {record.source_document_id} was not persisted",
                phase="verify_document_artifacts",
            )
        ns = WorkspaceNamespaces(self.config.workspace_id)
        with _temporary_namespace(self.engines.conversation, ns.conv_fg):
            parsed_nodes = self.engines.conversation.read.get_nodes(
                where={"doc_id": record.source_document_id},
                limit=10_000,
            )
        if not parsed_nodes:
            raise LongRunSystemicError(
                "graph_invariant_violation",
                f"no parsed nodes for source document {record.source_document_id}",
                phase="verify_document_artifacts",
            )
        if not all(_node_has_doc_provenance(node, record.source_document_id) for node in parsed_nodes):
            raise LongRunSystemicError(
                "graph_invariant_violation",
                f"parsed node without source provenance for {record.source_document_id}",
                phase="verify_document_artifacts",
            )

    def _verify_run_invariants(self) -> None:
        if any(record.status not in TERMINAL_STATES for record in self.records):
            raise AssertionError("every document must end in completed, failed, or quarantine")
        if list((self.run_dir / "processing").glob("*")):
            raise AssertionError("no document may remain in processing")
        for record in self.records:
            if record.status == "COMPLETED":
                self._verify_document(record)
            if record.status in {"FAILED", "QUARANTINED"} and not any(
                failure.doc_id == record.doc_id for failure in self.failure_records
            ):
                raise AssertionError(f"{record.doc_id} is terminal failure without failure record")
        maintenance = self.maintenance_summary()
        useful_maintenance = (
            maintenance["derived_artifact_count"] > 0
            or maintenance["workflow_step_count"] > 0
            or maintenance["job_status_counts"].get("DONE", 0) > 0
            or bool(maintenance["foreground_replies"])
        )
        if not useful_maintenance:
            raise AssertionError("background maintenance did not produce persisted evidence")
        self._verify_runtime_events_have_run_ids()
        self._verify_derived_nodes_have_provenance()
        projection = self.projection_summary()
        if projection["snapshot_status"] != "ok":
            raise AssertionError(f"projection read failed: {projection['snapshot_error']}")

    def _verify_runtime_events_have_run_ids(self) -> None:
        with _temporary_namespace(
            self.engines.conversation,
            WorkspaceNamespaces(self.config.workspace_id).conv_bg,
        ):
            events = self.engines.conversation.read.get_nodes(
                where={"workflow_id": WORKFLOW_ID},
                limit=10_000,
            )
        missing = [
            str(node.id)
            for node in events
            if (node.metadata or {}).get("entity_type")
            in {"workflow_run", "workflow_step_exec", "workflow_completed", "workflow_failed"}
            and not (node.metadata or {}).get("run_id")
        ]
        if missing:
            raise AssertionError(f"workflow events missing run_id: {missing[:5]}")

    def _verify_derived_nodes_have_provenance(self) -> None:
        ns = WorkspaceNamespaces(self.config.workspace_id)
        with _temporary_namespace(self.engines.derived_knowledge_engine(), ns.derived_knowledge):
            derived = self.engines.derived_knowledge_engine().read.get_nodes(
                where={"artifact_kind": "derived_knowledge", "workspace_id": self.config.workspace_id},
                limit=10_000,
            )
        for node in derived:
            if not getattr(node, "mentions", None):
                raise AssertionError(f"derived node {node.id} is missing provenance")

    def _build_parser(self):
        provider_settings = WorkflowProviderSettings(
            parser=ProviderEndpointConfig(
                provider="ollama",
                model=self.config.ollama_model,
                base_url=self.config.ollama_base_url,
            ),
            embedding=EmbeddingProviderConfig(provider="fake", model="longrun-embed", dimension=2),
        )

        def _parser(**kwargs):
            kwargs.pop("llm_provider", None)
            kwargs.pop("model", None)
            kwargs.pop("provider_settings", None)
            return parse_page_index_document(provider_settings=provider_settings, **kwargs)

        return _parser

    def _generate_corpus(self) -> None:
        topic = "urban watershed resilience and stormwater infrastructure"
        for index in range(1, self.config.doc_count + 1):
            doc_id = f"doc-{index:03d}"
            title = f"Watershed Resilience Brief {index:03d}"
            path = self.run_dir / "input" / f"{doc_id}.md"
            text = generate_longrun_document(index=index, title=title, topic=topic)
            token_count = _count_tokens(text)
            if not (self.config.token_min <= token_count <= self.config.token_max):
                raise AssertionError(f"generated {doc_id} has invalid token count {token_count}")
            path.write_text(text, encoding="utf-8")
            record = DocumentRecord(
                doc_id=doc_id,
                title=title,
                source_uri=f"file:///{path.name}",
                input_path=path,
                current_path=path,
                token_count=token_count,
            )
            self.records.append(record)
            self.contexts[doc_id] = record
            self._transition(record, "PENDING", phase="discover_pending", token_count=token_count)

    def _materialize_workflow_design(self) -> None:
        grounding = [Grounding(spans=[Span.from_dummy_for_workflow(WORKFLOW_ID)])]
        node_ids: dict[str, str] = {}
        for step in WORKFLOW_STEPS:
            node_id = str(stable_id("wf_node", WORKFLOW_ID, step))
            node_ids[step] = node_id
            self.engines.workflow.write.add_node(
                WorkflowNode(
                    id=node_id,
                    label=step,
                    type="entity",
                    summary=f"Long-run ingestion step: {step}",
                    mentions=grounding,
                    metadata={
                        "entity_type": "workflow_node",
                        "workflow_id": WORKFLOW_ID,
                        "wf_op": step,
                        "wf_start": step == WORKFLOW_STEPS[0],
                    },
                )
            )
        terminal_id = str(stable_id("wf_node", WORKFLOW_ID, "done"))
        self.engines.workflow.write.add_node(
            WorkflowNode(
                id=terminal_id,
                label="done",
                type="entity",
                summary="Long-run ingestion terminal state.",
                mentions=grounding,
                metadata={
                    "entity_type": "workflow_node",
                    "workflow_id": WORKFLOW_ID,
                    "wf_terminal": True,
                },
            )
        )
        targets = WORKFLOW_STEPS[1:] + ["done"]
        for source, target in zip(WORKFLOW_STEPS, targets):
            self.engines.workflow.write.add_edge(
                WorkflowEdge(
                    id=str(stable_id("wf_edge", WORKFLOW_ID, source, target)),
                    source_ids=[node_ids[source]],
                    target_ids=[terminal_id if target == "done" else node_ids[target]],
                    relation="workflow_transition",
                    type="relationship",
                    label=f"{source}_to_{target}",
                    summary=f"{source} transitions to {target}",
                    mentions=grounding,
                    source_edge_ids=[],
                    target_edge_ids=[],
                    metadata={
                        "entity_type": "workflow_edge",
                        "workflow_id": WORKFLOW_ID,
                        "wf_predicate": None,
                        "wf_is_default": True,
                        "wf_priority": 100,
                    },
                )
            )

    def _export_engine(
        self,
        engine: Any,
        *,
        where: dict[str, Any],
        namespace: str | None = None,
    ) -> dict[str, Any]:
        context = _temporary_namespace(engine, namespace) if namespace else nullcontext()
        with context:
            try:
                nodes = engine.read.get_nodes(where=where, limit=10_000)
            except Exception as exc:  # noqa: BLE001
                nodes = []
                node_error = f"{type(exc).__name__}: {exc}"
            else:
                node_error = None
            try:
                edges = engine.read.get_edges(where=where, limit=10_000)
            except Exception as exc:  # noqa: BLE001
                edges = []
                edge_error = f"{type(exc).__name__}: {exc}"
            else:
                edge_error = None
        return {
            "namespace": namespace,
            "node_error": node_error,
            "edge_error": edge_error,
            "nodes": [_model_to_dict(node) for node in nodes],
            "edges": [_model_to_dict(edge) for edge in edges],
        }

    def _state_signature(self) -> tuple[Any, ...]:
        return (
            tuple((record.doc_id, record.status, str(record.current_path)) for record in self.records),
            self.maintenance_poll_count,
            len(self.failure_records),
        )


def classify_exception(exc: Exception, *, phase: str) -> str:
    text = f"{type(exc).__name__}: {exc}".lower()
    if "ollama" in text and any(token in text for token in ("unavailable", "connection", "refused", "timeout")):
        return "ollama_unavailable_repeatedly"
    if phase == "parse_document" and any(token in text for token in ("json", "structured", "validation")):
        return "document_parse_failed"
    if any(token in text for token in ("invalid json", "jsondecode", "structured output")):
        return "llm_invalid_json"
    if "unsupported citation" in text:
        return "llm_unsupported_citation"
    if "ungrounded" in text or "citation" in text and "source" in text:
        return "llm_ungrounded_output"
    if "contradict" in text:
        return "llm_contradicts_source"
    if "low confidence" in text or "empty output" in text:
        return "llm_empty_or_low_confidence_output"
    if any(token in text for token in ("database", "sqlite", "write failed", "locked")):
        return "database_write_repeated_failure"
    if "projection" in text and "repair" in text:
        return "projection_repair_failure"
    if "invariant" in text or "orphan" in text:
        return "graph_invariant_violation"
    if "stuck" in text or "timeout" in text:
        return "runtime_worker_stuck"
    if phase == "persist_document":
        return "document_persist_failed_after_retries"
    if phase == "verify_document_artifacts":
        return "maintenance_artifact_missing_for_doc"
    return "same_error_repeated_across_unrelated_docs"


def normalized_fingerprint(*, code: str, phase: str, message: str) -> str:
    normalized = re.sub(r"\bdoc-\d+\b", "doc-*", message.lower())
    normalized = re.sub(r"longrun-[a-z0-9:-]+", "longrun-*", normalized)
    normalized = re.sub(r"[a-f0-9]{8,}", "hex-*", normalized)
    normalized = re.sub(r"\d+", "n", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()[:240]
    return f"{code}|{phase}|{normalized}"


def generate_longrun_document(*, index: int, title: str, topic: str) -> str:
    subtopic = [
        "green streets",
        "detention basins",
        "sensor networks",
        "community stewardship",
        "combined sewer overflow controls",
    ][index % 5]
    sections = [
        f"# {title}",
        "",
        f"This brief studies {topic} with emphasis on {subtopic}.",
        "",
    ]
    paragraph = (
        f"In district {index}, planners compare rainfall history, soil storage, pipe capacity, "
        f"and neighborhood access before selecting stormwater investments. The watershed team "
        f"uses field inspections, maintenance logs, and resident reports to decide whether {subtopic} "
        f"should be paired with tree trenches, permeable alleys, daylighted channels, or pump upgrades. "
        "Each recommendation keeps a direct link to observed flooding, measured runoff, and the public "
        "asset that needs attention. Operators prefer staged work because small verified repairs reveal "
        "which controls reduce nuisance flooding without shifting risk downstream. The program also "
        "tracks equity, because the most flood-prone blocks often have less canopy, older drainage "
        "records, and fewer safe routes during intense storms. "
    )
    for section in range(1, 8):
        sections.append(f"## Finding {section}")
        sections.append("")
        sections.append(
            paragraph
            + f"The finding for cycle {section} links inspection evidence to a maintenance decision, "
            f"so future reviews can distinguish source observations from derived planning guidance. "
            "This provenance matters when a later model summary is incomplete, unsupported, or too "
            "confident about benefits that were not measured in the source record."
        )
        sections.append("")
    return "\n".join(sections)


def _check_ollama_available(config: LongRunConfig) -> tuple[bool, str | None]:
    try:
        import requests
    except Exception as exc:  # noqa: BLE001
        return False, f"requests import failed: {exc}"
    try:
        response = requests.get(f"{config.ollama_base_url}/api/version", timeout=1.5)
    except Exception as exc:  # noqa: BLE001
        return False, f"local Ollama is not available at {config.ollama_base_url}: {exc}"
    if not response.ok:
        return False, f"local Ollama is not healthy at {config.ollama_base_url}: {response.status_code}"
    return True, None


def _count_tokens(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))


def _now_ms() -> int:
    return int(time.time() * 1000)


def _node_has_doc_provenance(node: Any, source_document_id: str) -> bool:
    for mention in getattr(node, "mentions", []) or []:
        for span in getattr(mention, "spans", []) or []:
            if str(getattr(span, "doc_id", "")) == str(source_document_id):
                return True
    return False


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _model_to_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return dict(getattr(value, "__dict__", {}) or {})


def _job_to_dict(job: Any) -> dict[str, Any]:
    return {
        "job_id": str(getattr(job, "job_id", "")),
        "namespace": str(getattr(job, "namespace", "")),
        "entity_kind": str(getattr(job, "entity_kind", "")),
        "entity_id": str(getattr(job, "entity_id", "")),
        "job_kind": str(getattr(job, "job_kind", "")),
        "status": str(getattr(job, "status", "")),
        "retry_count": int(getattr(job, "retry_count", 0) or 0),
        "payload": _jsonable(getattr(job, "payload", {})),
    }


def _lane_row_to_dict(row: Any) -> dict[str, Any]:
    return {
        "message_id": str(getattr(row, "message_id", "")),
        "msg_type": str(getattr(row, "msg_type", "")),
        "status": str(getattr(row, "status", "")),
        "inbox_id": str(getattr(row, "inbox_id", "")),
        "correlation_id": str(getattr(row, "correlation_id", "")),
        "payload_json": str(getattr(row, "payload_json", "") or ""),
    }


def test_longrun_failure_classifier_and_circuit_breaker_are_bounded():
    breaker = ErrorCircuitBreaker(threshold=2)
    quality = FailureRecord(
        run_id="run",
        doc_id="doc-001",
        phase="observe_background_maintenance",
        code="llm_ungrounded_output",
        scope="llm_quality",
        message="unsupported source citation",
        fingerprint=normalized_fingerprint(
            code="llm_ungrounded_output",
            phase="observe_background_maintenance",
            message="unsupported source citation",
        ),
        timestamp_ms=_now_ms(),
    )
    assert breaker.record(quality) is False

    systemic_records = [
        FailureRecord(
            run_id="run",
            doc_id=f"doc-{index:03d}",
            phase="persist_document",
            code="database_write_repeated_failure",
            scope="systemic",
            message="sqlite database write failed for doc-specific-id",
            fingerprint=normalized_fingerprint(
                code="database_write_repeated_failure",
                phase="persist_document",
                message="sqlite database write failed for doc-specific-id",
            ),
            timestamp_ms=_now_ms(),
        )
        for index in range(1, 4)
    ]
    assert breaker.record(systemic_records[0]) is False
    assert breaker.record(systemic_records[1]) is False
    assert breaker.record(systemic_records[2]) is True
    assert classify_exception(ValueError("invalid json structured output"), phase="observe") == "llm_invalid_json"
    assert classify_exception(RuntimeError("sqlite database write failed"), phase="persist_document") == (
        "database_write_repeated_failure"
    )


@pytest.mark.integration
@pytest.mark.longrun
@pytest.mark.requires_ollama
def test_longrun_runtime_workflow_ingestion(tmp_path: Path):
    config = LongRunConfig.from_env()
    if not config.enabled:
        pytest.skip("set KOGWISTAR_LLM_WIKI_LONGRUN=1 to run the long-run workflow ingestion test")

    run_dir = tmp_path / "longrun-workflow"
    harness = LongRunHarness(run_dir=run_dir, config=config)
    harness.prepare()

    try:
        __import__("langchain_ollama")
    except Exception as exc:  # noqa: BLE001
        failure = harness._failure_record(
            doc_id=None,
            phase="ollama_dependency_check",
            code="ollama_unavailable_repeatedly",
            scope="systemic",
            message=f"langchain_ollama import failed: {exc}",
        )
        harness._record_failure(failure)
        harness.dumper.dump(reason="ollama_dependency_unavailable", final=True)
        pytest.fail(f"langchain_ollama is required; diagnostic dump written to {harness.dumper.dump_dir}")

    ok, reason = _check_ollama_available(config)
    if not ok:
        failure = harness._failure_record(
            doc_id=None,
            phase="ollama_healthcheck",
            code="ollama_unavailable_repeatedly",
            scope="systemic",
            message=reason or "Ollama unavailable",
        )
        harness._record_failure(failure)
        harness.dumper.dump(reason="ollama_unavailable", final=True)
        pytest.fail(f"{reason}; diagnostic dump written to {harness.dumper.dump_dir}")

    harness.run()
