"""Long-run parser child-process entrypoint."""

from __future__ import annotations

import multiprocessing
import os
import threading
import time
import traceback
from collections.abc import Callable
from pathlib import Path

from kg_doc_parser.workflow_ingest.page_index import parse_page_index_document
from kg_doc_parser.workflow_ingest.providers import WorkflowProviderSettings
from kogwistar.runtime.budget import StateBackedBudgetLedger, budget_event_to_dict

from ..debug_run import LiveTracePrinter, env_flag_enabled
from ..parsing.longrun_support import append_trace_line as _append_trace_line
from ..parsing.longrun_support import dump_model as _dump_model
from ..parsing.longrun_support import now_ms as _now_ms
from ..parsing.longrun_support import write_json_file as _write_json_file
from ..parsing.parse_quality import (
    basic_sense_eval_from_graph_payload as _basic_sense_eval_from_graph_payload,
)


def run_longrun_parser_child(
    payload: dict[str, object],
    *,
    workflow_parser: Callable[..., object],
    summarize_budget_events: Callable[..., dict[str, object]],
) -> None:
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
    budget_ledger: StateBackedBudgetLedger | None = None
    trace_context = (
        f"doc={payload.get('doc_id')} "
        f"parser_lane={payload.get('parser_lane')} "
        f"provider={payload.get('parser_provider') or 'unknown'} "
        f"model={payload.get('parser_model') or 'unknown'} "
        f"parser_workflow_run_id={payload.get('parser_workflow_run_id') or 'unknown'}"
    )

    def _heartbeat(phase: str, **extra: object) -> None:
        _write_json_file(
            heartbeat_path,
            {
                "phase": phase,
                "timestamp_ms": _now_ms(),
                "parser_lane": payload["parser_lane"],
                "doc_id": payload["doc_id"],
                "parser_provider": payload.get("parser_provider"),
                "parser_model": payload.get("parser_model"),
                "parser_workflow_run_id": payload.get("parser_workflow_run_id"),
                "pid": os.getpid(),
                **extra,
            },
        )

    def _trace(message: str) -> None:
        contextual_message = f"{message} {trace_context}"
        _append_trace_line(trace_path, contextual_message)
        if dump_trace_path is not None:
            _append_trace_line(dump_trace_path, f"child::{contextual_message}")
        if live_trace_printer is not None:
            live_trace_printer.emit(
                {
                    "stage": "parser_trace",
                    "message": f"child::{contextual_message}",
                    "doc_id": payload.get("doc_id"),
                    "parser_lane": payload.get("parser_lane"),
                    "parser_provider": payload.get("parser_provider"),
                    "parser_model": payload.get("parser_model"),
                    "parser_workflow_run_id": payload.get("parser_workflow_run_id"),
                    "pid": os.getpid(),
                    "process_name": multiprocessing.current_process().name,
                    "thread_name": threading.current_thread().name,
                }
            )

    try:
        _trace(
            f"child_boot doc={payload.get('doc_id')} pid={os.getpid()} "
            f"process_name={multiprocessing.current_process().name} "
            f"parent_pid={os.getppid()} thread={threading.current_thread().name}"
        )
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
        usage_summary = summarize_budget_events([], provider_settings=provider_settings)
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
            from kg_doc_parser.workflow_ingest.semantics import (
                semantic_tree_to_kge_payload,
            )

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
            result = workflow_parser(
                source_document_id=source_document_id,
                title=str(payload["title"]),
                raw_text=str(payload["raw_text"]),
                provider_settings=provider_settings,
                engine_dir=engine_dir,
                budget_ledger=budget_ledger,
                trace=_trace,
                heartbeat=_heartbeat,
                run_id=str(payload.get("parser_workflow_run_id") or f"parser:{source_document_id}"),
                resume_from_checkpoint=bool(payload.get("resume_from_checkpoint")),
                usage_event_path=(
                    Path(str(payload["usage_event_path"]))
                    if payload.get("usage_event_path")
                    else None
                ),
                conversation_persistence_mode=str(
                    payload.get("conversation_persistence_mode") or "single_stage"
                ),
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
        workflow_status = str(diagnostics.get("workflow_status") or "").strip().lower()
        result_ok = workflow_status not in {"failure", "failed", "error"}
        _trace("child_write_result_json")
        _write_json_file(
            result_path,
            {
                "ok": result_ok,
                "parser_workflow_run_id": str(
                    payload.get("parser_workflow_run_id") or f"parser:{source_document_id}"
                ),
                "parser_lane": parser_lane,
                "title": title,
                "graph_payload": graph_payload,
                "evaluation": evaluation,
                "usage_summary": usage_summary,
                "usage_events": list(getattr(result, "usage_events", []) or []),
                "diagnostics": diagnostics,
                "workflow_status": workflow_status or None,
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
    except BaseException as exc:
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
                "usage_events": [
                    budget_event_to_dict(event) for event in (budget_ledger.events if budget_ledger else [])
                ],
                "llm_call_count": len(
                    {
                        str(event.meta.get("provider_run_id"))
                        for event in (budget_ledger.events if budget_ledger else [])
                        if event.meta.get("provider_run_id")
                    }
                ),
            },
        )
        _heartbeat("failed", failure_path=str(failure_path), error_type=type(exc).__name__)
        raise
