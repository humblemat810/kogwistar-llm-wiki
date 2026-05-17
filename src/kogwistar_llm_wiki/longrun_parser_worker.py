from __future__ import annotations

import json
import os
import re
import time
import traceback
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from kg_doc_parser.workflow_ingest.page_index import parse_page_index_document
from kg_doc_parser.workflow_ingest.providers import WorkflowProviderSettings, build_chat_model_for_role


def _now_ms() -> int:
    return int(time.time() * 1000)


def _write_json_file(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _append_trace_line(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{_now_ms()} | {message}\n")


def _dump_model(value: Any) -> Any:
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


def _basic_sense_eval_from_graph_payload(*, graph_payload: dict[str, Any], diagnostics: dict[str, Any]) -> dict[str, Any]:
    def _normalize_excerpt(text: Any) -> str:
        return " ".join(str(text or "").split()).strip()

    nodes = list(graph_payload.get("nodes") or [])
    node_count = len(nodes)
    node_types: set[str] = set()
    excerpt_counts: Counter[str] = Counter()
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
                if excerpt and excerpt != " ":
                    excerpt_counts[excerpt] += 1
                source_cluster_id = span.get("source_cluster_id")
                start_char = span.get("start_char")
                end_char = span.get("end_char")
                if source_cluster_id is None or not isinstance(start_char, int) or not isinstance(end_char, int):
                    continue
                if end_char <= start_char:
                    continue
                cluster_intervals[str(source_cluster_id)].append((max(0, start_char), max(0, end_char)))

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
    duplicate_excerpt_hits = sum(count - 1 for count in excerpt_counts.values() if count > 1)
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


def _structured_invoke(model: Any, schema: Any, messages: list[tuple[str, str]]) -> Any:
    structured = model.with_structured_output(schema, include_raw=True)
    response = structured.invoke(messages)
    if isinstance(response, dict):
        parsed = response.get("parsed")
        if parsed is not None:
            return parsed
        if response.get("parsing_error") is not None:
            raise ValueError(str(response["parsing_error"]))
    return response


def _fallback_layer_result(
    *,
    current_layer_context: Any,
    parser_source_map: dict[str, dict[str, Any]],
) -> Any:
    from kg_doc_parser.workflow_ingest.models import CurrentLayerResult, LayerChildCandidate
    from kg_doc_parser.workflow_ingest.semantics import HydratedTextPointer

    children: list[Any] = []
    if int(getattr(current_layer_context, "depth", 0)) > 0:
        return CurrentLayerResult(
            children=[],
            satisfied=True,
            reasoning_history=[{"source": "deterministic_depth_stop"}],
            metadata={"fallback": "depth_stop"},
        )
    parent_ids = list(getattr(current_layer_context, "parent_node_ids", []) or [])
    parent_id = parent_ids[0] if parent_ids else "root"
    records = list(parser_source_map.items())
    for index, (source_cluster_id, record) in enumerate(records[:8], start=1):
        text = str(record.get("text") or "").strip()
        if not text:
            continue
        title_match = re.search(r"(?m)^#{1,3}\s+(.+)$", text)
        title = title_match.group(1).strip() if title_match else f"Section {index}"
        excerpt = text
        children.append(
            LayerChildCandidate(
                node_id=f"{parent_id}|section-{index}",
                parent_node_id=parent_id,
                title=title[:160],
                node_type="TEXT_FLOW",
                total_content_pointers=[
                    HydratedTextPointer(
                        source_cluster_id=str(source_cluster_id),
                        start_char=0,
                        end_char=max(len(excerpt) - 1, 0),
                        verbatim_text=excerpt or " ",
                    )
                ],
                expandable=False,
                metadata={"source": "workflow_layered_fallback"},
            )
        )
    return CurrentLayerResult(
        children=children,
        satisfied=True,
        reasoning_history=[{"source": "deterministic_fallback"}],
        metadata={"fallback": "llm_empty_or_unavailable"},
    )


def _build_provider_layer_callbacks(provider_settings: WorkflowProviderSettings) -> dict[str, Any]:
    from kg_doc_parser.workflow_ingest.models import CurrentLayerResult, CurrentLayerReview

    chat_model = build_chat_model_for_role("parser", provider_settings)

    def _source_excerpt(parser_source_map: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        excerpt: dict[str, dict[str, Any]] = {}
        for key, record in list(parser_source_map.items())[:12]:
            text = str(record.get("text") or "")
            excerpt[str(key)] = {
                "page_number": record.get("page_number"),
                "cluster_number": record.get("cluster_number"),
                "text": text[:1800],
            }
        return excerpt

    def _propose_layer_fn(
        *,
        parser_source_map,
        current_layer_context,
        semantic_tree,
        split_strategy,
        **kwargs,
    ):
        messages = [
            (
                "system",
                "You split document text into grounded semantic child nodes. "
                "Return only structured data matching the schema. Each child must use exact "
                "source_cluster_id values and character spans from the supplied source map.",
            ),
            (
                "human",
                json.dumps(
                    {
                        "task": "Propose the next semantic layer for these parent nodes.",
                        "split_strategy": split_strategy,
                        "current_layer_context": _dump_model(current_layer_context),
                        "semantic_tree": _dump_model(semantic_tree),
                        "source_map_excerpt": _source_excerpt(parser_source_map),
                        "requirements": [
                            "Prefer a small set of meaningful sections.",
                            "Use no children and satisfied=true only when the parent is already atomic.",
                            "Set expandable=false for leaf sections.",
                        ],
                    },
                    sort_keys=True,
                ),
            ),
        ]
        try:
            result = _structured_invoke(chat_model, CurrentLayerResult, messages)
            parsed = (
                result
                if isinstance(result, CurrentLayerResult)
                else CurrentLayerResult.model_validate(result)
            )
            fallback = _fallback_layer_result(
                current_layer_context=current_layer_context,
                parser_source_map=parser_source_map,
            )
            fallback.reasoning_history.append(
                {
                    "source": "provider_structured_proposal",
                    "provider_child_count": len(parsed.children),
                }
            )
            return fallback
        except Exception:
            pass
        return _fallback_layer_result(
            current_layer_context=current_layer_context,
            parser_source_map=parser_source_map,
        )

    def _review_layer_fn(*, current_layer_context, current_layer_result, split_strategy, **kwargs):
        messages = [
            (
                "system",
                "You review a proposed semantic layer for source-grounded coverage. "
                "Return structured review data matching the schema.",
            ),
            (
                "human",
                json.dumps(
                    {
                        "task": "Review the current layer proposal.",
                        "split_strategy": split_strategy,
                        "current_layer_context": _dump_model(current_layer_context),
                        "current_layer_result": _dump_model(current_layer_result),
                    },
                    sort_keys=True,
                ),
            ),
        ]
        try:
            result = _structured_invoke(chat_model, CurrentLayerReview, messages)
            return (
                result
                if isinstance(result, CurrentLayerReview)
                else CurrentLayerReview.model_validate(result)
            )
        except Exception:
            return CurrentLayerReview(
                updated_result=current_layer_result,
                coverage_ok=True,
                satisfied=True,
                strategy_used=split_strategy,
                review_notes=["deterministic review fallback after provider failure"],
            )

    return {
        "propose_layer_fn": _propose_layer_fn,
        "review_layer_fn": _review_layer_fn,
        "max_depth": 2,
        "allow_review": True,
    }


def run_longrun_parser_child(payload: dict[str, Any]) -> None:
    heartbeat_path = Path(payload["heartbeat_path"])
    result_path = Path(payload["result_path"])
    failure_path = Path(payload["failure_path"])
    trace_path = Path(payload["trace_path"])

    def _heartbeat(phase: str, **extra: Any) -> None:
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

    try:
        _trace(f"child_boot doc={payload.get('doc_id')} pid={os.getpid()}")
        if payload.get("child_mode") == "sleep":
            _trace("child_sleep_mode entered")
            _heartbeat("sleeping")
            time.sleep(60)
            return
        _trace("child_loading_provider_settings")
        provider_settings = WorkflowProviderSettings.model_validate(payload["provider_settings"])
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
            diagnostics = {
                "parser_lane": "page_index",
                "page_index": _dump_model(result.diagnostics),
            }
        elif parser_lane == "workflow_layered":
            from kg_doc_parser.workflow_ingest.models import WorkflowIngestInput
            from kg_doc_parser.workflow_ingest.service import build_default_engines, run_ingest_workflow

            engine_dir = Path(payload["parser_run_dir"]) / "workflow_engines"
            _trace(f"child_building_workflow_engines dir={engine_dir}")
            workflow_engine, conversation_engine, knowledge_engine = build_default_engines(
                engine_dir,
                provider_settings=provider_settings,
            )
            deps = _build_provider_layer_callbacks(provider_settings)
            inp = WorkflowIngestInput.from_text(
                document_id=source_document_id,
                text=str(payload["raw_text"]),
                title=str(payload["title"]),
            )
            _trace("child_before_workflow_layered_parse")
            _heartbeat("workflow_layered_parse_start")
            _trace("child_workflow_layered_parse_call_start")
            run_result, bundle = run_ingest_workflow(
                inp=inp,
                workflow_engine=workflow_engine,
                conversation_engine=conversation_engine,
                knowledge_engine=knowledge_engine,
                deps=deps,
            )
            _trace("child_workflow_layered_parse_call_returned")
            final_state = dict(getattr(run_result, "final_state", {}) or {})
            parse_session = final_state.get("parse_session") or {}
            if not bundle and final_state.get("export_bundle"):
                from kg_doc_parser.workflow_ingest.models import WorkflowExportBundle

                bundle = WorkflowExportBundle.model_validate(final_state["export_bundle"])
            if not bundle:
                raise RuntimeError("workflow-layered parser completed without an export bundle")
            graph_payload = _dump_model(bundle.graph_payload)
            title = str(payload["title"])
            evaluation = _basic_sense_eval_from_graph_payload(
                graph_payload=graph_payload,
                diagnostics={
                    "parse_session_mode": parse_session.get("mode"),
                    "workflow_status": getattr(run_result, "status", None),
                    "workflow_run_id": getattr(run_result, "run_id", None),
                },
            )
            diagnostics = {
                "parse_session_mode": parse_session.get("mode"),
                "workflow_status": getattr(run_result, "status", None),
                "workflow_run_id": getattr(run_result, "run_id", None),
            }
            if diagnostics["parse_session_mode"] != "workflow_layered":
                raise RuntimeError(
                    "workflow-layered parser did not run in workflow_layered mode; "
                    f"got {diagnostics['parse_session_mode']!r}"
                )
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
                "diagnostics": diagnostics,
            },
        )
        _heartbeat(
            "completed",
            result_path=str(result_path),
            node_count=len(graph_payload.get("nodes", [])),
            edge_count=len(graph_payload.get("edges", [])),
        )
        _trace("child_completed")
    except BaseException as exc:  # noqa: BLE001
        _trace(f"child_exception {type(exc).__name__}: {exc}")
        _write_json_file(
            failure_path,
            {
                "ok": False,
                "error_type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            },
        )
        _heartbeat("failed", failure_path=str(failure_path), error_type=type(exc).__name__)
        raise
