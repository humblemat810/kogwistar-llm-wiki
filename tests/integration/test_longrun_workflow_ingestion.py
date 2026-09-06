from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import logging
import multiprocessing
import os
import queue
import re
import shutil
import time
import threading
import sys
import traceback
import zipfile
from collections import Counter, defaultdict
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from types import SimpleNamespace

if os.getenv("KOGWISTAR_LLM_WIKI_LONGRUN") == "1" or os.getenv("KOGWISTAR_LONGRUN_LIVE_TRACE") == "1":
    print(
        "[longrun.boot] test module import started "
        f"pid={os.getpid()} argv={sys.argv!r}",
        file=sys.stderr,
        flush=True,
    )

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.ci_full]

from kg_doc_parser.workflow_ingest.page_index import parse_page_index_document
from kg_doc_parser.workflow_ingest.providers import (
    EmbeddingProviderConfig,
    ProviderEndpointConfig,
    WorkflowProviderSettings,
    build_chat_model_for_role,
)

from kogwistar.id_provider import stable_id
from kogwistar.runtime import MappingStepResolver
from kogwistar.runtime.models import RunSuccess, RunSuspended, WorkflowEdge, WorkflowNode
from kogwistar.runtime.runtime import RunResult, WorkflowRuntime
from kogwistar.engine_core.models import Grounding, Span
from kogwistar.engine_core import RecoverySurface

from kogwistar_llm_wiki import IngestPipeline, IngestPipelineRequest
from kogwistar_llm_wiki.ingest_pipeline import (
    build_in_memory_namespace_engines,
    build_persistent_namespace_engines,
    build_postgres_namespace_engines,
)
from kogwistar_llm_wiki.longrun_trace_sink import LongRunJsonlTraceSink
from kogwistar_llm_wiki.debug_run import LiveTracePrinter, aggregate_stage_timings, env_flag_enabled
from kogwistar_llm_wiki.longrun_parser_worker import (
    _basic_sense_eval_from_graph_payload,
    run_longrun_parser_child,
)
from kogwistar_llm_wiki.provider_config import normalize_provider_name, resolve_parser_provider_settings
from kogwistar_llm_wiki.maintenance_designs import materialize_maintenance_designs
from kogwistar_llm_wiki.maintenance_policy import DERIVED_KNOWLEDGE_WORKFLOW_ID
from kogwistar_llm_wiki.namespaces import WorkspaceNamespaces
from kogwistar_llm_wiki.projection_worker import ProjectionWorker
from kogwistar_llm_wiki.utils import _temporary_namespace
from kogwistar_llm_wiki.worker import MaintenanceWorker
from kogwistar_llm_wiki.maintenance_statistics import build_maintenance_statistics


STATUSES = {
    "PENDING",
    "CLAIMED",
    "TOKEN_CHECKED",
    "PARSED",
    "PERSISTED",
    "SOURCE_SEEDED",
    "SUSPENDED",
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
    "await_resume",
    "enqueue_background_maintenance",
    "observe_background_maintenance",
    "verify_document_artifacts",
    "move_completed",
]
MAINTENANCE_FIRST_WORKFLOW_STEPS = [
    "claim_document",
    "token_check",
    "seed_source_map",
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


logger = logging.getLogger(__name__)


_PARSER_PARENT_POLL_LOG_INTERVAL_SECONDS = 30.0


class LongRunDocumentError(RuntimeError):
    """Document-scoped failure raised from a workflow step."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        phase: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.phase = phase
        self.details = dict(details or {})


class LongRunSystemicError(RuntimeError):
    """Abort-class failure raised when infrastructure or invariants look broken."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        phase: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.phase = phase
        self.details = dict(details or {})


@dataclass(frozen=True)
class LongRunConfig:
    enabled: bool
    mode: str
    doc_count: int
    doc_limit: int | None = None
    recovery_doc_limit: int | None = None
    recovery_attempts_per_doc: int = 1
    operation_mode: str = "parse_first"
    pg_database_mode: str = "fingerprint"
    parser_provider: str = "ollama"
    parser_model: str = "gemma4:e2b"
    parser_proposal_mode: str = "children"
    parser_temperature: float = 0.1
    parser_base_url: str = "http://localhost:11434"
    parser_api_key_env: str | None = None
    parser_api_version: str | None = None
    parser_max_retries: int = 2
    ollama_model: str = "gemma4:e2b"
    ollama_base_url: str = "http://localhost:11434"
    max_repeated_systemic_errors: int = 3
    max_post_doc_maintenance_steps: int = 100
    maintenance_schedule_mode: str = "drain"
    maintenance_workers: int = 1
    maintenance_steps_per_slice: int = 2
    maintenance_llm_calls_per_slice: int = 0
    maintenance_seconds_per_slice: int = 0
    max_llm_calls: int = 100
    backend: str = "chroma"
    parser_lane: str = "workflow_layered"
    conversation_persistence_mode: str = "single_stage"
    parse_timeout_seconds: int = 1200
    max_runtime_seconds: int = 3600
    dsn: str | None = None
    resume_probe_enabled: bool = False
    max_idle_loops: int = 25
    token_min: int = 500
    token_max: int = 2000
    workspace_id: str = "longrun"
    checkpoint_run_dir: str | None = None
    doc_profile: str = "medium"
    corpus_profile: str = "watershed_stress"
    skip_maintenance_invariant: bool = False
    live_trace: bool = False
    parser_workers: int = 1
    experiment_run: str = ""
    corpus_fingerprint: str = ""

    def __post_init__(self) -> None:
        if self.max_llm_calls <= 0:
            raise ValueError("max_llm_calls must be positive")
        if self.max_runtime_seconds <= 0:
            raise ValueError("max_runtime_seconds must be positive")
        if not 1 <= self.parser_workers <= 16:
            raise ValueError("parser_workers must be between 1 and 16")
        if not 1 <= self.maintenance_workers <= 16:
            raise ValueError("maintenance_workers must be between 1 and 16")
        if self.parser_workers > 1 and self.backend == "chroma":
            raise ValueError(
                "parser_workers > 1 requires postgres/pgvector; Chroma persistence is single-writer"
            )
        if self.maintenance_workers > 1 and self.backend == "chroma":
            raise ValueError(
                "maintenance_workers > 1 requires postgres/pgvector; Chroma persistence is single-writer"
            )
        if self.doc_limit is not None and not 1 <= self.doc_limit <= self.doc_count:
            raise ValueError("doc_limit must be between 1 and doc_count")
        if self.recovery_doc_limit is not None and not 1 <= self.recovery_doc_limit <= self.doc_count:
            raise ValueError("recovery_doc_limit must be between 1 and doc_count")
        if self.recovery_attempts_per_doc <= 0:
            raise ValueError("recovery_attempts_per_doc must be positive")
        if self.mode not in {"fresh", "continue", "auto", "retry_failed"}:
            raise ValueError("mode must be one of: fresh, continue, auto, retry_failed")
        if self.operation_mode not in {"parse_first", "maintenance_first", "hybrid"}:
            raise ValueError("operation_mode must be one of: parse_first, maintenance_first, hybrid")
        if self.maintenance_schedule_mode not in {"drain", "fair_slices"}:
            raise ValueError("maintenance_schedule_mode must be one of: drain, fair_slices")
        if self.maintenance_steps_per_slice <= 0:
            raise ValueError("maintenance_steps_per_slice must be positive")
        if self.maintenance_llm_calls_per_slice < 0:
            raise ValueError("maintenance_llm_calls_per_slice must be >= 0")
        if self.maintenance_seconds_per_slice < 0:
            raise ValueError("maintenance_seconds_per_slice must be >= 0")
        if self.pg_database_mode not in {"fingerprint", "shared"}:
            raise ValueError("pg_database_mode must be one of: fingerprint, shared")
        if self.parser_proposal_mode not in {"children", "boundaries"}:
            raise ValueError("parser_proposal_mode must be one of: children, boundaries")
        if self.conversation_persistence_mode not in {"single_stage", "two_stage"}:
            raise ValueError(
                "conversation_persistence_mode must be one of: single_stage, two_stage"
            )
        if self.corpus_profile not in {"watershed_stress", "daily_life"}:
            raise ValueError("corpus_profile must be one of: watershed_stress, daily_life")
        if any(char in self.experiment_run for char in '\\/:*?"<>|'):
            raise ValueError("experiment_run must be a simple label without path separators")
        if not self.corpus_fingerprint:
            object.__setattr__(self, "corpus_fingerprint", str(self._compute_corpus_fingerprint()))

    def _compute_corpus_fingerprint(self) -> str:
        parts: list[object] = [
            self.workspace_id,
            self.backend,
            self.operation_mode,
            self.maintenance_schedule_mode,
            self.maintenance_steps_per_slice,
            self.maintenance_llm_calls_per_slice,
            self.maintenance_seconds_per_slice,
            self.pg_database_mode,
            self.corpus_profile,
            self.doc_count,
            self.doc_profile,
            self.parser_lane,
            self.parser_provider,
            self.parser_model,
            self.parser_proposal_mode,
            self.token_min,
            self.token_max,
            self.skip_maintenance_invariant,
        ]
        if self.conversation_persistence_mode == "two_stage":
            parts.extend(["conversation_persistence_mode", self.conversation_persistence_mode])
        if self.experiment_run.strip():
            parts.append(self.experiment_run.strip())
        return str(stable_id("llm_wiki.longrun.corpus", *parts))

    def _legacy_corpus_fingerprint(self, *, resume_probe_enabled: bool) -> str:
        """Return the pre-migration fingerprint that included resume probing."""
        return str(
            stable_id(
                "llm_wiki.longrun.corpus",
                self.workspace_id,
                self.backend,
                self.operation_mode,
                self.pg_database_mode,
                self.corpus_profile,
                self.doc_count,
                self.doc_profile,
                self.parser_lane,
                self.parser_provider,
                self.parser_model,
                self.parser_proposal_mode,
                self.token_min,
                self.token_max,
                resume_probe_enabled,
                self.skip_maintenance_invariant,
            )
        )

    def _legacy_worker_sensitive_corpus_fingerprint(
        self,
        *,
        parser_workers: int,
        resume_probe_enabled: bool,
    ) -> str:
        """Return the older fingerprint that also included parser worker count."""
        return str(
            stable_id(
                "llm_wiki.longrun.corpus",
                self.workspace_id,
                self.backend,
                self.operation_mode,
                self.pg_database_mode,
                self.corpus_profile,
                self.doc_count,
                self.doc_profile,
                self.parser_lane,
                self.parser_provider,
                self.parser_model,
                self.parser_proposal_mode,
                parser_workers,
                self.token_min,
                self.token_max,
                resume_probe_enabled,
                self.skip_maintenance_invariant,
            )
        )

    def _historical_corpus_fingerprint(self) -> str:
        """Accept the checkpoint schema used before run-control fields existed."""
        return str(
            stable_id(
                "llm_wiki.longrun.corpus",
                self.workspace_id,
                self.backend,
                self.operation_mode,
                self.pg_database_mode,
                self.corpus_profile,
                self.doc_count,
                self.doc_profile,
                self.parser_lane,
                self.parser_provider,
                self.parser_model,
                self.parser_proposal_mode,
                self.token_min,
                self.token_max,
                self.skip_maintenance_invariant,
            )
        )

    def checkpoint_accepted_corpus_fingerprints(self) -> set[str]:
        accepted_fingerprints = {
            self.corpus_fingerprint,
            self._legacy_corpus_fingerprint(resume_probe_enabled=False),
            self._legacy_corpus_fingerprint(resume_probe_enabled=True),
            self._historical_corpus_fingerprint(),
        }
        accepted_fingerprints.update(
            self._legacy_worker_sensitive_corpus_fingerprint(
                parser_workers=parser_workers,
                resume_probe_enabled=resume_probe_enabled,
            )
            for parser_workers in range(1, 17)
            for resume_probe_enabled in (False, True)
        )
        return accepted_fingerprints

    @classmethod
    def from_env(cls) -> "LongRunConfig":
        doc_count = int(os.getenv("KOGWISTAR_LONGRUN_DOC_COUNT", "20"))
        if doc_count < 20 and os.getenv("KOGWISTAR_LONGRUN_ALLOW_SMALL") != "1":
            raise ValueError(
                "KOGWISTAR_LONGRUN_DOC_COUNT must be at least 20 unless "
                "KOGWISTAR_LONGRUN_ALLOW_SMALL=1 is set"
            )
        doc_limit_raw = os.getenv("KOGWISTAR_LONGRUN_DOC_LIMIT", "0").strip()
        doc_limit = int(doc_limit_raw) if doc_limit_raw else 0
        if doc_limit < 0 or doc_limit > doc_count:
            raise ValueError("KOGWISTAR_LONGRUN_DOC_LIMIT must be 0 or between 1 and doc_count")
        doc_profile = os.getenv("KOGWISTAR_LONGRUN_DOC_PROFILE", "medium").strip().lower() or "medium"
        if doc_profile not in {"tiny", "small", "medium"}:
            raise ValueError(
                "KOGWISTAR_LONGRUN_DOC_PROFILE must be one of {'tiny', 'small', 'medium'}; "
                f"got {doc_profile!r}"
            )
        corpus_profile = (
            os.getenv("KOGWISTAR_LONGRUN_CORPUS_PROFILE", "watershed_stress").strip().lower()
            or "watershed_stress"
        )
        if corpus_profile not in {"watershed_stress", "daily_life"}:
            raise ValueError(
                "KOGWISTAR_LONGRUN_CORPUS_PROFILE must be one of {'watershed_stress', 'daily_life'}; "
                f"got {corpus_profile!r}"
            )
        profile_token_min, profile_token_max = _longrun_doc_profile_token_bounds(doc_profile)
        token_min = int(os.getenv("KOGWISTAR_LONGRUN_TOKEN_MIN", str(profile_token_min)))
        token_max = int(os.getenv("KOGWISTAR_LONGRUN_TOKEN_MAX", str(profile_token_max)))
        if token_min <= 0 or token_max <= 0 or token_min > token_max:
            raise ValueError("KOGWISTAR_LONGRUN_TOKEN_MIN/MAX must define a positive ascending range")
        backend = os.getenv("KOGWISTAR_LONGRUN_BACKEND", "chroma").strip().lower() or "chroma"
        if backend not in {"chroma", "postgres", "pgvector"}:
            raise ValueError(
                "KOGWISTAR_LONGRUN_BACKEND must be one of {'chroma', 'postgres', 'pgvector'}; "
                f"got {backend!r}"
            )
        parser_lane = (
            os.getenv("KOGWISTAR_LONGRUN_PARSER", "workflow_layered").strip().lower()
            or "workflow_layered"
        )
        if parser_lane not in {"workflow_layered", "page_index"}:
            raise ValueError(
                "KOGWISTAR_LONGRUN_PARSER must be one of {'workflow_layered', 'page_index'}; "
                f"got {parser_lane!r}"
            )
        conversation_persistence_mode = (
            os.getenv("KOGWISTAR_LONGRUN_CONVERSATION_PERSISTENCE_MODE", "single_stage")
            .strip()
            .lower()
            or "single_stage"
        )
        if conversation_persistence_mode not in {"single_stage", "two_stage"}:
            raise ValueError(
                "KOGWISTAR_LONGRUN_CONVERSATION_PERSISTENCE_MODE must be one of: "
                "single_stage, two_stage"
            )
        parse_timeout_seconds = int(os.getenv("KOGWISTAR_LONGRUN_PARSE_TIMEOUT_SECONDS", "1200"))
        if parse_timeout_seconds <= 0:
            raise ValueError("KOGWISTAR_LONGRUN_PARSE_TIMEOUT_SECONDS must be positive")
        recovery_limit_raw = os.getenv("KOGWISTAR_LONGRUN_RECOVERY_DOC_LIMIT", "0").strip()
        recovery_doc_limit = int(recovery_limit_raw) if recovery_limit_raw else 0
        if recovery_doc_limit < 0 or recovery_doc_limit > doc_count:
            raise ValueError(
                "KOGWISTAR_LONGRUN_RECOVERY_DOC_LIMIT must be 0 or between 1 and doc_count"
            )
        recovery_attempts_per_doc = int(
            os.getenv("KOGWISTAR_LONGRUN_RECOVERY_ATTEMPTS_PER_DOC", "1")
        )
        if recovery_attempts_per_doc <= 0:
            raise ValueError("KOGWISTAR_LONGRUN_RECOVERY_ATTEMPTS_PER_DOC must be positive")
        operation_mode = os.getenv("KOGWISTAR_LONGRUN_OPERATION_MODE", "parse_first").strip().lower() or "parse_first"
        if operation_mode not in {"parse_first", "maintenance_first", "hybrid"}:
            raise ValueError(
                "KOGWISTAR_LONGRUN_OPERATION_MODE must be one of: parse_first, maintenance_first, hybrid"
            )
        max_runtime_seconds = int(os.getenv("KOGWISTAR_LONGRUN_MAX_RUNTIME_SECONDS", "3600"))
        if max_runtime_seconds <= 0:
            raise ValueError("KOGWISTAR_LONGRUN_MAX_RUNTIME_SECONDS must be positive")
        max_llm_calls = int(os.getenv("KOGWISTAR_LONGRUN_MAX_LLM_CALLS", "100"))
        if max_llm_calls <= 0:
            raise ValueError("KOGWISTAR_LONGRUN_MAX_LLM_CALLS must be positive")
        maintenance_schedule_mode = (
            os.getenv("KOGWISTAR_LONGRUN_MAINTENANCE_SCHEDULE", "drain").strip().lower()
            or "drain"
        )
        if maintenance_schedule_mode not in {"drain", "fair_slices"}:
            raise ValueError(
                "KOGWISTAR_LONGRUN_MAINTENANCE_SCHEDULE must be one of: drain, fair_slices"
            )
        maintenance_workers = int(
            os.getenv("KOGWISTAR_LONGRUN_MAINTENANCE_WORKERS", "1")
        )
        maintenance_steps_per_slice = int(
            os.getenv("KOGWISTAR_LONGRUN_MAINTENANCE_STEPS_PER_SLICE", "2")
        )
        maintenance_llm_calls_per_slice = int(
            os.getenv("KOGWISTAR_LONGRUN_MAINTENANCE_LLM_CALLS_PER_SLICE", "0")
        )
        maintenance_seconds_per_slice = int(
            os.getenv("KOGWISTAR_LONGRUN_MAINTENANCE_SECONDS_PER_SLICE", "0")
        )
        pg_database_mode = os.getenv("KOGWISTAR_LONGRUN_PG_DATABASE_MODE", "fingerprint").strip().lower() or "fingerprint"
        if pg_database_mode not in {"fingerprint", "shared"}:
            raise ValueError("KOGWISTAR_LONGRUN_PG_DATABASE_MODE must be one of: fingerprint, shared")
        pg_source = os.getenv("KOGWISTAR_LONGRUN_PG_SOURCE", "testcontainer").strip().lower() or "testcontainer"
        if pg_source not in {"testcontainer", "custom", "persistent"}:
            raise ValueError(
                "KOGWISTAR_LONGRUN_PG_SOURCE must be one of {'testcontainer', 'custom', 'persistent'}; "
                f"got {pg_source!r}"
            )
        dsn = _resolve_longrun_dsn()
        if backend in {"postgres", "pgvector"} and not dsn and not (
            backend == "pgvector" and pg_source in {"testcontainer", "persistent"}
        ):
            raise ValueError(
                "KOGWISTAR_LONGRUN_BACKEND=postgres/pgvector requires a DSN; set "
                "KOGWISTAR_LONGRUN_DSN or KOGWISTAR_LLM_WIKI_TEST_PG_DSN "
                "(or PG_DSN/DATABASE_URL). For `pgvector`, the repo's pytest "
                "session fixture can start either a disposable testcontainers-backed "
                "database or a persistent dev container automatically when Docker is available."
            )
        parser_provider = normalize_provider_name(
            os.getenv("KOGWISTAR_LONGRUN_PARSER_PROVIDER")
            or os.getenv("KOGWISTAR_PARSER_PROVIDER")
            or os.getenv("KG_DOC_PARSER_PROVIDER")
            or "ollama"
        ) or "ollama"
        parser_model = (
            os.getenv("KOGWISTAR_LONGRUN_PARSER_MODEL")
            or os.getenv("KOGWISTAR_PARSER_MODEL")
            or os.getenv("KOGWISTAR_OLLAMA_MODEL")
            or os.getenv("KG_DOC_PARSER_MODEL")
            or "gemma4:e2b"
        )
        parser_base_url_env = (
            os.getenv("KOGWISTAR_LONGRUN_PARSER_BASE_URL")
            or os.getenv("KOGWISTAR_PARSER_BASE_URL")
            or os.getenv("KOGWISTAR_OLLAMA_BASE_URL")
            or os.getenv("KG_DOC_PARSER_BASE_URL")
        )
        parser_api_key_env = (
            os.getenv("KOGWISTAR_LONGRUN_PARSER_API_KEY_ENV")
            or os.getenv("KOGWISTAR_PARSER_API_KEY_ENV")
            or os.getenv("KG_DOC_PARSER_API_KEY_ENV")
        )
        parser_api_version = (
            os.getenv("KOGWISTAR_LONGRUN_PARSER_API_VERSION")
            or os.getenv("KOGWISTAR_PARSER_API_VERSION")
            or os.getenv("KG_DOC_PARSER_API_VERSION")
        )
        parser_temperature_env = (
            os.getenv("KOGWISTAR_LONGRUN_PARSER_TEMPERATURE")
            or os.getenv("KOGWISTAR_PARSER_TEMPERATURE")
            or os.getenv("KG_DOC_PARSER_TEMPERATURE")
        )
        parser_max_retries_env = (
            os.getenv("KOGWISTAR_LONGRUN_PARSER_MAX_RETRIES")
            or os.getenv("KOGWISTAR_PARSER_MAX_RETRIES")
            or os.getenv("KG_DOC_PARSER_MAX_RETRIES")
        )
        resolved_parser_settings = resolve_parser_provider_settings(
            proposal_mode=(
                os.getenv("KOGWISTAR_LONGRUN_PARSER_PROPOSAL_MODE")
                or os.getenv("KOGWISTAR_PARSER_PROPOSAL_MODE")
                or os.getenv("KG_DOC_PARSER_PROPOSAL_MODE")
            ),
            provider=parser_provider,
            model=parser_model,
            temperature=float(parser_temperature_env) if parser_temperature_env else None,
            base_url=parser_base_url_env,
            api_key_env=parser_api_key_env,
            api_version=parser_api_version,
            max_retries=int(parser_max_retries_env) if parser_max_retries_env else None,
        )
        parser_spec = resolved_parser_settings.parser
        return cls(
            enabled=(
                os.getenv("KOGWISTAR_LLM_WIKI_LONGRUN") == "1"
                or backend in {"postgres", "pgvector"}
            ),
            mode=os.getenv("KOGWISTAR_LONGRUN_MODE", "auto").strip().lower() or "auto",
            doc_count=doc_count,
            doc_limit=doc_limit or None,
            recovery_doc_limit=recovery_doc_limit or None,
            recovery_attempts_per_doc=recovery_attempts_per_doc,
            doc_profile=doc_profile,
            corpus_profile=corpus_profile,
            parser_provider=parser_spec.provider,
            parser_model=parser_spec.model,
            parser_proposal_mode=resolved_parser_settings.proposal_mode,
            parser_temperature=parser_spec.temperature,
            parser_base_url=parser_spec.base_url or "http://localhost:11434",
            parser_api_key_env=parser_spec.api_key_env,
            parser_api_version=parser_spec.api_version,
            parser_max_retries=parser_spec.max_retries,
            backend=backend,
            parser_lane=parser_lane,
            conversation_persistence_mode=conversation_persistence_mode,
            parse_timeout_seconds=parse_timeout_seconds,
            operation_mode=operation_mode,
            maintenance_schedule_mode=maintenance_schedule_mode,
            maintenance_workers=maintenance_workers,
            maintenance_steps_per_slice=maintenance_steps_per_slice,
            maintenance_llm_calls_per_slice=maintenance_llm_calls_per_slice,
            maintenance_seconds_per_slice=maintenance_seconds_per_slice,
            pg_database_mode=pg_database_mode,
            max_runtime_seconds=max_runtime_seconds,
            max_llm_calls=max_llm_calls,
            dsn=dsn,
            ollama_model=parser_spec.model,
            ollama_base_url=parser_spec.base_url or "http://localhost:11434",
            max_repeated_systemic_errors=int(
                os.getenv("KOGWISTAR_LONGRUN_MAX_REPEATED_SYSTEMIC_ERRORS", "3")
            ),
            max_post_doc_maintenance_steps=int(
                os.getenv("KOGWISTAR_LONGRUN_MAX_POST_DOC_MAINTENANCE_STEPS", "100")
            ),
            token_min=token_min,
            token_max=token_max,
            checkpoint_run_dir=os.getenv("KOGWISTAR_LONGRUN_RUN_DIR"),
            resume_probe_enabled=os.getenv("KOGWISTAR_LONGRUN_RESUME_PROBE") == "1",
            skip_maintenance_invariant=os.getenv("KOGWISTAR_LONGRUN_SKIP_MAINTENANCE_INVARIANT") == "1",
            live_trace=env_flag_enabled(
                "KOGWISTAR_LONGRUN_LIVE_TRACE",
                "KOGWISTAR_LLM_WIKI_LIVE_TRACE",
                default=os.getenv("KOGWISTAR_LLM_WIKI_LONGRUN") == "1",
            ),
            parser_workers=int(os.getenv("KOGWISTAR_LONGRUN_PARSER_WORKERS", "1")),
            experiment_run=os.getenv("KOGWISTAR_LONGRUN_EXPERIMENT_RUN", "").strip(),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "mode": self.mode,
            "doc_count": self.doc_count,
            "doc_limit": self.doc_limit,
            "recovery_doc_limit": self.recovery_doc_limit,
            "recovery_attempts_per_doc": self.recovery_attempts_per_doc,
            "doc_profile": self.doc_profile,
            "corpus_profile": self.corpus_profile,
            "parser_provider": self.parser_provider,
            "parser_model": self.parser_model,
            "parser_proposal_mode": self.parser_proposal_mode,
            "parser_temperature": self.parser_temperature,
            "parser_base_url": self.parser_base_url,
            "parser_api_key_env": self.parser_api_key_env,
            "parser_api_version": self.parser_api_version,
            "parser_max_retries": self.parser_max_retries,
            "backend": self.backend,
            "parser_lane": self.parser_lane,
            "conversation_persistence_mode": self.conversation_persistence_mode,
            "operation_mode": self.operation_mode,
            "maintenance_schedule_mode": self.maintenance_schedule_mode,
            "maintenance_workers": self.maintenance_workers,
            "maintenance_steps_per_slice": self.maintenance_steps_per_slice,
            "maintenance_llm_calls_per_slice": self.maintenance_llm_calls_per_slice,
            "maintenance_seconds_per_slice": self.maintenance_seconds_per_slice,
            "pg_database_mode": self.pg_database_mode,
            "parse_timeout_seconds": self.parse_timeout_seconds,
            "max_runtime_seconds": self.max_runtime_seconds,
            "max_llm_calls": self.max_llm_calls,
            "dsn_present": self.dsn is not None,
            "resume_probe_enabled": self.resume_probe_enabled,
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
            "checkpoint_run_dir": self.checkpoint_run_dir,
            "corpus_fingerprint": self.corpus_fingerprint,
            "skip_maintenance_invariant": self.skip_maintenance_invariant,
            "live_trace": self.live_trace,
            "parser_workers": self.parser_workers,
            "experiment_run": self.experiment_run,
        }


@dataclass
class DocumentRecord:
    doc_id: str
    title: str
    source_uri: str
    input_path: Path
    current_path: Path
    status: str = "PENDING"
    started_at_ms: int | None = None
    ended_at_ms: int | None = None
    token_count: int | None = None
    run_id: str | None = None
    parser_workflow_run_id: str | None = None
    parser_resume_requested: bool = False
    source_document_id: str | None = None
    maintenance_job_id: str | None = None
    candidate_link_id: str | None = None
    promotion_evidence_pack_id: str | None = None
    promotion_candidate_id: str | None = None
    promoted_entity_id: str | None = None
    parsed_node_ids: list[str] = field(default_factory=list)
    parsed_edge_ids: list[str] = field(default_factory=list)
    resume_checkpoint_step_seq: int | None = None
    resume_checkpoint_namespace: str | None = None
    resume_suspended_node_id: str | None = None
    resume_suspended_token_id: str | None = None
    resumed_from_checkpoint: bool = False
    recovery_attempt_count: int = 0
    recovery_selected: bool = False
    last_step_name: str | None = None
    last_step_at_ms: int | None = None
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
    details: dict[str, Any] = field(default_factory=dict)


def _resolve_longrun_dsn() -> str | None:
    for env_name in (
        "KOGWISTAR_LONGRUN_DSN",
        "KOGWISTAR_LLM_WIKI_TEST_PG_DSN",
        "GKE_PG_DSN",
        "PG_DSN",
        "DATABASE_URL",
    ):
        value = os.getenv(env_name)
        if value:
            return value
    return None


def _append_trace_line(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{_now_ms()} | {message}\n")
    if env_flag_enabled(
        "KOGWISTAR_LONGRUN_LIVE_TRACE",
        "KOGWISTAR_LLM_WIKI_LIVE_TRACE",
        default=os.getenv("KOGWISTAR_LLM_WIKI_LONGRUN") == "1",
    ):
        LiveTracePrinter(prefix="longrun.parser").emit({"stage": "parser_trace", "message": message})


def _read_text_tail(path: Path | None, *, max_lines: int = 80, max_chars: int = 12_000) -> list[str]:
    """Read a bounded diagnostic tail without making failure reporting fail."""
    if path is None:
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, OSError, UnicodeError):
        return []
    if max_lines > 0:
        lines = lines[-max_lines:]
    if max_chars <= 0:
        return lines
    remaining = max_chars
    bounded: list[str] = []
    for line in reversed(lines):
        if remaining <= 0:
            break
        bounded.append(line[:remaining])
        remaining -= len(line) + 1
    return list(reversed(bounded))


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return dict(value) if isinstance(value, dict) else {}


def _emit_longrun_live(stage: str, **fields: object) -> None:
    if env_flag_enabled(
        "KOGWISTAR_LONGRUN_LIVE_TRACE",
        "KOGWISTAR_LLM_WIKI_LIVE_TRACE",
        default=os.getenv("KOGWISTAR_LLM_WIKI_LONGRUN") == "1",
    ):
        LiveTracePrinter(prefix="longrun").emit({"stage": stage, **fields})


def _write_json_file(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


class ErrorCircuitBreaker:
    """Stops a run when infrastructure or one identical parser failure repeats."""

    def __init__(self, threshold: int) -> None:
        self.threshold = threshold
        self._docs_by_fingerprint: dict[str, set[str]] = defaultdict(set)
        self._counts: Counter[str] = Counter()

    def record(self, failure: FailureRecord) -> bool:
        # A parser failure is normally document-local. The exact same normalized
        # parser failure across unrelated documents is a configuration or provider
        # problem, so it must not consume the whole corpus budget.
        should_track = failure.scope == "systemic" or failure.code == "document_parse_failed"
        if not should_track:
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
            "tracked_failure_codes": ["document_parse_failed", *sorted(SYSTEMIC_FAILURES)],
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
        self._write_json("progress_summary.json", self.harness.progress_summary())
        self._write_json("recovery_summary.json", self.harness.recovery_summary())
        self._write_json("promotion_provenance_summary.json", self.harness.promotion_provenance_summary())
        self._write_json("graph_export.json", self.harness.graph_export())
        self._write_json("projection_summary.json", self.harness.projection_summary())
        self._write_json("maintenance_summary.json", self.harness.maintenance_summary())
        self._write_json(
            "maintenance_statistics.json",
            self.harness.maintenance_statistics(),
        )
        self._write_json("llm_calls_summary.json", self.harness.llm_summary())
        parser_trace_tails: dict[str, list[str]] = {}
        for parser_trace in sorted((self.harness.run_dir / "parser_runs").glob("*/trace.log")):
            try:
                parser_trace_tails[parser_trace.parent.name] = (
                    parser_trace.read_text(encoding="utf-8").splitlines()[-200:]
                )
            except Exception as exc:  # noqa: BLE001
                parser_trace_tails[parser_trace.parent.name] = [
                    f"trace unavailable: {type(exc).__name__}: {exc}"
                ]
        self._write_json("parser_trace_tails.json", parser_trace_tails)
        # Preserve the original single-document artifact for existing inspection tools.
        if "doc-001" in parser_trace_tails:
            self._write_json("parser_trace_tail.json", parser_trace_tails["doc-001"])
        self._write_jsonl("sampled_prompts_and_responses.jsonl", [])
        self._copy_raw_documents()
        if final:
            self._write_report(reason=reason)
        else:
            self._write_report(reason=reason, filename="interim_report.md")
        if final:
            zip_path = self.run_dir / "longrun-dump.zip"
            with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
                for path in self.dump_dir.rglob("*"):
                    if path.is_file():
                        archive.write(path, path.relative_to(self.dump_dir))
            self._write_json("dump_package.json", {"zip_path": str(zip_path)})
        return self.dump_dir

    def prepare_final_report(self, *, reason: str) -> Path:
        """Freeze report text before resource close without publishing it yet."""
        path = self.dump_dir / "final_report.pending.md"
        self._write_report(reason=reason, filename=path.name)
        return path

    def commit_final_report(self, *, reason: str, report_path: Path) -> Path:
        """Publish the frozen report only after runtime resources are closed."""
        final_path = self.dump_dir / "final_report.md"
        report_path.replace(final_path)
        terminal = {
            "status": self.harness.lifecycle_status,
            "completed_at_ms": _now_ms(),
            "report_path": str(final_path),
            "reason": reason,
            "run_id": self.harness.run_id,
        }
        self._write_json("run_terminal.json", terminal)
        zip_path = self.run_dir / "longrun-dump.zip"
        with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in self.dump_dir.rglob("*"):
                if path.is_file():
                    archive.write(path, path.relative_to(self.dump_dir))
        self._write_json("dump_package.json", {"zip_path": str(zip_path)})
        return final_path

    def _write_json(self, name: str, payload: Any) -> None:
        path = self.dump_dir / name
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(
            json.dumps(_jsonable(payload), indent=2, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(path)

    def write_parser_layer_log(self, *, doc_id: str, layer_log: list[object]) -> Path:
        """Persist one parser layer log without letting documents overwrite each other."""
        path = self.dump_dir / "parser_layer_logs" / f"{doc_id}.json"
        _write_json_file(path, _jsonable(layer_log))
        if doc_id == "doc-001":
            self._write_json("parser_layer_log.json", layer_log)
        return path

    def _write_jsonl(self, name: str, rows: list[dict[str, Any]]) -> None:
        path = self.dump_dir / name
        with path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(_jsonable(row), sort_keys=True) + "\n")

    def append_failure_event(self, failure: FailureRecord) -> None:
        """Durably capture failures even when the run never reaches its final dump."""
        row = {
            "event_type": "longrun_failure_recorded",
            "recorded_at_ms": _now_ms(),
            **failure.__dict__,
        }
        path = self.dump_dir / "failure_events.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(_jsonable(row), sort_keys=True) + "\n")

    def _copy_raw_documents(self) -> None:
        target = self.dump_dir / "raw_documents"
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True, exist_ok=True)
        for record in self.harness.records:
            if record.current_path.exists():
                shutil.copy2(record.current_path, target / record.current_path.name)

    def _write_report(self, *, reason: str, filename: str = "final_report.md") -> None:
        counts = Counter(record.status for record in self.harness.records)
        progress = self.harness.progress_summary()
        recovery = self.harness.recovery_summary()
        parser_eval = self.harness.parser_eval_summary()
        parser_eval_json = json.dumps(_jsonable(parser_eval), indent=2, sort_keys=True)
        lines = [
            "# Long-Run Workflow Diagnostic Report",
            "",
            f"- Reason: `{reason}`",
            f"- Run id: `{self.harness.run_id}`",
            f"- Workspace: `{self.harness.config.workspace_id}`",
            f"- Dump directory: `{self.dump_dir}`",
            f"- Dump zip: `{self.run_dir / 'longrun-dump.zip'}`",
            f"- Current document: `{progress['current_document_id']}`",
            f"- Current step: `{progress['current_step']}`",
            f"- Last completed step: `{progress['last_completed_step']}`",
            f"- Last progress at ms: `{progress['last_progress_at_ms']}`",
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
                "",
                "## Progress Summary",
                "",
                f"```json\n{json.dumps(_jsonable(progress), indent=2, sort_keys=True)}\n```",
                "",
                "## LLM Call Budget",
                "",
                f"- Corpus fingerprint: `{self.harness.config.corpus_fingerprint}`",
                f"- Call count: `{self.harness.llm_call_count}`",
                f"- Call budget: `{self.harness.config.max_llm_calls}`",
                f"- Runtime budget seconds: `{self.harness.config.max_runtime_seconds}`",
                "",
                "## Parser Evaluation",
                "",
                f"- Parser provider: `{self.harness.config.parser_provider}`",
                f"- Parser model: `{self.harness.config.parser_model}`",
                f"- Composite verdict: `{parser_eval['composite_verdict'] or 'n/a'}`",
                f"- Average basic sense score: `{parser_eval['average_basic_sense_score'] if parser_eval['average_basic_sense_score'] is not None else 'n/a'}`",
                f"- Evaluated documents: `{parser_eval['evaluated_count']}/{parser_eval['document_count']}`",
                f"- Total input tokens: `{parser_eval['usage_totals']['input_tokens']}`",
                f"- Cached input tokens: `{parser_eval['usage_totals']['cached_input_tokens']}`",
                f"- Total output tokens: `{parser_eval['usage_totals']['output_tokens']}`",
                f"- Total tokens: `{parser_eval['usage_totals']['total_tokens']}`",
                f"- Total cost: `{parser_eval['usage_totals']['total_cost']}`",
                f"- Cost status: `{parser_eval['usage_totals']['cost_status']}`",
                f"- Slowest parser stage: `{parser_eval['timing_summary']['dominant_stage'] or 'n/a'}`",
                f"- Slowest stage total ms: `{parser_eval['timing_summary']['dominant_stage_total_ms']}`",
                f"- Slowest operation stage: `{parser_eval['timing_summary']['dominant_operation_stage'] or 'n/a'}`",
                f"- Slowest operation total ms: `{parser_eval['timing_summary']['dominant_operation_stage_total_ms']}`",
                "",
                f"```json\n{parser_eval_json}\n```",
                "",
                "## Recovery Summary",
                "",
                f"```json\n{json.dumps(_jsonable(recovery), indent=2, sort_keys=True)}\n```",
                "",
                "## Promotion Provenance Summary",
                "",
                f"```json\n{json.dumps(_jsonable(self.harness.promotion_provenance_summary()), indent=2, sort_keys=True)}\n```",
            ]
        )
        (self.dump_dir / filename).write_text("\n".join(lines) + "\n", encoding="utf-8")


class LongRunHarness:
    """Single-daemon-style test harness around runtime workflow ingestion."""

    def __init__(self, *, run_dir: Path, config: LongRunConfig) -> None:
        self.requested_run_dir = run_dir
        self.config = config
        self.run_dir = self._resolve_experiment_run_dir(run_dir, config)
        self.run_id = f"longrun-{stable_id('llm_wiki.longrun', str(self.run_dir), config.corpus_fingerprint)}"
        self.records: list[DocumentRecord] = []
        self.contexts: dict[str, DocumentRecord] = {}
        self.status_transitions: list[dict[str, Any]] = []
        self.failure_records: list[FailureRecord] = []
        self.circuit_breaker = ErrorCircuitBreaker(config.max_repeated_systemic_errors)
        self.maintenance_poll_count = 0
        self.projection_poll_count = 0
        self.llm_call_count = 0
        self.aborted = False
        self.abort_reason: str | None = None
        self.abort_preserves_pending = False
        self.early_stop_reason: str | None = None
        self._engines: Any | None = None
        self._engines_closed = False
        self._pipeline: IngestPipeline | None = None
        self._maintenance_workers: list[MaintenanceWorker] = []
        self._projection_worker: ProjectionWorker | None = None
        self.active_document_id: str | None = None
        self.active_step_name: str | None = None
        self.last_completed_step_name: str | None = None
        self.last_progress_at_ms: int | None = None
        self.parser_heartbeat: dict[str, Any] | None = None
        self.checkpoint_loaded = False
        self.checkpoint_manifest_path: Path | None = None
        self.resume_gate_consumed = False
        self.recovery_skipped_document_ids: list[str] = []
        self.recovery_missing_payload_document_ids: list[str] = []
        self.live_trace_printer = LiveTracePrinter(prefix="longrun") if config.live_trace else None
        self.run_started_monotonic: float | None = None
        self.lifecycle_status = "running"
        self.lifecycle_events: list[dict[str, Any]] = []
        self.maintenance_health: dict[str, Any] = {
            "active_worker_ids": [], "active_job_id": None,
            "active_document_id": None, "current_maintenance_kind": None,
            "phase_started_at_ms": None, "last_trace_timestamp_ms": None,
            "lease_age_seconds": None, "queue_pending_count": None,
            "queue_doing_count": None, "queue_failed_count": None,
            "last_error": None,
        }
        self._pending_final_report: tuple[str, Path] | None = None
        self._state_lock = threading.RLock()
        self._maintenance_trace_lock = threading.Lock()
        self.dumper = DiagnosticDumper(self.run_dir, self)

    @staticmethod
    def _resolve_experiment_run_dir(run_dir: Path, config: LongRunConfig) -> Path:
        """Keep different fingerprints from sharing a resettable dump folder.

        Existing same-fingerprint directories remain compatible with the old
        launch configuration, so continue/retry runs do not need a migration.
        A fresh run with a different fingerprint is placed under
        ``experiments/<fingerprint>`` below the requested directory.
        """
        run_dir = run_dir.expanduser().resolve()
        config_path = run_dir / "dump" / "run_config.json"
        existing_fingerprint: str | None = None
        if config_path.exists():
            try:
                payload = json.loads(config_path.read_text(encoding="utf-8"))
                value = payload.get("corpus_fingerprint")
                existing_fingerprint = str(value) if value else None
            except (OSError, UnicodeError, json.JSONDecodeError):
                existing_fingerprint = None

        same_fingerprint = existing_fingerprint == config.corpus_fingerprint
        if same_fingerprint:
            return run_dir

        # Older checkpoints predate run_config.json.  Resume modes must still
        # inspect those artifacts in place; otherwise the compatibility
        # resolver would move the manifest away before _load_checkpoint_state
        # can validate its document rows.
        legacy_manifest = run_dir / "dump" / "manifest.jsonl"
        if config.mode in {"continue", "retry_failed"} and legacy_manifest.exists():
            return run_dir

        has_existing_artifacts = run_dir.exists() and any(run_dir.iterdir())
        if not has_existing_artifacts:
            return run_dir

        # Unknown or mismatched content is never reset in place. This also
        # protects artifacts from older harness versions without run_config.
        isolated = run_dir / "experiments" / config.corpus_fingerprint
        isolated.mkdir(parents=True, exist_ok=True)
        return isolated

    @property
    def engines(self) -> Any:
        if self._engines is None:
            self._rebuild_runtime_objects()
        return self._engines

    @property
    def pipeline(self) -> IngestPipeline:
        if self._pipeline is None:
            self._rebuild_runtime_objects()
        assert self._pipeline is not None
        return self._pipeline

    @property
    def maintenance_worker(self) -> MaintenanceWorker:
        if not self._maintenance_workers:
            self._rebuild_runtime_objects()
        assert self._maintenance_workers
        return self._maintenance_workers[0]

    @property
    def projection_worker(self) -> ProjectionWorker:
        if self._projection_worker is None:
            self._rebuild_runtime_objects()
        assert self._projection_worker is not None
        return self._projection_worker

    def _build_namespace_engines(self):
        backend = self.config.backend.strip().lower()
        base_dir = self.run_dir / "engines"
        if backend == "chroma":
            return build_persistent_namespace_engines(base_dir)
        if backend in {"postgres", "pgvector"}:
            if not self.config.dsn:
                raise ValueError(
                    "postgres/pgvector long-run backend requires a DSN; set "
                    "KOGWISTAR_LONGRUN_DSN or KOGWISTAR_LLM_WIKI_TEST_PG_DSN"
                    " (the pgvector probe can also obtain one from the pytest "
                    "testcontainers fixture when Docker is available)"
                )
            return build_postgres_namespace_engines(
                base_dir=base_dir,
                dsn=self.config.dsn,
            )
        raise ValueError(
            "unsupported long-run backend: "
            f"{self.config.backend!r}; expected chroma, postgres, or pgvector"
        )

    def _rebuild_runtime_objects(self) -> None:
        if self.live_trace_printer is not None:
            self.live_trace_printer.emit(
                {
                    "stage": "rebuild_runtime_objects_start",
                    "run_id": self.run_id,
                    "backend": self.config.backend,
                    "live_trace": self.config.live_trace,
                }
            )
        if self._engines is not None and not self._engines_closed:
            self._close_runtime_objects(reason="runtime_rebuild")
        engines = self._build_namespace_engines()
        self._engines = engines
        self._engines_closed = False
        self._pipeline = IngestPipeline(engines, live_trace=self.config.live_trace)
        self._pipeline.parser = self._build_parser()
        fair_maintenance = (
            self.config.operation_mode == "maintenance_first"
            and self.config.maintenance_schedule_mode == "fair_slices"
        )
        self._maintenance_workers = [
            MaintenanceWorker(
                engines,
                fair_scheduling=fair_maintenance,
                maintenance_steps_per_slice=self.config.maintenance_steps_per_slice,
                maintenance_llm_calls_per_slice=self.config.maintenance_llm_calls_per_slice,
                maintenance_seconds_per_slice=self.config.maintenance_seconds_per_slice,
                worker_id=f"maintenance-{index + 1}",
                trace_sink=self._emit_maintenance_trace,
                document_parser=self._maintenance_parse_document,
            )
            for index in range(self.config.maintenance_workers)
        ]
        self._projection_worker = ProjectionWorker(engines)
        self.dumper = DiagnosticDumper(self.run_dir, self)
        if self.live_trace_printer is not None:
            self.live_trace_printer.emit({"stage": "rebuild_runtime_objects_complete", "run_id": self.run_id})

    def _maintenance_parse_document(self, ctx: Any) -> dict[str, object]:
        """Parse a seeded source as one planner-owned maintenance phase.

        The parser runs outside graph transactions. Only the resulting graph
        extraction is persisted, so the durable maintenance job can safely be
        retried or advanced to the next phase.
        """
        pipeline = self.pipeline
        workspace_id = str(ctx.workspace_id)
        source_document_id = str(ctx.payload.get("source_document_id") or "")
        ns = WorkspaceNamespaces(workspace_id)
        with _temporary_namespace(self.engines.kg, ns.source_space):
            document = self.engines.kg.read.get_document(source_document_id)
        metadata = dict(document.metadata or {})
        request = IngestPipelineRequest(
            workspace_id=workspace_id,
            source_uri=str(metadata.get("source_uri") or source_document_id),
            title=str(metadata.get("title") or source_document_id),
            raw_text=str(document.content or ""),
            source_format=str(metadata.get("source_format") or "text"),
            operation_mode="parse_first",
            parser_mode=str(metadata.get("parser_mode") or "heuristic"),
            parser_lane=str(metadata.get("parser_lane") or "page_index"),
            promotion_mode=str(metadata.get("promotion_mode") or "pending"),
            llm_provider=(str(metadata["llm_provider"]) if metadata.get("llm_provider") else None),
            llm_model=(str(metadata["llm_model"]) if metadata.get("llm_model") else None),
        )
        parse_result = pipeline.parse_source(
            request=request,
            source_document_id=source_document_id,
        )
        pipeline.create_parse_retry_history(
            request=request,
            source_document_id=source_document_id,
            parse_result=parse_result,
            namespace=ns.conv_bg,
        )
        extraction = pipeline.translate_parse_result(
            parse_result=parse_result,
            source_document_id=source_document_id,
        )
        pipeline.ingest_parse_result(
            request=request,
            source_document_id=source_document_id,
            graph_extraction=extraction,
            namespace=ns.conv_fg,
        )
        pipeline.record_source_readiness(
            request=request,
            source_document_id=source_document_id,
            stage="parsed_graph_persisted",
        )
        return {
            "node_count": len(extraction.nodes or []),
            "edge_count": len(extraction.edges or []),
            "llm_call_count": int(
                dict(getattr(parse_result, "usage_summary", {}) or {}).get("llm_call_count") or 0
            ),
        }

    def _build_runtime_event_sink(self, *, downstream_sink: Any | None = None) -> LongRunJsonlTraceSink:
        return LongRunJsonlTraceSink(
            jsonl_path=self.dumper.dump_dir / "runtime_events.jsonl",
            downstream_sink=downstream_sink,
            enrich_event=self._enrich_runtime_event,
            live_trace=self.config.live_trace,
        )

    def _workflow_node_labels(self) -> dict[str, str]:
        workflow_id = self._workflow_id()
        labels = {"start": "start", "run": "workflow_run"}
        for step_name in self._workflow_steps():
            labels[str(stable_id("wf_node", workflow_id, step_name))] = step_name
        labels[str(stable_id("wf_node", workflow_id, "done"))] = "done"
        return labels

    def _enrich_runtime_event(self, event: dict[str, Any]) -> dict[str, Any]:
        enriched = dict(event)
        node_id = enriched.get("node_id")
        if node_id is not None:
            step_name = self._workflow_node_labels().get(str(node_id))
            if step_name is not None:
                enriched.setdefault("step_name", step_name)
        return enriched

    def prepare(self) -> None:
        if self.live_trace_printer is not None:
            self.live_trace_printer.emit(
                {
                    "stage": "prepare_start",
                    "run_id": self.run_id,
                    "mode": self.config.mode,
                    "backend": self.config.backend,
                    "doc_count": self.config.doc_count,
                    "run_dir": str(self.run_dir),
                }
            )
        self._prepare_run_directory()
        loaded = False
        if self.config.mode == "continue":
            if self.live_trace_printer is not None:
                self.live_trace_printer.emit({"stage": "prepare_load_checkpoint_start", "run_id": self.run_id})
            loaded = self._load_checkpoint_state(strict=True)
            if not loaded:
                raise AssertionError(
                    "continue mode requires a checkpoint manifest matching the configured "
                    f"doc_count={self.config.doc_count} in {self.dumper.dump_dir}"
                )
        elif self.config.mode == "retry_failed":
            if self.live_trace_printer is not None:
                self.live_trace_printer.emit({"stage": "prepare_load_checkpoint_start", "run_id": self.run_id})
            loaded = self._load_checkpoint_state(strict=True)
            if not loaded:
                raise AssertionError(
                    "retry_failed mode requires a checkpoint manifest matching the configured "
                    f"doc_count={self.config.doc_count} in {self.dumper.dump_dir}"
                )
        elif self.config.mode == "auto":
            if self.live_trace_printer is not None:
                self.live_trace_printer.emit({"stage": "prepare_load_checkpoint_start", "run_id": self.run_id})
            loaded = self._load_checkpoint_state(strict=False)
        elif self.config.mode != "fresh":
            raise ValueError(
                f"unsupported KOGWISTAR_LONGRUN_MODE={self.config.mode!r}; "
                "expected fresh, continue, auto, or retry_failed"
            )
        if not loaded:
            if self.live_trace_printer is not None:
                self.live_trace_printer.emit({"stage": "prepare_reset_run_directory_start", "run_id": self.run_id})
            # Fresh preparation owns the requested run root.  When a
            # fingerprint mismatch caused this harness to resolve into an
            # experiment subdirectory, clear the old root before building any
            # engines as well; otherwise stale root-level engine state can
            # survive beside the isolated run and be observed by setup hooks.
            if self.config.mode == "fresh" and self.requested_run_dir != self.run_dir:
                self._reset_directory(self.requested_run_dir)
            self._reset_run_directory()
            if self.live_trace_printer is not None:
                self.live_trace_printer.emit({"stage": "prepare_reset_run_directory_complete", "run_id": self.run_id})
        self._rebuild_runtime_objects()
        self._prepare_run_directory()
        if self.live_trace_printer is not None:
            self.live_trace_printer.emit({"stage": "prepare_materialize_designs_start", "run_id": self.run_id})
        materialize_maintenance_designs(self.engines.workflow)
        self._materialize_workflow_design()
        if self.live_trace_printer is not None:
            self.live_trace_printer.emit({"stage": "prepare_materialize_designs_complete", "run_id": self.run_id})
        if not loaded:
            if self.live_trace_printer is not None:
                self.live_trace_printer.emit({"stage": "prepare_generate_corpus_start", "run_id": self.run_id})
            self._generate_corpus()
            if self.live_trace_printer is not None:
                self.live_trace_printer.emit({"stage": "prepare_generate_corpus_complete", "run_id": self.run_id})
        else:
            if self.live_trace_printer is not None:
                self.live_trace_printer.emit({"stage": "prepare_restore_checkpoint_start", "run_id": self.run_id})
            self._restore_checkpoint_history()
            self._normalize_recovery_attempt_counts()
            self._restore_missing_documents_from_dump()
            self._restore_progress_from_records()
            if self.config.mode == "retry_failed":
                self._prepare_failed_document_recovery()
            if self.live_trace_printer is not None:
                self.live_trace_printer.emit({"stage": "prepare_restore_checkpoint_complete", "run_id": self.run_id})
        self.dumper.dump(reason="prepared")
        if self.live_trace_printer is not None:
            self.live_trace_printer.emit({"stage": "prepare_complete", "run_id": self.run_id})

    def run(self) -> None:
        started = time.monotonic()
        self.run_started_monotonic = started
        idle_loops = 0
        self._lifecycle("run_started", status="running", started_at_ms=_now_ms())
        self.dumper.dump(reason="run_started")
        self._log_heartbeat(phase="run_started", started=started)
        if self.config.parser_workers > 1:
            self._run_parallel_documents(started=started)
            return
        processed_documents = 0
        for record in self.records:
            if record.status in TERMINAL_STATES:
                continue
            if self.config.mode == "retry_failed" and not record.recovery_selected:
                continue
            if self.aborted:
                break
            if self.config.doc_limit is not None and processed_documents >= self.config.doc_limit:
                self._stop_early(
                    f"document limit reached ({self.config.doc_limit}/{self.config.doc_count})"
                )
                break
            processed_documents += 1
            budget_stop_reason = self._budget_stop_reason(started=started)
            if budget_stop_reason is not None:
                self._abort(budget_stop_reason, preserve_pending=True)
                break
            previous_state = self._state_signature()
            outcome = "succeeded"
            try:
                outcome = self._run_document_workflow(record)
            except Exception as exc:  # noqa: BLE001
                failure = self._classify_exception(exc, doc_id=record.doc_id, phase="runtime")
                self._record_failure(failure)
                self._move_failed_or_quarantine(record, failure)
                self._log_heartbeat(phase=f"failed_{record.doc_id}", started=started)
                self._safe_failure_dump(reason=f"failure_{record.doc_id}")
                if self.circuit_breaker.record(failure):
                    self._abort(f"circuit breaker tripped: {failure.fingerprint}")
                    break
                continue
            if outcome == "suspended":
                self.dumper.dump(reason=f"suspended_{record.doc_id}", final=True)
                return
            self._poll_maintenance_once(phase="document_loop")
            if self._state_signature() == previous_state:
                idle_loops += 1
                if idle_loops > self.config.max_idle_loops:
                    self._abort("runtime_worker_stuck: no state changes while work remained")
                    break
            else:
                idle_loops = 0
            budget_stop_reason = self._budget_stop_reason(started=started)
            if budget_stop_reason is not None:
                self._abort(budget_stop_reason, preserve_pending=True)
                break
            self._mark_document_progress(record, step_name="document_complete")
            self._log_heartbeat(phase=f"checkpoint_{record.doc_id}", started=started)
            self.dumper.dump(reason=f"checkpoint_{record.doc_id}")

        self._finalize_run(started=started)

    def _run_parallel_documents(self, *, started: float) -> None:
        """Run independent document workflows with serialized maintenance polling.

        Document workflows own their source revision and checkpoint. The
        foreground harness remains the only caller that polls maintenance and
        writes run-level terminal reports.
        """
        candidates = [
            record
            for record in self.records
            if record.status not in TERMINAL_STATES
            and (self.config.mode != "retry_failed" or record.recovery_selected)
        ]
        if self.config.doc_limit is not None:
            candidates = candidates[: self.config.doc_limit]
            if len(candidates) < len([
                record for record in self.records
                if record.status not in TERMINAL_STATES
                and (self.config.mode != "retry_failed" or record.recovery_selected)
            ]):
                self._stop_early(
                    f"document limit reached ({self.config.doc_limit}/{self.config.doc_count})"
                )
        if not candidates:
            self._finalize_run(started=started)
            return

        self._emit_live_progress(
            "parser_pool_start",
            parser_workers=self.config.parser_workers,
            document_ids=[record.doc_id for record in candidates],
        )
        with ThreadPoolExecutor(
            max_workers=self.config.parser_workers,
            thread_name_prefix="llm-wiki-parser",
        ) as executor:
            futures = {
                executor.submit(self._run_document_workflow, record): record
                for record in candidates
            }
            for future in as_completed(futures):
                record = futures[future]
                try:
                    outcome = future.result()
                except Exception as exc:  # noqa: BLE001
                    failure = self._classify_exception(exc, doc_id=record.doc_id, phase="runtime")
                    self._record_failure(failure)
                    self._move_failed_or_quarantine(record, failure)
                    self._log_heartbeat(phase=f"failed_{record.doc_id}", started=started)
                    self._safe_failure_dump(reason=f"failure_{record.doc_id}")
                    if self.circuit_breaker.record(failure):
                        self._abort(f"circuit breaker tripped: {failure.fingerprint}")
                        for pending in futures:
                            if not pending.done():
                                pending.cancel()
                        break
                    continue
                if outcome == "suspended":
                    self._abort(
                        "parallel parser workflow suspended at the run-level resume gate",
                        preserve_pending=True,
                    )
                    break
                self._poll_maintenance_once(phase="parallel_document_complete")
                self._mark_document_progress(record, step_name="document_complete")
                self._log_heartbeat(phase=f"checkpoint_{record.doc_id}", started=started)
                self.dumper.dump(reason=f"checkpoint_{record.doc_id}")
                budget_stop_reason = self._budget_stop_reason(started=started)
                if budget_stop_reason is not None:
                    self._abort(budget_stop_reason, preserve_pending=True)
                    for pending in futures:
                        if not pending.done():
                            pending.cancel()
                    break
        self._emit_live_progress(
            "parser_pool_complete",
            parser_workers=self.config.parser_workers,
            completed_document_ids=[
                record.doc_id for record in candidates if record.status == "COMPLETED"
            ],
        )
        self._finalize_run(started=started)

    async def run_async(self) -> None:
        """Async entry point using the same bounded durable document scheduler."""
        await asyncio.to_thread(self.run)

    def _finalize_run(self, *, started: float) -> None:
        try:
            if self.early_stop_reason is not None:
                self._lifecycle("maintenance_drain_completed", status="incomplete", reason=self.early_stop_reason)
                self._log_heartbeat(phase="document_limit_reached", started=started)
                self.dumper.dump(reason="document_limit_reached")
                self._pending_final_report = (
                    "document_limit_reached",
                    self.dumper.prepare_final_report(reason="document_limit_reached"),
                )
                return
            if self.aborted:
                self.dumper.dump(reason="abort_snapshot")
                if not self.abort_preserves_pending:
                    self._quarantine_processing_docs()
                self.lifecycle_status = "incomplete"
                self.dumper.dump(reason="abort_finalized")
                self._pending_final_report = (
                    "abort_finalized",
                    self.dumper.prepare_final_report(reason="abort_finalized"),
                )
                raise AssertionError(self.abort_reason or "long-run workflow aborted")
            try:
                finalize_started = time.monotonic()
                self._lifecycle("maintenance_drain_started", status="draining_maintenance")
                self._emit_live_progress("finalize_start")
                maintenance_started = time.monotonic()
                self._emit_live_progress("finalize_maintenance_drain_start")
                self._drain_maintenance_after_documents()
                self._lifecycle("maintenance_drain_completed", status="finalizing")
                self._emit_live_progress(
                    "finalize_maintenance_drain_complete",
                    duration_ms=round((time.monotonic() - maintenance_started) * 1000),
                )
                projection_started = time.monotonic()
                self._emit_live_progress("finalize_projection_start")
                self._poll_projection_once()
                self._emit_live_progress(
                    "finalize_projection_complete",
                    duration_ms=round((time.monotonic() - projection_started) * 1000),
                )
                invariant_started = time.monotonic()
                self._emit_live_progress("finalize_invariants_start")
                self._verify_run_invariants()
                self._emit_live_progress(
                    "finalize_invariants_complete",
                    duration_ms=round((time.monotonic() - invariant_started) * 1000),
                    total_finalize_duration_ms=round((time.monotonic() - finalize_started) * 1000),
                )
            except Exception as exc:  # noqa: BLE001
                self._emit_live_failure(
                    stage="run_invariant_failure",
                    message=f"{type(exc).__name__}: {exc}",
                    details={"failed_document_count": sum(record.status == "FAILED" for record in self.records)},
                )
                self._log_heartbeat(phase="invariant_failure", started=started)
                self.lifecycle_status = "failed"
                self._safe_failure_dump(reason="invariant_failure")
                try:
                    self._pending_final_report = (
                        "invariant_failure",
                        self.dumper.prepare_final_report(reason="invariant_failure"),
                    )
                except Exception:
                    logger.exception("Could not freeze invariant failure report")
                raise
            dump_started = time.monotonic()
            self._lifecycle("finalization_started", status="finalizing")
            self._emit_live_progress("finalize_success_dump_start")
            self.dumper.dump(reason="success")
            self.lifecycle_status = "completed"
            self._pending_final_report = (
                "success",
                self.dumper.prepare_final_report(reason="success"),
            )
            self._emit_live_progress(
                "finalize_success_dump_complete",
                duration_ms=round((time.monotonic() - dump_started) * 1000),
            )
        finally:
            if self._pending_final_report is not None:
                self._lifecycle("resource_close_started")
            self._close_runtime_objects(reason="run_finally")
            if self._pending_final_report is not None:
                reason, report_path = self._pending_final_report
                self._lifecycle("resource_close_completed")
                self.dumper.commit_final_report(reason=reason, report_path=report_path)
                self._lifecycle("final_report_written")
                self._lifecycle("run_terminal", status=self.lifecycle_status)
                self._pending_final_report = None

    def _close_runtime_objects(self, *, reason: str) -> None:
        """Release pooled engines after the durable snapshot is written."""
        if self._engines is None or self._engines_closed:
            return
        try:
            close_started = time.monotonic()
            self._emit_live_progress("runtime_engines_close_start", reason=reason)
            close = getattr(self._engines, "close", None)
            if callable(close):
                close()
            self._engines_closed = True
            self._emit_live_progress(
                "runtime_engines_closed",
                reason=reason,
                duration_ms=round((time.monotonic() - close_started) * 1000),
            )
        except Exception as exc:  # noqa: BLE001
            self._emit_live_failure(
                stage="runtime_cleanup_failure",
                message=f"{type(exc).__name__}: {exc}",
                details={"reason": reason},
            )
            raise

    def manifest_row(self, record: DocumentRecord) -> dict[str, Any]:
        elapsed_ms = None
        if record.started_at_ms is not None:
            end_ms = record.ended_at_ms or _now_ms()
            elapsed_ms = max(0, int(end_ms - record.started_at_ms))
        return {
            "run_id": record.run_id or self.run_id,
            "parser_workflow_run_id": record.parser_workflow_run_id,
            "parser_resume_requested": record.parser_resume_requested,
            "corpus_fingerprint": self.config.corpus_fingerprint,
            "doc_id": record.doc_id,
            "title": record.title,
            "source_uri": record.source_uri,
            "current_path": str(record.current_path),
            "status": record.status,
            "started_at_ms": record.started_at_ms,
            "ended_at_ms": record.ended_at_ms,
            "elapsed_ms": elapsed_ms,
            "token_count": record.token_count,
            "tokenizer_method": TOKENIZER_METHOD,
            "source_document_id": record.source_document_id,
            "maintenance_job_id": record.maintenance_job_id,
            "promotion_evidence_pack_id": record.promotion_evidence_pack_id,
            "promoted_entity_id": record.promoted_entity_id,
            "parsed_node_ids": list(record.parsed_node_ids),
            "parsed_edge_ids": list(record.parsed_edge_ids),
            "resume_checkpoint_step_seq": record.resume_checkpoint_step_seq,
            "resume_checkpoint_namespace": record.resume_checkpoint_namespace,
            "resume_suspended_node_id": record.resume_suspended_node_id,
            "resume_suspended_token_id": record.resume_suspended_token_id,
            "resumed_from_checkpoint": record.resumed_from_checkpoint,
            "recovery_attempt_count": record.recovery_attempt_count,
            "recovery_selected": record.recovery_selected,
            "last_step_name": record.last_step_name,
            "last_step_at_ms": record.last_step_at_ms,
            "llm_quality_failures": list(record.llm_quality_failures),
        }

    def folder_inventory(self) -> dict[str, list[str]]:
        return {
            name: sorted(path.name for path in (self.run_dir / name).glob("*") if path.is_file())
            for name in ("input", "processing", "completed", "failed", "quarantine", "dump", "parser_runs")
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
            "curated_kg": self._export_engine(
                self.engines.kg,
                where={"workspace_id": workspace_id},
                namespace=ns.curated_kg_space,
            ),
            "derived_knowledge": self._export_engine(
                self.engines.derived_knowledge_engine(),
                where={"workspace_id": workspace_id},
                namespace=ns.derived_knowledge,
            ),
            "workflow_events": self._export_engine(
                self.engines.conversation,
                where={"workflow_id": self._workflow_id()},
                namespace=ns.conv_bg,
            ),
        }

    def recovery_summary(self) -> dict[str, Any]:
        ns = WorkspaceNamespaces(self.config.workspace_id)
        report = self.engines.conversation.recovery.inspect(
            workspace_id=self.config.workspace_id,
            namespaces=[
                ns.conv_fg,
                ns.conv_bg,
                ns.maintenance_jobs,
                ns.projection_jobs,
                ns.curated_kg_space,
            ],
            app_surfaces=[
                RecoverySurface(
                    surface_id=f"{self.config.workspace_id}:longrun",
                    surface_kind="longrun_harness",
                    status="running" if not self.aborted else "aborted",
                details={
                    "run_id": self.run_id,
                    "backend": self.config.backend,
                    "dsn_present": self.config.dsn is not None,
                    "resume_probe_enabled": self.config.resume_probe_enabled,
                    "doc_count": len(self.records),
                    "current_document_id": self._active_document_id(),
                    "current_step": self._active_step_name(),
                    "last_completed_step": self._last_completed_step_name(),
                    "last_progress_at_ms": self._last_progress_at_ms(),
                    },
                )
            ],
        )
        return _jsonable(report)

    def progress_summary(self) -> dict[str, Any]:
        counts = Counter(record.status for record in self.records)
        active = next((record for record in self.records if record.status not in TERMINAL_STATES), None)
        return {
            "run_id": self.run_id,
            "workspace_id": self.config.workspace_id,
            "backend": self.config.backend,
            "parser_provider": self.config.parser_provider,
            "parser_model": self.config.parser_model,
            "parser_lane": self.config.parser_lane,
            "parser_workers": self.config.parser_workers,
            "maintenance_schedule_mode": self.config.maintenance_schedule_mode,
            "maintenance_workers": self.config.maintenance_workers,
            "maintenance_steps_per_slice": self.config.maintenance_steps_per_slice,
            "maintenance_llm_calls_per_slice": self.config.maintenance_llm_calls_per_slice,
            "maintenance_seconds_per_slice": self.config.maintenance_seconds_per_slice,
            "doc_profile": self.config.doc_profile,
            "corpus_fingerprint": self.config.corpus_fingerprint,
            "resume_probe_enabled": self.config.resume_probe_enabled,
            "skip_maintenance_invariant": self.config.skip_maintenance_invariant,
            "doc_count": len(self.records),
            "manifest_checkpoint_loaded": self.checkpoint_loaded,
            "manifest_checkpoint_path": (
                str(self.checkpoint_manifest_path) if self.checkpoint_manifest_path else None
            ),
            "completed_count": counts.get("COMPLETED", 0),
            "failed_count": counts.get("FAILED", 0),
            "quarantined_count": counts.get("QUARANTINED", 0),
            "suspended_count": counts.get("SUSPENDED", 0),
            "doc_limit": self.config.doc_limit,
            "recovery_doc_limit": self.config.recovery_doc_limit,
            "recovery_attempts_per_doc": self.config.recovery_attempts_per_doc,
            "recovery_attempted_document_ids": sorted(
                record.doc_id for record in self.records if record.recovery_attempt_count > 0
            ),
            "recovery_skipped_document_ids": sorted(self.recovery_skipped_document_ids),
            "recovery_missing_payload_document_ids": sorted(
                self.recovery_missing_payload_document_ids
            ),
            "early_stop_reason": self.early_stop_reason,
            "current_document_id": self.active_document_id or (active.doc_id if active else None),
            "current_step": self.active_step_name,
            "last_completed_step": self.last_completed_step_name,
            "last_progress_at_ms": self.last_progress_at_ms,
            "active_document_status": None if active is None else active.status,
            "suspended_document_ids": sorted(
                record.doc_id for record in self.records if record.status == "SUSPENDED"
            ),
            "resumed_document_ids": sorted(
                record.doc_id for record in self.records if record.resumed_from_checkpoint
            ),
            "parser_heartbeat": self.parser_heartbeat,
            "parser_eval": self.parser_eval_summary(),
            "llm_call_count": self.llm_call_count,
            "llm_call_budget": self.config.max_llm_calls,
            "lifecycle_status": self.lifecycle_status,
            "selected_nonterminal_document_ids": [
                record.doc_id for record in self.records
                if record.status not in TERMINAL_STATES
                and (self.config.mode != "retry_failed" or record.recovery_selected)
            ],
            "maintenance_health": dict(self.maintenance_health),
            "lifecycle_event_count": len(self.lifecycle_events),
        }

    def maintenance_statistics(self) -> dict[str, Any]:
        path = self.dumper.dump_dir / "maintenance_worker_trace.jsonl"
        rows = self._load_jsonl_rows(path) if path.exists() else []
        return build_maintenance_statistics(rows)

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
            step_rows = self._node_rows_with_metadata(
                self.engines.conversation,
                where=_and_where(
                    {"entity_type": "workflow_step_exec"},
                    {"workspace_id": self.config.workspace_id},
                ),
            )
        maintenance_steps = [
            row
            for row in step_rows
            if (
                str((row["metadata"] or {}).get("workflow_id") or "") == DERIVED_KNOWLEDGE_WORKFLOW_ID
                or str((row["metadata"] or {}).get("workflow_id") or "").startswith("maintenance.")
            )
        ]
        derived_rows = self._node_rows_with_metadata(
            self.engines.derived_knowledge_engine(),
            where=_and_where(
                {"artifact_kind": "derived_knowledge"},
                {"workspace_id": self.config.workspace_id},
            )
        )
        return {
            "maintenance_poll_count": self.maintenance_poll_count,
            "job_status_counts": dict(Counter(str(job.status) for job in jobs)),
            "jobs": [_job_to_dict(job) for job in jobs],
            "maintenance_job_ids": [str(job.job_id) for job in jobs],
            "maintenance_source_document_ids": sorted(
                {
                    str(job.entity_id)
                    for job in jobs
                    if str(getattr(job, "entity_id", "") or "")
                }
            ),
            "maintenance_lane_messages": [_lane_row_to_dict(row) for row in lane_rows],
            "foreground_replies": [_lane_row_to_dict(row) for row in replies],
            "workflow_step_count": len(maintenance_steps),
            "maintenance_workflow_step_count": len(maintenance_steps),
            "maintenance_workflow_step_ids": [str(row["id"]) for row in maintenance_steps],
            "derived_artifact_count": len(derived_rows),
            "derived_artifact_ids": [str(row["id"]) for row in derived_rows],
            "statistics": self.maintenance_statistics(),
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
        usage_totals = self.parser_eval_summary()["usage_totals"]
        return {
            "provider": self.config.parser_provider,
            "model": self.config.parser_model,
            "proposal_mode": self.config.parser_proposal_mode,
            "temperature": self.config.parser_temperature,
            "base_url": self.config.parser_base_url,
            "api_key_env": self.config.parser_api_key_env,
            "api_version": self.config.parser_api_version,
            "max_retries": self.config.parser_max_retries,
            "parser_mode": self.config.parser_provider,
            "parser_lane": self.config.parser_lane,
            "parse_timeout_seconds": self.config.parse_timeout_seconds,
            "max_runtime_seconds": self.config.max_runtime_seconds,
            "max_llm_calls": self.config.max_llm_calls,
            "doc_limit": self.config.doc_limit,
            "corpus_fingerprint": self.config.corpus_fingerprint,
            "call_count": self.llm_call_count,
            "input_tokens": usage_totals["input_tokens"],
            "cached_input_tokens": usage_totals["cached_input_tokens"],
            "output_tokens": usage_totals["output_tokens"],
            "total_tokens": usage_totals["total_tokens"],
            "total_cost": usage_totals["total_cost"],
            "cost_status": usage_totals["cost_status"],
            "time_ms": usage_totals["time_ms"],
            "sampled_prompts_available": False,
            "quality_failures": [
                {"doc_id": record.doc_id, "failures": record.llm_quality_failures}
                for record in self.records
                if record.llm_quality_failures
            ],
        }

    def parser_eval_summary(self) -> dict[str, Any]:
        documents: list[dict[str, Any]] = []
        verdict_counts: Counter[str] = Counter()
        scores: list[float] = []
        usage_documents: list[dict[str, Any]] = []
        timing_summaries: list[dict[str, Any]] = []
        for record in self.records:
            parse_result = record.parse_result
            evaluation = getattr(parse_result, "evaluation", None) if parse_result is not None else None
            if not isinstance(evaluation, dict):
                continue
            entry = {"doc_id": record.doc_id, "title": record.title, **evaluation}
            usage_summary = getattr(parse_result, "usage_summary", None)
            if isinstance(usage_summary, dict):
                entry["usage_summary"] = usage_summary
                usage_documents.append({"doc_id": record.doc_id, "title": record.title, **usage_summary})
                timing_summary = usage_summary.get("timing_summary")
                if isinstance(timing_summary, dict):
                    timing_summaries.append(timing_summary)
            documents.append(entry)
            verdict = str(evaluation.get("basic_sense_verdict") or "").strip()
            if verdict:
                verdict_counts[verdict] += 1
            score = evaluation.get("basic_sense_score")
            if isinstance(score, (int, float)):
                scores.append(float(score))
        average_score = round(sum(scores) / len(scores), 1) if scores else None
        if average_score is None:
            composite_verdict = None
        elif average_score >= 70.0 and verdict_counts.get("weak", 0) == 0:
            composite_verdict = "good"
        elif average_score >= 40.0:
            composite_verdict = "mixed"
        else:
            composite_verdict = "weak"
        known_costs = [
            float(item["total_cost"])
            for item in usage_documents
            if isinstance(item.get("total_cost"), (int, float))
        ]
        cost_statuses = {
            str(item.get("cost_status"))
            for item in usage_documents
            if item.get("cost_status")
        }
        usage_totals = {
            "input_tokens": sum(int(item.get("input_tokens") or 0) for item in usage_documents),
            "cached_input_tokens": sum(int(item.get("cached_input_tokens") or 0) for item in usage_documents),
            "output_tokens": sum(int(item.get("output_tokens") or 0) for item in usage_documents),
            "total_tokens": sum(int(item.get("total_tokens") or 0) for item in usage_documents),
            "total_cost": round(sum(known_costs), 6) if known_costs else None,
            "cost_status": (
                "unavailable"
                if not cost_statuses
                else "estimated_partial"
                if "estimated_partial" in cost_statuses or len(known_costs) < len(usage_documents)
                else "estimated"
                if "estimated" in cost_statuses
                else "observed"
            ),
            "time_ms": sum(int(item.get("time_ms") or 0) for item in usage_documents),
            "event_count": sum(int(item.get("event_count") or 0) for item in usage_documents),
        }
        return {
            "enabled": bool(documents),
            "document_count": len(self.records),
            "evaluated_count": len(documents),
            "composite_verdict": composite_verdict,
            "average_basic_sense_score": average_score,
            "verdict_counts": dict(verdict_counts),
            "documents": documents,
            "usage_documents": usage_documents,
            "usage_totals": usage_totals,
            "timing_summary": aggregate_stage_timings(timing_summaries),
        }

    def _workflow_failure_exception(
        self,
        *,
        record: DocumentRecord,
        result: RunResult,
        run_id: str,
    ) -> LongRunDocumentError | LongRunSystemicError:
        workflow_errors = [str(error) for error in result.errors]
        error_text = "\n".join(workflow_errors).strip()
        failed_phase = record.last_step_name or "runtime"
        failure_message = f"workflow returned {result.status}"
        if error_text:
            failure_message = f"{failure_message}: {error_text[:2_000]}"
        failure_details = {
            "workflow_run_id": run_id,
            "workflow_status": result.status,
            "failed_step": failed_phase,
            "workflow_errors": [error[-4_000:] for error in workflow_errors],
            "parser_provider": self.config.parser_provider,
            "parser_model": self.config.parser_model,
        }
        failure_code = (
            classify_exception(RuntimeError(error_text), phase=failed_phase)
            if error_text
            else "document_parse_failed"
        )
        error_type = (
            LongRunSystemicError
            if failure_code in SYSTEMIC_FAILURES
            else LongRunDocumentError
        )
        return error_type(
            failure_code,
            failure_message,
            phase=failed_phase,
            details=failure_details,
        )

    def _run_document_workflow(self, record: DocumentRecord) -> str:
        self._mark_document_progress(record, step_name="workflow_start")
        if record.started_at_ms is None:
            record.started_at_ms = _now_ms()
        workflow_started = time.monotonic()
        if record.resume_suspended_token_id and record.resume_suspended_node_id:
            self._resume_document_workflow(record)
            self._emit_live_progress(
                "document_workflow_completed",
                doc_id=record.doc_id,
                duration_ms=round((time.monotonic() - workflow_started) * 1000),
                workflow_run_id=record.run_id,
                workflow_status="resumed",
            )
            return "succeeded"
        if record.status == "SUSPENDED":
            raise LongRunSystemicError(
                "graph_invariant_violation",
                f"suspended checkpoint for {record.doc_id} is missing resume metadata",
                phase="await_resume",
            )
        resolver = self._build_resolver()
        runtime = WorkflowRuntime(
            workflow_engine=self.engines.workflow,
            conversation_engine=self.engines.conversation,
            step_resolver=resolver,
            predicate_registry={},
            checkpoint_every_n_steps=1,
        )
        event_sink = self._build_runtime_event_sink(downstream_sink=getattr(runtime.emitter, "sink", None))
        runtime.sink = event_sink
        runtime.emitter.sink = event_sink
        run_id = f"{self.run_id}:{record.doc_id}"
        record.run_id = run_id
        result = runtime.run(
            workflow_id=self._workflow_id(),
            conversation_id=f"longrun:{self.config.workspace_id}",
            turn_node_id=record.doc_id,
            initial_state={
                "workspace_id": self.config.workspace_id,
                "doc_id": record.doc_id,
                "_deps": {"harness": self},
            },
            run_id=run_id,
        )
        if result.status == "suspended":
            self._emit_live_progress(
                "document_workflow_suspended",
                doc_id=record.doc_id,
                duration_ms=round((time.monotonic() - workflow_started) * 1000),
            )
            resume_checkpoint_step_seq = 10_000_000 + len(self.status_transitions)
            runtime._persist_checkpoint(
                conversation_id=f"longrun:{self.config.workspace_id}",
                workflow_id=self._workflow_id(),
                run_id=run_id,
                step_seq=resume_checkpoint_step_seq,
                state=dict(result.final_state),
                last_exec_node=None,
            )
            self._capture_resume_checkpoint(record, run_id=run_id)
            record.run_id = run_id
            return "suspended"
        if result.status != "succeeded":
            raise self._workflow_failure_exception(
                record=record,
                result=result,
                run_id=run_id,
            )
        record.ended_at_ms = _now_ms()
        self._mark_document_progress(record, step_name="workflow_done")
        self._emit_live_progress(
            "document_workflow_completed",
            doc_id=record.doc_id,
            duration_ms=round((time.monotonic() - workflow_started) * 1000),
            workflow_run_id=run_id,
            workflow_status=result.status,
        )
        return "succeeded"

    def _prepare_run_directory(self) -> None:
        for name in (
            "input",
            "processing",
            "completed",
            "failed",
            "quarantine",
            "dump",
            "engines",
            "parser_runs",
            "projection_vault",
        ):
            (self.run_dir / name).mkdir(parents=True, exist_ok=True)

    def _reset_run_directory(self) -> None:
        self._reset_directory(self.run_dir)

    @staticmethod
    def _reset_directory(directory: Path) -> None:
        if not directory.exists():
            return
        for child in directory.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                try:
                    child.unlink()
                except FileNotFoundError:
                    pass

    def _restore_checkpoint_history(self) -> None:
        self.status_transitions = self._load_jsonl_rows(self.dumper.dump_dir / "status_transitions.jsonl")
        self.failure_records = [
            FailureRecord(**row)
            for row in self._load_jsonl_rows(self.dumper.dump_dir / "failure_records.jsonl")
        ]

    def _load_jsonl_rows(self, path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                rows.append(json.loads(line))
        return rows

    def _restore_llm_usage_from_dump(self) -> None:
        summary_path = self.dumper.dump_dir / "llm_calls_summary.json"
        if summary_path.exists():
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                summary = {}
            call_count = summary.get("call_count")
            if isinstance(call_count, int) and call_count >= 0:
                self.llm_call_count = call_count
                return
        transition_rows = self._load_jsonl_rows(self.dumper.dump_dir / "status_transitions.jsonl")
        parsed_transition_count = sum(
            1
            for row in transition_rows
            if row.get("phase") == "parse_document" and row.get("status") == "PARSED"
        )
        self.llm_call_count = parsed_transition_count

    def _load_checkpoint_state(self, *, strict: bool) -> bool:
        manifest = self.dumper.dump_dir / "manifest.jsonl"
        if not manifest.exists():
            return False
        records: list[DocumentRecord] = []
        with manifest.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                current_path_value = str(row.get("current_path") or "").strip()
                if not current_path_value:
                    continue
                current_path = Path(current_path_value)
                record = DocumentRecord(
                    doc_id=str(row["doc_id"]),
                    title=str(row.get("title", "")),
                    source_uri=str(row.get("source_uri", "")),
                    input_path=Path(str(row.get("input_path") or current_path)),
                    current_path=current_path,
                    status=str(row.get("status", "PENDING")),
                    started_at_ms=row.get("started_at_ms"),
                    ended_at_ms=row.get("ended_at_ms"),
                    token_count=row.get("token_count"),
                    run_id=str(row.get("run_id") or self.run_id),
                    parser_workflow_run_id=row.get("parser_workflow_run_id"),
                    parser_resume_requested=bool(row.get("parser_resume_requested", False)),
                    source_document_id=row.get("source_document_id"),
                    maintenance_job_id=row.get("maintenance_job_id"),
                    candidate_link_id=row.get("candidate_link_id"),
                    promotion_evidence_pack_id=row.get("promotion_evidence_pack_id"),
                    promotion_candidate_id=row.get("promotion_candidate_id"),
                    promoted_entity_id=row.get("promoted_entity_id"),
                    parsed_node_ids=list(row.get("parsed_node_ids") or []),
                    parsed_edge_ids=list(row.get("parsed_edge_ids") or []),
                    resume_checkpoint_step_seq=row.get("resume_checkpoint_step_seq"),
                    resume_checkpoint_namespace=row.get("resume_checkpoint_namespace"),
                    resume_suspended_node_id=row.get("resume_suspended_node_id"),
                    resume_suspended_token_id=row.get("resume_suspended_token_id"),
                    resumed_from_checkpoint=bool(row.get("resumed_from_checkpoint", False)),
                    recovery_attempt_count=int(row.get("recovery_attempt_count") or 0),
                    recovery_selected=bool(
                        row.get("recovery_selected", False)
                        or (
                            str(row.get("status")) == "PENDING"
                            and str(row.get("last_step_name")) == "recovery_reopen"
                        )
                    ),
                    last_step_name=row.get("last_step_name"),
                    last_step_at_ms=row.get("last_step_at_ms"),
                    llm_quality_failures=list(row.get("llm_quality_failures") or []),
                )
                self._hydrate_record_artifacts(record)
                records.append(record)
        if not records:
            return False
        if len(records) != self.config.doc_count:
            if strict:
                raise AssertionError(
                    "checkpoint manifest doc count does not match the configured long-run doc count "
                    f"(checkpoint={len(records)}, configured={self.config.doc_count}, "
                    f"mode={self.config.mode!r}, manifest={manifest})"
                )
            return False
        manifest_fingerprints = {
            str(row.get("corpus_fingerprint") or "").strip()
            for row in self._load_jsonl_rows(manifest)
            if str(row.get("corpus_fingerprint") or "").strip()
        }
        accepted_fingerprints = self.config.checkpoint_accepted_corpus_fingerprints()
        if manifest_fingerprints and not manifest_fingerprints.issubset(accepted_fingerprints):
            if strict:
                raise AssertionError(
                    "checkpoint manifest corpus fingerprint does not match the configured long-run corpus fingerprint "
                    f"(checkpoint={sorted(manifest_fingerprints)!r}, configured={self.config.corpus_fingerprint!r}, "
                    f"mode={self.config.mode!r}, manifest={manifest})"
                )
            return False
        self.records = records
        self.contexts = {record.doc_id: record for record in records}
        self.checkpoint_loaded = True
        self.checkpoint_manifest_path = manifest
        self._restore_llm_usage_from_dump()
        self.resume_gate_consumed = any(
            record.resumed_from_checkpoint or record.resume_suspended_token_id is not None
            for record in records
        )
        return True

    def _latest_runtime_checkpoint(self, run_id: str) -> dict[str, Any] | None:
        ns = WorkspaceNamespaces(self.config.workspace_id)
        namespace_candidates: list[str | None] = [ns.conv_bg, ns.conv_fg, ns.curated_kg_space, None]
        best: dict[str, Any] | None = None
        for namespace in namespace_candidates:
            context = _temporary_namespace(self.engines.conversation, namespace) if namespace else nullcontext()
            with context:
                checkpoints = self.engines.conversation.read.get_nodes(
                    where=_and_where(
                        {"entity_type": "workflow_checkpoint"},
                        {"run_id": run_id},
                    ),
                    limit=10_000,
                )
            if not checkpoints:
                continue
            latest = max(
                checkpoints,
                key=lambda node: int((getattr(node, "metadata", {}) or {}).get("step_seq", -1)),
            )
            metadata = dict(getattr(latest, "metadata", {}) or {})
            state_json = metadata.get("state_json")
            if isinstance(state_json, str):
                state = json.loads(state_json)
            elif isinstance(state_json, dict):
                state = dict(state_json)
            else:
                state = {}
            candidate = {
                "step_seq": int(metadata.get("step_seq") or 0),
                "node_id": str(getattr(latest, "id", "")),
                "metadata": metadata,
                "state": state,
                "namespace": namespace,
            }
            if best is None or candidate["step_seq"] > best["step_seq"]:
                best = candidate
        return best

    def _capture_resume_checkpoint(self, record: DocumentRecord, *, run_id: str) -> None:
        latest = self._latest_runtime_checkpoint(run_id)
        if not latest:
            raise LongRunSystemicError(
                "graph_invariant_violation",
                f"missing workflow checkpoint for suspended document {record.doc_id}",
                phase="await_resume",
            )
        state = latest["state"] or {}
        rt_join = state.get("_rt_join") or {}
        suspended = list(rt_join.get("suspended") or [])
        if not suspended:
            raise LongRunSystemicError(
                "graph_invariant_violation",
                f"workflow checkpoint for {record.doc_id} is missing suspended token state",
                phase="await_resume",
            )
        suspended_node_id, _, suspended_token_id, _parent_token_id = suspended[0]
        record.resume_checkpoint_step_seq = int(latest["step_seq"])
        record.resume_checkpoint_namespace = str(latest.get("namespace") or "")
        record.resume_suspended_node_id = str(suspended_node_id)
        record.resume_suspended_token_id = str(suspended_token_id)
        with self._state_lock:
            self.resume_gate_consumed = True

    def _hydrate_record_artifacts(self, record: DocumentRecord) -> None:
        if record.parse_result is None:
            record.parse_result = SimpleNamespace(
                semantic_tree=SimpleNamespace(title=record.title)
            )
        if record.graph_extraction is None and (record.parsed_node_ids or record.parsed_edge_ids):
            record.graph_extraction = SimpleNamespace(
                nodes=[SimpleNamespace(id=node_id) for node_id in record.parsed_node_ids],
                edges=[SimpleNamespace(id=edge_id) for edge_id in record.parsed_edge_ids],
            )

    def _resume_document_workflow(self, record: DocumentRecord) -> None:
        if not record.resume_suspended_node_id or not record.resume_suspended_token_id:
            raise AssertionError(
                f"document {record.doc_id} has no suspended checkpoint state to resume"
            )
        self._hydrate_record_artifacts(record)
        run_id = record.run_id or f"{self.run_id}:{record.doc_id}"
        runtime = WorkflowRuntime(
            workflow_engine=self.engines.workflow,
            conversation_engine=self.engines.conversation,
            step_resolver=self._build_resolver(),
            predicate_registry={},
            checkpoint_every_n_steps=1,
        )
        event_sink = self._build_runtime_event_sink(downstream_sink=getattr(runtime.emitter, "sink", None))
        runtime.sink = event_sink
        runtime.emitter.sink = event_sink
        result = runtime.resume_run(
            run_id=run_id,
            suspended_node_id=str(record.resume_suspended_node_id),
            suspended_token_id=str(record.resume_suspended_token_id),
            client_result=RunSuccess(
                state_update=[("u", {"resumed_from_checkpoint": True})]
            ),
            workflow_id=self._workflow_id(),
            conversation_id=f"longrun:{self.config.workspace_id}",
            turn_node_id=record.doc_id,
        )
        if result.status != "succeeded":
            raise LongRunDocumentError(
                "document_parse_failed",
                f"resume returned {result.status}",
                phase="runtime_resume",
            )
        record.resumed_from_checkpoint = True
        record.run_id = run_id
        if record.ended_at_ms is None:
            record.ended_at_ms = _now_ms()
        self._mark_document_progress(record, step_name="resume_document")
        self._mark_document_progress(record, step_name="workflow_done")

    def _restore_missing_documents_from_dump(self) -> None:
        raw_dir = self.dumper.dump_dir / "raw_documents"
        if not raw_dir.exists():
            return
        for record in self.records:
            if record.current_path.exists():
                continue
            backup = raw_dir / record.current_path.name
            if backup.exists():
                record.current_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup, record.current_path)

    def _restore_progress_from_records(self) -> None:
        if not self.records:
            return
        active = next((record for record in self.records if record.status not in TERMINAL_STATES), None)
        if active is not None:
            self.active_document_id = active.doc_id
            self.active_step_name = active.last_step_name
            self.last_progress_at_ms = active.last_step_at_ms or active.started_at_ms
        else:
            latest = max(
                (record for record in self.records if record.last_step_at_ms is not None),
                key=lambda record: record.last_step_at_ms or 0,
                default=None,
            )
            if latest is not None:
                self.last_completed_step_name = latest.last_step_name
                self.last_progress_at_ms = latest.last_step_at_ms
                self.active_document_id = latest.doc_id
                self.active_step_name = latest.last_step_name

    def _normalize_recovery_attempt_counts(self) -> None:
        """Migrate old checkpoints that counted reopenings as attempts."""
        for record in self.records:
            if record.recovery_attempt_count <= 0 or not record.recovery_selected:
                continue
            reopened_at = max(
                (
                    int(row.get("timestamp_ms") or 0)
                    for row in self.status_transitions
                    if row.get("doc_id") == record.doc_id
                    and row.get("phase") == "recovery_reopen"
                ),
                default=0,
            )
            recovery_claims = sum(
                1
                for row in self.status_transitions
                if row.get("doc_id") == record.doc_id
                and row.get("phase") == "claim_document"
                and int(row.get("timestamp_ms") or 0) > reopened_at
            )
            if recovery_claims < record.recovery_attempt_count:
                record.recovery_attempt_count = recovery_claims

    def _prepare_failed_document_recovery(self) -> None:
        """Reopen a bounded set of terminal failures without erasing history."""
        durable_document_ids = {
            str(row.get("doc_id"))
            for row in self.status_transitions
            if row.get("status") in {
                "PERSISTED",
                "SOURCE_SEEDED",
                "MAINTENANCE_ENQUEUED",
                "MAINTENANCE_OBSERVED",
                "COMPLETED",
            }
            and row.get("doc_id")
        }
        pending_selected = any(
            record.recovery_selected and record.status not in TERMINAL_STATES
            for record in self.records
        )
        candidates = [] if pending_selected else [
            record
            for record in self.records
            if record.status in {"FAILED", "QUARANTINED"}
            and record.recovery_attempt_count < self.config.recovery_attempts_per_doc
            and record.doc_id not in durable_document_ids
        ]
        self.recovery_skipped_document_ids = [
            record.doc_id
            for record in self.records
            if record.status in {"FAILED", "QUARANTINED"}
            and record.doc_id in durable_document_ids
        ]
        for doc_id in self.recovery_skipped_document_ids:
            _emit_longrun_live(
                "document_recovery_skipped",
                doc_id=doc_id,
                reason="historical_durable_stage_exists",
            )
        self.recovery_missing_payload_document_ids = []
        recoverable_candidates: list[tuple[DocumentRecord, Path, str]] = []
        for record in candidates:
            source = record.current_path
            source_kind = "current"
            if not source.exists():
                backup = self.dumper.dump_dir / "raw_documents" / record.current_path.name
                if backup.exists():
                    source = backup
                    source_kind = "raw_documents"
            if not source.exists():
                source = record.input_path
                source_kind = "input"
            if source.exists():
                recoverable_candidates.append((record, source, source_kind))
                continue
            self.recovery_missing_payload_document_ids.append(record.doc_id)
            _emit_longrun_live(
                "document_recovery_skipped",
                doc_id=record.doc_id,
                reason="recovery_payload_missing",
                current_path=str(record.current_path),
                raw_documents_path=str(self.dumper.dump_dir / "raw_documents"),
                input_path=str(record.input_path),
            )
        if self.config.recovery_doc_limit is not None:
            recoverable_candidates = recoverable_candidates[: self.config.recovery_doc_limit]
        for record, source, source_kind in recoverable_candidates:
            previous_status = record.status
            target = self.run_dir / "processing" / record.input_path.name
            target.parent.mkdir(parents=True, exist_ok=True)
            if source != target:
                if target.exists():
                    target.unlink()
                shutil.move(str(source), str(target))
            record.current_path = target
            record.recovery_selected = True
            record.status = "PENDING"
            record.started_at_ms = None
            record.ended_at_ms = None
            record.token_count = None
            record.run_id = None
            record.maintenance_job_id = None
            record.candidate_link_id = None
            record.promotion_evidence_pack_id = None
            record.promotion_candidate_id = None
            record.promoted_entity_id = None
            record.parsed_node_ids = []
            record.parsed_edge_ids = []
            record.resume_checkpoint_step_seq = None
            record.resume_checkpoint_namespace = None
            record.resume_suspended_node_id = None
            record.resume_suspended_token_id = None
            record.resumed_from_checkpoint = False
            record.parse_result = None
            record.graph_extraction = None
            record.last_step_name = "recovery_reopen"
            record.last_step_at_ms = _now_ms()
            self.status_transitions.append(
                {
                    "run_id": self.run_id,
                    "doc_id": record.doc_id,
                    "phase": "recovery_reopen",
                    "status": "PENDING",
                    "previous_status": previous_status,
                    "recovery_attempt": record.recovery_attempt_count + 1,
                    "timestamp_ms": record.last_step_at_ms,
                }
            )
            if self.active_document_id is None:
                self.active_document_id = record.doc_id
                self.active_step_name = "recovery_reopen"
            _emit_longrun_live(
                "document_recovery_reopened",
                doc_id=record.doc_id,
                previous_status=previous_status,
                recovery_attempt=record.recovery_attempt_count,
                recovery_source=source_kind,
            )
        if not recoverable_candidates and not pending_selected:
            self.early_stop_reason = (
                "no recoverable failed or quarantined documents remain below the configured "
                "recovery attempt limit"
            )
        self.last_progress_at_ms = _now_ms()

    def _build_resolver(self) -> MappingStepResolver:
        resolver = MappingStepResolver()

        @resolver.register("noop")
        def _noop(ctx):
            record = self.contexts[str(ctx.state_view["doc_id"])]
            self._mark_document_progress(record, step_name="noop")
            return RunSuccess(state_update=[("u", {"noop": True})])

        @resolver.register("claim_document")
        def _claim(ctx):
            record = self.contexts[str(ctx.state_view["doc_id"])]
            was_pending = record.status == "PENDING"
            self._mark_document_progress(record, step_name="claim_document")
            target = self.run_dir / "processing" / record.input_path.name
            if record.current_path != target:
                shutil.move(str(record.current_path), str(target))
                record.current_path = target
            if self.config.mode == "retry_failed" and record.recovery_selected and was_pending:
                record.recovery_attempt_count += 1
            self._transition(record, "CLAIMED", phase="claim_document")
            return RunSuccess(state_update=[("u", {"claimed_path": str(target)})])

        @resolver.register("token_check")
        def _token_check(ctx):
            record = self.contexts[str(ctx.state_view["doc_id"])]
            self._mark_document_progress(record, step_name="token_check")
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
            self._mark_document_progress(record, step_name="parse_document")
            request = self._request_for(record)
            source_document_id = self.pipeline._source_document_id(request)
            try:
                record.parse_result = self._run_parse_with_subprocess(
                    record=record,
                    request=request,
                    source_document_id=source_document_id,
                )
                usage_summary = getattr(record.parse_result, "usage_summary", {})
                actual_call_count = int(usage_summary.get("llm_call_count") or 0)
                self.llm_call_count += actual_call_count or 1
                record.parser_resume_requested = False
            except (LongRunDocumentError, LongRunSystemicError) as exc:
                self.llm_call_count += int(exc.details.get("llm_call_count") or 0)
                if exc.details.get("failure_kind") in {"parser_timeout", "parser_child_failure"}:
                    record.parser_resume_requested = True
                failure_usage_events = list(exc.details.get("usage_events") or [])
                failure_usage_events.extend(exc.details.get("usage_event_file_events") or [])
                if failure_usage_events:
                    unique_events = {
                        str(event.get("event_id")): event
                        for event in failure_usage_events
                        if isinstance(event, dict) and event.get("event_id")
                    }
                    self.pipeline._persist_parser_usage_events(  # noqa: SLF001 - parent owns attribution
                        request=request,
                        source_document_id=source_document_id,
                        provider=self.config.parser_provider,
                        model=self.config.parser_model,
                        attempt_id=str(record.run_id or f"{self.run_id}:{record.doc_id}:failure"),
                        usage_events=list(unique_events.values()) or failure_usage_events,
                    )
                raise
            except Exception as exc:  # noqa: BLE001
                code = classify_exception(exc, phase="parse_document")
                if code in RECOVERABLE_LLM_QUALITY_FAILURES:
                    code = "document_parse_failed"
                details = {
                    "error_type": type(exc).__name__,
                    "error_message": str(exc)[:2_000],
                    "traceback": traceback.format_exc()[-8_000:],
                }
                if code in SYSTEMIC_FAILURES:
                    raise LongRunSystemicError(
                        code,
                        str(exc),
                        phase="parse_document",
                        details=details,
                    ) from exc
                raise LongRunDocumentError(
                    code,
                    str(exc),
                    phase="parse_document",
                    details=details,
                ) from exc
            record.source_document_id = source_document_id
            self._transition(record, "PARSED", phase="parse_document")
            return RunSuccess(
                state_update=[
                    (
                        "u",
                        {
                            "source_document_id": source_document_id,
                            "parser_lane": self.config.parser_lane,
                        },
                    )
                ]
            )

        @resolver.register("persist_document")
        def _persist(ctx):
            record = self.contexts[str(ctx.state_view["doc_id"])]
            self._mark_document_progress(record, step_name="persist_document")
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
                record.parsed_node_ids = [
                    str(getattr(node, "id"))
                    for node in getattr(record.graph_extraction, "nodes", [])
                ]
                record.parsed_edge_ids = [
                    str(getattr(edge, "id"))
                    for edge in getattr(record.graph_extraction, "edges", [])
                ]
                self.pipeline.ingest_parse_result(
                    request=request,
                    source_document_id=source_document_id,
                    graph_extraction=record.graph_extraction,
                    namespace=ns.conv_fg,
                )
                usage_events = list(getattr(record.parse_result, "usage_events", []) or [])
                if usage_events:
                    self.pipeline._persist_parser_usage_events(  # noqa: SLF001 - parent owns attribution
                        request=request,
                        source_document_id=source_document_id,
                        provider=self.config.parser_provider,
                        model=self.config.parser_model,
                        attempt_id=str(record.run_id or f"{self.run_id}:{record.doc_id}"),
                        usage_events=usage_events,
                    )
                self.pipeline.record_source_readiness(
                    request=request,
                    source_document_id=source_document_id,
                    stage="parsed_graph_persisted",
                )
            except Exception as exc:  # noqa: BLE001
                raise LongRunDocumentError(
                    "document_persist_failed_after_retries",
                    str(exc),
                    phase="persist_document",
                ) from exc
            self._transition(record, "PERSISTED", phase="persist_document")
            return RunSuccess(
                state_update=[
                    (
                        "u",
                        {
                            "persisted": True,
                            "parsed_node_ids": list(record.parsed_node_ids),
                            "parsed_edge_ids": list(record.parsed_edge_ids),
                        },
                    )
                ]
            )

        @resolver.register("seed_source_map")
        def _seed_source_map(ctx):
            record = self.contexts[str(ctx.state_view["doc_id"])]
            self._mark_document_progress(record, step_name="seed_source_map")
            request = self._request_for(record)
            source_document_id = self.pipeline._source_document_id(request)
            ns = self.pipeline.namespaces_for(request.workspace_id)
            try:
                self.pipeline.register_source(
                    request=request,
                    source_document_id=source_document_id,
                    namespace=ns.conv_fg,
                )
                self.pipeline.seed_source_map(
                    request=request,
                    source_document_id=source_document_id,
                    namespace=ns.conv_bg,
                )
            except Exception as exc:  # noqa: BLE001
                raise LongRunDocumentError(
                    "document_persist_failed_after_retries",
                    str(exc),
                    phase="seed_source_map",
                ) from exc
            record.source_document_id = source_document_id
            self._transition(record, "SOURCE_SEEDED", phase="seed_source_map")
            return RunSuccess(state_update=[("u", {"source_document_id": source_document_id})])

        @resolver.register("await_resume")
        def _await_resume(ctx):
            record = self.contexts[str(ctx.state_view["doc_id"])]
            self._mark_document_progress(record, step_name="await_resume")
            with self._state_lock:
                if not self.config.resume_probe_enabled or self.resume_gate_consumed:
                    return RunSuccess(state_update=[("u", {"resume_gate_skipped": True})])
                # This is one run-level gate. Claim it atomically so parallel
                # document workers cannot suspend multiple documents.
                self.resume_gate_consumed = True
            self._transition(
                record,
                "SUSPENDED",
                phase="await_resume",
                resume_gate=True,
            )
            return RunSuspended(
                state_update=[("u", {"resume_gate_suspended": True})],
                resume_payload={
                    "doc_id": record.doc_id,
                    "workspace_id": self.config.workspace_id,
                    "run_id": record.run_id or f"{self.run_id}:{record.doc_id}",
                    "gate": "longrun_resume_probe",
                },
            )

        @resolver.register("enqueue_background_maintenance")
        def _enqueue(ctx):
            record = self.contexts[str(ctx.state_view["doc_id"])]
            self._mark_document_progress(record, step_name="enqueue_background_maintenance")
            request = self._request_for(record)
            ns = self.pipeline.namespaces_for(request.workspace_id)
            source_document_id = str(record.source_document_id)
            if self.config.operation_mode == "maintenance_first":
                maintenance_job_id = self.pipeline.create_maintenance_request(
                    request=request,
                    source_document_id=source_document_id,
                    namespace=ns.conv_bg,
                    maintenance_kind="document_seed_graph",
                )
                record.maintenance_job_id = maintenance_job_id
                self._transition(record, "MAINTENANCE_ENQUEUED", phase="enqueue_background_maintenance")
                return RunSuccess(state_update=[("u", {"maintenance_job_id": maintenance_job_id})])

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
            promotion_evidence_pack_id, promotion_evidence_pack_digest = (
                self.pipeline.create_promotion_evidence_pack(
                    request=request,
                    source_document_id=source_document_id,
                    candidate_link_id=candidate_link_id,
                    graph_extraction=record.graph_extraction,
                    namespace=ns.conv_bg,
                )
            )
            promotion_candidate_id = self.pipeline.create_promotion_candidate(
                request=request,
                source_document_id=source_document_id,
                candidate_link_id=candidate_link_id,
                promotion_evidence_pack_id=promotion_evidence_pack_id,
                promotion_evidence_pack_digest=promotion_evidence_pack_digest,
                namespace=ns.conv_bg,
            )
            promotion_decision = self.pipeline.policies.promotion.decide(
                promotion_mode=request.promotion_mode,
                auto_accept_threshold=request.auto_accept_threshold,
                metadata={
                    "workspace_id": request.workspace_id,
                    "source_document_id": source_document_id,
                    "promotion_candidate_id": promotion_candidate_id,
                    "promotion_evidence_pack_id": promotion_evidence_pack_id,
                },
            )
            promoted_entity_id = self.pipeline.promote_to_knowledge(
                request=request,
                source_document_id=source_document_id,
                promotion_candidate_id=promotion_candidate_id,
                promotion_evidence_pack_id=promotion_evidence_pack_id,
                promotion_evidence_pack_digest=promotion_evidence_pack_digest,
                promotion_decision=promotion_decision,
                namespace=ns.curated_kg_space,
            )
            record.maintenance_job_id = maintenance_job_id
            record.candidate_link_id = candidate_link_id
            record.promotion_evidence_pack_id = promotion_evidence_pack_id
            record.promotion_candidate_id = promotion_candidate_id
            record.promoted_entity_id = promoted_entity_id
            self._transition(record, "MAINTENANCE_ENQUEUED", phase="enqueue_background_maintenance")
            return RunSuccess(
                state_update=[
                    (
                        "u",
                        {
                            "maintenance_job_id": maintenance_job_id,
                            "promotion_evidence_pack_id": promotion_evidence_pack_id,
                            "promoted_entity_id": promoted_entity_id,
                        },
                    )
                ]
            )

        @resolver.register("observe_background_maintenance")
        def _observe(ctx):
            record = self.contexts[str(ctx.state_view["doc_id"])]
            self._mark_document_progress(record, step_name="observe_background_maintenance")
            try:
                if self.config.parser_workers == 1:
                    self._poll_maintenance_once(phase="observe_background_maintenance")
                else:
                    self._emit_live_progress(
                        "maintenance_poll_deferred",
                        doc_id=record.doc_id,
                        reason="single_foreground_maintenance_worker",
                    )
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
            self._mark_document_progress(record, step_name="verify_document_artifacts")
            if self.config.operation_mode == "maintenance_first":
                self._verify_seeded_source_map(record)
            else:
                self._verify_document(record)
            return RunSuccess(state_update=[("u", {"verified": True})])

        @resolver.register("move_completed")
        def _move_completed(ctx):
            record = self.contexts[str(ctx.state_view["doc_id"])]
            self._mark_document_progress(record, step_name="move_completed")
            target = self.run_dir / "completed" / record.current_path.name
            if record.current_path.exists() and record.current_path != target:
                shutil.move(str(record.current_path), str(target))
                record.current_path = target
            self._transition(record, "COMPLETED", phase="move_completed")
            record.ended_at_ms = _now_ms()
            return RunSuccess(state_update=[("u", {"completed_path": str(target)})])

        return resolver

    def _request_for(self, record: DocumentRecord) -> IngestPipelineRequest:
        return IngestPipelineRequest(
            workspace_id=self.config.workspace_id,
            source_uri=record.source_uri,
            title=record.title,
            raw_text=record.current_path.read_text(encoding="utf-8"),
            source_format="markdown",
            operation_mode=self.config.operation_mode,
            parser_mode=self.config.parser_provider,
            promotion_mode="sync",
            llm_provider=self.config.parser_provider,
            llm_model=self.config.parser_model,
        )

    def _transition(self, record: DocumentRecord, status: str, *, phase: str, **extra: Any) -> None:
        with self._state_lock:
            if status not in STATUSES:
                raise ValueError(f"unknown long-run status {status!r}")
            record.status = status
            record.last_step_name = phase
            record.last_step_at_ms = _now_ms()
            if status in TERMINAL_STATES and record.ended_at_ms is None:
                record.ended_at_ms = record.last_step_at_ms
            self.last_completed_step_name = phase
            self.last_progress_at_ms = record.last_step_at_ms
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
        self.dumper.append_failure_event(failure)
        self._emit_live_failure(
            stage="document_failure_recorded",
            message=failure.message,
            details={
                "code": failure.code,
                "scope": failure.scope,
                "fingerprint": failure.fingerprint,
                **failure.details,
            },
            doc_id=failure.doc_id,
            phase=failure.phase,
        )
        logger.error(
            "Longrun failure recorded run_id=%s doc_id=%s phase=%s code=%s scope=%s "
            "fingerprint=%s details=%s message=%s",
            failure.run_id,
            failure.doc_id,
            failure.phase,
            failure.code,
            failure.scope,
            failure.fingerprint,
            _jsonable(failure.details),
            failure.message,
        )

    def _safe_failure_dump(self, *, reason: str, final: bool = False) -> None:
        """Best-effort snapshotting must not hide the already-recorded root failure."""
        try:
            self.dumper.dump(reason=reason, final=final)
        except Exception as exc:  # noqa: BLE001
            self._emit_live_failure(
                stage="diagnostic_dump_failure",
                message=f"{type(exc).__name__}: {exc}",
                details={"reason": reason, "final": final},
            )
            logger.exception("Longrun diagnostic dump failed reason=%s final=%s", reason, final)

    def _failure_record(
        self,
        *,
        doc_id: str | None,
        phase: str,
        code: str,
        scope: str,
        message: str,
        details: dict[str, Any] | None = None,
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
            details=dict(details or {}),
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
            details=dict(getattr(exc, "details", {}) or {}),
        )

    def _emit_live_failure(
        self,
        *,
        stage: str,
        message: str,
        details: dict[str, Any],
        doc_id: str | None = None,
        phase: str | None = None,
    ) -> None:
        payload = {
            "stage": stage,
            "run_id": self.run_id,
            "doc_id": doc_id,
            "phase": phase,
            "message": message[:1_000],
            "details": _jsonable(details),
        }
        if self.live_trace_printer is not None:
            self.live_trace_printer.emit(payload)

    def _emit_live_progress(self, stage: str, **details: Any) -> None:
        if self.live_trace_printer is not None:
            self.live_trace_printer.emit(
                {
                    "stage": stage,
                    "run_id": self.run_id,
                    "details": _jsonable(details),
                }
            )

    def _lifecycle(self, event: str, *, status: str | None = None, **details: Any) -> None:
        if status is not None:
            self.lifecycle_status = status
        row = {
            "event": event,
            "status": self.lifecycle_status,
            "run_id": self.run_id,
            "timestamp_ms": _now_ms(),
            **_jsonable(details),
        }
        self.lifecycle_events.append(row)
        with (self.dumper.dump_dir / "lifecycle_events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
        self.dumper._write_json("run_lifecycle.json", {"status": self.lifecycle_status, "last_event": row})
        self.dumper._write_json("maintenance_health.json", self.maintenance_health)
        self._emit_live_progress(event, lifecycle_status=self.lifecycle_status, **details)

    def _emit_maintenance_trace(self, payload: dict[str, object]) -> None:
        """Persist and optionally stream worker-level queue diagnostics."""
        record = {
            "stage": "maintenance_worker_trace",
            "run_id": self.run_id,
            **payload,
        }
        path = self.dumper.dump_dir / "maintenance_worker_trace.jsonl"
        with self._maintenance_trace_lock:
            if payload.get("event") == "maintenance_parse_complete":
                self.llm_call_count += int(payload.get("llm_call_count") or 0)
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True, default=str))
                handle.write("\n")
            self.maintenance_health.update(
                {
                    "active_worker_ids": [payload.get("worker_id")] if payload.get("worker_id") else [],
                    "active_job_id": payload.get("job_id"),
                    "active_document_id": payload.get("doc_id"),
                    "current_maintenance_kind": payload.get("kind") or payload.get("maintenance_kind"),
                    "last_trace_timestamp_ms": _now_ms(),
                    "last_error": payload.get("error") or payload.get("last_error"),
                }
            )
            self.dumper._write_json("maintenance_health.json", self.maintenance_health)
        if self.live_trace_printer is not None:
            self.live_trace_printer.emit(record)

    def _move_failed_or_quarantine(self, record: DocumentRecord, failure: FailureRecord) -> None:
        target_dir = "quarantine" if failure.scope == "systemic" else "failed"
        status = "QUARANTINED" if failure.scope == "systemic" else "FAILED"
        target = self.run_dir / target_dir / record.current_path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        if record.current_path.exists() and record.current_path != target:
            shutil.move(str(record.current_path), str(target))
            record.current_path = target
        self._transition(record, status, phase="move_failed_or_quarantine", failure_code=failure.code)

    def _budget_stop_reason(self, *, started: float) -> str | None:
        if self.llm_call_count >= self.config.max_llm_calls:
            return (
                "runtime_worker_stuck: llm call budget exceeded "
                f"({self.llm_call_count}/{self.config.max_llm_calls})"
            )
        if time.monotonic() - started >= self.config.max_runtime_seconds:
            return "runtime_worker_stuck: max runtime exceeded"
        return None

    def _abort(self, reason: str, *, preserve_pending: bool = False) -> None:
        self.aborted = True
        self.abort_reason = reason
        self.abort_preserves_pending = preserve_pending
        self._log_heartbeat(phase="abort", started=None)

    def _stop_early(self, reason: str) -> None:
        self.early_stop_reason = reason
        self._log_heartbeat(phase="early_stop", started=None)

    def _log_heartbeat(self, *, phase: str, started: float | None) -> None:
        summary = self.progress_summary()
        elapsed_s = None if started is None else max(0.0, time.monotonic() - started)
        live_payload = {
            "stage": "heartbeat",
            "phase": phase,
            "run_id": summary["run_id"],
            "parser_provider": summary["parser_provider"],
            "parser_model": summary["parser_model"],
            "doc_id": summary["current_document_id"],
            "current_step": summary["current_step"],
            "completed": summary["completed_count"],
            "failed": summary["failed_count"],
            "quarantined": summary["quarantined_count"],
            "suspended": summary["suspended_count"],
            "llm_calls": summary["llm_call_count"],
            "llm_call_budget": summary["llm_call_budget"],
            "elapsed_s": None if elapsed_s is None else round(elapsed_s, 2),
        }
        if self.live_trace_printer is not None:
            self.live_trace_printer.emit(live_payload)
        logger.info(
            (
                "Longrun heartbeat phase=%s run_id=%s parser=%s/%s doc=%s step=%s "
                "completed=%s failed=%s quarantined=%s suspended=%s "
                "llm_calls=%s/%s elapsed_s=%s checkpoint_loaded=%s"
            ),
            phase,
            summary["run_id"],
            summary["parser_provider"],
            summary["parser_model"],
            summary["current_document_id"],
            summary["current_step"],
            summary["completed_count"],
            summary["failed_count"],
            summary["quarantined_count"],
            summary["suspended_count"],
            summary["llm_call_count"],
            summary["llm_call_budget"],
            None if elapsed_s is None else round(elapsed_s, 2),
            summary["manifest_checkpoint_loaded"],
        )

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
        workers = self._maintenance_workers or [self.maintenance_worker]
        if len(workers) == 1:
            workers[0].process_pending_jobs(self.config.workspace_id)
            return
        with ThreadPoolExecutor(
            max_workers=len(workers),
            thread_name_prefix="llm-wiki-maintenance",
        ) as executor:
            futures = [
                executor.submit(worker.process_pending_jobs, self.config.workspace_id)
                for worker in workers
            ]
            for future in futures:
                future.result()

    def _drain_maintenance_after_documents(self) -> None:
        ns = WorkspaceNamespaces(self.config.workspace_id)
        for _ in range(self.config.max_post_doc_maintenance_steps):
            budget_stop_reason = self._budget_stop_reason(
                started=(self.run_started_monotonic or time.monotonic())
            )
            if budget_stop_reason is not None:
                self.early_stop_reason = budget_stop_reason
                self._emit_live_progress(
                    "maintenance_drain_stopped",
                    reason=budget_stop_reason,
                    pending_jobs=len(
                        self.engines.conversation.meta_sqlite.list_index_jobs(
                            namespace=ns.maintenance_jobs,
                            status="PENDING",
                            limit=10_000,
                        )
                    ),
                )
                return
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

    def _remaining_runtime_seconds(self) -> float | None:
        if self.run_started_monotonic is None:
            return None
        elapsed = time.monotonic() - self.run_started_monotonic
        return max(0.0, float(self.config.max_runtime_seconds) - elapsed)

    def _parser_child_failure_message(
        self,
        *,
        record: DocumentRecord,
        process: multiprocessing.Process,
        process_pid: int | None = None,
        process_exitcode: int | None = None,
        failure: dict[str, Any],
        trace_path: Path,
        failure_path: Path,
    ) -> str:
        # Keep this formatter usable with process-shaped test doubles as well as
        # multiprocessing.Process instances.  The exit code is the useful
        # diagnostic; a PID is optional when a child never exposed one.
        pid = process_pid if process_pid is not None else getattr(process, "pid", None)
        exitcode = (
            process_exitcode
            if process_exitcode is not None
            else getattr(process, "exitcode", None)
        )
        if failure:
            return (
                f"parser lane {self.config.parser_lane!r} failed for {record.doc_id}: "
                f"{failure.get('error_type', 'unknown')}: {failure.get('message', 'no result written')} "
                f"(exitcode={exitcode}, pid={pid}, trace_path={trace_path}, failure_path={failure_path})"
            )
        return (
            f"parser lane {self.config.parser_lane!r} exited without result for {record.doc_id} "
            f"(exitcode={exitcode}, pid={pid}, trace_path={trace_path}, failure_path={failure_path})"
        )

    def _workflow_id(self) -> str:
        return f"{WORKFLOW_ID}.{self.config.operation_mode}"

    def _workflow_steps(self) -> list[str]:
        if self.config.operation_mode == "maintenance_first":
            return list(MAINTENANCE_FIRST_WORKFLOW_STEPS)
        return list(WORKFLOW_STEPS)

    @staticmethod
    def _node_rows_with_metadata(engine: Any, *, where: dict[str, Any], limit: int = 10_000) -> list[dict[str, Any]]:
        got = engine.read._node_get_raw(  # noqa: SLF001 - harness needs metadata-only inspection
            where=where,
            limit=limit,
            include=["metadatas"],
        )
        ids = list(got.get("ids") or [])
        documents = list(got.get("documents") or [])
        metadatas = list(got.get("metadatas") or [])
        rows: list[dict[str, Any]] = []
        for idx, node_id in enumerate(ids):
            metadata = metadatas[idx] if idx < len(metadatas) and isinstance(metadatas[idx], dict) else {}
            document = documents[idx] if idx < len(documents) else None
            rows.append({"id": str(node_id), "document": document, "metadata": dict(metadata)})
        return rows

    def _mark_document_progress(self, record: DocumentRecord, *, step_name: str) -> None:
        with self._state_lock:
            self.active_document_id = record.doc_id
            self.active_step_name = step_name
            self.last_progress_at_ms = _now_ms()

    def _active_document_id(self) -> str | None:
        return self.active_document_id

    def _active_step_name(self) -> str | None:
        return self.active_step_name

    def _last_completed_step_name(self) -> str | None:
        return self.last_completed_step_name

    def _last_progress_at_ms(self) -> int | None:
        return self.last_progress_at_ms

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
        source_document = self.engines.kg.backend.document_get(
            ids=[record.source_document_id],
            include=["documents", "metadatas"],
        )
        if source_document["ids"] != [record.source_document_id]:
            raise LongRunSystemicError(
                "graph_invariant_violation",
                f"source document {record.source_document_id} was not persisted to SOURCE",
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
        with _temporary_namespace(self.engines.kg, ns.source_space):
            source_nodes = self.engines.kg.read.get_nodes(
                where=_and_where(
                    {"doc_id": record.source_document_id},
                    {"graph_space": "source"},
                ),
                limit=10_000,
            )
        if not source_nodes:
            raise LongRunSystemicError(
                "graph_invariant_violation",
                f"no source nodes for source document {record.source_document_id}",
                phase="verify_document_artifacts",
            )
        if not all(node.metadata.get("graph_space") == "source" for node in source_nodes):
            raise LongRunSystemicError(
                "graph_invariant_violation",
                f"source node without source graph_space for {record.source_document_id}",
                phase="verify_document_artifacts",
            )
        if not all(_node_has_doc_provenance(node, record.source_document_id) for node in parsed_nodes):
            raise LongRunSystemicError(
                "graph_invariant_violation",
                f"parsed node without source provenance for {record.source_document_id}",
                phase="verify_document_artifacts",
            )
        if record.promoted_entity_id:
            self._verify_promotion_provenance(record)

    def _verify_seeded_source_map(self, record: DocumentRecord) -> None:
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
        with _temporary_namespace(self.engines.conversation, ns.conv_bg):
            seed_nodes = self.engines.conversation.read.get_nodes(
                where=_and_where(
                    {"artifact_kind": "source_map_seed"},
                    {"source_document_id": record.source_document_id},
                ),
                limit=10_000,
            )
        if not seed_nodes:
            raise LongRunSystemicError(
                "graph_invariant_violation",
                f"no source map seed for source document {record.source_document_id}",
                phase="verify_document_artifacts",
            )
        if not record.maintenance_job_id:
            raise LongRunSystemicError(
                "graph_invariant_violation",
                f"no maintenance job for source document {record.source_document_id}",
                phase="verify_document_artifacts",
            )

    def _verify_promotion_provenance(self, record: DocumentRecord) -> dict[str, Any]:
        if not record.promoted_entity_id:
            raise LongRunSystemicError(
                "graph_invariant_violation",
                f"{record.doc_id} has no promoted entity id to verify",
                phase="verify_document_artifacts",
            )
        ns = WorkspaceNamespaces(self.config.workspace_id)
        with _temporary_namespace(self.engines.kg, ns.curated_kg_space):
            promoted_nodes = self.engines.kg.read.get_nodes(ids=[record.promoted_entity_id], limit=1)
        if not promoted_nodes:
            raise LongRunSystemicError(
                "graph_invariant_violation",
                f"promoted node {record.promoted_entity_id} missing for {record.doc_id}",
                phase="verify_document_artifacts",
            )
        promoted = promoted_nodes[0]
        promoted_md = promoted.metadata or {}
        required = [
            "promotion_candidate_id",
            "promotion_evidence_pack_id",
            "promotion_evidence_pack_digest",
            "promotion_decision_reason",
        ]
        missing = [key for key in required if not promoted_md.get(key)]
        if missing:
            raise LongRunSystemicError(
                "graph_invariant_violation",
                f"promoted node {record.promoted_entity_id} missing metadata {missing} for {record.doc_id}",
                phase="verify_document_artifacts",
            )
        pack_id = str(promoted_md["promotion_evidence_pack_id"])
        with _temporary_namespace(self.engines.conversation, ns.conv_bg):
            packs = self.engines.conversation.read.get_nodes(ids=[pack_id], limit=1)
        if not packs:
            raise LongRunSystemicError(
                "graph_invariant_violation",
                f"promotion evidence pack {pack_id} missing for {record.doc_id}",
                phase="verify_document_artifacts",
            )
        pack = packs[0]
        pack_md = pack.metadata or {}
        digest = _decode_metadata_json(pack_md.get("promotion_evidence_pack_digest"))
        if pack_md.get("artifact_kind") != "promotion_evidence_pack":
            raise LongRunSystemicError(
                "graph_invariant_violation",
                f"promotion evidence pack {pack_id} has wrong artifact kind",
                phase="verify_document_artifacts",
            )
        if not digest.get("node_ids") or digest.get("edge_ids") is None:
            raise LongRunSystemicError(
                "graph_invariant_violation",
                f"promotion evidence pack {pack_id} is missing typed evidence ids",
                phase="verify_document_artifacts",
            )
        return {
            "doc_id": record.doc_id,
            "promoted_entity_id": record.promoted_entity_id,
            "promotion_candidate_id": promoted_md["promotion_candidate_id"],
            "promotion_evidence_pack_id": pack_id,
            "promotion_evidence_pack_node_count": len(list(digest.get("node_ids") or [])),
            "promotion_evidence_pack_edge_count": len(list(digest.get("edge_ids") or [])),
        }

    def _verify_run_invariants(self) -> None:
        # A bounded retry intentionally leaves unrelated corpus documents pending.
        # They belong to a later continue run, so only the selected retry batch
        # must satisfy the terminal-state invariant here.
        scoped_records = [
            record
            for record in self.records
            if self.config.mode != "retry_failed" or record.recovery_selected
        ]
        non_terminal_ids = [
            f"{record.doc_id}:{record.status}"
            for record in scoped_records
            if record.status not in TERMINAL_STATES
        ]
        if non_terminal_ids:
            raise AssertionError(
                "every active document must end in completed, failed, or quarantine; "
                f"non_terminal={non_terminal_ids}"
            )
        if list((self.run_dir / "processing").glob("*")):
            raise AssertionError("no document may remain in processing")
        for record in scoped_records:
            if record.status == "COMPLETED":
                if self.config.operation_mode == "maintenance_first":
                    self._verify_seeded_source_map(record)
                else:
                    self._verify_document(record)
            if record.status in {"FAILED", "QUARANTINED"} and not any(
                failure.doc_id == record.doc_id for failure in self.failure_records
            ):
                raise AssertionError(f"{record.doc_id} is terminal failure without failure record")
        if not any(record.status == "COMPLETED" for record in scoped_records):
            raise AssertionError(self._no_completed_documents_failure_message())
        provenance = self.promotion_provenance_summary()
        if provenance["missing_count"] > 0:
            raise AssertionError(
                f"promotion provenance missing for {provenance['missing_document_ids']}"
            )
        maintenance = self.maintenance_summary()
        useful_maintenance = (
            maintenance["derived_artifact_count"] > 0
            or maintenance["maintenance_workflow_step_count"] > 0
            or maintenance["job_status_counts"].get("DONE", 0) > 0
            or bool(maintenance["foreground_replies"])
        )
        if not useful_maintenance and not self.config.skip_maintenance_invariant:
            raise AssertionError(self._maintenance_invariant_failure_message(maintenance))
        self._verify_runtime_events_have_run_ids()
        self._verify_derived_nodes_have_provenance()
        projection = self.projection_summary()
        if projection["snapshot_status"] != "ok":
            raise AssertionError(f"projection read failed: {projection['snapshot_error']}")
        progress = self.progress_summary()
        if progress["doc_count"] != len(self.records):
            raise AssertionError("progress summary doc count mismatch")

    def _no_completed_documents_failure_message(self) -> str:
        grouped: dict[str, dict[str, Any]] = {}
        for failure in self.failure_records:
            bucket = grouped.setdefault(
                failure.fingerprint,
                {
                    "code": failure.code,
                    "phase": failure.phase,
                    "scope": failure.scope,
                    "count": 0,
                    "document_ids": [],
                    "message": failure.message[:1_000],
                    "details": failure.details,
                },
            )
            bucket["count"] += 1
            if failure.doc_id:
                bucket["document_ids"].append(failure.doc_id)
        root_failures = sorted(grouped.values(), key=lambda item: item["count"], reverse=True)
        return (
            "no documents completed; maintenance was not expected because parse/persist never "
            "produced an eligible document; root_failures="
            f"{json.dumps(root_failures, sort_keys=True)}"
        )

    def _maintenance_invariant_failure_message(self, maintenance: dict[str, Any]) -> str:
        diagnostic = {
            "operation_mode": self.config.operation_mode,
            "backend": self.config.backend,
            "maintenance_poll_count": maintenance.get("maintenance_poll_count"),
            "max_post_doc_maintenance_steps": self.config.max_post_doc_maintenance_steps,
            "job_status_counts": maintenance.get("job_status_counts"),
            "maintenance_job_ids": maintenance.get("maintenance_job_ids"),
            "maintenance_source_document_ids": maintenance.get("maintenance_source_document_ids"),
            "maintenance_workflow_step_count": maintenance.get("maintenance_workflow_step_count"),
            "derived_artifact_count": maintenance.get("derived_artifact_count"),
            "foreground_reply_count": len(list(maintenance.get("foreground_replies") or [])),
            "lane_message_count": len(list(maintenance.get("maintenance_lane_messages") or [])),
            "completed_document_ids": [
                record.doc_id for record in self.records if record.status == "COMPLETED"
            ],
            "failed_document_ids": [
                record.doc_id for record in self.records if record.status in {"FAILED", "QUARANTINED"}
            ],
        }
        return (
            "background maintenance did not produce persisted evidence; "
            f"diagnostic={json.dumps(diagnostic, sort_keys=True)}"
        )

    def promotion_provenance_summary(self) -> dict[str, Any]:
        verified: list[dict[str, Any]] = []
        missing: list[dict[str, Any]] = []
        for record in self.records:
            if record.status != "COMPLETED" or not record.promoted_entity_id:
                continue
            try:
                verified.append(self._verify_promotion_provenance(record))
            except Exception as exc:  # noqa: BLE001
                missing.append(
                    {
                        "doc_id": record.doc_id,
                        "promoted_entity_id": record.promoted_entity_id,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                )
        return {
            "promoted_count": len(verified) + len(missing),
            "verified_count": len(verified),
            "missing_count": len(missing),
            "verified": verified,
            "missing": missing,
            "missing_document_ids": [row["doc_id"] for row in missing],
        }

    def _verify_runtime_events_have_run_ids(self) -> None:
        with _temporary_namespace(
            self.engines.conversation,
            WorkspaceNamespaces(self.config.workspace_id).conv_bg,
        ):
            events = self.engines.conversation.read.get_nodes(
                where={"workflow_id": self._workflow_id()},
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
                where=_and_where(
                    {"artifact_kind": "derived_knowledge"},
                    {"workspace_id": self.config.workspace_id},
                ),
                limit=10_000,
            )
        for node in derived:
            if not getattr(node, "mentions", None):
                raise AssertionError(f"derived node {node.id} is missing provenance")

    def _build_parser(self):
        provider_settings = self._provider_settings()

        def _parser(**kwargs):
            kwargs.pop("llm_provider", None)
            kwargs.pop("model", None)
            kwargs.pop("provider_settings", None)
            return parse_page_index_document(provider_settings=provider_settings, **kwargs)

        return _parser

    def _provider_settings(self) -> WorkflowProviderSettings:
        return WorkflowProviderSettings(
            proposal_mode=self.config.parser_proposal_mode,
            parser=ProviderEndpointConfig(
                provider=self.config.parser_provider,
                model=self.config.parser_model,
                temperature=self.config.parser_temperature,
                base_url=self.config.parser_base_url,
                api_key_env=self.config.parser_api_key_env,
                api_version=self.config.parser_api_version,
                max_retries=self.config.parser_max_retries,
            ),
            embedding=EmbeddingProviderConfig(provider="fake", model="longrun-embed", dimension=2),
        )

    def _run_parse_with_subprocess(
        self,
        *,
        record: DocumentRecord,
        request: IngestPipelineRequest,
        source_document_id: str,
    ) -> Any:
        parser_run_dir = self.run_dir / "parser_runs" / record.doc_id
        parser_run_dir.mkdir(parents=True, exist_ok=True)
        result_path = parser_run_dir / "result.json"
        failure_path = parser_run_dir / "failure.json"
        failure_payload_path = parser_run_dir / "failure_payload.json"
        usage_event_path = parser_run_dir / "usage_events.jsonl"
        heartbeat_path = self.dumper.dump_dir / "parser_heartbeat.json"
        trace_path = parser_run_dir / "trace.log"
        dump_trace_path = self.dumper.dump_dir / "parser_trace.log"
        remaining_runtime_seconds = self._remaining_runtime_seconds()
        effective_timeout_seconds = float(self.config.parse_timeout_seconds)
        parser_provider = self.config.parser_provider
        parser_model = self.config.parser_model
        if remaining_runtime_seconds is not None:
            effective_timeout_seconds = min(effective_timeout_seconds, remaining_runtime_seconds)
        if effective_timeout_seconds <= 0:
            raise LongRunSystemicError(
                "runtime_worker_stuck",
                f"runtime budget exhausted before parser start for {record.doc_id}",
                phase="parse_document",
            )
        record.parser_workflow_run_id = str(
            record.parser_workflow_run_id or f"parser:{source_document_id}"
        )
        for path in (result_path, failure_path, failure_payload_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        if not record.parser_resume_requested:
            for path in (trace_path, dump_trace_path):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
        payload = {
            "parser_lane": self.config.parser_lane,
            "child_mode": "sleep" if self.config.parse_timeout_seconds <= 1 else "parse",
            "doc_id": record.doc_id,
            "source_document_id": source_document_id,
            "title": request.title,
            "raw_text": request.raw_text,
            "source_format": request.source_format,
            "parser_mode": request.parser_mode,
            "provider_settings": self._provider_settings().model_dump(
                field_mode="backend",
                dump_format="json",
            ),
            "parser_run_dir": str(parser_run_dir),
            "result_path": str(result_path),
            "failure_path": str(failure_path),
            "failure_payload_path": str(failure_payload_path),
            "usage_event_path": str(usage_event_path),
            "parser_workflow_run_id": str(
                record.parser_workflow_run_id or f"parser:{source_document_id}"
            ),
            "resume_from_checkpoint": bool(record.parser_resume_requested),
            "heartbeat_path": str(heartbeat_path),
            "trace_path": str(trace_path),
            "dump_trace_path": str(dump_trace_path),
            "live_trace": self.config.live_trace,
            "parser_provider": parser_provider,
            "parser_model": parser_model,
            "conversation_persistence_mode": self.config.conversation_persistence_mode,
        }
        _append_trace_line(
            dump_trace_path,
            (
                f"parent_spawn_start doc={record.doc_id} lane={self.config.parser_lane} "
                f"parser={parser_provider}/{parser_model} "
                f"parser_workflow_run_id={payload['parser_workflow_run_id']} "
                f"resume_from_checkpoint={payload['resume_from_checkpoint']} "
                f"effective_timeout={effective_timeout_seconds:.2f}s "
                f"configured_parse_timeout={self.config.parse_timeout_seconds}s "
                f"remaining_runtime={None if remaining_runtime_seconds is None else round(remaining_runtime_seconds, 2)}s"
            ),
        )
        started_ms = _now_ms()
        started_monotonic = time.monotonic()
        self._write_parser_heartbeat(
            {
                "phase": "parent_waiting",
                "timestamp_ms": started_ms,
                "parser_lane": self.config.parser_lane,
                "parser_provider": parser_provider,
                "parser_model": parser_model,
                "doc_id": record.doc_id,
                "timeout_seconds": effective_timeout_seconds,
                "configured_parse_timeout_seconds": self.config.parse_timeout_seconds,
                "remaining_runtime_seconds": remaining_runtime_seconds,
                "result_path": str(result_path),
                "failure_path": str(failure_path),
                "parser_workflow_run_id": payload["parser_workflow_run_id"],
                "resume_from_checkpoint": payload["resume_from_checkpoint"],
            }
        )
        context = multiprocessing.get_context("spawn")
        process = context.Process(
            name=f"llm-wiki-parser-{record.doc_id}",
            target=run_longrun_parser_child,
            args=(payload,),
        )
        process.start()
        process_pid = process.pid
        _append_trace_line(
            dump_trace_path,
            (
                f"parent_spawned pid={process.pid} name={process.name} "
                f"parent_pid={os.getpid()} thread={threading.current_thread().name} "
                f"alive={process.is_alive()} doc={record.doc_id}"
            ),
        )
        last_poll_log_seconds = -_PARSER_PARENT_POLL_LOG_INTERVAL_SECONDS
        while process.is_alive():
            elapsed_seconds = time.monotonic() - started_monotonic
            if elapsed_seconds - last_poll_log_seconds >= _PARSER_PARENT_POLL_LOG_INTERVAL_SECONDS:
                _append_trace_line(
                    dump_trace_path,
                    (
                        f"parent_poll pid={process.pid} elapsed={elapsed_seconds:.2f}s "
                        f"alive={process.is_alive()} interval={_PARSER_PARENT_POLL_LOG_INTERVAL_SECONDS:.0f}s"
                    ),
                )
                last_poll_log_seconds = elapsed_seconds
            if elapsed_seconds > effective_timeout_seconds:
                _append_trace_line(
                    dump_trace_path,
                    (
                        f"parent_timeout pid={process.pid} elapsed={elapsed_seconds:.2f}s "
                        f"effective_timeout={effective_timeout_seconds:.2f}s"
                    ),
                )
                self._terminate_parser_child(process)
                self._close_parser_child(process)
                record.parser_resume_requested = True
                self._write_parser_heartbeat(
                    {
                        "phase": "timeout",
                        "timestamp_ms": _now_ms(),
                        "parser_lane": self.config.parser_lane,
                        "doc_id": record.doc_id,
                        "pid": process_pid,
                        "timeout_seconds": effective_timeout_seconds,
                        "configured_parse_timeout_seconds": self.config.parse_timeout_seconds,
                        "remaining_runtime_seconds": remaining_runtime_seconds,
                        "result_path": str(result_path),
                        "failure_path": str(failure_path),
                        "parser_workflow_run_id": payload["parser_workflow_run_id"],
                        "resume_from_checkpoint": payload["resume_from_checkpoint"],
                    }
                )
                raise LongRunDocumentError(
                    "document_parse_failed",
                    (
                        f"parser lane {self.config.parser_lane!r} timed out after effective "
                        f"{effective_timeout_seconds:.2f}s for {record.doc_id}"
                    ),
                    phase="parse_document",
                    details={
                        "failure_kind": "parser_timeout",
                        "parser_lane": self.config.parser_lane,
                        "effective_timeout_seconds": effective_timeout_seconds,
                        "configured_parse_timeout_seconds": self.config.parse_timeout_seconds,
                        "remaining_runtime_seconds": remaining_runtime_seconds,
                        "parser_trace_path": str(trace_path),
                        "parser_failure_path": str(failure_path),
                        "parser_failure_payload_path": str(failure_payload_path),
                        "parser_workflow_run_id": payload["parser_workflow_run_id"],
                        "resume_from_checkpoint": payload["resume_from_checkpoint"],
                        "usage_events": self._read_usage_event_file(usage_event_path),
                        "parser_heartbeat": dict(self.parser_heartbeat or {}),
                        "parser_trace_tail": _read_text_tail(trace_path),
                        "dump_trace_tail": _read_text_tail(dump_trace_path),
                    },
                )
            self._write_parser_heartbeat(
                {
                    "phase": "parent_waiting",
                    "timestamp_ms": _now_ms(),
                    "parser_lane": self.config.parser_lane,
                    "doc_id": record.doc_id,
                    "pid": process.pid,
                    "timeout_seconds": effective_timeout_seconds,
                    "configured_parse_timeout_seconds": self.config.parse_timeout_seconds,
                    "remaining_runtime_seconds": remaining_runtime_seconds,
                    "result_path": str(result_path),
                    "failure_path": str(failure_path),
                    "parser_workflow_run_id": payload["parser_workflow_run_id"],
                    "resume_from_checkpoint": payload["resume_from_checkpoint"],
                }
            )
            try:
                process.join(timeout=1.0)
            except KeyboardInterrupt:
                _append_trace_line(dump_trace_path, f"parent_keyboard_interrupt pid={process_pid}")
                self._terminate_parser_child(process)
                self._close_parser_child(process)
                record.parser_resume_requested = True
                self._write_parser_heartbeat(
                    {
                        "phase": "interrupted",
                        "timestamp_ms": _now_ms(),
                        "parser_lane": self.config.parser_lane,
                        "doc_id": record.doc_id,
                        "pid": process_pid,
                        "result_path": str(result_path),
                        "failure_path": str(failure_path),
                        "parser_workflow_run_id": payload["parser_workflow_run_id"],
                        "resume_from_checkpoint": payload["resume_from_checkpoint"],
                    }
                )
                # Persist the stable parser identity and resume intent before
                # allowing Ctrl+C to unwind the caller.
                self.dumper.dump(reason=f"parser_interrupted_{record.doc_id}")
                raise
        process.join(timeout=1.0)
        process_exitcode = process.exitcode
        _append_trace_line(
            dump_trace_path,
            f"parent_join_complete pid={process_pid} exitcode={process_exitcode}",
        )
        if result_path.exists():
            _append_trace_line(dump_trace_path, "parent_read_result_json")
            result_payload = json.loads(result_path.read_text(encoding="utf-8"))
            parser_workflow_run_id = str(
                payload.get("parser_workflow_run_id") or f"parser:{source_document_id}"
            )
            resume_from_checkpoint = bool(payload.get("resume_from_checkpoint"))
            layer_log = result_payload.get("layer_log")
            if isinstance(layer_log, list):
                layer_log_dump_path = self.dumper.write_parser_layer_log(
                    doc_id=record.doc_id,
                    layer_log=layer_log,
                )
                _append_trace_line(
                    dump_trace_path,
                    f"parent_layer_log_persisted path={layer_log_dump_path}",
                )
            if result_payload.get("ok") is not True:
                workflow_status = str(result_payload.get("workflow_status") or "failure")
                usage_summary = dict(result_payload.get("usage_summary") or {})
                _append_trace_line(
                    dump_trace_path,
                    f"parent_rejected_result workflow_status={workflow_status} "
                    f"parser_workflow_run_id={parser_workflow_run_id}",
                )
                self._write_parser_heartbeat(
                    {
                        "phase": "failed",
                        "timestamp_ms": _now_ms(),
                        "parser_lane": payload.get("parser_lane", self.config.parser_lane),
                        "doc_id": record.doc_id,
                        "pid": process.pid,
                        "result_path": str(result_path),
                        "failure_path": str(failure_path),
                        "parser_workflow_run_id": parser_workflow_run_id,
                        "resume_from_checkpoint": resume_from_checkpoint,
                        "workflow_status": workflow_status,
                        "diagnostics": result_payload.get("diagnostics") or {},
                    }
                )
                self._close_parser_child(process)
                raise LongRunDocumentError(
                    "document_parse_failed",
                    f"parser workflow returned non-success status {workflow_status!r} for {record.doc_id}",
                    phase="parse_document",
                    details={
                        "failure_kind": "parser_workflow_failure",
                        "parser_lane": self.config.parser_lane,
                        "workflow_status": workflow_status,
                        "parser_workflow_run_id": parser_workflow_run_id,
                        "resume_from_checkpoint": resume_from_checkpoint,
                        "llm_call_count": int(usage_summary.get("llm_call_count") or 0),
                        "usage_summary": usage_summary,
                        "usage_events": list(result_payload.get("usage_events") or []),
                        "parser_trace_path": str(trace_path),
                        "parser_failure_path": str(failure_path),
                        "parser_trace_tail": _read_text_tail(trace_path),
                        "dump_trace_tail": _read_text_tail(dump_trace_path),
                    },
                )
            self._write_parser_heartbeat(
                {
                    "phase": "completed",
                    "timestamp_ms": _now_ms(),
                    "parser_lane": payload.get("parser_lane", self.config.parser_lane),
                    "doc_id": record.doc_id,
                    "pid": process.pid,
                    "result_path": str(result_path),
                    "failure_path": str(failure_path),
                    "parser_workflow_run_id": parser_workflow_run_id,
                    "resume_from_checkpoint": resume_from_checkpoint,
                    "diagnostics": result_payload.get("diagnostics") or {},
                }
            )
            self._close_parser_child(process)
            return self._parse_result_from_payload(result_payload)
        failure: dict[str, Any] = {}
        if failure_path.exists():
            _append_trace_line(dump_trace_path, "parent_read_failure_json")
            failure = _read_json_object(failure_path)
        child_trace_tail = _read_text_tail(trace_path)
        dump_trace_tail = _read_text_tail(dump_trace_path)
        self._write_parser_heartbeat(
            {
                "phase": "failed",
                "timestamp_ms": _now_ms(),
                "parser_lane": self.config.parser_lane,
                "doc_id": record.doc_id,
                "pid": process_pid,
                "trace_path": str(trace_path),
                "result_path": str(result_path),
                "failure_path": str(failure_path),
                "parser_workflow_run_id": payload["parser_workflow_run_id"],
                "resume_from_checkpoint": payload["resume_from_checkpoint"],
                "failure": failure,
                "trace_tail": child_trace_tail,
            }
        )
        self._close_parser_child(process)
        raise LongRunDocumentError(
            "document_parse_failed",
            self._parser_child_failure_message(
                record=record,
                process=process,
                process_pid=process_pid,
                process_exitcode=process_exitcode,
                failure=failure,
                trace_path=trace_path,
                failure_path=failure_path,
            ),
            phase="parse_document",
            details={
                "failure_kind": "parser_child_failure",
                "parser_lane": self.config.parser_lane,
                "child_exitcode": process_exitcode,
                "parser_trace_path": str(trace_path),
                "parser_failure_path": str(failure_path),
                "parser_failure_payload_path": str(failure_payload_path),
                "child_error_type": failure.get("error_type"),
                "child_error_message": str(failure.get("message") or "")[:1_000],
                "llm_call_count": int(failure.get("llm_call_count") or 0),
                "usage_events": list(failure.get("usage_events") or []),
                "usage_event_file_events": self._read_usage_event_file(usage_event_path),
                "parser_workflow_run_id": payload["parser_workflow_run_id"],
                "resume_from_checkpoint": payload["resume_from_checkpoint"],
                "child_failure_payload_path": failure.get("payload_path"),
                "child_failure": {
                    "error_type": failure.get("error_type"),
                    "message": str(failure.get("message") or "")[:1_000],
                    "traceback": str(failure.get("traceback") or "")[-4_000:],
                },
                "parser_trace_tail": child_trace_tail,
                "dump_trace_tail": dump_trace_tail,
            },
        )

    @staticmethod
    def _read_usage_event_file(path: Path) -> list[dict[str, Any]]:
        """Read flushed child usage events without making recovery brittle."""
        if not path.exists():
            return []
        events: list[dict[str, Any]] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            return []
        for line in lines:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                events.append(value)
        return events

    def _terminate_parser_child(self, process: multiprocessing.Process) -> None:
        if not process.is_alive():
            return
        process.terminate()
        process.join(timeout=5.0)
        if process.is_alive() and hasattr(process, "kill"):
            process.kill()
            process.join(timeout=5.0)
        self._close_parser_child(process)

    def _close_parser_child(self, process: multiprocessing.Process) -> None:
        close = getattr(process, "close", None)
        if callable(close):
            try:
                close()
            except (OSError, ValueError):
                pass

    def _write_parser_heartbeat(self, payload: dict[str, Any]) -> None:
        self.parser_heartbeat = dict(payload)
        _write_json_file(self.dumper.dump_dir / "parser_heartbeat.json", self.parser_heartbeat)

    def _parse_result_from_payload(self, payload: dict[str, Any]) -> Any:
        return SimpleNamespace(
            semantic_tree=SimpleNamespace(title=str(payload.get("title") or "Parsed document")),
            graph_payload=dict(payload.get("graph_payload") or {}),
            parser_lane=str(payload.get("parser_lane") or self.config.parser_lane),
            diagnostics=dict(payload.get("diagnostics") or {}),
            evaluation=dict(payload.get("evaluation") or {}),
            usage_summary=dict(payload.get("usage_summary") or {}),
            usage_events=list(payload.get("usage_events") or []),
            layer_log=list(payload.get("layer_log") or []),
        )

    def _generate_corpus(self) -> None:
        for index in range(1, self.config.doc_count + 1):
            doc_id = f"doc-{index:03d}"
            title = _longrun_document_title(
                index=index,
                corpus_profile=self.config.corpus_profile,
            )
            path = self.run_dir / "input" / f"{doc_id}.md"
            text = generate_longrun_document(
                index=index,
                title=title,
                profile=self.config.doc_profile,
                corpus_profile=self.config.corpus_profile,
            )
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
        workflow_id = self._workflow_id()
        grounding = [Grounding(spans=[Span.from_dummy_for_workflow(workflow_id)])]
        node_ids: dict[str, str] = {}
        workflow_steps = self._workflow_steps()
        for step in workflow_steps:
            node_id = str(stable_id("wf_node", workflow_id, step))
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
                        "workflow_id": workflow_id,
                        "wf_op": step,
                        "wf_start": step == workflow_steps[0],
                    },
                )
            )
        terminal_id = str(stable_id("wf_node", workflow_id, "done"))
        self.engines.workflow.write.add_node(
            WorkflowNode(
                id=terminal_id,
                label="done",
                type="entity",
                summary="Long-run ingestion terminal state.",
                mentions=grounding,
                metadata={
                    "entity_type": "workflow_node",
                    "workflow_id": workflow_id,
                    "wf_terminal": True,
                },
            )
        )
        targets = workflow_steps[1:] + ["done"]
        for source, target in zip(workflow_steps, targets):
            self.engines.workflow.write.add_edge(
                WorkflowEdge(
                    id=str(stable_id("wf_edge", workflow_id, source, target)),
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
                        "workflow_id": workflow_id,
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
    structured_output_failure = any(
        token in text
        for token in (
            "invalid json",
            "jsondecode",
            "structured output",
            "schema validation",
            "validation error",
            "validationerror",
        )
    )
    if phase == "parse_document" and structured_output_failure:
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
    if "stuck" in text or "timeout" in text or "runtime budget exhausted" in text:
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


def _longrun_document_title(*, index: int, corpus_profile: str) -> str:
    if corpus_profile == "daily_life":
        subjects = [
            "Apartment Move Checklist",
            "Weekly Meal Plan",
            "Garden Maintenance Notes",
            "Family Trip Itinerary",
            "Home Office Setup",
            "Pet Care Routine",
            "Neighborhood Book Club",
            "Car Service Log",
            "Birthday Party Plan",
            "Monthly Household Budget",
        ]
        return f"{subjects[(index - 1) % len(subjects)]} {index:03d}"
    return f"Watershed Resilience Brief {index:03d}"


def generate_longrun_document(
    *,
    index: int,
    title: str,
    topic: str | None = None,
    profile: str = "medium",
    corpus_profile: str = "watershed_stress",
) -> str:
    profile = str(profile or "medium").strip().lower()
    corpus_profile = str(corpus_profile or "watershed_stress").strip().lower()
    if corpus_profile == "daily_life":
        return _generate_daily_life_longrun_document(index=index, title=title, profile=profile)
    topic = topic or "urban watershed resilience and stormwater infrastructure"
    section_counts = {
        "tiny": 2,
        "small": 4,
        "medium": 7,
    }
    intro_by_profile = {
        "tiny": f"This brief studies {topic} with a focus on {title.lower()}.",
        "small": f"This brief studies {topic} with emphasis on watershed priorities and local maintenance.",
        "medium": f"This brief studies {topic} with emphasis on {['green streets', 'detention basins', 'sensor networks', 'community stewardship', 'combined sewer overflow controls'][index % 5]}.",
    }
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
        intro_by_profile.get(profile, intro_by_profile["medium"]),
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
    section_count = section_counts.get(profile, section_counts["medium"])
    for section in range(1, section_count + 1):
        sections.append(f"## Finding {section}")
        sections.append("")
        sections.append(
            (
                paragraph
                if profile == "medium"
                else (
                    f"In district {index}, the team records {topic} observations and chooses a small response. "
                    f"The finding for cycle {section} keeps the decision tied to the source record."
                    if profile == "tiny"
                    else (
                        f"In district {index}, the team records {topic} observations and chooses a small response. "
                        f"The finding for cycle {section} stays linked to the source record and the maintenance plan."
                    )
                )
            )
            + (
                " This provenance matters when a later model summary is incomplete, unsupported, or too "
                "confident about benefits that were not measured in the source record."
                if profile == "medium"
                else ""
            )
        )
        sections.append("")
    return "\n".join(sections)


def _generate_daily_life_longrun_document(*, index: int, title: str, profile: str) -> str:
    section_counts = {
        "tiny": 2,
        "small": 4,
        "medium": 7,
    }
    subject = [
        "moving boxes and utility transfers",
        "weekday dinners and grocery shopping",
        "balcony herbs and shared garden chores",
        "train tickets, hotel check-in, and museum reservations",
        "desk lighting, cable labels, and backup routines",
        "feeding times, vet reminders, and dog-walking coverage",
        "reading notes, discussion snacks, and library pickups",
        "oil changes, tire pressure, and registration reminders",
        "guest list planning, decorations, and cake pickup",
        "rent, subscriptions, savings goals, and repair funds",
    ][(index - 1) % 10]
    location = [
        "Oak Street",
        "Maple Court",
        "Riverside Station",
        "North Market",
        "Cedar Building",
        "Elm Park",
        "Hillview Library",
        "Lakeside Garage",
        "Sunset Hall",
        "Pine Avenue",
    ][(index - 1) % 10]
    helper = [
        "Maya",
        "Jon",
        "Priya",
        "Alex",
        "Nora",
        "Sam",
        "Lena",
        "Diego",
        "Iris",
        "Owen",
    ][(index - 1) % 10]
    section_count = section_counts.get(profile, section_counts["medium"])
    sections = [
        f"# {title}",
        "",
        (
            f"This everyday note tracks {subject} for the household around {location}. "
            f"{helper} keeps the checklist practical, with dates, owners, and small follow-up items "
            "that should be easy to parse without needing domain-specific background knowledge."
        ),
        "",
    ]
    short_body = (
        f"The note for {location} records who owns each task, what evidence confirms completion, "
        "and what should be checked again later. The goal is not to prove a technical claim; it is "
        "to keep normal life organized with clear headings and modest cross-links to related notes."
    )
    medium_body = (
        f"At {location}, {helper} reviews {subject} and writes down the next concrete action. "
        "The plan names the person responsible, the expected date, and the small source of truth, "
        "such as a receipt, calendar invite, appliance manual, email confirmation, or checklist photo. "
        "Related notes may mention the same person, place, or household item, but the wording is varied "
        "enough that a parser should not confuse every document with every other document. If something "
        "changes, the note says whether to update the calendar, ask a neighbor, file a receipt, or move "
        "the item into a later maintenance queue. This gives the graph useful linkable content while still "
        "feeling like ordinary user data instead of an adversarial benchmark."
    )
    for section in range(1, section_count + 1):
        sections.append(f"## Step {section}: {['Plan', 'Gather', 'Confirm', 'Follow Up', 'Archive', 'Share', 'Review'][(section - 1) % 7]}")
        sections.append("")
        if profile == "tiny":
            sections.append(
                f"{helper} records one action about {subject}. The item stays linked to {location} and has a clear owner."
            )
        elif profile == "small":
            sections.append(
                f"{short_body} For step {section}, {helper} checks whether the task is done, blocked, or waiting."
            )
        else:
            sections.append(
                f"{medium_body} For step {section}, the expected outcome is written in plain language so a later "
                "maintenance worker can add links without inventing facts."
            )
        sections.append("")
    return "\n".join(sections)


def _longrun_doc_profile_token_bounds(profile: str) -> tuple[int, int]:
    profile = str(profile or "medium").strip().lower()
    bounds = {
        "tiny": (70, 150),
        "small": (150, 800),
        "medium": (500, 2000),
    }
    return bounds.get(profile, bounds["medium"])


def test_generate_longrun_document_profiles_scale_by_size():
    topic = "urban watershed resilience and stormwater infrastructure"
    tiny = generate_longrun_document(index=1, title="Watershed Brief 001", topic=topic, profile="tiny")
    small = generate_longrun_document(index=1, title="Watershed Brief 001", topic=topic, profile="small")
    medium = generate_longrun_document(index=1, title="Watershed Brief 001", topic=topic, profile="medium")

    tiny_tokens = _count_tokens(tiny)
    small_tokens = _count_tokens(small)
    medium_tokens = _count_tokens(medium)

    assert tiny_tokens < small_tokens < medium_tokens
    assert 70 <= tiny_tokens <= 150
    assert 150 <= small_tokens <= 520
    assert 500 <= medium_tokens <= 2000
    assert tiny.count("## Finding") == 2
    assert small.count("## Finding") == 4
    assert medium.count("## Finding") == 7


def test_generate_longrun_document_daily_life_profile_is_realistic_and_varied():
    first = generate_longrun_document(
        index=1,
        title=_longrun_document_title(index=1, corpus_profile="daily_life"),
        profile="medium",
        corpus_profile="daily_life",
    )
    second = generate_longrun_document(
        index=2,
        title=_longrun_document_title(index=2, corpus_profile="daily_life"),
        profile="medium",
        corpus_profile="daily_life",
    )

    assert "Watershed" not in first
    assert "ordinary user data" in first
    assert "Apartment Move Checklist" in first
    assert "Weekly Meal Plan" in second
    assert first != second
    assert 500 <= _count_tokens(first) <= 2000
    assert first.count("## Step") == 7


def test_generate_daily_life_twenty_doc_corpus_has_varied_titles_and_valid_token_bounds():
    docs = [
        generate_longrun_document(
            index=index,
            title=_longrun_document_title(index=index, corpus_profile="daily_life"),
            profile="medium",
            corpus_profile="daily_life",
        )
        for index in range(1, 21)
    ]
    titles = [doc.splitlines()[0] for doc in docs]
    token_counts = [_count_tokens(doc) for doc in docs]

    assert len(docs) == 20
    assert len(set(titles)) == 20
    assert all("Watershed" not in doc for doc in docs)
    assert all(500 <= token_count <= 2000 for token_count in token_counts)


def test_longrun_doc_profile_token_bounds_are_applied_by_default():
    assert _longrun_doc_profile_token_bounds("tiny") == (70, 150)
    assert _longrun_doc_profile_token_bounds("small") == (150, 800)
    assert _longrun_doc_profile_token_bounds("medium") == (500, 2000)


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


def _longrun_requires_ollama(config: LongRunConfig) -> bool:
    return normalize_provider_name(config.parser_provider) == "ollama"


def test_longrun_ollama_healthcheck_gate_is_provider_specific() -> None:
    assert _longrun_requires_ollama(
        LongRunConfig(enabled=False, mode="fresh", doc_count=1, parser_provider="ollama")
    )
    assert not _longrun_requires_ollama(
        LongRunConfig(enabled=False, mode="fresh", doc_count=1, parser_provider="azure")
    )


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


def _and_where(*clauses: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return {"$and": [dict(clause) for clause in clauses]}


def _decode_metadata_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str) and value.strip():
        parsed = json.loads(value)
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _model_to_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump()
        except TypeError:
            return value.model_dump(mode="python")
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

    repeated_parser_failures = [
        FailureRecord(
            run_id="run",
            doc_id=f"doc-{index:03d}",
            phase="parse_document",
            code="document_parse_failed",
            scope="document",
            message="parser child exited without result exitcode=1",
            fingerprint=normalized_fingerprint(
                code="document_parse_failed",
                phase="parse_document",
                message="parser child exited without result exitcode=1",
            ),
            timestamp_ms=_now_ms(),
        )
        for index in range(1, 4)
    ]
    assert breaker.record(repeated_parser_failures[0]) is False
    assert breaker.record(repeated_parser_failures[1]) is False
    assert breaker.record(repeated_parser_failures[2]) is True
    assert classify_exception(ValueError("invalid json structured output"), phase="observe") == "llm_invalid_json"
    assert classify_exception(RuntimeError("sqlite database write failed"), phase="persist_document") == (
        "database_write_repeated_failure"
    )


def test_workflow_failure_preserves_captured_harness_exception(tmp_path: Path):
    harness = LongRunHarness(
        run_dir=tmp_path / "captured-harness-failure",
        config=LongRunConfig(enabled=False, mode="fresh", doc_count=1),
    )
    record = DocumentRecord(
        doc_id="doc-001",
        title="Captured failure",
        source_uri="file:///doc-001.md",
        input_path=tmp_path / "doc-001.md",
        current_path=tmp_path / "doc-001.md",
        last_step_name="parse_document",
    )
    result = RunResult(
        run_id="run-doc-001",
        final_state={},
        mq=queue.Queue(),
        status="failure",
        errors=[
            "'LongRunHarness' object has no attribute '_write_json'",
            "Traceback: AttributeError: 'LongRunHarness' object has no attribute '_write_json'",
        ],
    )

    error = harness._workflow_failure_exception(
        record=record,
        result=result,
        run_id=result.run_id,
    )
    failure = harness._classify_exception(error, doc_id=record.doc_id, phase="runtime")

    assert isinstance(error, LongRunSystemicError)
    assert "_write_json" in str(error)
    assert error.details["failed_step"] == "parse_document"
    assert error.details["workflow_errors"] == result.errors
    assert failure.scope == "systemic"
    assert failure.details["workflow_run_id"] == result.run_id
    assert "_write_json" in failure.fingerprint


def test_longrun_failure_is_streamed_and_persisted_immediately(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    harness = LongRunHarness(
        run_dir=tmp_path / "failure-observability",
        config=LongRunConfig(enabled=False, mode="fresh", doc_count=1, live_trace=True),
    )
    failure = harness._failure_record(
        doc_id="doc-001",
        phase="parse_document",
        code="document_parse_failed",
        scope="document",
        message="parser child exited without result",
        details={
            "parser_trace_path": "C:/runs/doc-001/trace.log",
            "parser_failure_path": "C:/runs/doc-001/failure.json",
            "child_exitcode": 1,
        },
    )

    harness._record_failure(failure)

    event_path = harness.dumper.dump_dir / "failure_events.jsonl"
    assert event_path.exists()
    event = json.loads(event_path.read_text(encoding="utf-8").strip())
    assert event["event_type"] == "longrun_failure_recorded"
    assert event["code"] == "document_parse_failed"
    assert event["details"]["child_exitcode"] == 1
    assert event["details"]["parser_failure_path"].endswith("failure.json")
    assert "[longrun] document_failure_recorded" in capsys.readouterr().err


def test_longrun_all_failed_invariant_reports_root_failures_not_maintenance(
    tmp_path: Path,
):
    harness = LongRunHarness(
        run_dir=tmp_path / "all-failed",
        config=LongRunConfig(enabled=False, mode="fresh", doc_count=1),
    )
    record = DocumentRecord(
        doc_id="doc-001",
        title="Failure fixture",
        source_uri="file:///doc-001.md",
        input_path=tmp_path / "input.md",
        current_path=tmp_path / "failed.md",
        status="FAILED",
    )
    harness.records.append(record)
    harness._record_failure(
        harness._failure_record(
            doc_id=record.doc_id,
            phase="parse_document",
            code="document_parse_failed",
            scope="document",
            message="parser child timed out",
            details={"parser_trace_path": "C:/runs/doc-001/trace.log"},
        )
    )

    with pytest.raises(AssertionError, match="no documents completed") as exc_info:
        harness._verify_run_invariants()

    message = str(exc_info.value)
    assert "background maintenance did not produce persisted evidence" not in message
    assert "document_parse_failed" in message
    assert "parser_trace_path" in message


def test_longrun_checkpoint_state_is_loaded_across_reruns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    run_dir = tmp_path / "continuation-probe"
    config = LongRunConfig(
        enabled=False,
        mode="auto",
        doc_count=2,
        ollama_model="gemma4:e2b",
        ollama_base_url="http://localhost:11434",
        max_repeated_systemic_errors=3,
        max_post_doc_maintenance_steps=1,
    )

    def _dummy_parse_result():
        return SimpleNamespace(nodes=[], edges=[])

    def _complete_record(harness: LongRunHarness, record: DocumentRecord) -> None:
        record.started_at_ms = record.started_at_ms or _now_ms()
        harness._transition(record, "CLAIMED", phase="claim_document")
        harness._transition(record, "TOKEN_CHECKED", phase="token_check", token_count=record.token_count)
        harness._transition(record, "PARSED", phase="parse_document")
        harness._transition(record, "PERSISTED", phase="persist_document")
        harness._transition(record, "MAINTENANCE_ENQUEUED", phase="enqueue_background_maintenance")
        harness._transition(record, "MAINTENANCE_OBSERVED", phase="observe_background_maintenance")
        target = harness.run_dir / "completed" / record.current_path.name
        if record.current_path.exists() and record.current_path != target:
            shutil.move(str(record.current_path), str(target))
            record.current_path = target
        harness._transition(record, "COMPLETED", phase="move_completed")
        record.ended_at_ms = _now_ms()

    first = LongRunHarness(run_dir=run_dir, config=config)
    first.prepare()
    _complete_record(first, first.records[0])
    first._record_failure(
        first._failure_record(
            doc_id=first.records[0].doc_id,
            phase="token_check",
            code="token_count_out_of_range",
            scope="document",
            message="synthetic checkpoint failure for history rehydration",
        )
    )
    first.dumper.dump(reason="probe_checkpoint")

    manifest_path = run_dir / "dump" / "manifest.jsonl"
    assert manifest_path.exists()
    first_manifest = manifest_path.read_text(encoding="utf-8").splitlines()
    assert any('"status": "COMPLETED"' in line for line in first_manifest)
    assert any('"doc-002"' in line and '"status": "PENDING"' in line for line in first_manifest)

    second = LongRunHarness(run_dir=run_dir, config=config)
    second.prepare()
    assert second.checkpoint_loaded is True
    assert second.progress_summary()["manifest_checkpoint_loaded"] is True
    assert second.progress_summary()["completed_count"] >= 1
    assert second.progress_summary()["current_document_id"] == "doc-002"
    assert second.status_transitions
    assert second.status_transitions[0]["doc_id"] == "doc-001"
    assert second.failure_records
    assert second.failure_records[0].doc_id == "doc-001"
    assert second.failure_records[0].code == "token_count_out_of_range"
    _complete_record(second, second.records[1])
    second.dumper.dump(reason="probe_resume")
    final_manifest = (run_dir / "dump" / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    assert any('"doc-001"' in line and '"status": "COMPLETED"' in line for line in final_manifest)
    assert any('"doc-002"' in line and '"status": "COMPLETED"' in line for line in final_manifest)
    final_status_transitions = (run_dir / "dump" / "status_transitions.jsonl").read_text(encoding="utf-8").splitlines()
    final_failure_records = (run_dir / "dump" / "failure_records.jsonl").read_text(encoding="utf-8").splitlines()
    assert any('"doc_id":"doc-001"' in line or '"doc_id": "doc-001"' in line for line in final_status_transitions)
    assert any(
        '"code":"token_count_out_of_range"' in line or '"code": "token_count_out_of_range"' in line
        for line in final_failure_records
    )


def test_longrun_maintenance_summary_counts_only_maintenance_steps(tmp_path: Path):
    config = LongRunConfig(
        enabled=False,
        mode="fresh",
        doc_count=1,
        ollama_model="gemma4:e2b",
        ollama_base_url="http://localhost:11434",
        max_repeated_systemic_errors=3,
        max_post_doc_maintenance_steps=1,
    )
    harness = LongRunHarness(run_dir=tmp_path / "maintenance-summary", config=config)
    harness.prepare()

    step_rows = {
        "ids": ["ingest-step", "maintenance-step-1", "maintenance-step-2"],
        "documents": ["{}", "{}", "{}"],
        "metadatas": [
            {"entity_type": "workflow_step_exec", "workflow_id": harness._workflow_id()},
            {"entity_type": "workflow_step_exec", "workflow_id": DERIVED_KNOWLEDGE_WORKFLOW_ID},
            {"entity_type": "workflow_step_exec", "workflow_id": "maintenance.execution_wisdom.v1"},
        ],
    }
    derived_rows = {
        "ids": ["derived-1"],
        "documents": ["{}"],
        "metadatas": [{"artifact_kind": "derived_knowledge"}],
    }
    jobs = [SimpleNamespace(job_id="job-1", entity_id="doc-001", status="DONE")]

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(harness.engines.conversation.meta_sqlite, "list_index_jobs", lambda **kwargs: jobs)
    monkeypatch.setattr(
        harness.engines.conversation,
        "list_projected_lane_messages",
        lambda inbox_id: [SimpleNamespace(message_id="reply-1")] if inbox_id == "inbox:foreground" else [],
    )
    monkeypatch.setattr(
        harness.engines.conversation.read,
        "_node_get_raw",
        lambda **kwargs: step_rows,
    )
    monkeypatch.setattr(
        harness.engines.derived_knowledge_engine().read,
        "_node_get_raw",
        lambda **kwargs: derived_rows,
    )
    try:
        summary = harness.maintenance_summary()
    finally:
        monkeypatch.undo()

    assert summary["workflow_step_count"] == 2
    assert summary["maintenance_workflow_step_count"] == 2
    assert summary["maintenance_job_ids"] == ["job-1"]
    assert summary["maintenance_source_document_ids"] == ["doc-001"]
    assert summary["job_status_counts"]["DONE"] == 1
    assert summary["derived_artifact_count"] == 1


def _stub_longrun_invariant_dependencies(
    harness: LongRunHarness,
    *,
    maintenance_summary: dict[str, Any],
) -> pytest.MonkeyPatch:
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(harness, "_verify_document", lambda record: None)
    monkeypatch.setattr(harness, "_verify_runtime_events_have_run_ids", lambda: None)
    monkeypatch.setattr(harness, "_verify_derived_nodes_have_provenance", lambda: None)
    monkeypatch.setattr(
        harness,
        "promotion_provenance_summary",
        lambda: {"missing_count": 0, "missing_document_ids": []},
    )
    monkeypatch.setattr(harness, "maintenance_summary", lambda: maintenance_summary)
    monkeypatch.setattr(harness, "projection_summary", lambda: {"snapshot_status": "ok", "snapshot_error": None})
    monkeypatch.setattr(harness, "progress_summary", lambda: {"doc_count": len(harness.records)})
    return monkeypatch


def test_longrun_run_invariant_fails_without_maintenance_evidence(tmp_path: Path):
    harness = LongRunHarness(
        run_dir=tmp_path / "maintenance-invariant-fail",
        config=LongRunConfig(
            enabled=False,
            mode="fresh",
            doc_count=1,
            ollama_model="gemma4:e2b",
            ollama_base_url="http://localhost:11434",
            max_repeated_systemic_errors=3,
            max_post_doc_maintenance_steps=1,
        ),
    )
    record = DocumentRecord(
        doc_id="doc-001",
        title="Maintenance invariant",
        source_uri="file:///doc-001.md",
        input_path=tmp_path / "doc-001.md",
        current_path=tmp_path / "doc-001.md",
        status="COMPLETED",
        source_document_id="source-doc-1",
    )
    harness.records = [record]
    harness.contexts = {record.doc_id: record}
    monkeypatch = _stub_longrun_invariant_dependencies(
        harness,
        maintenance_summary={
            "derived_artifact_count": 0,
            "maintenance_workflow_step_count": 0,
            "job_status_counts": {},
            "foreground_replies": [],
            "maintenance_job_ids": [],
            "maintenance_source_document_ids": [],
            "jobs": [],
            "maintenance_lane_messages": [],
        },
    )
    try:
        with pytest.raises(AssertionError) as exc_info:
            harness._verify_run_invariants()
        message = str(exc_info.value)
        assert "background maintenance did not produce persisted evidence" in message
        assert '"operation_mode": "parse_first"' in message
        assert '"job_status_counts": {}' in message
        assert '"derived_artifact_count": 0' in message
        assert '"completed_document_ids": ["doc-001"]' in message
    finally:
        monkeypatch.undo()


def test_longrun_run_invariant_passes_with_maintenance_evidence(tmp_path: Path):
    harness = LongRunHarness(
        run_dir=tmp_path / "maintenance-invariant-pass",
        config=LongRunConfig(
            enabled=False,
            mode="fresh",
            doc_count=1,
            ollama_model="gemma4:e2b",
            ollama_base_url="http://localhost:11434",
            max_repeated_systemic_errors=3,
            max_post_doc_maintenance_steps=1,
        ),
    )
    record = DocumentRecord(
        doc_id="doc-001",
        title="Maintenance invariant",
        source_uri="file:///doc-001.md",
        input_path=tmp_path / "doc-001.md",
        current_path=tmp_path / "doc-001.md",
        status="COMPLETED",
        source_document_id="source-doc-1",
    )
    harness.records = [record]
    harness.contexts = {record.doc_id: record}
    monkeypatch = _stub_longrun_invariant_dependencies(
        harness,
        maintenance_summary={
            "derived_artifact_count": 0,
            "maintenance_workflow_step_count": 0,
            "job_status_counts": {"DONE": 1},
            "foreground_replies": [],
            "maintenance_job_ids": ["job-1"],
            "maintenance_source_document_ids": ["source-doc-1"],
            "jobs": [{"job_id": "job-1"}],
            "maintenance_lane_messages": [],
        },
    )
    try:
        harness._verify_run_invariants()
    finally:
        monkeypatch.undo()


def test_longrun_retry_invariant_scopes_validation_to_selected_batch(tmp_path: Path):
    harness = LongRunHarness(
        run_dir=tmp_path / "retry-invariant-scope",
        config=LongRunConfig(
            enabled=False,
            mode="retry_failed",
            doc_count=2,
            ollama_model="gemma4:e2b",
            ollama_base_url="http://localhost:11434",
            max_repeated_systemic_errors=3,
            max_post_doc_maintenance_steps=1,
        ),
    )
    selected = DocumentRecord(
        doc_id="doc-001",
        title="Recovered document",
        source_uri="file:///doc-001.md",
        input_path=tmp_path / "doc-001.md",
        current_path=tmp_path / "completed" / "doc-001.md",
        status="COMPLETED",
        recovery_selected=True,
    )
    untouched = DocumentRecord(
        doc_id="doc-002",
        title="Deferred document",
        source_uri="file:///doc-002.md",
        input_path=tmp_path / "doc-002.md",
        current_path=tmp_path / "input" / "doc-002.md",
        status="PENDING",
    )
    harness.records = [selected, untouched]
    harness.contexts = {record.doc_id: record for record in harness.records}
    monkeypatch = _stub_longrun_invariant_dependencies(
        harness,
        maintenance_summary={
            "derived_artifact_count": 0,
            "maintenance_workflow_step_count": 0,
            "job_status_counts": {"DONE": 1},
            "foreground_replies": [],
            "maintenance_job_ids": ["job-1"],
            "maintenance_source_document_ids": ["source-doc-1"],
            "jobs": [{"job_id": "job-1"}],
            "maintenance_lane_messages": [],
        },
    )
    try:
        harness._verify_run_invariants()
    finally:
        monkeypatch.undo()


@pytest.mark.parametrize("backend", ["postgres", "pgvector"])
def test_longrun_config_from_env_accepts_backend_selection(
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
):
    monkeypatch.setenv("KOGWISTAR_LLM_WIKI_LONGRUN", "1")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_MODE", "fresh")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_BACKEND", backend)
    monkeypatch.setenv(
        "KOGWISTAR_LONGRUN_DSN",
        "postgresql+psycopg://demo:demo@127.0.0.1:5432/demo",
    )
    config = LongRunConfig.from_env()
    assert config.backend == backend
    assert config.dsn == "postgresql+psycopg://demo:demo@127.0.0.1:5432/demo"


def test_longrun_fair_maintenance_slice_config_is_fingerprinted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KOGWISTAR_LLM_WIKI_LONGRUN", "1")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_ALLOW_SMALL", "1")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_DOC_COUNT", "1")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_MODE", "fresh")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_BACKEND", "chroma")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_OPERATION_MODE", "maintenance_first")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_MAINTENANCE_SCHEDULE", "fair_slices")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_MAINTENANCE_STEPS_PER_SLICE", "3")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_MAINTENANCE_LLM_CALLS_PER_SLICE", "1")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_MAINTENANCE_SECONDS_PER_SLICE", "12")

    fair = LongRunConfig.from_env()
    drain = LongRunConfig(
        enabled=False,
        mode="fresh",
        doc_count=1,
        operation_mode="maintenance_first",
    )

    assert fair.maintenance_schedule_mode == "fair_slices"
    assert fair.maintenance_steps_per_slice == 3
    assert fair.maintenance_llm_calls_per_slice == 1
    assert fair.maintenance_seconds_per_slice == 12
    assert fair.corpus_fingerprint != drain.corpus_fingerprint


def test_longrun_maintenance_workers_are_execution_config_not_fingerprint() -> None:
    base = dict(
        enabled=False,
        mode="fresh",
        backend="postgres",
        operation_mode="maintenance_first",
        maintenance_schedule_mode="fair_slices",
        maintenance_steps_per_slice=2,
        doc_count=2,
    )
    one = LongRunConfig(maintenance_workers=1, **base)
    two = LongRunConfig(maintenance_workers=2, **base)

    assert one.maintenance_workers == 1
    assert two.maintenance_workers == 2
    assert one.corpus_fingerprint == two.corpus_fingerprint
    assert two.as_dict()["maintenance_workers"] == 2


def test_longrun_maintenance_workers_require_multi_writer_backend() -> None:
    with pytest.raises(ValueError, match="maintenance_workers > 1"):
        LongRunConfig(
            enabled=False,
            mode="fresh",
            backend="chroma",
            doc_count=1,
            maintenance_workers=2,
        )


def test_maintenance_statistics_group_by_document_operation_and_failure() -> None:
    stats = build_maintenance_statistics(
        [
            {
                "event": "maintenance_poll_start",
                "worker_id": "maintenance-1",
            },
            {
                "event": "maintenance_runtime_attempt_start",
                "worker_id": "maintenance-1",
                "source_document_id": "doc-1",
                "maintenance_kind": "distill",
            },
            {
                "event": "maintenance_graph_effect",
                "worker_id": "maintenance-1",
                "source_document_id": "doc-1",
                "maintenance_kind": "distill",
                "source_node_count": 3,
                "derived_node_count": 1,
            },
            {
                "event": "maintenance_runtime_attempt_complete",
                "worker_id": "maintenance-1",
                "source_document_id": "doc-1",
                "maintenance_kind": "distill",
                "runtime_status": "suspended",
                "duration_ms": 1200,
            },
            {
                "event": "maintenance_runtime_attempt_failed",
                "worker_id": "maintenance-2",
                "source_document_id": "doc-2",
                "maintenance_kind": "crosslink",
                "error_type": "ValidationError",
            },
        ]
    )

    assert stats["documents"]["doc-1"]["average_duration_ms"] == 1200
    assert stats["document_count"] == 2
    assert stats["documents"]["doc-1"]["suspended_count"] == 1
    assert stats["documents"]["doc-1"]["derived_node_count"] == 1
    assert stats["documents"]["doc-1"]["operation_categories"]["distillation"] == 2
    assert stats["documents"]["doc-2"]["operation_categories"]["linking"] == 1
    assert stats["failure_hotspots"]["failed:ValidationError"] == 1


def test_maintenance_statistics_include_planner_parse_effects() -> None:
    stats = build_maintenance_statistics(
        [
            {
                "event": "maintenance_parse_complete",
                "source_document_id": "doc-parse",
                "maintenance_kind": "document_parse_graph",
                "duration_ms": 1234,
                "node_count": 4,
                "edge_count": 2,
                "llm_call_count": 3,
            },
        ]
    )

    document = stats["documents"]["doc-parse"]
    assert document["parse_count"] == 1
    assert document["parsed_node_count"] == 4
    assert document["parsed_edge_count"] == 2
    assert document["llm_call_count"] == 3
    assert document["operation_categories"]["breakdown"] == 1


@pytest.mark.parametrize("backend", ["postgres", "pgvector"])
def test_longrun_config_from_env_rejects_missing_postgres_dsn(
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
):
    if backend == "pgvector":
        monkeypatch.setenv("KOGWISTAR_LONGRUN_PG_SOURCE", "custom")
    monkeypatch.setenv("KOGWISTAR_LLM_WIKI_LONGRUN", "1")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_MODE", "fresh")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_BACKEND", backend)
    for env_name in (
        "KOGWISTAR_LONGRUN_DSN",
        "KOGWISTAR_LLM_WIKI_TEST_PG_DSN",
        "GKE_PG_DSN",
        "PG_DSN",
        "DATABASE_URL",
    ):
        monkeypatch.delenv(env_name, raising=False)
    with pytest.raises(ValueError, match="requires a DSN"):
        LongRunConfig.from_env()


def test_longrun_config_from_env_rejects_unsupported_backend(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KOGWISTAR_LLM_WIKI_LONGRUN", "1")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_MODE", "fresh")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_BACKEND", "memory")
    with pytest.raises(ValueError, match="must be one of"):
        LongRunConfig.from_env()


def test_longrun_config_from_env_accepts_parser_selection(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KOGWISTAR_LLM_WIKI_LONGRUN", "1")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_MODE", "fresh")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_DOC_COUNT", "1")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_ALLOW_SMALL", "1")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PARSER", "page_index")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_OPERATION_MODE", "maintenance_first")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PARSER_PROPOSAL_MODE", "boundaries")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PARSE_TIMEOUT_SECONDS", "7")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_MAX_RUNTIME_SECONDS", "42")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_MAX_LLM_CALLS", "9")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_DOC_LIMIT", "1")

    config = LongRunConfig.from_env()

    assert config.parser_lane == "page_index"
    assert config.operation_mode == "maintenance_first"
    assert config.parser_proposal_mode == "boundaries"
    assert config.parse_timeout_seconds == 7
    assert config.max_runtime_seconds == 42
    assert config.max_llm_calls == 9
    assert config.doc_limit == 1


def test_longrun_config_from_env_accepts_parser_provider_and_model(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KOGWISTAR_LLM_WIKI_LONGRUN", "1")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_MODE", "fresh")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_DOC_COUNT", "1")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_ALLOW_SMALL", "1")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PARSER_PROVIDER", "azure_openai")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PARSER_MODEL", "gpt4o")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PARSER_BASE_URL", "https://example.openai.azure.com/")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PARSER_API_KEY_ENV", "OPENAI_API_KEY_GPT4O")

    config = LongRunConfig.from_env()

    assert config.parser_provider == "azure"
    assert config.parser_model == "gpt4o"
    assert config.parser_base_url == "https://example.openai.azure.com/"
    assert config.parser_api_key_env == "OPENAI_API_KEY_GPT4O"


def test_longrun_config_from_env_uses_model_specific_azure_endpoint(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KOGWISTAR_LLM_WIKI_LONGRUN", "1")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_MODE", "fresh")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_DOC_COUNT", "1")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_ALLOW_SMALL", "1")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PARSER_PROVIDER", "azure_openai")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PARSER_MODEL", "gpt-5-mini")
    monkeypatch.delenv("KOGWISTAR_LONGRUN_PARSER_BASE_URL", raising=False)
    monkeypatch.delenv("KOGWISTAR_PARSER_BASE_URL", raising=False)
    monkeypatch.setenv(
        "OPENAI_DEPLOYMENT_ENDPOINT_GPT5_MINI",
        "https://gpt5-mini.example.openai.azure.com/",
    )
    monkeypatch.setenv("OPENAI_API_KEY_GPT5_MINI", "test-key")
    monkeypatch.setenv("OPENAI_DEPLOYMENT_VERSION_GPT5_MINI", "2025-01-01-preview")

    config = LongRunConfig.from_env()

    assert config.parser_provider == "azure"
    assert config.parser_model == "gpt-5-mini"
    assert config.parser_base_url == "https://gpt5-mini.example.openai.azure.com/"
    assert config.parser_api_key_env == "OPENAI_API_KEY_GPT5_MINI"
    assert config.parser_api_version == "2025-01-01-preview"
    assert config.parser_max_retries == 2


def test_longrun_config_from_env_defaults_to_workflow_layered(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KOGWISTAR_LLM_WIKI_LONGRUN", "1")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_MODE", "fresh")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_DOC_COUNT", "1")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_ALLOW_SMALL", "1")
    monkeypatch.delenv("KOGWISTAR_LONGRUN_PARSER", raising=False)

    config = LongRunConfig.from_env()

    assert config.parser_lane == "workflow_layered"


def test_longrun_config_from_env_supports_one_doc_no_resume_probe(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KOGWISTAR_LLM_WIKI_LONGRUN", "1")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_MODE", "fresh")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_DOC_COUNT", "1")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_ALLOW_SMALL", "1")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PARSER", "page_index")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_RESUME_PROBE", "0")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_DOC_PROFILE", "tiny")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_SKIP_MAINTENANCE_INVARIANT", "1")

    config = LongRunConfig.from_env()

    assert config.mode == "fresh"
    assert config.doc_count == 1
    assert config.parser_lane == "page_index"
    assert config.resume_probe_enabled is False
    assert config.doc_profile == "tiny"
    assert config.corpus_profile == "watershed_stress"
    assert (config.token_min, config.token_max) == _longrun_doc_profile_token_bounds("tiny")
    assert config.skip_maintenance_invariant is True
    assert config.corpus_fingerprint


def test_longrun_config_from_env_supports_daily_life_corpus_profile(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KOGWISTAR_LLM_WIKI_LONGRUN", "1")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_MODE", "fresh")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_DOC_COUNT", "20")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_CORPUS_PROFILE", "daily_life")

    config = LongRunConfig.from_env()

    assert config.corpus_profile == "daily_life"
    assert config.corpus_fingerprint


def test_longrun_config_from_env_supports_two_stage_conversation_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KOGWISTAR_LLM_WIKI_LONGRUN", "1")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_MODE", "fresh")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_DOC_COUNT", "1")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_ALLOW_SMALL", "1")
    monkeypatch.setenv(
        "KOGWISTAR_LONGRUN_CONVERSATION_PERSISTENCE_MODE", "two_stage"
    )

    config = LongRunConfig.from_env()

    assert config.conversation_persistence_mode == "two_stage"
    assert config.as_dict()["conversation_persistence_mode"] == "two_stage"
    assert config.corpus_fingerprint != LongRunConfig(
        enabled=False,
        mode="fresh",
        doc_count=1,
    ).corpus_fingerprint


def test_longrun_config_rejects_invalid_conversation_persistence_mode() -> None:
    with pytest.raises(ValueError, match="conversation_persistence_mode"):
        LongRunConfig(
            enabled=False,
            mode="fresh",
            doc_count=1,
            conversation_persistence_mode="invalid",
        )


def test_longrun_corpus_fingerprint_changes_across_operation_modes() -> None:
    base = dict(
        enabled=False,
        mode="auto",
        doc_count=20,
        parser_lane="workflow_layered",
        ollama_model="gemma4:e2b",
        ollama_base_url="http://localhost:11434",
        max_repeated_systemic_errors=3,
        max_post_doc_maintenance_steps=1,
    )
    parse_first = LongRunConfig(operation_mode="parse_first", **base)
    maintenance_first = LongRunConfig(operation_mode="maintenance_first", **base)
    hybrid = LongRunConfig(operation_mode="hybrid", **base)

    assert len({parse_first.corpus_fingerprint, maintenance_first.corpus_fingerprint, hybrid.corpus_fingerprint}) == 3


def test_longrun_corpus_fingerprint_changes_across_proposal_modes() -> None:
    base = dict(
        enabled=False,
        mode="auto",
        doc_count=20,
        operation_mode="parse_first",
        parser_lane="workflow_layered",
        ollama_model="gemma4:e2b",
        ollama_base_url="http://localhost:11434",
        max_repeated_systemic_errors=3,
        max_post_doc_maintenance_steps=1,
    )
    children = LongRunConfig(parser_proposal_mode="children", **base)
    boundaries = LongRunConfig(parser_proposal_mode="boundaries", **base)

    assert children.corpus_fingerprint != boundaries.corpus_fingerprint


def test_longrun_corpus_fingerprint_changes_across_corpus_profiles() -> None:
    base = dict(
        enabled=False,
        mode="auto",
        doc_count=20,
        operation_mode="parse_first",
        parser_lane="workflow_layered",
        ollama_model="gemma4:e2b",
        ollama_base_url="http://localhost:11434",
        max_repeated_systemic_errors=3,
        max_post_doc_maintenance_steps=1,
    )
    stress = LongRunConfig(corpus_profile="watershed_stress", **base)
    daily_life = LongRunConfig(corpus_profile="daily_life", **base)

    assert stress.corpus_fingerprint != daily_life.corpus_fingerprint


def test_longrun_corpus_fingerprint_ignores_run_mode_and_budgets() -> None:
    base = dict(
        enabled=False,
        doc_count=20,
        operation_mode="parse_first",
        parser_lane="workflow_layered",
        parser_provider="azure",
        parser_model="gpt-5-mini",
        max_repeated_systemic_errors=3,
        max_post_doc_maintenance_steps=1,
    )
    fresh = LongRunConfig(mode="fresh", max_llm_calls=100, max_runtime_seconds=1200, **base)
    continue_run = LongRunConfig(mode="continue", max_llm_calls=250, max_runtime_seconds=2400, **base)
    auto = LongRunConfig(mode="auto", max_llm_calls=500, max_runtime_seconds=3600, **base)

    assert fresh.corpus_fingerprint == continue_run.corpus_fingerprint == auto.corpus_fingerprint


def test_longrun_corpus_fingerprint_ignores_document_limit() -> None:
    base = dict(
        enabled=False,
        mode="auto",
        doc_count=20,
        operation_mode="parse_first",
        parser_lane="workflow_layered",
        parser_provider="azure",
        parser_model="gpt-5-mini",
        max_repeated_systemic_errors=3,
        max_post_doc_maintenance_steps=1,
    )
    limited = LongRunConfig(doc_limit=2, **base)
    complete = LongRunConfig(doc_limit=None, **base)

    assert limited.corpus_fingerprint == complete.corpus_fingerprint


def test_longrun_corpus_fingerprint_ignores_resume_probe() -> None:
    base = dict(
        enabled=False,
        mode="auto",
        doc_count=20,
        operation_mode="parse_first",
        parser_lane="workflow_layered",
        parser_provider="azure",
        parser_model="gpt-5-mini",
        max_repeated_systemic_errors=3,
        max_post_doc_maintenance_steps=1,
    )
    normal = LongRunConfig(resume_probe_enabled=False, **base)
    probe = LongRunConfig(resume_probe_enabled=True, **base)

    assert normal.corpus_fingerprint == probe.corpus_fingerprint


def test_longrun_accepts_historical_fingerprint_without_run_control_fields() -> None:
    config = LongRunConfig(
        enabled=False,
        mode="continue",
        doc_count=20,
        backend="pgvector",
        operation_mode="parse_first",
        pg_database_mode="fingerprint",
        corpus_profile="daily_life",
        parser_lane="workflow_layered",
        parser_provider="azure",
        parser_model="gpt-5-mini",
        parser_proposal_mode="boundaries",
    )

    assert config._historical_corpus_fingerprint() == "7478c448-16f3-5f53-aabb-519a2033c72e"


def test_longrun_parser_workers_are_bounded_but_do_not_change_fingerprint() -> None:
    base = dict(
        enabled=False,
        mode="fresh",
        doc_count=3,
        backend="pgvector",
        dsn="postgresql://user:pass@localhost/db",
    )
    one = LongRunConfig(parser_workers=1, **base)
    two = LongRunConfig(parser_workers=2, **base)

    assert one.parser_workers == 1
    assert two.parser_workers == 2
    assert one.corpus_fingerprint == two.corpus_fingerprint
    assert two.as_dict()["parser_workers"] == 2


def test_longrun_different_fingerprint_isolated_from_existing_run_dir(tmp_path: Path):
    base = tmp_path / "experiment"
    (base / "dump").mkdir(parents=True)
    (base / "dump" / "run_config.json").write_text(
        json.dumps({"corpus_fingerprint": "old-fingerprint"}),
        encoding="utf-8",
    )
    config = LongRunConfig(
        enabled=False,
        mode="fresh",
        doc_count=20,
        corpus_profile="daily_life",
        parser_model="gpt-5-mini",
        parser_provider="azure",
        parser_proposal_mode="boundaries",
        ollama_model="gpt-5-mini",
        ollama_base_url="http://localhost:11434",
    )

    harness = LongRunHarness(run_dir=base, config=config)

    assert harness.run_dir != base
    assert harness.run_dir == base / "experiments" / config.corpus_fingerprint
    assert json.loads((base / "dump" / "run_config.json").read_text(encoding="utf-8"))[
        "corpus_fingerprint"
    ] == "old-fingerprint"


def test_longrun_experiment_run_changes_fingerprint_for_repeat_comparison():
    base = dict(
        enabled=False,
        mode="fresh",
        doc_count=20,
        backend="pgvector",
        dsn="postgresql://user:pass@localhost/db",
        parser_provider="azure",
        parser_model="gpt-5-mini",
        parser_proposal_mode="boundaries",
    )
    run_one = LongRunConfig(experiment_run="1", **base)
    run_two = LongRunConfig(experiment_run="2", **base)

    assert run_one.corpus_fingerprint != run_two.corpus_fingerprint
    assert run_one.as_dict()["experiment_run"] == "1"


def test_longrun_parser_workers_reject_unsafe_storage_but_allow_resume_gate() -> None:
    with pytest.raises(ValueError, match="requires postgres/pgvector"):
        LongRunConfig(enabled=False, mode="fresh", doc_count=2, parser_workers=2)
    config = LongRunConfig(
        enabled=False,
        mode="fresh",
        doc_count=2,
        backend="pgvector",
        dsn="postgresql://user:pass@localhost/db",
        parser_workers=2,
        resume_probe_enabled=True,
    )
    assert config.parser_workers == 2
    assert config.resume_probe_enabled is True
    assert config.corpus_fingerprint == LongRunConfig(
        enabled=False,
        mode="fresh",
        doc_count=2,
        backend="pgvector",
        dsn="postgresql://user:pass@localhost/db",
        parser_workers=1,
        resume_probe_enabled=True,
    ).corpus_fingerprint


def test_longrun_parallel_parser_scheduler_bounds_document_workflows(tmp_path: Path) -> None:
    config = LongRunConfig(
        enabled=False,
        mode="fresh",
        doc_count=3,
        backend="pgvector",
        dsn="postgresql://user:pass@localhost/db",
        parser_workers=2,
    )
    harness = LongRunHarness(run_dir=tmp_path / "parallel", config=config)
    harness.records = [
        DocumentRecord(
            doc_id=f"doc-{index}",
            title=f"Document {index}",
            source_uri=f"doc-{index}.md",
            input_path=tmp_path / f"doc-{index}.md",
            current_path=tmp_path / f"doc-{index}.md",
        )
        for index in range(3)
    ]

    active = 0
    maximum_active = 0
    active_lock = threading.Lock()

    def fake_workflow(record: DocumentRecord) -> str:
        nonlocal active, maximum_active
        with active_lock:
            active += 1
            maximum_active = max(maximum_active, active)
        time.sleep(0.02)
        record.status = "COMPLETED"
        with active_lock:
            active -= 1
        return "succeeded"

    harness._run_document_workflow = fake_workflow  # type: ignore[method-assign]
    harness._poll_maintenance_once = lambda **_: None  # type: ignore[method-assign]
    harness._log_heartbeat = lambda **_: None  # type: ignore[method-assign]
    harness.dumper.dump = lambda **_: None  # type: ignore[method-assign]
    harness._finalize_run = lambda **_: None  # type: ignore[method-assign]

    harness._run_parallel_documents(started=time.monotonic())

    assert maximum_active == 2
    assert all(record.status == "COMPLETED" for record in harness.records)


def test_longrun_async_entrypoint_uses_same_terminal_scheduler(tmp_path: Path) -> None:
    config = LongRunConfig(enabled=False, mode="fresh", doc_count=1)
    harness = LongRunHarness(run_dir=tmp_path / "async", config=config)
    called = False

    def fake_run() -> None:
        nonlocal called
        called = True

    harness.run = fake_run  # type: ignore[method-assign]
    asyncio.run(harness.run_async())

    assert called is True


def test_longrun_parallel_parser_isolates_document_failure_and_keeps_maintenance_foreground(
    tmp_path: Path,
) -> None:
    config = LongRunConfig(
        enabled=False,
        mode="fresh",
        doc_count=3,
        backend="pgvector",
        dsn="postgresql://user:pass@localhost/db",
        parser_workers=2,
    )
    harness = LongRunHarness(run_dir=tmp_path / "parallel-failure", config=config)
    records = [
        DocumentRecord(
            doc_id=f"doc-{index}",
            title=f"Document {index}",
            source_uri=f"doc-{index}.md",
            input_path=tmp_path / f"doc-{index}.md",
            current_path=tmp_path / f"doc-{index}.md",
        )
        for index in range(3)
    ]
    for record in records:
        record.current_path.write_text(record.doc_id, encoding="utf-8")
    harness.records = records
    maintenance_polls: list[str] = []
    completed_calls: list[str] = []

    def fake_workflow(record: DocumentRecord) -> str:
        if record.doc_id == "doc-1":
            raise LongRunDocumentError(
                "document_parse_failed",
                "synthetic parser failure",
                phase="parse_document",
            )
        record.status = "COMPLETED"
        completed_calls.append(record.doc_id)
        return "succeeded"

    harness._run_document_workflow = fake_workflow  # type: ignore[method-assign]
    harness._poll_maintenance_once = lambda *, phase: maintenance_polls.append(phase)  # type: ignore[method-assign]
    harness._log_heartbeat = lambda **_: None  # type: ignore[method-assign]
    harness._safe_failure_dump = lambda **_: None  # type: ignore[method-assign]
    harness.dumper.dump = lambda **_: None  # type: ignore[method-assign]
    harness._finalize_run = lambda **_: None  # type: ignore[method-assign]

    harness._run_parallel_documents(started=time.monotonic())

    assert completed_calls == ["doc-0", "doc-2"] or completed_calls == ["doc-2", "doc-0"]
    assert records[1].status == "FAILED"
    assert {record.status for record in records if record.doc_id != "doc-1"} == {"COMPLETED"}
    assert len(maintenance_polls) == 2
    assert [failure.doc_id for failure in harness.failure_records] == ["doc-1"]


def test_longrun_finalization_trace_reports_slow_phases(tmp_path: Path) -> None:
    harness = LongRunHarness(
        run_dir=tmp_path / "finalization-trace",
        config=LongRunConfig(enabled=False, mode="fresh", doc_count=1),
    )
    events: list[dict[str, Any]] = []
    harness.live_trace_printer = SimpleNamespace(emit=events.append)
    harness._drain_maintenance_after_documents = lambda: None  # type: ignore[method-assign]
    harness._poll_projection_once = lambda: None  # type: ignore[method-assign]
    harness._verify_run_invariants = lambda: None  # type: ignore[method-assign]
    harness.dumper.dump = lambda **_: None  # type: ignore[method-assign]
    harness.recovery_summary = lambda: {}  # type: ignore[method-assign]
    harness.maintenance_summary = lambda: {}  # type: ignore[method-assign]
    harness.projection_summary = lambda: {}  # type: ignore[method-assign]
    harness._engines = SimpleNamespace(close=lambda: None)

    harness._finalize_run(started=time.monotonic())

    stages = [event["stage"] for event in events]
    assert stages.index("finalize_maintenance_drain_start") < stages.index(
        "finalize_maintenance_drain_complete"
    )
    assert stages.index("finalize_projection_start") < stages.index(
        "finalize_projection_complete"
    )
    assert stages.index("finalize_invariants_start") < stages.index(
        "finalize_invariants_complete"
    )
    assert stages.index("finalize_success_dump_start") < stages.index(
        "finalize_success_dump_complete"
    )
    assert stages.index("runtime_engines_close_start") < stages.index(
        "runtime_engines_closed"
    )
    timed_stages = {
        "finalize_maintenance_drain_complete",
        "finalize_projection_complete",
        "finalize_invariants_complete",
        "finalize_success_dump_complete",
        "runtime_engines_closed",
    }
    assert all(isinstance(event["details"]["duration_ms"], int) for event in events if event["stage"] in timed_stages)


def test_longrun_async_entrypoint_propagates_scheduler_failure(tmp_path: Path) -> None:
    harness = LongRunHarness(
        run_dir=tmp_path / "async-failure",
        config=LongRunConfig(enabled=False, mode="fresh", doc_count=1),
    )

    def failing_run() -> None:
        raise LongRunSystemicError("scheduler_failed", "synthetic scheduler failure", phase="scheduler")

    harness.run = failing_run  # type: ignore[method-assign]
    with pytest.raises(LongRunSystemicError, match="synthetic scheduler failure"):
        asyncio.run(harness.run_async())


def test_longrun_harness_reports_call_budget_and_corpus_fingerprint(tmp_path: Path):
    config = LongRunConfig(
        enabled=False,
        mode="fresh",
        doc_count=1,
        max_llm_calls=5,
        max_runtime_seconds=123,
        ollama_model="gemma4:e2b",
        ollama_base_url="http://localhost:11434",
        max_repeated_systemic_errors=3,
        max_post_doc_maintenance_steps=1,
    )
    harness = LongRunHarness(run_dir=tmp_path / "budget", config=config)
    harness.llm_call_count = 3

    llm_summary = harness.llm_summary()
    progress = harness.progress_summary()
    manifest_row = harness.manifest_row(
        DocumentRecord(
            doc_id="doc-001",
            title="Doc 1",
            source_uri="file:///doc-001.md",
            input_path=tmp_path / "budget" / "input" / "doc-001.md",
            current_path=tmp_path / "budget" / "input" / "doc-001.md",
        )
    )

    assert llm_summary["max_runtime_seconds"] == 123
    assert llm_summary["max_llm_calls"] == 5
    assert llm_summary["call_count"] == 3
    assert llm_summary["corpus_fingerprint"] == config.corpus_fingerprint
    assert progress["llm_call_count"] == 3
    assert progress["llm_call_budget"] == 5
    assert progress["corpus_fingerprint"] == config.corpus_fingerprint
    assert manifest_row["corpus_fingerprint"] == config.corpus_fingerprint


def test_longrun_request_uses_configured_operation_mode(tmp_path: Path):
    config = LongRunConfig(
        enabled=False,
        mode="fresh",
        doc_count=1,
        operation_mode="maintenance_first",
        ollama_model="gemma4:e2b",
        ollama_base_url="http://localhost:11434",
        max_repeated_systemic_errors=3,
        max_post_doc_maintenance_steps=1,
    )
    harness = LongRunHarness(run_dir=tmp_path / "operation-mode", config=config)
    harness.prepare()
    record = harness.records[0]

    request = harness._request_for(record)

    assert request.operation_mode == "maintenance_first"


def test_longrun_request_uses_configured_parser_provider_and_model(tmp_path: Path):
    config = LongRunConfig(
        enabled=False,
        mode="fresh",
        doc_count=1,
        parser_provider="azure",
        parser_model="gpt-5-mini",
        parser_base_url="https://example.openai.azure.com/",
        parser_api_key_env="OPENAI_API_KEY_GPT5_MINI",
        ollama_model="gemma4:e2b",
        ollama_base_url="http://localhost:11434",
        max_repeated_systemic_errors=3,
        max_post_doc_maintenance_steps=1,
    )
    harness = LongRunHarness(run_dir=tmp_path / "parser-provider", config=config)
    harness.prepare()
    record = harness.records[0]

    request = harness._request_for(record)

    assert request.parser_mode == "azure"
    assert request.llm_provider == "azure"
    assert request.llm_model == "gpt-5-mini"
    assert harness._provider_settings().parser.provider == "azure"
    assert harness._provider_settings().parser.model == "gpt-5-mini"


def test_longrun_provider_settings_preserve_proposal_mode(tmp_path: Path):
    config = LongRunConfig(
        enabled=False,
        mode="fresh",
        doc_count=1,
        parser_proposal_mode="boundaries",
        ollama_model="gemma4:e2b",
        ollama_base_url="http://localhost:11434",
        max_repeated_systemic_errors=3,
        max_post_doc_maintenance_steps=1,
    )
    harness = LongRunHarness(run_dir=tmp_path / "proposal-mode", config=config)

    settings = harness._provider_settings()

    assert settings.proposal_mode == "boundaries"
    assert harness.llm_summary()["proposal_mode"] == "boundaries"
    assert "total_cost" in harness.llm_summary()
    assert "cost_status" in harness.llm_summary()


def test_longrun_workflow_steps_are_operation_mode_specific(tmp_path: Path):
    parse_first = LongRunHarness(
        run_dir=tmp_path / "parse-first-steps",
        config=LongRunConfig(
            enabled=False,
            mode="fresh",
            doc_count=1,
            operation_mode="parse_first",
            max_repeated_systemic_errors=3,
            max_post_doc_maintenance_steps=1,
        ),
    )
    maintenance_first = LongRunHarness(
        run_dir=tmp_path / "maintenance-first-steps",
        config=LongRunConfig(
            enabled=False,
            mode="fresh",
            doc_count=1,
            operation_mode="maintenance_first",
            max_repeated_systemic_errors=3,
            max_post_doc_maintenance_steps=1,
        ),
    )

    assert "parse_document" in parse_first._workflow_steps()
    assert "persist_document" in parse_first._workflow_steps()
    assert "parse_document" not in maintenance_first._workflow_steps()
    assert "persist_document" not in maintenance_first._workflow_steps()
    assert "seed_source_map" in maintenance_first._workflow_steps()
    assert parse_first._workflow_id() != maintenance_first._workflow_id()


def test_longrun_maintenance_first_skips_parser_and_seeds_source_map(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    config = LongRunConfig(
        enabled=False,
        mode="fresh",
        doc_count=1,
        operation_mode="maintenance_first",
        skip_maintenance_invariant=True,
        max_repeated_systemic_errors=3,
        max_post_doc_maintenance_steps=1,
    )
    harness = LongRunHarness(run_dir=tmp_path / "maintenance-first-run", config=config)
    harness.prepare()
    monkeypatch.setattr(
        harness,
        "_run_parse_with_subprocess",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("maintenance_first must not parse")),
    )
    monkeypatch.setattr(harness, "_poll_maintenance_once", lambda **kwargs: None)
    monkeypatch.setattr(harness, "_drain_maintenance_after_documents", lambda: None)
    monkeypatch.setattr(harness, "_poll_projection_once", lambda: None)

    harness.run()

    record = harness.records[0]
    phases = [row["phase"] for row in harness.status_transitions if row["doc_id"] == record.doc_id]
    assert record.status == "COMPLETED"
    assert record.source_document_id
    assert record.maintenance_job_id
    assert "seed_source_map" in phases
    assert "parse_document" not in phases
    assert "persist_document" not in phases


def test_longrun_retry_failed_reopens_bounded_terminal_documents(tmp_path: Path):
    run_dir = tmp_path / "retry-failed"
    config = LongRunConfig(
        enabled=False,
        mode="retry_failed",
        doc_count=3,
        recovery_doc_limit=1,
        recovery_attempts_per_doc=1,
    )
    harness = LongRunHarness(run_dir=run_dir, config=config)
    harness._prepare_run_directory()
    records: list[DocumentRecord] = []
    for index in range(1, 4):
        doc_id = f"doc-{index:03d}"
        path = run_dir / "quarantine" / f"{doc_id}.md"
        path.write_text(f"payload {doc_id}", encoding="utf-8")
        records.append(
            DocumentRecord(
                doc_id=doc_id,
                title=doc_id,
                source_uri=f"doc://{doc_id}",
                input_path=run_dir / "input" / path.name,
                current_path=path,
                status="QUARANTINED",
            )
        )
    harness.records = records
    harness.contexts = {record.doc_id: record for record in records}
    harness.status_transitions.append(
        {"doc_id": "doc-002", "status": "PERSISTED", "phase": "persist_document"}
    )

    harness._prepare_failed_document_recovery()

    assert records[0].status == "PENDING"
    assert records[0].current_path == run_dir / "processing" / "doc-001.md"
    assert records[0].recovery_attempt_count == 0
    assert records[0].recovery_selected is True
    assert [record.status for record in records[1:]] == ["QUARANTINED", "QUARANTINED"]
    assert harness.recovery_skipped_document_ids == ["doc-002"]
    assert any(row["phase"] == "recovery_reopen" for row in harness.status_transitions)


def test_longrun_retry_failed_does_not_exceed_attempt_limit(tmp_path: Path):
    run_dir = tmp_path / "retry-failed-attempt-limit"
    config = LongRunConfig(
        enabled=False,
        mode="retry_failed",
        doc_count=1,
        recovery_attempts_per_doc=1,
    )
    harness = LongRunHarness(run_dir=run_dir, config=config)
    harness._prepare_run_directory()
    path = run_dir / "quarantine" / "doc-001.md"
    path.write_text("payload", encoding="utf-8")
    record = DocumentRecord(
        doc_id="doc-001",
        title="doc-001",
        source_uri="doc://doc-001",
        input_path=run_dir / "input" / path.name,
        current_path=path,
        status="QUARANTINED",
        recovery_attempt_count=1,
    )
    harness.records = [record]
    harness.contexts = {record.doc_id: record}

    harness._prepare_failed_document_recovery()

    assert record.status == "QUARANTINED"
    assert record.current_path == path
    assert "attempt limit" in (harness.early_stop_reason or "")


def test_longrun_retry_failed_falls_back_to_input_payload(tmp_path: Path):
    run_dir = tmp_path / "retry-failed-missing-payload"
    config = LongRunConfig(
        enabled=False,
        mode="retry_failed",
        doc_count=1,
        recovery_attempts_per_doc=1,
    )
    harness = LongRunHarness(run_dir=run_dir, config=config)
    harness._prepare_run_directory()
    input_path = run_dir / "input" / "doc-001.md"
    input_path.parent.mkdir(parents=True, exist_ok=True)
    input_path.write_text("input payload", encoding="utf-8")
    record = DocumentRecord(
        doc_id="doc-001",
        title="doc-001",
        source_uri="doc://doc-001",
        input_path=input_path,
        current_path=run_dir / "quarantine" / "doc-001.md",
        status="QUARANTINED",
    )
    harness.records = [record]
    harness.contexts = {record.doc_id: record}

    harness._prepare_failed_document_recovery()

    assert record.status == "PENDING"
    assert record.recovery_selected is True
    assert record.current_path == run_dir / "processing" / "doc-001.md"
    assert record.current_path.read_text(encoding="utf-8") == "input payload"
    assert harness.recovery_missing_payload_document_ids == []
    assert "no recoverable" not in (harness.early_stop_reason or "")


def test_longrun_retry_failed_finishes_interrupted_batch_before_selecting_more(
    tmp_path: Path,
):
    run_dir = tmp_path / "retry-failed-batch"
    config = LongRunConfig(
        enabled=False,
        mode="retry_failed",
        doc_count=2,
        recovery_doc_limit=1,
    )
    harness = LongRunHarness(run_dir=run_dir, config=config)
    harness._prepare_run_directory()
    terminal_path = run_dir / "quarantine" / "doc-002.md"
    terminal_path.write_text("payload", encoding="utf-8")
    interrupted = DocumentRecord(
        doc_id="doc-001",
        title="doc-001",
        source_uri="doc://doc-001",
        input_path=run_dir / "input" / "doc-001.md",
        current_path=run_dir / "processing" / "doc-001.md",
        status="PENDING",
        recovery_selected=True,
    )
    new_failure = DocumentRecord(
        doc_id="doc-002",
        title="doc-002",
        source_uri="doc://doc-002",
        input_path=run_dir / "input" / terminal_path.name,
        current_path=terminal_path,
        status="QUARANTINED",
    )
    harness.records = [interrupted, new_failure]
    harness.contexts = {record.doc_id: record for record in harness.records}

    harness._prepare_failed_document_recovery()

    assert interrupted.status == "PENDING"
    assert new_failure.status == "QUARANTINED"
    assert new_failure.recovery_selected is False


def test_longrun_restore_llm_usage_counts_parse_transitions_not_seeded_records(tmp_path: Path):
    config = LongRunConfig(
        enabled=False,
        mode="fresh",
        doc_count=1,
        operation_mode="maintenance_first",
        max_repeated_systemic_errors=3,
        max_post_doc_maintenance_steps=1,
    )
    harness = LongRunHarness(run_dir=tmp_path / "restore-llm-usage", config=config)
    harness.prepare()
    record = harness.records[0]
    harness._transition(record, "SOURCE_SEEDED", phase="seed_source_map")
    harness._transition(record, "MAINTENANCE_ENQUEUED", phase="enqueue_background_maintenance")
    harness.dumper.dump(reason="checkpoint_without_llm_summary")
    (harness.dumper.dump_dir / "llm_calls_summary.json").unlink()

    restored = LongRunHarness(run_dir=harness.run_dir, config=config)
    restored.records = list(harness.records)
    restored._restore_llm_usage_from_dump()

    assert restored.llm_call_count == 0


def test_longrun_run_emits_console_heartbeat(tmp_path: Path, caplog: pytest.LogCaptureFixture):
    config = LongRunConfig(
        enabled=False,
        mode="fresh",
        doc_count=1,
        max_llm_calls=5,
        max_runtime_seconds=120,
        ollama_model="gemma4:e2b",
        ollama_base_url="http://localhost:11434",
        max_repeated_systemic_errors=3,
        max_post_doc_maintenance_steps=1,
    )
    harness = LongRunHarness(run_dir=tmp_path / "heartbeat", config=config)
    harness.prepare()
    caplog.set_level(logging.INFO)
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(harness, "_run_document_workflow", lambda record: "succeeded")
    monkeypatch.setattr(harness, "_poll_maintenance_once", lambda **kwargs: None)
    monkeypatch.setattr(harness, "_drain_maintenance_after_documents", lambda: None)
    monkeypatch.setattr(harness, "_poll_projection_once", lambda: None)
    monkeypatch.setattr(harness, "_verify_run_invariants", lambda: None)

    try:
        harness.run()
    finally:
        monkeypatch.undo()

    assert "Longrun heartbeat phase=run_started" in caplog.text
    assert "Longrun heartbeat phase=checkpoint_doc-001" in caplog.text


def test_longrun_run_aborts_when_llm_call_budget_is_exceeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config = LongRunConfig(
        enabled=False,
        mode="fresh",
        doc_count=1,
        max_llm_calls=1,
        ollama_model="gemma4:e2b",
        ollama_base_url="http://localhost:11434",
        max_repeated_systemic_errors=3,
        max_post_doc_maintenance_steps=1,
    )
    harness = LongRunHarness(run_dir=tmp_path / "budget-abort", config=config)
    harness.prepare()
    harness.llm_call_count = 1
    workflow_called = False

    def _unexpected_workflow(record: DocumentRecord) -> str:
        nonlocal workflow_called
        workflow_called = True
        return "succeeded"

    monkeypatch.setattr(harness, "_run_document_workflow", _unexpected_workflow)
    monkeypatch.setattr(harness, "_poll_maintenance_once", lambda **kwargs: None)

    with pytest.raises(AssertionError, match="llm call budget exceeded"):
        harness.run()

    assert harness.aborted is True
    assert harness.abort_reason and "llm call budget exceeded" in harness.abort_reason
    assert workflow_called is False
    assert harness.records[0].status == "PENDING"
    assert harness.records[0].current_path.parent == harness.run_dir / "input"


def test_longrun_doc_limit_stops_cleanly_without_full_corpus_completion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config = LongRunConfig(
        enabled=False,
        mode="fresh",
        doc_count=20,
        doc_limit=2,
        max_llm_calls=100,
        max_runtime_seconds=120,
        ollama_model="gemma4:e2b",
        ollama_base_url="http://localhost:11434",
        max_repeated_systemic_errors=3,
        max_post_doc_maintenance_steps=1,
    )
    harness = LongRunHarness(run_dir=tmp_path / "document-limit", config=config)
    harness.prepare()
    called: list[str] = []

    def _workflow(record: DocumentRecord) -> str:
        called.append(record.doc_id)
        return "succeeded"

    monkeypatch.setattr(harness, "_run_document_workflow", _workflow)
    monkeypatch.setattr(harness, "_poll_maintenance_once", lambda **kwargs: None)
    monkeypatch.setattr(harness, "_drain_maintenance_after_documents", lambda: None)
    monkeypatch.setattr(harness, "_poll_projection_once", lambda: None)
    monkeypatch.setattr(harness, "_verify_run_invariants", lambda: None)

    harness.run()

    assert called == ["doc-001", "doc-002"]
    assert (harness.dumper.dump_dir / "interim_report.md").exists()
    assert (harness.dumper.dump_dir / "final_report.md").exists()
    terminal = json.loads((harness.dumper.dump_dir / "run_terminal.json").read_text(encoding="utf-8"))
    assert terminal["status"] == "incomplete"
    assert harness.aborted is False
    assert harness.early_stop_reason == "document limit reached (2/20)"
    assert (harness.dumper.dump_dir / "final_report.md").exists()


def test_longrun_run_aborts_when_runtime_budget_is_exceeded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config = LongRunConfig(
        enabled=False,
        mode="fresh",
        doc_count=1,
        max_runtime_seconds=1,
        max_llm_calls=10,
        ollama_model="gemma4:e2b",
        ollama_base_url="http://localhost:11434",
        max_repeated_systemic_errors=3,
        max_post_doc_maintenance_steps=1,
    )
    harness = LongRunHarness(run_dir=tmp_path / "runtime-abort", config=config)
    harness.prepare()
    monkeypatch.setattr(
        harness,
        "_run_document_workflow",
        lambda record: (time.sleep(1.2) or "succeeded"),
    )
    monkeypatch.setattr(harness, "_poll_maintenance_once", lambda **kwargs: None)

    with pytest.raises(AssertionError, match="max runtime exceeded"):
        harness.run()

    assert harness.aborted is True
    assert harness.abort_reason and "max runtime exceeded" in harness.abort_reason
    assert harness.records[0].status == "PENDING"
    assert harness.records[0].current_path.parent == harness.run_dir / "input"


def test_longrun_config_from_env_auto_enables_pgvector_testcontainer_probe(monkeypatch: pytest.MonkeyPatch):
    for env_name in (
        "KOGWISTAR_LONGRUN_DSN",
        "KOGWISTAR_LLM_WIKI_TEST_PG_DSN",
        "GKE_PG_DSN",
        "PG_DSN",
        "DATABASE_URL",
    ):
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.delenv("KOGWISTAR_LLM_WIKI_LONGRUN", raising=False)
    monkeypatch.setenv("KOGWISTAR_LONGRUN_MODE", "fresh")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_DOC_COUNT", "1")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_ALLOW_SMALL", "1")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_BACKEND", "pgvector")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PG_SOURCE", "testcontainer")

    config = LongRunConfig.from_env()

    assert config.enabled is True
    assert config.backend == "pgvector"
    assert config.dsn is None


def test_longrun_config_from_env_auto_enables_pgvector_persistent_probe(monkeypatch: pytest.MonkeyPatch):
    for env_name in (
        "KOGWISTAR_LONGRUN_DSN",
        "KOGWISTAR_LLM_WIKI_TEST_PG_DSN",
        "GKE_PG_DSN",
        "PG_DSN",
        "DATABASE_URL",
    ):
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.delenv("KOGWISTAR_LLM_WIKI_LONGRUN", raising=False)
    monkeypatch.setenv("KOGWISTAR_LONGRUN_MODE", "fresh")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_DOC_COUNT", "1")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_ALLOW_SMALL", "1")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_BACKEND", "pgvector")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PG_SOURCE", "persistent")

    config = LongRunConfig.from_env()

    assert config.enabled is True
    assert config.backend == "pgvector"
    assert config.dsn is None


def test_longrun_config_from_env_rejects_pgvector_custom_without_dsn(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("KOGWISTAR_LLM_WIKI_LONGRUN", raising=False)
    monkeypatch.setenv("KOGWISTAR_LONGRUN_MODE", "fresh")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_DOC_COUNT", "1")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_ALLOW_SMALL", "1")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_BACKEND", "pgvector")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PG_SOURCE", "custom")
    for env_name in (
        "KOGWISTAR_LONGRUN_DSN",
        "KOGWISTAR_LLM_WIKI_TEST_PG_DSN",
        "GKE_PG_DSN",
        "PG_DSN",
        "DATABASE_URL",
    ):
        monkeypatch.delenv(env_name, raising=False)

    with pytest.raises(ValueError, match="requires a DSN"):
        LongRunConfig.from_env()


def test_longrun_config_from_env_rejects_legacy_parser_lane(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("KOGWISTAR_LLM_WIKI_LONGRUN", "1")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_MODE", "fresh")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_DOC_COUNT", "1")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_ALLOW_SMALL", "1")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PARSER", "legacy_tree")

    with pytest.raises(ValueError, match="KOGWISTAR_LONGRUN_PARSER"):
        LongRunConfig.from_env()


def test_longrun_config_from_env_rejects_non_positive_parse_timeout(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("KOGWISTAR_LLM_WIKI_LONGRUN", "1")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_MODE", "fresh")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_DOC_COUNT", "1")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_ALLOW_SMALL", "1")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PARSE_TIMEOUT_SECONDS", "0")

    with pytest.raises(ValueError, match="must be positive"):
        LongRunConfig.from_env()


def test_longrun_runtime_event_sink_writes_jsonl(tmp_path: Path):
    event_path = tmp_path / "dump" / "runtime_events.jsonl"
    sink = LongRunJsonlTraceSink(jsonl_path=event_path)

    sink.emit(
        {
            "event_id": "evt-test",
            "type": "workflow_run_started",
            "run_id": "run-test",
            "step_seq": 0,
        }
    )

    rows = event_path.read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1
    row = json.loads(rows[0])
    assert row["type"] == "workflow_run_started"
    assert row["run_id"] == "run-test"
    assert isinstance(row["observed_at_ms"], int)


def test_longrun_runtime_event_sink_live_trace_mirrors_to_console(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    event_path = tmp_path / "dump" / "runtime_events.jsonl"
    sink = LongRunJsonlTraceSink(jsonl_path=event_path, live_trace=True)

    sink.emit(
        {
            "event_id": "evt-test",
            "type": "workflow_step_started",
            "run_id": "run-test",
            "step": "parse_document",
        }
    )

    captured = capsys.readouterr()
    assert "[longrun.runtime] workflow_step_started" in captured.err
    assert "run_id=run-test" in captured.err
    assert "step=parse_document" in captured.err
    assert event_path.exists()


def test_longrun_runtime_event_sink_enriches_step_name(tmp_path: Path):
    event_path = tmp_path / "dump" / "runtime_events.jsonl"
    sink = LongRunJsonlTraceSink(
        jsonl_path=event_path,
        enrich_event=lambda event: {**event, "step_name": "parse_document"},
    )

    sink.emit(
        {
            "event_id": "evt-test",
            "type": "step_attempt_completed",
            "run_id": "run-test",
            "node_id": "wf-node-1",
            "step_seq": 2,
        }
    )

    row = json.loads(event_path.read_text(encoding="utf-8").splitlines()[0])
    assert row["node_id"] == "wf-node-1"
    assert row["step_name"] == "parse_document"
    assert row["step_seq"] == 2


def test_longrun_harness_rebuilds_pipeline_with_live_trace_enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    captured: dict[str, Any] = {}
    module = sys.modules[__name__]

    class FakePipeline:
        def __init__(self, engines: Any, *, live_trace: bool | None = None) -> None:
            captured["live_trace"] = live_trace
            self.parser = None

    class FakeWorker:
        def __init__(self, engines: Any, **kwargs: Any) -> None:
            self.engines = engines
            if kwargs:
                captured["worker_kwargs"] = kwargs

    class FakeDumper:
        def __init__(self, run_dir: Path, harness: Any) -> None:
            self.dump_dir = run_dir / "dump"
            self.dump_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(module, "IngestPipeline", FakePipeline)
    monkeypatch.setattr(module, "MaintenanceWorker", FakeWorker)
    monkeypatch.setattr(module, "ProjectionWorker", FakeWorker)
    monkeypatch.setattr(module, "DiagnosticDumper", FakeDumper)
    monkeypatch.setattr(
        LongRunHarness,
        "_build_namespace_engines",
        lambda self: SimpleNamespace(conversation=None, workflow=None, kg=None, wisdom=None, derived_knowledge=None),
    )

    config = LongRunConfig(
        enabled=True,
        mode="fresh",
        doc_count=1,
        operation_mode="parse_first",
        pg_database_mode="fingerprint",
        parser_provider="ollama",
        parser_model="gemma4:e2b",
        parser_proposal_mode="children",
        parser_temperature=0.1,
        parser_base_url="http://localhost:11434",
        parser_api_key_env=None,
        parser_api_version=None,
        parser_max_retries=1,
        ollama_model="gemma4:e2b",
        ollama_base_url="http://localhost:11434",
        max_repeated_systemic_errors=3,
        max_post_doc_maintenance_steps=1,
        max_llm_calls=1,
        backend="chroma",
        parser_lane="workflow_layered",
        parse_timeout_seconds=1,
        max_runtime_seconds=1,
        dsn=None,
        resume_probe_enabled=False,
        max_idle_loops=1,
        token_min=1,
        token_max=2,
        workspace_id="demo",
        checkpoint_run_dir=str(tmp_path / "run"),
        doc_profile="small",
        skip_maintenance_invariant=False,
        live_trace=True,
        corpus_fingerprint="fingerprint",
    )

    harness = LongRunHarness(run_dir=tmp_path / "run", config=config)
    harness._rebuild_runtime_objects()

    assert captured["live_trace"] is True
    assert captured["worker_kwargs"]["fair_scheduling"] is False
    assert captured["worker_kwargs"]["worker_id"] == "maintenance-1"
    assert callable(captured["worker_kwargs"]["trace_sink"])


def test_longrun_harness_enrich_runtime_event_adds_step_name(tmp_path: Path):
    config = LongRunConfig(
        enabled=False,
        mode="fresh",
        doc_count=1,
        operation_mode="parse_first",
        pg_database_mode="fingerprint",
        parser_provider="ollama",
        parser_model="gemma4:e2b",
        parser_proposal_mode="children",
        parser_temperature=0.1,
        parser_base_url="http://localhost:11434",
        parser_api_key_env=None,
        parser_api_version=None,
        parser_max_retries=1,
        ollama_model="gemma4:e2b",
        ollama_base_url="http://localhost:11434",
        max_repeated_systemic_errors=3,
        max_post_doc_maintenance_steps=1,
        max_llm_calls=1,
        backend="chroma",
        parser_lane="page_index",
        parse_timeout_seconds=30,
        max_runtime_seconds=30,
        dsn=None,
        resume_probe_enabled=False,
        max_idle_loops=1,
        token_min=1,
        token_max=2,
        workspace_id="demo",
        checkpoint_run_dir=str(tmp_path / "run"),
        doc_profile="small",
        skip_maintenance_invariant=False,
        live_trace=False,
        corpus_fingerprint="fingerprint",
    )
    harness = LongRunHarness(run_dir=tmp_path / "run", config=config)

    parse_node_id = str(stable_id("wf_node", harness._workflow_id(), "parse_document"))
    enriched = harness._enrich_runtime_event(
        {
            "event_id": "evt-test",
            "type": "step_attempt_completed",
            "run_id": "run-test",
            "node_id": parse_node_id,
            "step_seq": 2,
        }
    )

    assert enriched["step_name"] == "parse_document"


def _fake_provider_settings_payload() -> dict[str, Any]:
    return WorkflowProviderSettings(
        parser=ProviderEndpointConfig(provider="fake", model="fake-parser"),
        embedding=EmbeddingProviderConfig(provider="fake", model="fake-embed", dimension=2),
    ).model_dump(field_mode="backend", dump_format="json")


def _parser_child_payload(tmp_path: Path, *, parser_lane: str) -> dict[str, Any]:
    run_dir = tmp_path / f"parser-{parser_lane}"
    return {
        "parser_lane": parser_lane,
        "conversation_persistence_mode": "single_stage",
        "parser_provider": "fake",
        "parser_model": "fake-parser",
        "parser_workflow_run_id": "parser:source-doc-001",
        "doc_id": "doc-001",
        "source_document_id": "source-doc-001",
        "title": "Parser Dispatch",
        "raw_text": "# Parser Dispatch\n\nAlpha clause.\n\n## Finding\n\nBeta clause.",
        "source_format": "markdown",
        "parser_mode": "heuristic",
        "provider_settings": _fake_provider_settings_payload(),
        "parser_run_dir": str(run_dir),
        "result_path": str(run_dir / "result.json"),
        "failure_path": str(run_dir / "failure.json"),
        "heartbeat_path": str(run_dir / "heartbeat.json"),
        "trace_path": str(run_dir / "trace.log"),
        "dump_trace_path": str(run_dir / "dump-trace.log"),
        "live_trace": False,
    }


def _basic_sense_graph_payload(*, repeated_excerpt: bool = False) -> dict[str, Any]:
    excerpt_a = "Alpha heading"
    excerpt_b = excerpt_a if repeated_excerpt else "Beta section"
    return {
        "nodes": [
            {
                "id": "n-1",
                "metadata": {"semantic_node_type": "SECTION", "level_from_root": 0},
                "mentions": [
                    {
                        "spans": [
                            {
                                "excerpt": excerpt_a,
                                "source_cluster_id": "cluster-1",
                                "start_char": 0,
                                "end_char": 40,
                            }
                        ]
                    }
                ],
            },
            {
                "id": "n-2",
                "metadata": {"semantic_node_type": "SUBSECTION", "level_from_root": 1},
                "mentions": [
                    {
                        "spans": [
                            {
                                "excerpt": excerpt_b,
                                "source_cluster_id": "cluster-1",
                                "start_char": 40,
                                "end_char": 80,
                            }
                        ]
                    }
                ],
            },
            {
                "id": "n-3",
                "metadata": {"semantic_node_type": "PARAGRAPH", "level_from_root": 2},
                "mentions": [
                    {
                        "spans": [
                            {
                                "excerpt": "Closing detail",
                                "source_cluster_id": "cluster-1",
                                "start_char": 80,
                                "end_char": 100,
                            }
                        ]
                    }
                ],
            },
        ],
        "edges": [],
    }


def test_longrun_basic_sense_eval_scores_structurally_healthy_tree_higher_than_repetitive_tree():
    healthy_eval = _basic_sense_eval_from_graph_payload(
        graph_payload=_basic_sense_graph_payload(repeated_excerpt=False),
        diagnostics={
            "parser_lane": "page_index",
            "page_index": {"assignment_mode": "heuristic_deterministic"},
        },
    )
    weak_eval = _basic_sense_eval_from_graph_payload(
        graph_payload={
            "nodes": [
                {
                    "id": "n-weak",
                    "metadata": {"semantic_node_type": "SECTION", "level_from_root": 0},
                    "mentions": [],
                }
            ],
            "edges": [],
        },
        diagnostics={
            "parser_lane": "page_index",
            "page_index": {
                "assignment_mode": "deterministic_fallback",
                "fallback_reason": "validation_failed",
            },
        },
    )

    assert healthy_eval["basic_sense_score"] > weak_eval["basic_sense_score"]
    assert healthy_eval["basic_sense_verdict"] in {"good", "mixed"}
    assert weak_eval["basic_sense_verdict"] == "weak"
    assert healthy_eval["fallback_used"] is False
    assert weak_eval["fallback_used"] is True


def test_longrun_final_report_renders_parser_eval_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    config = LongRunConfig(
        enabled=False,
        mode="fresh",
        doc_count=1,
        ollama_model="gemma4:e2b",
        ollama_base_url="http://localhost:11434",
        max_repeated_systemic_errors=3,
        max_post_doc_maintenance_steps=1,
        parser_lane="page_index",
        resume_probe_enabled=False,
    )
    harness = LongRunHarness(run_dir=tmp_path / "parser-eval-report", config=config)
    harness.prepare()
    record = harness.records[0]
    harness._transition(record, "COMPLETED", phase="probe_done")
    record.parse_result = SimpleNamespace(
        semantic_tree=SimpleNamespace(title=record.title),
        evaluation=_basic_sense_eval_from_graph_payload(
            graph_payload=_basic_sense_graph_payload(repeated_excerpt=False),
            diagnostics={
                "parser_lane": "page_index",
                "page_index": {"assignment_mode": "heuristic_deterministic"},
            },
        ),
    )
    monkeypatch.setattr(
        harness,
        "maintenance_summary",
        lambda: {
            "maintenance_poll_count": 0,
            "job_status_counts": {},
            "jobs": [],
            "maintenance_job_ids": [],
            "maintenance_source_document_ids": [],
            "maintenance_lane_messages": [],
            "foreground_replies": [],
            "workflow_step_count": 0,
            "maintenance_workflow_step_count": 0,
            "maintenance_workflow_step_ids": [],
            "derived_artifact_count": 0,
            "derived_artifact_ids": [],
        },
    )
    monkeypatch.setattr(
        harness,
        "projection_summary",
        lambda: {
            "projection_poll_count": 0,
            "snapshot_status": "ok",
            "snapshot_error": None,
            "entity_count": 0,
            "entity_ids": [],
            "manifest": None,
        },
    )
    monkeypatch.setattr(
        harness,
        "promotion_provenance_summary",
        lambda: {
            "promoted_count": 0,
            "verified_count": 0,
            "missing_count": 0,
            "verified": [],
            "missing": [],
            "missing_document_ids": [],
        },
    )
    monkeypatch.setattr(harness, "graph_export", lambda: {})
    monkeypatch.setattr(
        harness,
        "recovery_summary",
        lambda: {
            "status": "ok",
            "documents": [],
        },
    )
    monkeypatch.setattr(
        harness,
        "llm_summary",
        lambda: {
            "provider": "ollama",
            "model": config.ollama_model,
            "base_url": config.ollama_base_url,
            "parser_mode": "ollama",
            "parser_lane": config.parser_lane,
            "parse_timeout_seconds": config.parse_timeout_seconds,
            "sampled_prompts_available": False,
            "quality_failures": [],
        },
    )

    harness.dumper.dump(reason="probe", final=True)

    progress = json.loads((harness.dumper.dump_dir / "progress_summary.json").read_text(encoding="utf-8"))
    assert progress["parser_eval"]["evaluated_count"] == 1
    assert progress["parser_eval"]["composite_verdict"] in {"good", "mixed"}
    assert progress["parser_eval"]["documents"][0]["basic_sense_verdict"] in {"good", "mixed"}

    report = (harness.dumper.dump_dir / "final_report.md").read_text(encoding="utf-8")
    assert "## Parser Evaluation" in report
    assert "Composite verdict" in report
    assert "Average basic sense score" in report
    assert "Slowest parser stage" in report


def test_longrun_page_index_parser_lane_writes_graph_payload(tmp_path: Path):
    payload = _parser_child_payload(tmp_path, parser_lane="page_index")

    run_longrun_parser_child(payload)

    result = json.loads(Path(payload["result_path"]).read_text(encoding="utf-8"))
    assert result["ok"] is True
    assert result["parser_workflow_run_id"] == "parser:source-doc-001"
    assert result["parser_lane"] == "page_index"
    assert result["diagnostics"]["parser_lane"] == "page_index"
    assert result["diagnostics"]["page_index"]["assignment_mode"] in {
        "heuristic_deterministic",
        "llm_flat_assignment",
        "llm_flat_assignment_retry",
        "llm_flat_assignment_structure_retry",
        "deterministic_fallback",
    }
    assert set(result["evaluation"]) >= {
        "basic_sense_score",
        "basic_sense_verdict",
        "coverage_ratio",
        "max_depth",
        "node_count",
        "node_type_diversity",
        "duplicate_excerpt_hits",
        "fallback_used",
    }
    assert {
        "assignment_attempt_count",
        "assignment_retry_used",
        "assignment_retry_succeeded",
        "structure_retry_used",
        "structure_retry_succeeded",
        "retry_used",
        "retry_succeeded",
        "assignment_mode",
        "final_outcome",
    } <= set(result["evaluation"])
    assert result["graph_payload"]["nodes"]
    assert result["layer_log"] is None


def test_longrun_workflow_layered_parser_lane_uses_workflow_mode(tmp_path: Path):
    payload = _parser_child_payload(tmp_path, parser_lane="workflow_layered")

    run_longrun_parser_child(payload)

    result = json.loads(Path(payload["result_path"]).read_text(encoding="utf-8"))
    assert result["ok"] is True
    assert result["parser_workflow_run_id"] == "parser:source-doc-001"
    assert result["parser_lane"] == "workflow_layered"
    assert result["diagnostics"]["parse_session_mode"] == "workflow_layered"
    assert result["usage_summary"]["provider"] == "fake"
    assert result["usage_summary"]["event_count"] >= 0
    layer_log_path = Path(payload["parser_run_dir"]) / "parser_layer_log.json"
    assert layer_log_path.exists()
    assert json.loads(layer_log_path.read_text(encoding="utf-8")) == result["layer_log"]
    assert set(result["evaluation"]) >= {
        "basic_sense_score",
        "basic_sense_verdict",
        "coverage_ratio",
        "max_depth",
        "node_count",
        "node_type_diversity",
        "duplicate_excerpt_hits",
        "fallback_used",
    }
    assert result["graph_payload"]["nodes"]


def test_longrun_workflow_child_marks_explicit_workflow_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _parser_child_payload(tmp_path, parser_lane="workflow_layered")
    failed_result = SimpleNamespace(
        graph_payload={"nodes": [], "edges": []},
        evaluation={},
        usage_summary={"llm_call_count": 1},
        diagnostics={"workflow_status": "failure"},
        layer_log=[],
        usage_events=[],
        semantic_tree=SimpleNamespace(title="Failed workflow"),
    )
    monkeypatch.setattr(
        "kogwistar_llm_wiki.longrun_parser_worker.run_workflow_layered_parse",
        lambda **kwargs: failed_result,
    )

    run_longrun_parser_child(payload)

    result = json.loads(Path(payload["result_path"]).read_text(encoding="utf-8"))
    assert result["ok"] is False
    assert result["workflow_status"] == "failure"
    assert result["parser_workflow_run_id"] == "parser:source-doc-001"


def test_longrun_parent_preserves_child_usage_events(tmp_path: Path):
    harness = LongRunHarness(
        run_dir=tmp_path / "usage-payload",
        config=LongRunConfig(enabled=False, mode="fresh", doc_count=1),
    )
    event = {
        "event_id": "provider-event-1",
        "run_id": "parser:source-doc-1",
        "source": "langchain-provider",
        "kind": "token",
        "amount": 12,
        "unit": "input_tokens",
        "scope": "run",
        "ts_ms": 1,
        "meta": {"provider_run_id": "provider-run-1"},
    }

    result = harness._parse_result_from_payload({"usage_events": [event]})

    assert result.usage_events == [event]


def test_longrun_parent_persists_per_document_usage_attribution(tmp_path: Path):
    harness = LongRunHarness(
        run_dir=tmp_path / "usage-persistence",
        config=LongRunConfig(enabled=False, mode="fresh", doc_count=1),
    )
    record = DocumentRecord(
        doc_id="doc-001",
        title="Usage document",
        source_uri="file:///doc-001.md",
        input_path=tmp_path / "doc-001.md",
        current_path=tmp_path / "doc-001.md",
    )
    record.current_path.write_text("usage test document", encoding="utf-8")
    request = harness._request_for(record)
    event = {
        "event_id": "provider-event-1",
        "run_id": "parser:source-doc-1",
        "source": "langchain-provider",
        "kind": "token",
        "amount": 12,
        "unit": "input_tokens",
        "scope": "run",
        "ts_ms": 1,
        "meta": {"provider_run_id": "provider-run-1"},
    }

    harness.pipeline._persist_parser_usage_events(  # noqa: SLF001 - regression of parent handoff
        request=request,
        source_document_id="source-doc-1",
        provider="fake",
        model="fake-model",
        attempt_id="longrun:doc-001",
        usage_events=[event],
    )

    namespace = harness.pipeline.namespaces_for(request.workspace_id).usage_events
    rows = list(
        harness.engines.conversation.meta_sqlite.iter_entity_events(
            namespace=namespace,
            from_seq=1,
        )
    )
    assert len(rows) == 1
    payload = json.loads(rows[0][4])
    assert payload["attribution"]["source_document_id"] == "source-doc-1"
    assert payload["attribution"]["operation_kind"] == "parser"
    assert payload["attribution"]["provider"] == "fake"


def test_longrun_parser_child_failure_writes_failure_json_and_mirrors_trace(tmp_path: Path):
    payload = _parser_child_payload(tmp_path, parser_lane="page_index")
    payload["parser_lane"] = "unsupported_lane"

    with pytest.raises(ValueError, match="unsupported long-run parser lane"):
        run_longrun_parser_child(payload)

    failure_path = Path(str(payload["failure_path"]))
    failure_payload_path = Path(
        str(payload.get("failure_payload_path") or failure_path.with_name("failure_payload.json"))
    )
    dump_trace_path = Path(str(payload["dump_trace_path"]))

    assert failure_path.exists()
    failure = json.loads(failure_path.read_text(encoding="utf-8"))
    assert failure["ok"] is False
    assert failure["error_type"]
    assert failure["payload_path"] == str(failure_payload_path)
    assert failure_payload_path.exists()
    failure_payload = json.loads(failure_payload_path.read_text(encoding="utf-8"))
    assert failure_payload["doc_id"] == payload["doc_id"]
    assert failure_payload["parser_lane"] == "unsupported_lane"
    assert failure_payload["raw_text"] == payload["raw_text"]
    assert dump_trace_path.exists()
    trace_text = dump_trace_path.read_text(encoding="utf-8")
    assert "child::child_loading_provider_settings" in trace_text
    assert "child::child_exception" in trace_text


def test_longrun_parser_child_live_trace_emits_semantic_child_progress(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    payload = _parser_child_payload(tmp_path, parser_lane="page_index")
    payload["parser_lane"] = "unsupported_lane"
    payload["live_trace"] = True

    with pytest.raises(ValueError, match="unsupported long-run parser lane"):
        run_longrun_parser_child(payload)

    captured = capsys.readouterr()
    assert "[longrun.parser] parser_trace" in captured.err
    assert "message=child::child_loading_provider_settings" in captured.err
    assert "message=child::child_exception ValueError" in captured.err
    assert "provider=fake" in captured.err
    assert "model=fake-parser" in captured.err
    assert "parser_workflow_run_id=parser:source-doc-001" in captured.err


def test_longrun_parser_child_failure_message_includes_exitcode_and_paths(tmp_path: Path):
    config = LongRunConfig(
        enabled=False,
        mode="fresh",
        doc_count=1,
        operation_mode="parse_first",
        pg_database_mode="fingerprint",
        parser_provider="ollama",
        parser_model="gemma4:e2b",
        parser_proposal_mode="children",
        parser_temperature=0.1,
        parser_base_url="http://localhost:11434",
        parser_api_key_env=None,
        parser_api_version=None,
        parser_max_retries=1,
        ollama_model="gemma4:e2b",
        ollama_base_url="http://localhost:11434",
        max_repeated_systemic_errors=3,
        max_post_doc_maintenance_steps=1,
        max_llm_calls=1,
        backend="chroma",
        parser_lane="page_index",
        parse_timeout_seconds=30,
        max_runtime_seconds=30,
        dsn=None,
        resume_probe_enabled=False,
        max_idle_loops=1,
        token_min=1,
        token_max=2,
        workspace_id="demo",
        checkpoint_run_dir=str(tmp_path / "run"),
        doc_profile="small",
        skip_maintenance_invariant=False,
        live_trace=False,
        corpus_fingerprint="fingerprint",
    )
    harness = LongRunHarness(run_dir=tmp_path / "run", config=config)
    record = DocumentRecord(
        doc_id="doc-001",
        title="Demo",
        source_uri="file:///demo.md",
        input_path=tmp_path / "doc-001.md",
        current_path=tmp_path / "doc-001.md",
    )

    process = SimpleNamespace(exitcode=17)
    message = harness._parser_child_failure_message(
        record=record,
        process=process,
        failure={},
        trace_path=tmp_path / "trace.log",
        failure_path=tmp_path / "failure.json",
    )

    assert "exited without result" in message
    assert "exitcode=17" in message
    assert "trace.log" in message
    assert "failure.json" in message


def test_longrun_parser_subprocess_timeout_records_heartbeat(tmp_path: Path):
    config = LongRunConfig(
        enabled=False,
        mode="fresh",
        doc_count=1,
        ollama_model="gemma4:e2b",
        ollama_base_url="http://localhost:11434",
        max_repeated_systemic_errors=3,
        max_post_doc_maintenance_steps=1,
        parser_lane="workflow_layered",
        parse_timeout_seconds=1,
    )
    harness = LongRunHarness(run_dir=tmp_path / "parser-timeout", config=config)
    harness._prepare_run_directory()
    record = DocumentRecord(
        doc_id="doc-001",
        title="Parser Timeout",
        source_uri="file:///doc-001.md",
        input_path=tmp_path / "doc-001.md",
        current_path=tmp_path / "doc-001.md",
    )
    request = IngestPipelineRequest(
        workspace_id=config.workspace_id,
        source_uri=record.source_uri,
        title=record.title,
        raw_text="# Parser Timeout\n\nAlpha",
        source_format="markdown",
        parser_mode="heuristic",
    )

    with pytest.raises(LongRunDocumentError, match="timed out") as exc_info:
        harness._run_parse_with_subprocess(
            record=record,
            request=request,
            source_document_id="source-doc-001",
        )

    assert harness.parser_heartbeat is not None
    assert harness.parser_heartbeat["phase"] == "timeout"
    heartbeat_path = harness.dumper.dump_dir / "parser_heartbeat.json"
    assert heartbeat_path.exists()
    assert exc_info.value.details["parser_trace_path"].endswith("trace.log")
    assert exc_info.value.details["parser_failure_payload_path"].endswith("failure_payload.json")
    assert exc_info.value.details["failure_kind"] == "parser_timeout"
    assert isinstance(exc_info.value.details["parser_heartbeat"], dict)
    assert isinstance(exc_info.value.details["parser_trace_tail"], list)
    assert any(
        "parent_timeout" in line
        for line in exc_info.value.details["dump_trace_tail"]
    )


def test_longrun_parser_subprocess_parent_poll_trace_is_throttled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    config = LongRunConfig(
        enabled=False,
        mode="fresh",
        doc_count=1,
        ollama_model="gemma4:e2b",
        ollama_base_url="http://localhost:11434",
        max_repeated_systemic_errors=3,
        max_post_doc_maintenance_steps=1,
        parser_lane="workflow_layered",
        parse_timeout_seconds=1200,
        live_trace=False,
    )
    harness = LongRunHarness(run_dir=tmp_path / "parser-poll-throttle", config=config)
    harness._prepare_run_directory()
    monkeypatch.setattr(harness, "_parse_result_from_payload", lambda payload: payload)

    record = DocumentRecord(
        doc_id="doc-001",
        title="Parser Poll Throttle",
        source_uri="file:///doc-001.md",
        input_path=tmp_path / "doc-001.md",
        current_path=tmp_path / "doc-001.md",
    )
    request = IngestPipelineRequest(
        workspace_id=config.workspace_id,
        source_uri=record.source_uri,
        title=record.title,
        raw_text="# Parser Poll\n\nAlpha",
        source_format="markdown",
        parser_mode="heuristic",
    )

    monotonic_values = iter([0.0, 5.0, 10.0, 20.0, 35.0, 50.0])

    def _fake_monotonic() -> float:
        return next(monotonic_values)

    monkeypatch.setattr(time, "monotonic", _fake_monotonic)

    class _FakeProcess:
        def __init__(self, result_path: Path) -> None:
            self.pid = 4242
            self.name = "fake-parser-process"
            self.exitcode = 0
            self._alive = True
            self._join_count = 0
            self._result_path = result_path

        def start(self) -> None:
            return None

        def is_alive(self) -> bool:
            return self._alive

        def join(self, timeout: float | None = None) -> None:
            del timeout
            self._join_count += 1
            if self._join_count >= 5:
                self._alive = False
                _write_json_file(
                    self._result_path,
                    {
                        "ok": True,
                        "parser_lane": "workflow_layered",
                        "title": "Parser Poll Throttle",
                        "graph_payload": {"nodes": [], "edges": []},
                        "evaluation": {},
                        "usage_summary": {},
                        "diagnostics": {},
                        "layer_log": [
                            {
                                "stage": "workflow_layered_parse_complete",
                                "source_document_id": "source-doc-001",
                            }
                        ],
                    },
                )

    class _FakeContext:
        def __init__(self, result_path: Path) -> None:
            self._result_path = result_path

        def Process(
            self,
            target: object,
            args: tuple[object, ...],
            **kwargs: object,
        ) -> _FakeProcess:
            del target, args, kwargs
            return _FakeProcess(self._result_path)

    result_path = harness.run_dir / "parser_runs" / record.doc_id / "result.json"
    monkeypatch.setattr(multiprocessing, "get_context", lambda method: _FakeContext(result_path))

    harness._run_parse_with_subprocess(
        record=record,
        request=request,
        source_document_id="source-doc-001",
    )

    dump_trace_path = harness.dumper.dump_dir / "parser_trace.log"
    trace_text = dump_trace_path.read_text(encoding="utf-8")
    assert trace_text.count("parent_poll") == 2
    assert "elapsed=5.00s" in trace_text
    assert "elapsed=35.00s" in trace_text
    assert "elapsed=10.00s" not in trace_text
    assert "elapsed=20.00s" not in trace_text
    assert "elapsed=50.00s" not in trace_text
    persisted_layer_log = json.loads(
        (harness.dumper.dump_dir / "parser_layer_logs" / "doc-001.json").read_text(
            encoding="utf-8"
        )
    )
    assert persisted_layer_log == [
        {
            "source_document_id": "source-doc-001",
            "stage": "workflow_layered_parse_complete",
        }
    ]
    assert "parent_layer_log_persisted" in trace_text


def test_longrun_parser_subprocess_closes_process_handle_on_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    config = LongRunConfig(
        enabled=False,
        mode="fresh",
        doc_count=1,
        ollama_model="gemma4:e2b",
        ollama_base_url="http://localhost:11434",
        max_repeated_systemic_errors=3,
        max_post_doc_maintenance_steps=1,
        parser_lane="workflow_layered",
        parse_timeout_seconds=1200,
        live_trace=False,
    )
    harness = LongRunHarness(run_dir=tmp_path / "parser-close-success", config=config)
    harness._prepare_run_directory()
    monkeypatch.setattr(harness, "_parse_result_from_payload", lambda payload: payload)

    record = DocumentRecord(
        doc_id="doc-001",
        title="Parser Close Success",
        source_uri="file:///doc-001.md",
        input_path=tmp_path / "doc-001.md",
        current_path=tmp_path / "doc-001.md",
    )
    request = IngestPipelineRequest(
        workspace_id=config.workspace_id,
        source_uri=record.source_uri,
        title=record.title,
        raw_text="# Parser Close Success\n\nAlpha",
        source_format="markdown",
        parser_mode="heuristic",
    )

    class _FakeProcess:
        def __init__(self, result_path: Path) -> None:
            self.pid = 4243
            self.name = "fake-parser-process"
            self.exitcode = 0
            self._alive = True
            self.closed = False
            self.terminated = False
            self.killed = False
            self._result_path = result_path
            self._join_count = 0

        def start(self) -> None:
            return None

        def is_alive(self) -> bool:
            return self._alive

        def join(self, timeout: float | None = None) -> None:
            del timeout
            self._join_count += 1
            if self._join_count == 1:
                _write_json_file(
                    self._result_path,
                    {
                        "ok": True,
                        "parser_lane": "workflow_layered",
                        "title": "Parser Close Success",
                        "graph_payload": {"nodes": [], "edges": []},
                        "evaluation": {},
                        "usage_summary": {},
                        "diagnostics": {},
                        "layer_log": [
                            {
                                "stage": "workflow_layered_parse_complete",
                                "source_document_id": "source-doc-001",
                            }
                        ],
                    },
                )
                self._alive = False

        def terminate(self) -> None:
            self.terminated = True
            self._alive = False

        def kill(self) -> None:
            self.killed = True
            self._alive = False

        def close(self) -> None:
            self.closed = True

    class _FakeContext:
        def __init__(self, result_path: Path) -> None:
            self._result_path = result_path
            self.process: _FakeProcess | None = None

        def Process(
            self,
            target: object,
            args: tuple[object, ...],
            **kwargs: object,
        ) -> _FakeProcess:
            del target, args, kwargs
            self.process = _FakeProcess(self._result_path)
            return self.process

    result_path = harness.run_dir / "parser_runs" / record.doc_id / "result.json"
    fake_context = _FakeContext(result_path)
    monkeypatch.setattr(multiprocessing, "get_context", lambda method: fake_context)

    harness._run_parse_with_subprocess(
        record=record,
        request=request,
        source_document_id="source-doc-001",
    )

    assert fake_context.process is not None
    assert fake_context.process.closed is True
    assert fake_context.process.terminated is False
    assert fake_context.process.killed is False


def test_longrun_parser_subprocess_timeout_closes_process_and_preserves_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    config = LongRunConfig(
        enabled=False,
        mode="fresh",
        doc_count=1,
        ollama_model="gemma4:e2b",
        ollama_base_url="http://localhost:11434",
        max_repeated_systemic_errors=3,
        max_post_doc_maintenance_steps=1,
        parser_lane="workflow_layered",
        parse_timeout_seconds=1,
        live_trace=False,
    )
    harness = LongRunHarness(run_dir=tmp_path / "parser-close-timeout", config=config)
    harness._prepare_run_directory()

    record = DocumentRecord(
        doc_id="doc-001",
        title="Parser Close Timeout",
        source_uri="file:///doc-001.md",
        input_path=tmp_path / "doc-001.md",
        current_path=tmp_path / "doc-001.md",
    )
    request = IngestPipelineRequest(
        workspace_id=config.workspace_id,
        source_uri=record.source_uri,
        title=record.title,
        raw_text="# Parser Close Timeout\n\nAlpha",
        source_format="markdown",
        parser_mode="heuristic",
    )

    # The child remains alive long enough to exercise the parent's timeout
    # branch, without starting a real process or making the test sleep.
    monotonic_values = iter([0.0, 2.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(monotonic_values))

    class _FakeProcess:
        def __init__(self) -> None:
            self.pid = 4244
            self.name = "fake-parser-timeout-process"
            self.exitcode = None
            self._alive = True
            self.closed = False
            self.terminated = False
            self.killed = False

        def start(self) -> None:
            return None

        def is_alive(self) -> bool:
            return self._alive

        def join(self, timeout: float | None = None) -> None:
            del timeout

        def terminate(self) -> None:
            self.terminated = True
            self.exitcode = -15
            self._alive = False

        def kill(self) -> None:
            self.killed = True
            self.exitcode = -9
            self._alive = False

        def close(self) -> None:
            self.closed = True

    class _FakeContext:
        def __init__(self) -> None:
            self.process: _FakeProcess | None = None
            self.payload: dict[str, Any] | None = None

        def Process(
            self,
            target: object,
            args: tuple[object, ...],
            **kwargs: object,
        ) -> _FakeProcess:
            del target, kwargs
            self.payload = dict(args[0])
            self.process = _FakeProcess()
            return self.process

    fake_context = _FakeContext()
    monkeypatch.setattr(multiprocessing, "get_context", lambda method: fake_context)

    with pytest.raises(LongRunDocumentError, match="timed out"):
        harness._run_parse_with_subprocess(
            record=record,
            request=request,
            source_document_id="source-doc-001",
        )

    assert fake_context.process is not None
    assert fake_context.process.terminated is True
    assert fake_context.process.killed is False
    assert fake_context.process.closed is True
    assert fake_context.payload is not None
    assert fake_context.payload["doc_id"] == "doc-001"
    assert fake_context.payload["source_document_id"] == "source-doc-001"
    assert fake_context.payload["raw_text"] == request.raw_text
    assert fake_context.payload["parser_workflow_run_id"] == "parser:source-doc-001"
    assert fake_context.payload["resume_from_checkpoint"] is False
    assert fake_context.payload["parser_provider"] == config.parser_provider
    assert fake_context.payload["parser_model"] == config.parser_model
    assert record.parser_resume_requested is True
    assert harness.parser_heartbeat is not None
    assert harness.parser_heartbeat["phase"] == "timeout"
    assert harness.parser_heartbeat["pid"] == 4244
    assert harness.parser_heartbeat["resume_from_checkpoint"] is False


def test_longrun_parser_subprocess_timeout_respects_remaining_runtime_budget(tmp_path: Path):
    config = LongRunConfig(
        enabled=False,
        mode="fresh",
        doc_count=1,
        ollama_model="gemma4:e2b",
        ollama_base_url="http://localhost:11434",
        max_repeated_systemic_errors=3,
        max_post_doc_maintenance_steps=1,
        parser_lane="workflow_layered",
        parse_timeout_seconds=1200,
        max_runtime_seconds=1,
    )
    harness = LongRunHarness(run_dir=tmp_path / "parser-runtime-budget-timeout", config=config)
    harness._prepare_run_directory()
    harness.run_started_monotonic = time.monotonic()
    record = DocumentRecord(
        doc_id="doc-001",
        title="Parser Runtime Budget Timeout",
        source_uri="file:///doc-001.md",
        input_path=tmp_path / "doc-001.md",
        current_path=tmp_path / "doc-001.md",
    )
    request = IngestPipelineRequest(
        workspace_id=config.workspace_id,
        source_uri=record.source_uri,
        title=record.title,
        raw_text="# Parser Runtime Budget Timeout\n\nAlpha",
        source_format="markdown",
        parser_mode="heuristic",
    )

    started = time.monotonic()
    with pytest.raises(LongRunDocumentError, match="timed out after effective"):
        harness._run_parse_with_subprocess(
            record=record,
            request=request,
            source_document_id="source-doc-001",
        )

    assert time.monotonic() - started < 10
    assert harness.parser_heartbeat is not None
    assert harness.parser_heartbeat["phase"] == "timeout"
    assert harness.parser_heartbeat["timeout_seconds"] < config.parse_timeout_seconds
    assert harness.parser_heartbeat["configured_parse_timeout_seconds"] == config.parse_timeout_seconds


@pytest.mark.parametrize(
    ("backend", "expected_factory"),
    [
        ("chroma", "persistent"),
        ("postgres", "postgres"),
        ("pgvector", "postgres"),
    ],
)
def test_longrun_backend_dispatch_uses_selected_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    backend: str,
    expected_factory: str,
):
    build_calls: dict[str, Any] = {}

    def _fake_persistent(base_dir, split_derived_knowledge=False):
        build_calls["persistent"] = {
            "base_dir": Path(base_dir),
            "split_derived_knowledge": split_derived_knowledge,
        }
        return SimpleNamespace(kind="persistent")

    def _fake_postgres(base_dir, dsn, split_derived_knowledge=False):
        build_calls["postgres"] = {
            "base_dir": Path(base_dir),
            "dsn": dsn,
            "split_derived_knowledge": split_derived_knowledge,
        }
        return SimpleNamespace(kind="postgres")

    monkeypatch.setattr(sys.modules[__name__], "build_persistent_namespace_engines", _fake_persistent)
    monkeypatch.setattr(sys.modules[__name__], "build_postgres_namespace_engines", _fake_postgres)

    config = LongRunConfig(
        enabled=False,
        mode="fresh",
        doc_count=1,
        ollama_model="gemma4:e2b",
        ollama_base_url="http://localhost:11434",
        max_repeated_systemic_errors=3,
        max_post_doc_maintenance_steps=1,
        backend=backend,
        dsn=(
            "postgresql+psycopg://demo:demo@127.0.0.1:5432/demo"
            if backend in {"postgres", "pgvector"}
            else None
        ),
    )
    harness = LongRunHarness(run_dir=tmp_path / f"backend-{backend}", config=config)

    engines = harness._build_namespace_engines()

    assert engines.kind == expected_factory
    assert build_calls[expected_factory]["base_dir"] == harness.run_dir / "engines"
    if backend in {"postgres", "pgvector"}:
        assert build_calls["postgres"]["dsn"] == "postgresql+psycopg://demo:demo@127.0.0.1:5432/demo"
        assert "persistent" not in build_calls
    else:
        assert build_calls["persistent"]["split_derived_knowledge"] is False
        assert "postgres" not in build_calls


def test_longrun_fresh_prepare_resets_run_directory_before_engine_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    import sys

    run_dir = tmp_path / "fresh-reset-order"
    stale_marker = run_dir / "engines" / "stale.marker"
    stale_marker.parent.mkdir(parents=True, exist_ok=True)
    stale_marker.write_text("stale", encoding="utf-8")

    config = LongRunConfig(
        enabled=False,
        mode="fresh",
        doc_count=1,
        ollama_model="gemma4:e2b",
        ollama_base_url="http://localhost:11434",
        max_repeated_systemic_errors=3,
        max_post_doc_maintenance_steps=1,
    )

    original_build = build_persistent_namespace_engines
    build_calls: list[Path] = []

    def _build(path: Path):
        build_calls.append(path)
        assert not stale_marker.exists()
        return original_build(path)

    monkeypatch.setattr(sys.modules[__name__], "build_persistent_namespace_engines", _build)

    harness = LongRunHarness(run_dir=run_dir, config=config)
    harness.prepare()

    assert build_calls
    assert not stale_marker.exists()


def test_longrun_auto_checkpoint_mismatch_falls_back_to_fresh(tmp_path: Path):
    run_dir = tmp_path / "checkpoint-mismatch"
    dump_dir = run_dir / "dump"
    dump_dir.mkdir(parents=True, exist_ok=True)
    stale_manifest = {
        "run_id": "stale",
        "doc_id": "doc-001",
        "title": "Stale checkpoint document",
        "source_uri": "file:///doc-001.md",
        "current_path": str(run_dir / "input" / "doc-001.md"),
        "status": "COMPLETED",
        "started_at_ms": _now_ms(),
        "ended_at_ms": _now_ms(),
        "elapsed_ms": 1,
        "token_count": 512,
        "tokenizer_method": TOKENIZER_METHOD,
        "source_document_id": "stale-source",
        "maintenance_job_id": None,
        "promoted_entity_id": None,
        "last_step_name": "move_completed",
        "last_step_at_ms": _now_ms(),
        "llm_quality_failures": [],
    }
    (dump_dir / "manifest.jsonl").write_text(json.dumps(stale_manifest) + "\n", encoding="utf-8")

    config = LongRunConfig(
        enabled=False,
        mode="auto",
        doc_count=2,
        ollama_model="gemma4:e2b",
        ollama_base_url="http://localhost:11434",
        max_repeated_systemic_errors=3,
        max_post_doc_maintenance_steps=1,
    )
    harness = LongRunHarness(run_dir=run_dir, config=config)
    harness.prepare()

    assert harness.checkpoint_loaded is False
    assert len(harness.records) == 2
    assert {record.doc_id for record in harness.records} == {"doc-001", "doc-002"}
    assert all(record.status == "PENDING" for record in harness.records)


def test_longrun_auto_checkpoint_corpus_fingerprint_mismatch_falls_back_to_fresh(tmp_path: Path):
    run_dir = tmp_path / "checkpoint-corpus-mismatch"
    dump_dir = run_dir / "dump"
    dump_dir.mkdir(parents=True, exist_ok=True)
    stale_manifest = {
        "run_id": "stale",
        "corpus_fingerprint": "stale-fingerprint",
        "doc_id": "doc-001",
        "title": "Stale checkpoint document",
        "source_uri": "file:///doc-001.md",
        "current_path": str(run_dir / "input" / "doc-001.md"),
        "status": "COMPLETED",
        "started_at_ms": _now_ms(),
        "ended_at_ms": _now_ms(),
        "elapsed_ms": 1,
        "token_count": 512,
        "tokenizer_method": TOKENIZER_METHOD,
        "source_document_id": "stale-source",
        "maintenance_job_id": None,
        "promoted_entity_id": None,
        "last_step_name": "move_completed",
        "last_step_at_ms": _now_ms(),
        "llm_quality_failures": [],
    }
    (dump_dir / "manifest.jsonl").write_text(json.dumps(stale_manifest) + "\n", encoding="utf-8")

    config = LongRunConfig(
        enabled=False,
        mode="auto",
        doc_count=1,
        doc_profile="small",
        token_min=150,
        token_max=800,
        ollama_model="gemma4:e2b",
        ollama_base_url="http://localhost:11434",
        max_repeated_systemic_errors=3,
        max_post_doc_maintenance_steps=1,
    )
    harness = LongRunHarness(run_dir=run_dir, config=config)
    harness.prepare()

    assert harness.checkpoint_loaded is False
    assert len(harness.records) == 1
    assert harness.records[0].doc_id == "doc-001"


def test_longrun_checkpoint_accepted_fingerprints_ignore_parser_workers() -> None:
    config = LongRunConfig(
        enabled=False,
        mode="continue",
        doc_count=1,
        backend="pgvector",
        dsn="postgresql://user:pass@localhost/db",
        doc_profile="small",
        token_min=150,
        token_max=800,
        ollama_model="gemma4:e2b",
        ollama_base_url="http://localhost:11434",
        max_repeated_systemic_errors=3,
        max_post_doc_maintenance_steps=1,
        parser_workers=4,
    )

    accepted = config.checkpoint_accepted_corpus_fingerprints()
    legacy_worker_sensitive = config._legacy_worker_sensitive_corpus_fingerprint(
        parser_workers=1,
        resume_probe_enabled=False,
    )

    assert config.corpus_fingerprint in accepted
    assert legacy_worker_sensitive in accepted


def test_longrun_harness_writes_promotion_evidence_pack(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    config = LongRunConfig(
        enabled=False,
        mode="fresh",
        doc_count=1,
        ollama_model="gemma4:e2b",
        ollama_base_url="http://localhost:11434",
        max_repeated_systemic_errors=3,
        max_post_doc_maintenance_steps=1,
    )
    harness = LongRunHarness(run_dir=tmp_path / "promotion-provenance", config=config)
    harness.prepare()

    record = harness.records[0]
    parsed_nodes = [SimpleNamespace(id="parsed-node-1"), SimpleNamespace(id="parsed-node-2")]
    parsed_edges = [SimpleNamespace(id="parsed-edge-1")]

    monkeypatch.setattr(
        harness,
        "_run_parse_with_subprocess",
        lambda **kwargs: SimpleNamespace(semantic_tree=SimpleNamespace(title=record.title)),
    )
    monkeypatch.setattr(harness.pipeline, "register_source", lambda **kwargs: None)
    monkeypatch.setattr(
        harness.pipeline,
        "translate_parse_result",
        lambda **kwargs: SimpleNamespace(nodes=parsed_nodes, edges=parsed_edges),
    )
    monkeypatch.setattr(harness.pipeline, "ingest_parse_result", lambda **kwargs: None)
    monkeypatch.setattr(harness, "_poll_maintenance_once", lambda **kwargs: None)
    monkeypatch.setattr(harness, "_verify_document", lambda record: None)

    harness._run_document_workflow(record)

    ns = WorkspaceNamespaces(config.workspace_id)
    assert record.promotion_evidence_pack_id
    assert record.promotion_candidate_id
    assert record.promoted_entity_id

    with _temporary_namespace(harness.engines.kg, ns.curated_kg_space):
        promoted_nodes = harness.engines.kg.read.get_nodes(ids=[record.promoted_entity_id], limit=1)
    assert len(promoted_nodes) == 1
    promoted = promoted_nodes[0]
    assert promoted.metadata.get("promotion_candidate_id") == record.promotion_candidate_id
    assert promoted.metadata.get("promotion_evidence_pack_id") == record.promotion_evidence_pack_id
    assert promoted.metadata.get("promotion_evidence_pack_digest")
    assert promoted.metadata.get("promotion_decision_reason")
    assert promoted.metadata.get("graph_space") == "curated_kg"

    with _temporary_namespace(harness.engines.conversation, ns.conv_bg):
        candidate_nodes = harness.engines.conversation.read.get_nodes(ids=[record.promotion_candidate_id], limit=1)
    assert len(candidate_nodes) == 1
    candidate = candidate_nodes[0]
    assert candidate.metadata.get("promotion_evidence_pack_id") == record.promotion_evidence_pack_id
    assert candidate.metadata.get("promotion_evidence_pack_digest")

    with _temporary_namespace(harness.engines.conversation, ns.conv_bg):
        packs = harness.engines.conversation.read.get_nodes(ids=[record.promotion_evidence_pack_id], limit=1)
    assert len(packs) == 1
    pack = packs[0]
    digest = _decode_metadata_json(pack.metadata.get("promotion_evidence_pack_digest"))
    assert digest.get("node_ids") == ["parsed-node-1", "parsed-node-2"]
    assert digest.get("edge_ids") == ["parsed-edge-1"]


def test_longrun_resume_probe_suspends_and_resumes_checkpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    run_dir = tmp_path / "resume-probe"
    fresh_config = LongRunConfig(
        enabled=False,
        mode="fresh",
        doc_count=1,
        ollama_model="gemma4:e2b",
        ollama_base_url="http://localhost:11434",
        resume_probe_enabled=True,
        max_repeated_systemic_errors=3,
        max_post_doc_maintenance_steps=1,
    )
    first = LongRunHarness(run_dir=run_dir, config=fresh_config)
    first.prepare()

    first_record = first.records[0]
    parsed_nodes = [SimpleNamespace(id="parsed-node-1"), SimpleNamespace(id="parsed-node-2")]
    parsed_edges = [SimpleNamespace(id="parsed-edge-1")]
    monkeypatch.setattr(
        first,
        "_run_parse_with_subprocess",
        lambda **kwargs: SimpleNamespace(semantic_tree=SimpleNamespace(title=first_record.title)),
    )
    monkeypatch.setattr(first.pipeline, "register_source", lambda **kwargs: None)
    monkeypatch.setattr(
        first.pipeline,
        "translate_parse_result",
        lambda **kwargs: SimpleNamespace(nodes=parsed_nodes, edges=parsed_edges),
    )
    monkeypatch.setattr(first.pipeline, "ingest_parse_result", lambda **kwargs: None)

    first.run()

    assert first_record.status == "SUSPENDED"
    assert first_record.resume_suspended_node_id
    assert first_record.resume_suspended_token_id
    assert first_record.resume_checkpoint_step_seq is not None
    assert first.progress_summary()["suspended_count"] == 1
    assert first.progress_summary()["suspended_document_ids"] == [first_record.doc_id]

    continue_config = LongRunConfig(
        enabled=False,
        mode="continue",
        doc_count=1,
        ollama_model="gemma4:e2b",
        ollama_base_url="http://localhost:11434",
        resume_probe_enabled=True,
        max_repeated_systemic_errors=3,
        max_post_doc_maintenance_steps=1,
    )
    second = LongRunHarness(run_dir=run_dir, config=continue_config)
    second.prepare()
    resumed_record = second.records[0]
    monkeypatch.setattr(
        second,
        "_run_parse_with_subprocess",
        lambda **kwargs: SimpleNamespace(semantic_tree=SimpleNamespace(title=resumed_record.title)),
    )
    monkeypatch.setattr(second.pipeline, "register_source", lambda **kwargs: None)
    monkeypatch.setattr(
        second.pipeline,
        "translate_parse_result",
        lambda **kwargs: SimpleNamespace(nodes=parsed_nodes, edges=parsed_edges),
    )
    monkeypatch.setattr(second.pipeline, "ingest_parse_result", lambda **kwargs: None)
    monkeypatch.setattr(second, "_poll_maintenance_once", lambda **kwargs: None)
    monkeypatch.setattr(second, "_verify_document", lambda record: None)

    second._run_document_workflow(resumed_record)
    second.dumper.dump(reason="probe_resume")

    assert second.checkpoint_loaded is True
    assert resumed_record.resumed_from_checkpoint is True
    assert resumed_record.resume_suspended_node_id == first_record.resume_suspended_node_id
    assert resumed_record.resume_suspended_token_id == first_record.resume_suspended_token_id
    assert resumed_record.resume_checkpoint_step_seq == first_record.resume_checkpoint_step_seq
    assert resumed_record.status == "COMPLETED"
    assert second.progress_summary()["resumed_document_ids"] == [resumed_record.doc_id]


def test_longrun_suspended_checkpoint_without_resume_tokens_fails(tmp_path: Path):
    run_dir = tmp_path / "resume-metadata-missing"
    dump_dir = run_dir / "dump"
    dump_dir.mkdir(parents=True, exist_ok=True)
    manifest_row = {
        "run_id": "resume-metadata-missing:doc-001",
        "doc_id": "doc-001",
        "title": "Resume metadata missing",
        "source_uri": "file:///doc-001.md",
        "current_path": str(run_dir / "processing" / "doc-001.md"),
        "status": "SUSPENDED",
        "started_at_ms": _now_ms(),
        "ended_at_ms": None,
        "elapsed_ms": None,
        "token_count": 512,
        "tokenizer_method": TOKENIZER_METHOD,
        "source_document_id": "source-doc-001",
        "maintenance_job_id": None,
        "candidate_link_id": None,
        "promotion_evidence_pack_id": None,
        "promotion_candidate_id": None,
        "promoted_entity_id": None,
        "resume_checkpoint_step_seq": None,
        "resume_suspended_node_id": None,
        "resume_suspended_token_id": None,
        "resumed_from_checkpoint": False,
        "last_step_name": "await_resume",
        "last_step_at_ms": _now_ms(),
        "llm_quality_failures": [],
    }
    (dump_dir / "manifest.jsonl").write_text(json.dumps(manifest_row) + "\n", encoding="utf-8")

    config = LongRunConfig(
        enabled=False,
        mode="continue",
        doc_count=1,
        ollama_model="gemma4:e2b",
        ollama_base_url="http://localhost:11434",
        max_repeated_systemic_errors=3,
        max_post_doc_maintenance_steps=1,
    )
    harness = LongRunHarness(run_dir=run_dir, config=config)
    harness.prepare()

    with pytest.raises(LongRunSystemicError, match="missing resume metadata"):
        harness._run_document_workflow(harness.records[0])


def test_longrun_promoted_node_without_promotion_pack_fails_invariant(tmp_path: Path):
    config = LongRunConfig(
        enabled=False,
        mode="auto",
        doc_count=1,
        ollama_model="gemma4:e2b",
        ollama_base_url="http://localhost:11434",
        max_repeated_systemic_errors=3,
        max_post_doc_maintenance_steps=1,
    )
    harness = LongRunHarness(run_dir=tmp_path / "promotion-invariant", config=config)
    record = DocumentRecord(
        doc_id="doc-001",
        title="Broken promotion provenance",
        source_uri="file:///doc-001.md",
        input_path=tmp_path / "doc-001.md",
        current_path=tmp_path / "doc-001.md",
        status="COMPLETED",
        source_document_id="source-doc-1",
        promoted_entity_id="promoted-node-1",
    )

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        harness.engines.kg.read,
        "get_nodes",
        lambda **kwargs: [
            SimpleNamespace(
                id="promoted-node-1",
                metadata={
                    "promotion_candidate_id": "candidate-node-1",
                    "promotion_evidence_pack_digest": {"node_ids": ["n-1"], "edge_ids": []},
                    "promotion_decision_reason": "explicit promotion approval accepted by default policy",
                },
            )
        ],
    )
    try:
        with pytest.raises(LongRunSystemicError, match="missing metadata"):
            harness._verify_promotion_provenance(record)
    finally:
        monkeypatch.undo()


@pytest.mark.integration
@pytest.mark.longrun
def test_longrun_runtime_workflow_ingestion(tmp_path: Path):
    _emit_longrun_live("test_entry", argv=repr(sys.argv), tmp_path=str(tmp_path))
    _emit_longrun_live("config_from_env_start")
    config = LongRunConfig.from_env()
    _emit_longrun_live(
        "config_from_env_complete",
        enabled=config.enabled,
        backend=config.backend,
        mode=config.mode,
        operation_mode=config.operation_mode,
        parser_provider=config.parser_provider,
        parser_model=config.parser_model,
        parser_lane=config.parser_lane,
        live_trace=config.live_trace,
    )
    run_dir = Path(config.checkpoint_run_dir).expanduser() if config.checkpoint_run_dir else tmp_path / "longrun-workflow"
    _emit_longrun_live("harness_construct_start", run_dir=str(run_dir))
    harness = LongRunHarness(run_dir=run_dir, config=config)
    _emit_longrun_live("harness_construct_complete", run_id=harness.run_id)
    _emit_longrun_live("harness_prepare_start", run_id=harness.run_id)
    harness.prepare()
    _emit_longrun_live("harness_prepare_complete", run_id=harness.run_id)

    if _longrun_requires_ollama(config):
        _emit_longrun_live(
            "ollama_healthcheck_start",
            base_url=config.ollama_base_url,
            model=config.ollama_model,
        )
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
        _emit_longrun_live("ollama_healthcheck_complete", ok=ok, reason=reason)
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

    _emit_longrun_live("harness_run_start", run_id=harness.run_id)
    harness.run()
    _emit_longrun_live("harness_run_complete", run_id=harness.run_id)
