"""Parser dispatch and provider-bound source parsing for ingestion."""

from __future__ import annotations

import inspect
import shutil
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Protocol

from kogwistar.runtime.budget import budget_event_from_dict

from ..models import IngestPipelineRequest
from ..provider_config import resolve_parser_provider_settings
from ..usage.events import persist_usage_events


class SemanticTreeLike(Protocol):
    title: str


class ParseSourceResult(Protocol):
    semantic_tree: SemanticTreeLike


class SourceParsingMixin:
    """Provide parser dispatch without enlarging the orchestration façade."""

    def parse_source(self, *, request: IngestPipelineRequest, source_document_id: str) -> ParseSourceResult:
        self._trace_event(
            "parse_source_start",
            workspace_id=request.workspace_id,
            source_document_id=source_document_id,
            parser_lane=request.parser_lane,
            parser_mode=request.parser_mode,
        )
        if request.parser_lane == "workflow_layered":
            result = self._parse_workflow_layered_source(
                request=request,
                source_document_id=source_document_id,
            )
            self._trace_event(
                "parse_source_complete",
                workspace_id=request.workspace_id,
                source_document_id=source_document_id,
                parser_lane=request.parser_lane,
                parser_mode=request.parser_mode,
                parse_backend="workflow_layered",
            )
            return result
        parser_kwargs = self._build_parser_kwargs(
            request=request,
            source_document_id=source_document_id,
            trace_log=self._trace_text if self.debug_trace_path is not None else None,
        )
        self._trace_event(
            "parse_source_dispatch",
            workspace_id=request.workspace_id,
            source_document_id=source_document_id,
            parser_lane=request.parser_lane,
            parser_mode=request.parser_mode,
            parse_backend="direct",
        )
        result = self.parser(**parser_kwargs)
        self._trace_event(
            "parse_source_complete",
            workspace_id=request.workspace_id,
            source_document_id=source_document_id,
            parser_lane=request.parser_lane,
            parser_mode=request.parser_mode,
            parse_backend="direct",
        )
        return result

    def _parse_workflow_layered_source(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
    ) -> SimpleNamespace:
        provider = request.llm_provider or self._provider_from_mode(request.parser_mode)
        model = request.llm_model or self._model_from_env(provider)
        if provider is None:
            raise ValueError(f"missing llm provider for parser_mode={request.parser_mode!r}")

        provider_settings = resolve_parser_provider_settings(
            provider=provider,
            model=model,
        )
        engine_dir = Path(tempfile.mkdtemp(prefix="kogwistar-workflow-layered-"))
        self._trace_event(
            "workflow_layered_parse_start",
            workspace_id=request.workspace_id,
            source_document_id=source_document_id,
            provider=provider,
            model=model,
            engine_dir=str(engine_dir),
        )
        try:
            # Resolve through the façade module so the long-standing test seam
            # ``ingest_pipeline.run_workflow_layered_parse`` remains patchable.
            from .. import ingest_pipeline

            result = ingest_pipeline.run_workflow_layered_parse(
                source_document_id=source_document_id,
                title=request.title,
                raw_text=request.raw_text,
                provider_settings=provider_settings,
                engine_dir=engine_dir,
                trace=self._trace_text if self.debug_trace_path is not None else None,
                conversation_persistence_mode=self.conversation_persistence_mode,
            )
        finally:
            shutil.rmtree(engine_dir, ignore_errors=True)
            self._trace_event(
                "workflow_layered_engine_dir_cleaned",
                workspace_id=request.workspace_id,
                source_document_id=source_document_id,
                engine_dir=str(engine_dir),
            )
        self._persist_parser_usage_events(
            request=request,
            source_document_id=source_document_id,
            provider=provider,
            model=model,
            attempt_id=str(getattr(result, "workflow_run_id", None) or uuid.uuid4()),
            usage_events=list(getattr(result, "usage_events", []) or []),
        )
        try:
            usage_snapshot = self.refresh_usage_projection(request.workspace_id)
            self._trace_event(
                "usage_projection_refreshed",
                workspace_id=request.workspace_id,
                source_document_id=source_document_id,
                last_materialized_seq=usage_snapshot.last_materialized_seq,
                materialization_status=usage_snapshot.materialization_status,
            )
        except Exception as exc:  # noqa: BLE001 - projection refresh must not fail ingestion
            self._trace_event(
                "usage_projection_refresh_failed",
                workspace_id=request.workspace_id,
                source_document_id=source_document_id,
                error_type=type(exc).__name__,
                error=str(exc),
            )
        self._trace_event(
            "workflow_layered_parse_complete",
            workspace_id=request.workspace_id,
            source_document_id=source_document_id,
            provider=provider,
            model=model,
            proposal_mode=getattr(provider_settings, "proposal_mode", None),
            parse_session_mode=getattr(result, "parse_session", {}).get("mode")
            if getattr(result, "parse_session", None)
            else None,
        )
        return SimpleNamespace(
            semantic_tree=result.semantic_tree,
            graph_payload=result.graph_payload,
            evaluation=result.evaluation,
            diagnostics=result.diagnostics,
            usage_summary=result.usage_summary,
            usage_events=list(getattr(result, "usage_events", []) or []),
            layer_log=result.layer_log,
            parse_session=getattr(result, "parse_session", None),
        )

    def _persist_parser_usage_events(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        provider: str,
        model: str,
        attempt_id: str,
        usage_events: list[object],
    ) -> None:
        if not usage_events:
            return
        namespaces = self.namespaces_for(request.workspace_id)
        events = [
            budget_event_from_dict(raw_event)
            for raw_event in usage_events
            if isinstance(raw_event, dict)
        ]
        persist_usage_events(
            self.engines.conversation.meta_sqlite,
            namespace=namespaces.usage_events,
            events=events,
            workspace_id=request.workspace_id,
            attempt_id=attempt_id,
            source_document_id=source_document_id,
            operation_kind="parser",
            provider=provider,
            model=model,
        )

    def _build_parser_kwargs(
        self,
        *,
        request: IngestPipelineRequest,
        source_document_id: str,
        trace_log: Callable[[str], None] | None = None,
    ) -> dict[str, object]:
        parser_kwargs: dict[str, object] = {
            "document_id": source_document_id,
            "title": request.title,
            "raw_text": request.raw_text,
            "source_format": request.source_format,
            "mode": request.parser_mode,
        }

        if request.parser_mode == "heuristic":
            return parser_kwargs

        provider = request.llm_provider or self._provider_from_mode(request.parser_mode)
        model = request.llm_model or self._model_from_env(provider)
        if provider is None:
            raise ValueError(f"missing llm provider for parser_mode={request.parser_mode!r}")
        sig = inspect.signature(self.parser)
        params = sig.parameters
        supports_kwargs = any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values())

        if "llm_provider" in params or supports_kwargs:
            parser_kwargs["llm_provider"] = provider
        if "model" in params or supports_kwargs:
            parser_kwargs["model"] = model
        if "provider_settings" in params or supports_kwargs:
            parser_kwargs["provider_settings"] = resolve_parser_provider_settings(
                provider=provider,
                model=model,
            )
        if trace_log is not None and ("trace_log" in params or supports_kwargs):
            parser_kwargs["trace_log"] = trace_log

        if not supports_kwargs and not any(
            key in parser_kwargs for key in ("llm_provider", "model", "provider_settings")
        ):
            raise ValueError(
                "Configured parser does not expose llm_provider/model or provider_settings; "
                "upgrade kg-doc-parser or provide a compatible parser callable."
            )

        return parser_kwargs

    @staticmethod
    def _provider_from_mode(mode: str) -> str | None:
        if mode in {"ollama", "gemini", "openai", "azure_openai", "azure"}:
            if mode == "azure_openai":
                return "azure"
            return mode
        return None

    @staticmethod
    def _model_from_env(provider: str | None) -> str | None:
        import os

        if provider == "ollama":
            return (
                os.getenv("KOGWISTAR_PARSER_MODEL")
                or os.getenv("OLLAMA_MODEL")
                or os.getenv("KG_DOC_PARSER_MODEL")
            )
        if provider == "gemini":
            return (
                os.getenv("KOGWISTAR_PARSER_MODEL")
                or os.getenv("GEMINI_MODEL")
                or os.getenv("KG_DOC_PARSER_MODEL")
            )
        if provider in {"openai", "azure"}:
            return (
                os.getenv("KOGWISTAR_PARSER_MODEL")
                or os.getenv("OPENAI_MODEL")
                or os.getenv("KG_DOC_PARSER_MODEL")
            )
        return os.getenv("KOGWISTAR_PARSER_MODEL") or os.getenv("KG_DOC_PARSER_MODEL")
