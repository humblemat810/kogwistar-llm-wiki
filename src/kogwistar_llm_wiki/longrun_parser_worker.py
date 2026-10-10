"""Compatibility facade for long-run parser execution."""

from pathlib import Path
from typing import Literal

from kg_doc_parser.workflow_ingest.layerwise_llm import build_layerwise_llm_callbacks
from kg_doc_parser.workflow_ingest.providers import WorkflowProviderSettings
from kogwistar.runtime.budget import StateBackedBudgetLedger

from .ingest.contracts import TraceLog
from .parsing.layered_workflow import (
    LayeredParseResult,
    _build_provider_layer_callbacks,  # noqa: F401 - compatibility export
    _summarize_budget_events,
)
from .parsing.layered_workflow import (
    run_workflow_layered_parse as _run_workflow_layered_parse,
)
from .parsing.longrun_child import run_longrun_parser_child as _run_longrun_parser_child
from .parsing.parse_quality import (
    basic_sense_eval_from_graph_payload as _basic_sense_eval_from_graph_payload,
)


def run_workflow_layered_parse(
    *,
    source_document_id: str,
    title: str,
    raw_text: str,
    provider_settings: WorkflowProviderSettings,
    engine_dir: Path,
    budget_ledger: StateBackedBudgetLedger | None = None,
    trace: TraceLog | None = None,
    heartbeat: TraceLog | None = None,
    run_id: str | None = None,
    resume_from_checkpoint: bool = False,
    usage_event_path: Path | None = None,
    conversation_persistence_mode: Literal["single_stage", "two_stage"] = "single_stage",
) -> LayeredParseResult:
    """Run the layered parser while preserving root-level patch seams."""
    return _run_workflow_layered_parse(
        source_document_id=source_document_id,
        title=title,
        raw_text=raw_text,
        provider_settings=provider_settings,
        engine_dir=engine_dir,
        budget_ledger=budget_ledger,
        trace=trace,
        heartbeat=heartbeat,
        run_id=run_id,
        resume_from_checkpoint=resume_from_checkpoint,
        usage_event_path=usage_event_path,
        conversation_persistence_mode=conversation_persistence_mode,
        build_callbacks=build_layerwise_llm_callbacks,
        basic_sense_eval=_basic_sense_eval_from_graph_payload,
    )


def run_longrun_parser_child(payload: dict[str, object]) -> None:
    """Run the child process while preserving root-level test seams."""
    return _run_longrun_parser_child(
        payload,
        workflow_parser=run_workflow_layered_parse,
        summarize_budget_events=_summarize_budget_events,
    )


__all__ = [
    "run_longrun_parser_child",
    "run_workflow_layered_parse",
]
