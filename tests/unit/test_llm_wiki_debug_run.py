from __future__ import annotations

import logging
import json
from types import SimpleNamespace

from kogwistar_llm_wiki import IngestPipeline, IngestPipelineRequest
from kg_doc_parser.workflow_ingest.page_index import parse_page_index_document
from kg_doc_parser.workflow_ingest.semantics import semantic_tree_to_kge_payload
from kogwistar_llm_wiki.debug_run import (
    LiveTracePrinter,
    ParseStatisticsStore,
    append_jsonl,
    build_parse_statistics_record,
    configure_debug_logging,
    env_flag_enabled,
    format_live_trace,
)


def _tree(*children):
    return SimpleNamespace(child_nodes=list(children))


def test_debug_run_helpers_write_jsonl_and_sqlite_round_trip(tmp_path):
    debug_dir = configure_debug_logging(tmp_path / "debug")
    assert debug_dir == (tmp_path / "debug").resolve()
    logging.getLogger("kogwistar_llm_wiki.test").info("debug-run logging enabled")
    assert (debug_dir / "llm_wiki.log").exists()

    trace_path = debug_dir / "run_trace.jsonl"
    append_jsonl(trace_path, {"stage": "ingest_run_start", "workspace_id": "demo"})
    line = trace_path.read_text(encoding="utf-8").strip()
    assert json.loads(line)["stage"] == "ingest_run_start"

    record = build_parse_statistics_record(
        workspace_id="demo",
        source_document_id="doc-1",
        source_uri="file:///demo.md",
        title="Demo",
        raw_text="alpha beta gamma",
        parser_lane="workflow_layered",
        parser_mode="azure_openai",
        proposal_mode="boundaries",
        provider="azure",
        model="gpt-5-nano",
        parse_runtime_ms=123,
        semantic_tree=_tree(_tree(_tree()), _tree(_tree())),
        graph_payload={"nodes": [{"id": "n1"}, {"id": "n2"}], "edges": [{"id": "e1"}]},
        diagnostics={"retry_used": True},
        evaluation={
            "basic_sense_score": 91.5,
            "basic_sense_verdict": "good",
            "coverage_ratio": 0.75,
            "fallback_used": False,
        },
        status="ok",
    )

    store = ParseStatisticsStore(debug_dir / "llm_wiki_stats.sqlite3")
    store.record_parse_run(record)
    rows = store.latest_rows(limit=1)

    assert len(rows) == 1
    row = rows[0]
    assert row["workspace_id"] == "demo"
    assert row["source_document_id"] == "doc-1"
    assert row["proposal_mode"] == "boundaries"
    assert row["provider"] == "azure"
    assert row["model"] == "gpt-5-nano"
    assert row["document_length_words"] == 3
    assert row["tree_depth"] == 3
    assert row["tree_node_count"] == 5
    assert row["tree_leaf_count"] == 2
    assert row["node_count"] == 2
    assert row["edge_count"] == 1
    assert row["fallback_used"] == 0
    assert row["retry_used"] == 1
    assert json.loads(row["details_json"])["evaluation"]["basic_sense_verdict"] == "good"


def test_append_jsonl_writes_machine_readable_lines(tmp_path):
    trace_path = tmp_path / "trace.jsonl"
    append_jsonl(trace_path, {"stage": "demo", "timestamp_ms": 123, "workspace_id": "ws-1"})
    append_jsonl(trace_path, {"stage": "next", "timestamp_ms": 456, "workspace_id": "ws-1"})

    lines = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    assert [line["stage"] for line in lines] == ["demo", "next"]
    assert all("timestamp_ms" in line for line in lines)
    assert all(line["workspace_id"] == "ws-1" for line in lines)


def test_live_trace_printer_emits_compact_console_line(capsys):
    LiveTracePrinter(prefix="demo.trace").emit(
        {
            "stage": "parse_source_start",
            "workspace_id": "demo",
            "source_document_id": "doc-1",
            "parser_lane": "workflow_layered",
        }
    )

    captured = capsys.readouterr()
    assert "[demo.trace] parse_source_start" in captured.err
    assert "workspace_id=demo" in captured.err
    assert "source_document_id=doc-1" in captured.err
    assert "parser_lane=workflow_layered" in captured.err


def test_format_live_trace_includes_runtime_node_and_payload_json_fields():
    line = format_live_trace(
        "longrun.runtime",
        {
            "type": "step_attempt_completed",
            "run_id": "run-1",
            "node_id": "parse-node",
            "step_seq": 3,
            "attempt": 2,
            "payload_json": json.dumps(
                {
                    "workflow_id": "llm_wiki.longrun_ingestion.v1.parse_first",
                    "status": "ok",
                    "duration_ms": 42,
                    "next_nodes": ["maintenance-node"],
                }
            ),
        },
    )

    assert line.startswith("[longrun.runtime] step_attempt_completed")
    assert "run_id=run-1" in line
    assert "node_id=parse-node" in line
    assert "step_seq=3" in line
    assert "attempt=2" in line
    assert "workflow_id=llm_wiki.longrun_ingestion.v1.parse_first" in line
    assert "status=ok" in line
    assert "duration_ms=42" in line
    assert 'next_nodes=["maintenance-node"]' in line


def test_env_flag_enabled_parses_live_trace_flags(monkeypatch):
    monkeypatch.setenv("KOGWISTAR_LLM_WIKI_LIVE_TRACE", "true")

    assert env_flag_enabled("KOGWISTAR_LLM_WIKI_LIVE_TRACE")


def test_parse_statistics_store_latest_rows_returns_newest_first(tmp_path):
    debug_dir = configure_debug_logging(tmp_path / "debug-order")
    store = ParseStatisticsStore(debug_dir / "llm_wiki_stats.sqlite3")
    base_kwargs = dict(
        workspace_id="demo",
        source_document_id="doc-1",
        source_uri="file:///demo.md",
        title="Demo",
        raw_text="alpha beta gamma",
        parser_lane="workflow_layered",
        parser_mode="azure_openai",
        proposal_mode="boundaries",
        provider="azure",
        model="gpt-5-mini",
        parse_runtime_ms=1,
        semantic_tree=_tree(_tree(_tree()), _tree(_tree())),
        graph_payload={"nodes": [], "edges": []},
        diagnostics={},
        evaluation={},
        status="ok",
    )

    store.record_parse_run(build_parse_statistics_record(**base_kwargs))
    store.record_parse_run(build_parse_statistics_record(**{**base_kwargs, "source_document_id": "doc-2"}))
    rows = store.latest_rows(limit=2)

    assert [row["source_document_id"] for row in rows] == ["doc-2", "doc-1"]


def test_debug_run_traces_capture_ingest_progress(namespace_engines, tmp_path):
    captured = {}

    def fake_parser(**kwargs):
        captured.update(kwargs)
        kwargs.pop("mode", None)
        kwargs.pop("llm_provider", None)
        kwargs.pop("model", None)
        kwargs.pop("provider_settings", None)
        return parse_page_index_document(mode="heuristic", **kwargs)

    pipeline = IngestPipeline(namespace_engines, parser=fake_parser, debug_run_dir=tmp_path / "debug")
    request = IngestPipelineRequest(
        workspace_id="demo",
        source_uri="file:///demo.md",
        title="Demo",
        raw_text="# Demo\n\nBody text for tracing.",
        parser_mode="heuristic",
        parser_lane="page_index",
        promotion_mode="pending",
    )

    artifacts = pipeline.run(request)
    trace_path = tmp_path / "debug" / "run_trace.jsonl"
    trace_lines = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    stages = [line["stage"] for line in trace_lines]

    assert artifacts.source_document_id
    assert "ingest_run_start" in stages
    assert "parse_source_start" in stages
    assert "parse_source_dispatch" in stages
    assert "parse_source_complete" in stages
    assert "translate_parse_result_start" in stages
    assert "translate_parse_result_complete" in stages
    assert "ingest_parse_result_start" in stages
    assert "ingest_parse_result_persisted_source" in stages
    assert "ingest_parse_result_persisted_compatibility" in stages
    assert "ingest_parse_result_complete" in stages
    assert "create_maintenance_request_start" in stages
    assert "create_maintenance_request_complete" in stages
    assert "create_candidate_link_start" in stages
    assert "create_candidate_link_complete" in stages
    assert "create_promotion_candidate_start" in stages
    assert "create_promotion_candidate_complete" in stages
    assert "parse_statistics_recorded" in stages
    assert "ingest_run_complete" in stages


def test_debug_run_live_trace_mirrors_ingest_progress(namespace_engines, tmp_path, capsys):
    def fake_parser(**kwargs):
        kwargs.pop("mode", None)
        kwargs.pop("llm_provider", None)
        kwargs.pop("model", None)
        kwargs.pop("provider_settings", None)
        return parse_page_index_document(mode="heuristic", **kwargs)

    pipeline = IngestPipeline(
        namespace_engines,
        parser=fake_parser,
        debug_run_dir=tmp_path / "debug-live",
        live_trace=True,
    )
    request = IngestPipelineRequest(
        workspace_id="demo",
        source_uri="file:///demo.md",
        title="Demo",
        raw_text="# Demo\n\nBody text for tracing.",
        parser_mode="heuristic",
        parser_lane="page_index",
        promotion_mode="pending",
    )

    pipeline.run(request)
    captured = capsys.readouterr()

    assert "[llm-wiki.ingest] ingest_run_start" in captured.err
    assert "workspace_id=demo" in captured.err
    assert "[llm-wiki.ingest] ingest_run_complete" in captured.err


def test_debug_run_trace_payloads_include_context_fields(namespace_engines, tmp_path):
    def fake_parser(**kwargs):
        kwargs.pop("mode", None)
        kwargs.pop("llm_provider", None)
        kwargs.pop("model", None)
        kwargs.pop("provider_settings", None)
        return parse_page_index_document(mode="heuristic", **kwargs)

    pipeline = IngestPipeline(namespace_engines, parser=fake_parser, debug_run_dir=tmp_path / "debug-payloads")
    request = IngestPipelineRequest(
        workspace_id="demo",
        source_uri="file:///demo.md",
        title="Demo",
        raw_text="# Demo\n\nBody text for tracing.",
        parser_mode="heuristic",
        parser_lane="page_index",
        promotion_mode="pending",
    )

    pipeline.run(request)
    trace_path = tmp_path / "debug-payloads" / "run_trace.jsonl"
    trace_lines = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    start_event = next(entry for entry in trace_lines if entry["stage"] == "ingest_run_start")
    parse_event = next(entry for entry in trace_lines if entry["stage"] == "parse_source_start")
    translate_event = next(entry for entry in trace_lines if entry["stage"] == "translate_parse_result_complete")

    assert start_event["workspace_id"] == "demo"
    assert start_event["source_document_id"]
    assert start_event["source_uri"] == "file:///demo.md"
    assert isinstance(start_event["timestamp_ms"], int)

    assert parse_event["workspace_id"] == "demo"
    assert parse_event["parser_lane"] == "page_index"
    assert parse_event["parser_mode"] == "heuristic"
    assert isinstance(parse_event["timestamp_ms"], int)

    assert translate_event["source_document_id"]
    assert translate_event["node_count"] >= 1
    assert translate_event["edge_count"] >= 0
    assert isinstance(translate_event["timestamp_ms"], int)


def test_debug_run_trace_event_order_is_progressive(namespace_engines, tmp_path):
    def fake_parser(**kwargs):
        kwargs.pop("mode", None)
        kwargs.pop("llm_provider", None)
        kwargs.pop("model", None)
        kwargs.pop("provider_settings", None)
        return parse_page_index_document(mode="heuristic", **kwargs)

    pipeline = IngestPipeline(namespace_engines, parser=fake_parser, debug_run_dir=tmp_path / "debug-ordering")
    request = IngestPipelineRequest(
        workspace_id="demo",
        source_uri="file:///demo.md",
        title="Demo",
        raw_text="# Demo\n\nBody text for tracing.",
        parser_mode="heuristic",
        parser_lane="page_index",
        promotion_mode="pending",
    )

    pipeline.run(request)
    trace_path = tmp_path / "debug-ordering" / "run_trace.jsonl"
    stages = [json.loads(line)["stage"] for line in trace_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    expected_sequence = [
        "ingest_run_start",
        "parse_source_start",
        "parse_source_dispatch",
        "parse_source_complete",
        "translate_parse_result_start",
        "translate_parse_result_complete",
        "ingest_parse_result_start",
        "ingest_parse_result_persisted_source",
        "ingest_parse_result_persisted_compatibility",
        "ingest_parse_result_complete",
        "create_maintenance_request_start",
        "create_maintenance_request_complete",
        "create_candidate_link_start",
        "create_candidate_link_complete",
        "create_promotion_candidate_start",
        "create_promotion_candidate_complete",
        "ingest_run_complete",
    ]

    last_index = -1
    for stage in expected_sequence:
        next_index = stages.index(stage, last_index + 1)
        assert next_index > last_index
        last_index = next_index


def test_debug_run_maintenance_first_skips_parse_and_still_traces_run(namespace_engines, tmp_path):
    called = {"count": 0}

    def fake_parser(**kwargs):
        called["count"] += 1
        raise AssertionError("maintenance_first should not call the parser")

    pipeline = IngestPipeline(namespace_engines, parser=fake_parser, debug_run_dir=tmp_path / "debug-maintenance")
    request = IngestPipelineRequest(
        workspace_id="demo",
        source_uri="file:///demo.md",
        title="Demo",
        raw_text="# Demo\n\nBody text for tracing.",
        parser_mode="heuristic",
        parser_lane="page_index",
        operation_mode="maintenance_first",
        promotion_mode="pending",
    )

    artifacts = pipeline.run(request)
    trace_path = tmp_path / "debug-maintenance" / "run_trace.jsonl"
    trace_lines = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    stages = [line["stage"] for line in trace_lines]

    assert called["count"] == 0
    assert artifacts.graph_status == "seeded"
    assert "ingest_run_start" in stages
    assert "ingest_run_complete" in stages
    assert "parse_statistics_recorded" not in stages


def test_debug_run_records_workflow_layered_progress_stages(namespace_engines, tmp_path, monkeypatch):
    def fake_run_workflow_layered_parse(**kwargs):
        return SimpleNamespace(
            semantic_tree=SimpleNamespace(title=kwargs["title"]),
            graph_payload={"nodes": [], "edges": []},
            evaluation={"basic_sense_score": 88.0, "basic_sense_verdict": "good", "coverage_ratio": 0.9},
            diagnostics={"parser_lane": "workflow_layered", "parse_session_mode": "workflow_layered"},
            usage_summary={"provider": "azure", "model": "gpt-5-mini", "total_cost": 0.0},
            layer_log=[{"stage": "workflow_layered_parse_start"}],
            parse_session={"mode": "workflow_layered"},
        )

    monkeypatch.setattr("kogwistar_llm_wiki.ingest_pipeline.run_workflow_layered_parse", fake_run_workflow_layered_parse)

    pipeline = IngestPipeline(namespace_engines, debug_run_dir=tmp_path / "debug-workflow")
    request = IngestPipelineRequest(
        workspace_id="demo",
        source_uri="file:///demo.md",
        title="Demo",
        raw_text="# Demo\n\nBody text for tracing.",
        parser_mode="azure_openai",
        parser_lane="workflow_layered",
        llm_provider="azure_openai",
        llm_model="gpt-5-mini",
        promotion_mode="pending",
    )

    result = pipeline.parse_source(request=request, source_document_id="doc-1")
    trace_path = tmp_path / "debug-workflow" / "run_trace.jsonl"
    trace_lines = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    stages = [line["stage"] for line in trace_lines]

    assert result.semantic_tree.title == "Demo"
    assert "parse_source_start" in stages
    assert "workflow_layered_parse_start" in stages
    assert "workflow_layered_parse_complete" in stages
    assert "parse_source_complete" in stages


def test_debug_run_records_workflow_layered_stats_content(namespace_engines, tmp_path, monkeypatch):
    def fake_run_workflow_layered_parse(**kwargs):
        heuristic_result = parse_page_index_document(
            document_id=kwargs["source_document_id"],
            title=kwargs["title"],
            raw_text="# Demo\n\nBody text for tracing.",
            source_format="text",
            mode="heuristic",
        )
        return SimpleNamespace(
            semantic_tree=SimpleNamespace(title=kwargs["title"]),
            graph_payload=semantic_tree_to_kge_payload(heuristic_result.semantic_tree, doc_id=kwargs["source_document_id"]),
            evaluation={
                "basic_sense_score": 91.0,
                "basic_sense_verdict": "good",
                "coverage_ratio": 0.67,
                "fallback_used": False,
            },
            diagnostics={
                "parser_lane": "workflow_layered",
                "parse_session_mode": "workflow_layered",
                "proposal_summary": {
                    "proposal_mode": "boundaries",
                    "proposal_source": "llm",
                    "boundary_proposed_count": 3,
                },
            },
            usage_summary={"provider": "azure", "model": "gpt-5-mini", "total_cost": 0.0},
            layer_log=[{"stage": "workflow_layered_parse_start"}],
            parse_session={"mode": "workflow_layered"},
        )

    monkeypatch.setattr("kogwistar_llm_wiki.ingest_pipeline.run_workflow_layered_parse", fake_run_workflow_layered_parse)

    pipeline = IngestPipeline(namespace_engines, debug_run_dir=tmp_path / "debug-workflow-stats")
    request = IngestPipelineRequest(
        workspace_id="demo",
        source_uri="file:///demo.md",
        title="Demo",
        raw_text="# Demo\n\nBody text for tracing.",
        parser_mode="azure_openai",
        parser_lane="workflow_layered",
        llm_provider="azure_openai",
        llm_model="gpt-5-mini",
        promotion_mode="pending",
    )

    pipeline.run(request)
    stats_path = tmp_path / "debug-workflow-stats" / "llm_wiki_stats.sqlite3"
    rows = ParseStatisticsStore(stats_path).latest_rows(limit=1)

    assert len(rows) == 1
    row = rows[0]
    assert row["workspace_id"] == "demo"
    assert row["parser_lane"] == "workflow_layered"
    assert row["parser_mode"] == "azure_openai"
    assert row["proposal_mode"] == "boundaries"
    assert row["provider"] == "azure"
    assert row["model"] == "gpt-5-mini"
    assert row["node_count"] >= 2
    assert row["edge_count"] >= 1
    assert row["basic_sense_verdict"] == "good"
    assert row["coverage_ratio"] == 0.67
