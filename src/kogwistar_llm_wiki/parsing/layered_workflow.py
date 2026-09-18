"""Layered workflow parser execution and usage accounting."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

from kg_doc_parser.workflow_ingest.layerwise_llm import (
    LayerwiseCallback,
    build_layerwise_llm_callbacks,
)
from kg_doc_parser.workflow_ingest.providers import WorkflowProviderSettings
from kogwistar.runtime.budget import StateBackedBudgetLedger, budget_event_to_dict
from kogwistar.runtime.budget_adapters import summarize_budget_events

from ..diagnostics.debug_helpers import summarize_stage_timings
from ..providers.role_config import provider_config_summary
from ..usage.provider import ProviderUsageCallback, resolve_token_pricing
from .longrun_support import close_resources_quietly as _close_resources_quietly
from .longrun_support import dump_model as _dump_model
from .longrun_support import now_ms as _now_ms
from .longrun_support import proposal_mode_summary as _proposal_mode_summary
from .parse_quality import (
    basic_sense_eval_from_graph_payload as _basic_sense_eval_from_graph_payload,
)


def _summarize_budget_events(events: list[object], *, provider_settings: WorkflowProviderSettings) -> dict[str, object]:
    provider_summary = provider_config_summary(provider_settings)
    summary = {
        "provider": provider_summary.get("provider"),
        "model": provider_summary.get("model"),
        "temperature": provider_summary.get("temperature"),
        "api_key_env": provider_summary.get("api_key_env"),
        "base_url": provider_summary.get("base_url"),
        "project": provider_summary.get("project"),
        "location": provider_summary.get("location"),
        "max_retries": provider_summary.get("max_retries"),
    } | summarize_budget_events(events)
    provider_run_ids = {
        str(getattr(event, "meta", {}).get("provider_run_id"))
        for event in events
        if getattr(event, "meta", {}).get("provider_run_id")
    }
    summary["llm_call_count"] = len(provider_run_ids)
    cost_events = [
        event for event in events
        if getattr(event, "kind", None) == "cost" or getattr(event, "unit", None) == "total_cost"
    ]
    statuses = {
        str(getattr(event, "meta", {}).get("cost_status"))
        for event in cost_events
        if getattr(event, "meta", {}).get("cost_status")
    }
    if not cost_events or statuses == {"unavailable_missing_tokens"}:
        summary["total_cost"] = None
        summary["cost_status"] = "unavailable_missing_tokens"
        summary["cost_source"] = None
    elif statuses:
        summary["cost_status"] = "+".join(sorted(statuses))
        summary["cost_source"] = sorted(
            {
                str(getattr(event, "meta", {}).get("cost_source"))
                for event in cost_events
                if getattr(event, "meta", {}).get("cost_source")
            }
        )
    else:
        summary["cost_status"] = "provider_reported"
        summary["cost_source"] = "provider"
    return summary


def _build_provider_layer_callbacks(
    provider_settings: WorkflowProviderSettings,
    *,
    layer_event: Callable[..., None] | None = None,
    budget_ledger: StateBackedBudgetLedger | None = None,
    run_id: str = "",
    source_document_id: str = "",
    usage_event_sink: Callable[[object], None] | None = None,
    build_callbacks: Callable[..., dict[str, LayerwiseCallback | int | bool]] | None = None,
) -> dict[str, LayerwiseCallback | int | bool]:
    model_callbacks: list[object] = []
    if budget_ledger is not None:
        parser = provider_settings.parser
        model_callbacks.append(
            ProviderUsageCallback(
                ledger=budget_ledger,
                run_id=run_id or f"parser:{source_document_id}",
                source_document_id=source_document_id,
                provider=parser.provider,
                model=parser.model,
                pricing=resolve_token_pricing(
                    provider=parser.provider,
                    model=parser.model,
                ),
                event_sink=usage_event_sink,
            )
        )
    return (build_callbacks or build_layerwise_llm_callbacks)(
        provider_settings,
        event_sink=layer_event,
        model_callbacks=model_callbacks,
    )


def run_workflow_layered_parse(
    *,
    source_document_id: str,
    title: str,
    raw_text: str,
    provider_settings: WorkflowProviderSettings,
    engine_dir: Path,
    budget_ledger: StateBackedBudgetLedger | None = None,
    trace: Callable[[str], None] | None = None,
    heartbeat: Callable[[str], None] | None = None,
    run_id: str | None = None,
    resume_from_checkpoint: bool = False,
    usage_event_path: Path | None = None,
    conversation_persistence_mode: Literal["single_stage", "two_stage"] = "single_stage",
    build_callbacks: Callable[..., dict[str, LayerwiseCallback | int | bool]] | None = None,
    basic_sense_eval: Callable[..., dict[str, object]] | None = None,
) -> SimpleNamespace:
    from kg_doc_parser.workflow_ingest.models import (
        WorkflowExportBundle,
        WorkflowIngestInput,
    )
    from kg_doc_parser.workflow_ingest.service import (
        build_default_engines,
        run_ingest_workflow,
    )

    if conversation_persistence_mode not in {"single_stage", "two_stage"}:
        raise ValueError(
            "conversation_persistence_mode must be one of: single_stage, two_stage"
        )

    layer_log: list[dict[str, object]] = []

    def _layer_event(stage: str, **extra: object) -> None:
        entry = {
            "stage": stage,
            "timestamp_ms": _now_ms(),
            "source_document_id": source_document_id,
            **extra,
        }
        layer_log.append(entry)
        if trace is not None:
            trace(f"{stage} {json.dumps(extra, sort_keys=True)}")

    budget_ledger = budget_ledger or StateBackedBudgetLedger(
        {
            "token_budget": 10_000_000,
            "budget_scope": "run",
            "budget_kind": "token",
        }
    )
    usage_event_sink: Callable[[object], None] | None = None
    if usage_event_path is not None:
        usage_event_path.parent.mkdir(parents=True, exist_ok=True)

        def _append_usage_event(event: object) -> None:
            with usage_event_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(budget_event_to_dict(event), sort_keys=True) + "\n")
                handle.flush()

        usage_event_sink = _append_usage_event
    engine_dir = Path(engine_dir)
    _layer_event(
        "workflow_layered_provider_settings_loaded",
        provider=provider_settings.parser.provider,
        model=provider_settings.parser.model,
        proposal_mode=provider_settings.proposal_mode,
        conversation_persistence_mode=conversation_persistence_mode,
        workflow_run_id=str(run_id or f"parser:{source_document_id}"),
        resume_from_checkpoint=resume_from_checkpoint,
    )
    _layer_event("workflow_layered_engines_build_start", engine_dir=str(engine_dir))
    engine_kwargs: dict[str, object] = {"provider_settings": provider_settings}
    # Do not add a new keyword on the historical default path. This keeps
    # parser-service wrappers compatible while making the opt-in explicit.
    if conversation_persistence_mode != "single_stage":
        engine_kwargs["conversation_persistence_mode"] = conversation_persistence_mode
    workflow_engine, conversation_engine, knowledge_engine = build_default_engines(
        engine_dir,
        **engine_kwargs,
    )
    _layer_event(
        "workflow_layered_engines_build_done",
        conversation_persistence_mode=conversation_persistence_mode,
    )
    deps = _build_provider_layer_callbacks(
        provider_settings,
        layer_event=_layer_event,
        budget_ledger=budget_ledger,
        run_id=str(run_id or f"parser:{source_document_id}"),
        source_document_id=source_document_id,
        usage_event_sink=usage_event_sink,
        build_callbacks=build_callbacks,
    )
    inp = WorkflowIngestInput.from_text(
        document_id=source_document_id,
        text=str(raw_text),
        title=str(title),
    )
    _layer_event("workflow_layered_parse_start")
    if resume_from_checkpoint:
        _layer_event(
            "workflow_layered_resume_requested",
            workflow_run_id=str(run_id or f"parser:{source_document_id}"),
            engine_dir=str(engine_dir),
        )
    if heartbeat is not None:
        heartbeat("workflow_layered_parse_start")
    try:
        run_result, bundle = run_ingest_workflow(
            inp=inp,
            workflow_engine=workflow_engine,
            conversation_engine=conversation_engine,
            knowledge_engine=knowledge_engine,
            deps={**deps, "budget_ledger": budget_ledger},
            run_id=run_id,
            resume_from_checkpoint=resume_from_checkpoint,
        )
    finally:
        _close_resources_quietly(workflow_engine, conversation_engine, knowledge_engine)
        _layer_event("workflow_layered_engines_closed")
    _layer_event(
        "workflow_layered_parse_returned",
        workflow_status=getattr(run_result, "status", None),
        workflow_run_id=getattr(run_result, "run_id", None),
    )
    final_state = dict(getattr(run_result, "final_state", {}) or {})
    _layer_event(
        "workflow_layered_postparse_state_snapshot",
        workflow_status=getattr(run_result, "status", None),
        has_semantic_tree="semantic_tree" in final_state,
        has_validation_report="validation_report" in final_state,
        has_export_bundle="export_bundle" in final_state,
        workflow_error_count=len(list(final_state.get("workflow_errors") or [])),
    )
    parse_session = final_state.get("parse_session") or {}
    proposal_summary = _proposal_mode_summary(final_state)
    _layer_event(
        "workflow_layered_parse_summary_ready",
        parse_session_mode=parse_session.get("mode"),
        proposal_mode=proposal_summary.get("proposal_mode"),
        boundary_proposed_count=proposal_summary.get("boundary_proposed_count"),
        boundary_accepted_count=proposal_summary.get("boundary_accepted_count"),
        boundary_shifted_count=proposal_summary.get("boundary_shifted_count"),
        boundary_rejected_count=proposal_summary.get("boundary_rejected_count"),
        unresolved_interval_count=proposal_summary.get("unresolved_interval_count"),
        provider_child_count=proposal_summary.get("provider_child_count"),
    )
    bundle_source = "result"
    if not bundle and final_state.get("export_bundle"):
        bundle = WorkflowExportBundle.model_validate(final_state["export_bundle"])
        bundle_source = "final_state_export_bundle"
    if not bundle and final_state.get("semantic_tree"):
        from kg_doc_parser.workflow_ingest.models import (
            WorkflowExportBundle as _WorkflowExportBundle,
        )
        from kg_doc_parser.workflow_ingest.semantics import (
            SemanticNode,
            semantic_tree_to_kge_payload,
        )

        semantic_tree = SemanticNode.model_validate(final_state["semantic_tree"])
        graph_payload = semantic_tree_to_kge_payload(semantic_tree, doc_id=source_document_id)
        bundle = _WorkflowExportBundle(
            graph_payload=graph_payload,
            authoritative_source_map=final_state.get("authoritative_source_map") or {},
            embedding_spaces=list(final_state.get("embedding_spaces") or []),
            consolidation_candidates=[],
            retrieval_metadata=dict(final_state.get("retrieval_metadata") or {}),
            persistence_mode="local_debug",
            kg_authority="local",
            canonical_write_confirmed=False,
            parser_owner="local",
            server_parser_used=False,
            persisted_to_knowledge_engine=False,
        )
        bundle_source = "synthesized_from_semantic_tree"
        _layer_event(
            "workflow_layered_export_bundle_synthesized",
            workflow_status=getattr(run_result, "status", None),
            graph_node_count=len(graph_payload.get("nodes", []) or []),
            graph_edge_count=len(graph_payload.get("edges", []) or []),
        )
    if not bundle:
        _layer_event(
            "workflow_layered_export_bundle_missing",
            workflow_status=getattr(run_result, "status", None),
            final_state_keys=sorted(final_state.keys()),
        )
        raise RuntimeError("workflow-layered parser completed without an export bundle")
    _layer_event(
        "workflow_layered_export_bundle_ready",
        workflow_status=getattr(run_result, "status", None),
        bundle_source=bundle_source,
        graph_node_count=len(bundle.graph_payload.get("nodes", []) or []),
        graph_edge_count=len(bundle.graph_payload.get("edges", []) or []),
    )

    graph_payload = _dump_model(bundle.graph_payload)
    evaluation = (basic_sense_eval or _basic_sense_eval_from_graph_payload)(
        graph_payload=graph_payload,
        diagnostics={
            "parse_session_mode": parse_session.get("mode"),
            "workflow_status": getattr(run_result, "status", None),
            "workflow_run_id": getattr(run_result, "run_id", None),
        },
    )
    _layer_event(
        "workflow_layered_evaluation_ready",
        basic_sense_score=evaluation.get("basic_sense_score"),
        basic_sense_verdict=evaluation.get("basic_sense_verdict"),
        coverage_ratio=evaluation.get("coverage_ratio"),
        node_count=evaluation.get("node_count"),
        node_type_diversity=evaluation.get("node_type_diversity"),
        duplicate_excerpt_hits=evaluation.get("duplicate_excerpt_hits"),
        fallback_used=evaluation.get("fallback_used"),
    )
    usage_summary = _summarize_budget_events(
        list(getattr(budget_ledger, "events", []) or []),
        provider_settings=provider_settings,
    )
    timing_summary = summarize_stage_timings(layer_log)
    usage_summary["timing_summary"] = timing_summary
    if proposal_summary:
        usage_summary["proposal_summary"] = proposal_summary
        if proposal_summary.get("proposal_mode") is not None:
            usage_summary["proposal_mode"] = proposal_summary["proposal_mode"]
    _layer_event(
        "workflow_layered_usage_summary_ready",
        total_cost=usage_summary.get("total_cost"),
        total_tokens=usage_summary.get("total_tokens"),
        prompt_tokens=usage_summary.get("prompt_tokens"),
        completion_tokens=usage_summary.get("completion_tokens"),
        proposal_mode=usage_summary.get("proposal_mode"),
    )
    diagnostics = {
        "parser_lane": "workflow_layered",
        "parse_session_mode": parse_session.get("mode"),
        "workflow_status": getattr(run_result, "status", None),
        "workflow_run_id": getattr(run_result, "run_id", None),
        "layer_log": layer_log,
        "proposal_summary": proposal_summary,
        "timing_summary": timing_summary,
    }
    if diagnostics["parse_session_mode"] != "workflow_layered":
        raise RuntimeError(
            "workflow-layered parser did not run in workflow_layered mode; "
            f"got {diagnostics['parse_session_mode']!r}"
        )
    _layer_event(
        "workflow_layered_parse_complete",
        node_count=len(graph_payload.get("nodes", [])),
        edge_count=len(graph_payload.get("edges", [])),
        total_cost=usage_summary["total_cost"],
        parse_session_mode=parse_session.get("mode"),
    )
    return SimpleNamespace(
        semantic_tree=SimpleNamespace(title=str(title)),
        graph_payload=graph_payload,
        evaluation=evaluation,
        diagnostics=diagnostics,
        usage_summary=usage_summary,
        usage_events=[budget_event_to_dict(event) for event in budget_ledger.events],
        layer_log=layer_log,
        parse_session=parse_session,
        workflow_status=getattr(run_result, "status", None),
        workflow_run_id=getattr(run_result, "run_id", None),
    )
