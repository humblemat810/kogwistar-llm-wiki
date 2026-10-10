"""Long-run parser child-process entrypoint."""

from __future__ import annotations

import multiprocessing
import os
import threading
import time
import traceback
from pathlib import Path
from typing import Literal, Protocol, cast

from kg_doc_parser.workflow_ingest.page_index import (
    PageIndexMode,
    PageIndexParseResult,
    PageIndexSourceFormat,
    parse_page_index_document,
)
from kg_doc_parser.workflow_ingest.providers import WorkflowProviderSettings
from kogwistar.json_types import JsonValue
from kogwistar.runtime.budget import (
    BudgetEvent,
    StateBackedBudgetLedger,
    budget_event_to_dict,
)

from ..diagnostics.debug_helpers import LiveTracePrinter, env_flag_enabled
from ..ingest.contracts import TraceLog
from ..parsing.layered_workflow import LayeredParseResult
from ..parsing.longrun_support import append_trace_line as _append_trace_line
from ..parsing.longrun_support import dump_model as _dump_model
from ..parsing.longrun_support import now_ms as _now_ms
from ..parsing.longrun_support import object_mapping as _object_mapping
from ..parsing.longrun_support import payload_int as _payload_int
from ..parsing.longrun_support import payload_path as _payload_path
from ..parsing.longrun_support import write_json_file as _write_json_file
from ..parsing.parse_quality import (
    basic_sense_eval_from_graph_payload as _basic_sense_eval_from_graph_payload,
)
from ..usage.provider import (
    ProviderUsageCallback,
    provider_call_count,
    resolve_token_pricing,
)


class WorkflowParser(Protocol):
    """Typed parser boundary used by the isolated long-run child."""

    def __call__(
        self,
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
    ) -> LayeredParseResult: ...


class BudgetSummaryBuilder(Protocol):
    """Summarize serialized budget events for the child-process result."""

    def __call__(
        self,
        events: list[BudgetEvent],
        *,
        provider_settings: WorkflowProviderSettings,
    ) -> dict[str, object]: ...


def run_longrun_parser_child(
    payload: dict[str, object],
    *,
    workflow_parser: WorkflowParser,
    summarize_budget_events: BudgetSummaryBuilder,
) -> None:
    heartbeat_path = _payload_path(payload, "heartbeat_path")
    result_path = _payload_path(payload, "result_path")
    failure_path = _payload_path(payload, "failure_path")
    failure_payload_path = Path(
        str(payload.get("failure_payload_path") or failure_path.with_name("failure_payload.json"))
    )
    trace_path = _payload_path(payload, "trace_path")
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
                cast(dict[str, JsonValue], {
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
                })
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
            "token_budget": _payload_int(payload, "token_budget", default=10_000_000),
            "budget_scope": "run",
            "budget_kind": "token",
        }
        budget_ledger = StateBackedBudgetLedger(budget_state)
        usage_summary = summarize_budget_events([], provider_settings=provider_settings)
        parser_lane = str(payload["parser_lane"])
        source_document_id = str(payload["source_document_id"])
        layered_result: LayeredParseResult | None = None
        _trace(f"child_lane_selected lane={parser_lane} source_document_id={source_document_id}")
        _heartbeat("started")
        if parser_lane == "page_index":
            _trace("child_before_page_index_parse")
            _heartbeat("page_index_parse_start")
            _trace("child_page_index_parse_call_start")
            page_index_result: PageIndexParseResult = parse_page_index_document(
                document_id=source_document_id,
                title=str(payload["title"]),
                raw_text=str(payload["raw_text"]),
                source_format=cast(PageIndexSourceFormat, str(payload["source_format"])),
                mode=cast(PageIndexMode, str(payload["parser_mode"])),
                provider_settings=provider_settings,
                callbacks=[
                    ProviderUsageCallback(
                        ledger=budget_ledger,
                        run_id=str(payload.get("parser_workflow_run_id") or f"parser:{source_document_id}"),
                        source_document_id=source_document_id,
                        provider=provider_settings.parser.provider,
                        model=provider_settings.parser.model,
                        pricing=resolve_token_pricing(
                            provider=provider_settings.parser.provider,
                            model=provider_settings.parser.model,
                        ),
                    )
                ],
                trace_log=lambda message: _trace(f"page_index::{message}"),
            )
            _trace("child_page_index_parse_call_returned")
            from kg_doc_parser.workflow_ingest.semantics import (
                semantic_tree_to_kge_payload,
            )

            _trace("child_page_index_graph_payload_start")
            graph_payload = semantic_tree_to_kge_payload(
                page_index_result.semantic_tree,
                doc_id=source_document_id,
            )
            _trace("child_page_index_graph_payload_done")
            title = str(getattr(page_index_result.semantic_tree, "title", payload["title"]))
            evaluation = _basic_sense_eval_from_graph_payload(
                graph_payload=cast(dict[str, object], graph_payload),
                diagnostics={
                    "parser_lane": "page_index",
                    "page_index": _dump_model(page_index_result.diagnostics),
                },
            )
            page_index_diag = _object_mapping(_dump_model(page_index_result.diagnostics))
            evaluation.update(
                {
                    "assignment_attempt_count": _payload_int(
                        page_index_diag, "assignment_attempt_count", default=0
                    ),
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
                "page_index": _dump_model(page_index_result.diagnostics),
            }
        elif parser_lane == "workflow_layered":
            engine_dir = _payload_path(payload, "parser_run_dir") / "workflow_engines"
            _trace(f"child_building_workflow_engines dir={engine_dir}")
            _trace("child_before_workflow_layered_parse")
            _heartbeat("workflow_layered_parse_start")
            raw_conversation_mode = str(
                payload.get("conversation_persistence_mode") or "single_stage"
            )
            conversation_mode = cast(
                Literal["single_stage", "two_stage"],
                raw_conversation_mode
                if raw_conversation_mode in {"single_stage", "two_stage"}
                else "single_stage",
            )
            layered_result = workflow_parser(
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
                conversation_persistence_mode=conversation_mode,
            )
            _trace("child_workflow_layered_parse_call_returned")
            _heartbeat("workflow_layered_parse_complete")
            title = str(payload["title"])
            graph_payload = layered_result.graph_payload
            evaluation = layered_result.evaluation
            usage_summary = layered_result.usage_summary
            diagnostics = dict(layered_result.diagnostics)
        else:
            raise ValueError(f"unsupported long-run parser lane: {parser_lane!r}")
        if parser_lane == "page_index":
            usage_summary = summarize_budget_events(
                budget_ledger.events,
                provider_settings=provider_settings,
            )
            usage_summary["llm_call_count"] = provider_call_count(budget_ledger.events)
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
                "usage_events": (
                    [budget_event_to_dict(event) for event in budget_ledger.events]
                    if parser_lane == "page_index"
                    else list(layered_result.usage_events if layered_result is not None else [])
                ),
                "diagnostics": diagnostics,
                "workflow_status": workflow_status or None,
                "layer_log": layered_result.layer_log if layered_result is not None else None,
            },
        )
        _heartbeat("result_written", result_path=str(result_path))
        if layered_result is not None and layered_result.layer_log:
            _write_json_file(result_path.with_name("parser_layer_log.json"), layered_result.layer_log)
        node_value = graph_payload.get("nodes")
        edge_value = graph_payload.get("edges")
        _heartbeat(
            "completed",
            result_path=str(result_path),
            node_count=len(node_value) if isinstance(node_value, list) else 0,
            edge_count=len(edge_value) if isinstance(edge_value, list) else 0,
        )
        _trace("child_completed")
    except BaseException as exc:
        _trace(f"child_exception {type(exc).__name__}: {exc}")
        try:
            _write_json_file(failure_payload_path, dict(payload))
        except Exception as payload_exc:
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
