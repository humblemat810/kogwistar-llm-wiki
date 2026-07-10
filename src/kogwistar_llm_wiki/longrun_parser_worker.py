from __future__ import annotations

import json
import os
import time
import traceback
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace
from typing import Callable

from kg_doc_parser.workflow_ingest.page_index import parse_page_index_document
from kg_doc_parser.workflow_ingest.layerwise_llm import LayerwiseCallback, build_layerwise_llm_callbacks
from kg_doc_parser.workflow_ingest.providers import WorkflowProviderSettings
from kogwistar.runtime.budget_adapters import summarize_budget_events
from kogwistar.runtime.budget import StateBackedBudgetLedger, budget_event_to_dict

from .debug_run import LiveTracePrinter, env_flag_enabled
from .provider_config import provider_config_summary


def _now_ms() -> int:
    return int(time.time() * 1000)


def _write_json_file(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _append_trace_line(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{_now_ms()} | {message}\n")


def _dump_model(value: object) -> object:
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(field_mode="backend", dump_format="json")
        except TypeError:
            return value.model_dump()
    if isinstance(value, dict):
        return {str(key): _dump_model(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_dump_model(item) for item in value]
    return value


def _summarize_budget_events(events: list[object], *, provider_settings: WorkflowProviderSettings) -> dict[str, object]:
    provider_summary = provider_config_summary(provider_settings)
    return {
        "provider": provider_summary.get("provider"),
        "model": provider_summary.get("model"),
        "temperature": provider_summary.get("temperature"),
        "api_key_env": provider_summary.get("api_key_env"),
        "base_url": provider_summary.get("base_url"),
        "project": provider_summary.get("project"),
        "location": provider_summary.get("location"),
        "max_retries": provider_summary.get("max_retries"),
    } | summarize_budget_events(events)


def _proposal_mode_summary(final_state: dict[str, object]) -> dict[str, object]:
    current_layer_result = dict(final_state.get("current_layer_result") or {})
    metadata = dict(current_layer_result.get("metadata") or {})
    if not metadata:
        return {}
    summary: dict[str, object] = {
        "proposal_mode": metadata.get("proposal_mode"),
        "proposal_source": metadata.get("proposal_source"),
        "proposal_failure_reason": metadata.get("proposal_failure_reason"),
        "boundary_proposed_count": metadata.get("boundary_proposed_count"),
        "boundary_accepted_count": metadata.get("boundary_accepted_count"),
        "boundary_shifted_count": metadata.get("boundary_shifted_count"),
        "boundary_rejected_count": metadata.get("boundary_rejected_count"),
        "boundary_refinement_count": metadata.get("boundary_refinement_count"),
        "boundary_refinement_attempts": metadata.get("boundary_refinement_attempts"),
        "boundary_summary_count": metadata.get("boundary_summary_count"),
        "unresolved_interval_count": metadata.get("unresolved_interval_count"),
        "provider_child_count": metadata.get("provider_child_count"),
    }
    return {key: value for key, value in summary.items() if value is not None}


def _basic_sense_eval_from_graph_payload(*, graph_payload: dict[str, object], diagnostics: dict[str, object]) -> dict[str, object]:
    def _normalize_excerpt(text: object) -> str:
        return " ".join(str(text or "").split()).strip()

    nodes = list(graph_payload.get("nodes") or [])
    node_count = len(nodes)
    node_types: set[str] = set()
    excerpt_spans_by_cluster: dict[str, dict[str, list[tuple[int, int]]]] = defaultdict(lambda: defaultdict(list))
    cluster_intervals: dict[str, list[tuple[int, int]]] = defaultdict(list)
    max_depth = 0

    for node in nodes:
        metadata = dict(node.get("metadata") or {})
        node_type = str(metadata.get("semantic_node_type") or node.get("type") or "").strip()
        if node_type:
            node_types.add(node_type)
        level_from_root = metadata.get("level_from_root")
        if isinstance(level_from_root, int):
            max_depth = max(max_depth, level_from_root + 1)
        for mention in node.get("mentions") or []:
            for span in mention.get("spans") or []:
                excerpt = _normalize_excerpt(span.get("excerpt"))
                source_cluster_id = span.get("source_cluster_id")
                start_char = span.get("start_char")
                end_char = span.get("end_char")
                if source_cluster_id is None or not isinstance(start_char, int) or not isinstance(end_char, int):
                    continue
                if end_char <= start_char:
                    continue
                cluster_id = str(source_cluster_id)
                interval = (max(0, start_char), max(0, end_char))
                cluster_intervals[cluster_id].append(interval)
                if excerpt and excerpt != " ":
                    excerpt_spans_by_cluster[cluster_id][excerpt].append(interval)

    covered_total = 0
    source_total = 0
    for intervals in cluster_intervals.values():
        if not intervals:
            continue
        intervals.sort()
        merged: list[tuple[int, int]] = []
        cur_start, cur_end = intervals[0]
        for start_char, end_char in intervals[1:]:
            if start_char <= cur_end:
                cur_end = max(cur_end, end_char)
            else:
                merged.append((cur_start, cur_end))
                cur_start, cur_end = start_char, end_char
        merged.append((cur_start, cur_end))
        covered_total += sum(end_char - start_char for start_char, end_char in merged)
        source_total += max(end_char for _, end_char in merged)

    coverage_ratio = covered_total / source_total if source_total else 0.0
    duplicate_excerpt_hits = 0
    for excerpt_groups in excerpt_spans_by_cluster.values():
        for intervals in excerpt_groups.values():
            if len(intervals) <= 1:
                continue
            intervals.sort()
            merged_span_count = 0
            cur_start, cur_end = intervals[0]
            for start_char, end_char in intervals[1:]:
                if start_char <= cur_end:
                    cur_end = max(cur_end, end_char)
                else:
                    merged_span_count += 1
                    cur_start, cur_end = start_char, end_char
            merged_span_count += 1
            duplicate_excerpt_hits += max(0, len(intervals) - merged_span_count)
    page_index_diag = dict(diagnostics.get("page_index") or {})
    assignment_mode = str(page_index_diag.get("assignment_mode") or diagnostics.get("assignment_mode") or "")
    fallback_used = bool(
        page_index_diag.get("fallback_reason")
        or diagnostics.get("fallback_reason")
        or assignment_mode == "deterministic_fallback"
        or page_index_diag.get("refine_excerpts_fallback")
    )

    score = (
        min(coverage_ratio, 1.0) * 45.0
        + min(max_depth, 8) / 8.0 * 20.0
        + min(len(node_types), 6) / 6.0 * 15.0
        + min(node_count, 20) / 20.0 * 10.0
        - min(duplicate_excerpt_hits, 5) * 7.0
        - (10.0 if fallback_used else 0.0)
    )
    score = max(0.0, min(100.0, round(score, 1)))
    if score >= 70.0 and coverage_ratio >= 0.45 and duplicate_excerpt_hits == 0:
        verdict = "good"
    elif score >= 40.0:
        verdict = "mixed"
    else:
        verdict = "weak"

    return {
        "basic_sense_score": score,
        "basic_sense_verdict": verdict,
        "coverage_ratio": round(coverage_ratio, 4),
        "max_depth": max_depth,
        "node_count": node_count,
        "node_type_diversity": len(node_types),
        "duplicate_excerpt_hits": duplicate_excerpt_hits,
        "fallback_used": fallback_used,
    }


def _build_provider_layer_callbacks(
    provider_settings: WorkflowProviderSettings,
    *,
    layer_event: Callable[..., None] | None = None,
) -> dict[str, LayerwiseCallback | int | bool]:
    return build_layerwise_llm_callbacks(provider_settings, event_sink=layer_event)


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
) -> SimpleNamespace:
    from kg_doc_parser.workflow_ingest.models import WorkflowIngestInput, WorkflowExportBundle
    from kg_doc_parser.workflow_ingest.service import build_default_engines, run_ingest_workflow

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
    engine_dir = Path(engine_dir)
    _layer_event(
        "workflow_layered_provider_settings_loaded",
        provider=provider_settings.parser.provider,
        model=provider_settings.parser.model,
        proposal_mode=provider_settings.proposal_mode,
    )
    _layer_event("workflow_layered_engines_build_start", engine_dir=str(engine_dir))
    workflow_engine, conversation_engine, knowledge_engine = build_default_engines(
        engine_dir,
        provider_settings=provider_settings,
    )
    _layer_event("workflow_layered_engines_build_done")
    deps = _build_provider_layer_callbacks(provider_settings, layer_event=_layer_event)
    inp = WorkflowIngestInput.from_text(
        document_id=source_document_id,
        text=str(raw_text),
        title=str(title),
    )
    _layer_event("workflow_layered_parse_start")
    if heartbeat is not None:
        heartbeat("workflow_layered_parse_start")
    run_result, bundle = run_ingest_workflow(
        inp=inp,
        workflow_engine=workflow_engine,
        conversation_engine=conversation_engine,
        knowledge_engine=knowledge_engine,
        deps={**deps, "budget_ledger": budget_ledger},
    )
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
        from kg_doc_parser.workflow_ingest.models import WorkflowExportBundle as _WorkflowExportBundle
        from kg_doc_parser.workflow_ingest.semantics import SemanticNode, semantic_tree_to_kge_payload

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
    evaluation = _basic_sense_eval_from_graph_payload(
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


def run_longrun_parser_child(payload: dict[str, object]) -> None:
    heartbeat_path = Path(payload["heartbeat_path"])
    result_path = Path(payload["result_path"])
    failure_path = Path(payload["failure_path"])
    failure_payload_path = Path(
        str(payload.get("failure_payload_path") or failure_path.with_name("failure_payload.json"))
    )
    trace_path = Path(payload["trace_path"])
    dump_trace_path = Path(str(payload["dump_trace_path"])) if payload.get("dump_trace_path") else None
    live_trace = bool(payload.get("live_trace")) or env_flag_enabled(
        "KOGWISTAR_LONGRUN_LIVE_TRACE",
        "KOGWISTAR_LLM_WIKI_LIVE_TRACE",
        default=os.getenv("KOGWISTAR_LLM_WIKI_LONGRUN") == "1",
    )
    live_trace_printer = LiveTracePrinter(prefix="longrun.parser") if live_trace else None

    def _heartbeat(phase: str, **extra: object) -> None:
        _write_json_file(
            heartbeat_path,
            {
                "phase": phase,
                "timestamp_ms": _now_ms(),
                "parser_lane": payload["parser_lane"],
                "doc_id": payload["doc_id"],
                "pid": os.getpid(),
                **extra,
            },
        )

    def _trace(message: str) -> None:
        _append_trace_line(trace_path, message)
        if dump_trace_path is not None:
            _append_trace_line(dump_trace_path, f"child::{message}")
        if live_trace_printer is not None:
            live_trace_printer.emit(
                {
                    "stage": "parser_trace",
                    "message": f"child::{message}",
                    "doc_id": payload.get("doc_id"),
                    "parser_lane": payload.get("parser_lane"),
                }
            )

    try:
        _trace(f"child_boot doc={payload.get('doc_id')} pid={os.getpid()}")
        if payload.get("child_mode") == "sleep":
            _trace("child_sleep_mode entered")
            _heartbeat("sleeping")
            time.sleep(60)
            return
        _trace("child_loading_provider_settings")
        provider_settings = WorkflowProviderSettings.model_validate(payload["provider_settings"])
        budget_state = {
            "token_budget": int(payload.get("token_budget") or 10_000_000),
            "budget_scope": "run",
            "budget_kind": "token",
        }
        budget_ledger = StateBackedBudgetLedger(budget_state)
        usage_summary = _summarize_budget_events([], provider_settings=provider_settings)
        parser_lane = str(payload["parser_lane"])
        source_document_id = str(payload["source_document_id"])
        _trace(f"child_lane_selected lane={parser_lane} source_document_id={source_document_id}")
        _heartbeat("started")
        if parser_lane == "page_index":
            _trace("child_before_page_index_parse")
            _heartbeat("page_index_parse_start")
            _trace("child_page_index_parse_call_start")
            result = parse_page_index_document(
                document_id=source_document_id,
                title=str(payload["title"]),
                raw_text=str(payload["raw_text"]),
                source_format=str(payload["source_format"]),
                mode=str(payload["parser_mode"]),
                provider_settings=provider_settings,
                trace_log=lambda message: _trace(f"page_index::{message}"),
            )
            _trace("child_page_index_parse_call_returned")
            from kg_doc_parser.workflow_ingest.semantics import semantic_tree_to_kge_payload

            _trace("child_page_index_graph_payload_start")
            graph_payload = semantic_tree_to_kge_payload(
                result.semantic_tree,
                doc_id=source_document_id,
            )
            _trace("child_page_index_graph_payload_done")
            title = str(getattr(result.semantic_tree, "title", payload["title"]))
            evaluation = _basic_sense_eval_from_graph_payload(
                graph_payload=graph_payload,
                diagnostics={"parser_lane": "page_index", "page_index": _dump_model(result.diagnostics)},
            )
            page_index_diag = dict(_dump_model(result.diagnostics) or {})
            evaluation.update(
                {
                    "assignment_attempt_count": int(page_index_diag.get("assignment_attempt_count") or 0),
                    "assignment_retry_used": bool(page_index_diag.get("assignment_retry_used")),
                    "assignment_retry_succeeded": bool(page_index_diag.get("assignment_retry_succeeded")),
                    "structure_retry_used": bool(page_index_diag.get("structure_retry_used")),
                    "structure_retry_succeeded": bool(page_index_diag.get("structure_retry_succeeded")),
                    "retry_used": bool(page_index_diag.get("retry_used")),
                    "retry_succeeded": bool(page_index_diag.get("retry_succeeded")),
                    "assignment_mode": str(page_index_diag.get("assignment_mode") or ""),
                    "final_outcome": str(page_index_diag.get("final_outcome") or ""),
                }
            )
            diagnostics = {
                "parser_lane": "page_index",
                "page_index": _dump_model(result.diagnostics),
            }
        elif parser_lane == "workflow_layered":
            engine_dir = Path(payload["parser_run_dir"]) / "workflow_engines"
            _trace(f"child_building_workflow_engines dir={engine_dir}")
            _trace("child_before_workflow_layered_parse")
            _heartbeat("workflow_layered_parse_start")
            result = run_workflow_layered_parse(
                source_document_id=source_document_id,
                title=str(payload["title"]),
                raw_text=str(payload["raw_text"]),
                provider_settings=provider_settings,
                engine_dir=engine_dir,
                budget_ledger=budget_ledger,
                trace=_trace,
                heartbeat=_heartbeat,
            )
            _trace("child_workflow_layered_parse_call_returned")
            _heartbeat("workflow_layered_parse_complete")
            title = str(payload["title"])
            graph_payload = result.graph_payload
            evaluation = result.evaluation
            usage_summary = result.usage_summary
            diagnostics = dict(result.diagnostics)
        else:
            raise ValueError(f"unsupported long-run parser lane: {parser_lane!r}")
        _trace("child_write_result_json")
        _write_json_file(
            result_path,
            {
                "ok": True,
                "parser_lane": parser_lane,
                "title": title,
                "graph_payload": graph_payload,
                "evaluation": evaluation,
                "usage_summary": usage_summary,
                "usage_events": list(getattr(result, "usage_events", []) or []),
                "diagnostics": diagnostics,
                "layer_log": getattr(result, "layer_log", None) if parser_lane == "workflow_layered" else None,
            },
        )
        _heartbeat("result_written", result_path=str(result_path))
        if parser_lane == "workflow_layered" and getattr(result, "layer_log", None):
            _write_json_file(result_path.with_name("parser_layer_log.json"), list(result.layer_log))
        _heartbeat(
            "completed",
            result_path=str(result_path),
            node_count=len(graph_payload.get("nodes", [])),
            edge_count=len(graph_payload.get("edges", [])),
        )
        _trace("child_completed")
    except BaseException as exc:  # noqa: BLE001
        _trace(f"child_exception {type(exc).__name__}: {exc}")
        try:
            _write_json_file(failure_payload_path, dict(payload))
        except Exception as payload_exc:  # noqa: BLE001
            _trace(f"failure_payload_write_error {type(payload_exc).__name__}: {payload_exc}")
        _write_json_file(
            failure_path,
            {
                "ok": False,
                "error_type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
                "payload_path": str(failure_payload_path),
            },
        )
        _heartbeat("failed", failure_path=str(failure_path), error_type=type(exc).__name__)
        raise
