from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from pathlib import Path

import pytest

from kg_doc_parser.workflow_ingest.providers import ProviderEndpointConfig, WorkflowProviderSettings
from kg_doc_parser.workflow_ingest import service as workflow_service
from kg_doc_parser.workflow_ingest.semantics import SemanticNode
from kogwistar_llm_wiki import longrun_parser_worker as worker


def test_run_workflow_layered_parse_wires_event_sink_into_parser_callbacks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    captured: dict[str, Any] = {}

    def fake_build_layerwise_llm_callbacks(provider_settings, *, event_sink=None, **kwargs):
        captured["provider_settings"] = provider_settings
        captured["event_sink"] = event_sink
        return {
            "propose_layer_fn": lambda **kw: SimpleNamespace(
                children=[],
                satisfied=True,
                reasoning_history=[],
                metadata={"proposal_source": "llm"},
            ),
            "review_layer_fn": lambda **kw: SimpleNamespace(
                updated_result=kw["current_layer_result"],
                coverage_ok=True,
                satisfied=True,
                strategy_used=kw["split_strategy"],
                review_notes=[],
            ),
            "max_depth": 2,
            "allow_review": True,
        }

    def fake_build_default_engines(engine_dir, *, provider_settings):
        captured["engine_dir"] = str(engine_dir)
        return SimpleNamespace(), SimpleNamespace(), SimpleNamespace()

    def fake_run_ingest_workflow(**kwargs):
        captured["workflow_deps"] = kwargs["deps"]
        captured["event_sink"]("wiring_test", payload="ok")
        return (
            SimpleNamespace(
                status="succeeded",
                run_id="run-1",
                final_state={
                    "parse_session": {"mode": "workflow_layered"},
                    "current_layer_result": {
                        "metadata": {
                            "proposal_mode": "boundaries",
                            "boundary_proposed_count": 2,
                            "boundary_accepted_count": 1,
                            "boundary_shifted_count": 1,
                            "boundary_rejected_count": 0,
                            "boundary_refinement_count": 0,
                            "boundary_refinement_attempts": 0,
                            "boundary_summary_count": 1,
                            "unresolved_interval_count": 0,
                        }
                    },
                },
            ),
            SimpleNamespace(graph_payload={"nodes": [], "edges": []}),
        )

    monkeypatch.setattr(worker, "build_layerwise_llm_callbacks", fake_build_layerwise_llm_callbacks)
    monkeypatch.setattr(workflow_service, "build_default_engines", fake_build_default_engines)
    monkeypatch.setattr(workflow_service, "run_ingest_workflow", fake_run_ingest_workflow)

    result = worker.run_workflow_layered_parse(
        source_document_id="doc-1",
        title="Demo",
        raw_text="Alpha",
        provider_settings=WorkflowProviderSettings(
            parser=ProviderEndpointConfig(provider="fake", model="fake-model"),
        ),
        engine_dir=tmp_path / "engine",
    )

    assert captured["provider_settings"].parser.model == "fake-model"
    assert callable(captured["event_sink"])
    assert captured["workflow_deps"]["propose_layer_fn"] is not None
    assert any(entry["stage"] == "wiring_test" for entry in result.layer_log)
    assert any(entry["stage"] == "workflow_layered_parse_summary_ready" for entry in result.layer_log)
    assert any(entry["stage"] == "workflow_layered_evaluation_ready" for entry in result.layer_log)
    assert any(entry["stage"] == "workflow_layered_usage_summary_ready" for entry in result.layer_log)
    assert any(entry["stage"] == "workflow_layered_parse_complete" for entry in result.layer_log)
    assert result.diagnostics["parse_session_mode"] == "workflow_layered"
    assert result.usage_summary["proposal_mode"] == "boundaries"
    assert result.diagnostics["proposal_summary"]["boundary_proposed_count"] == 2


def test_run_workflow_layered_parse_synthesizes_missing_export_bundle_from_semantic_tree(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    captured: dict[str, Any] = {}

    def fake_build_layerwise_llm_callbacks(provider_settings, *, event_sink=None, **kwargs):
        captured["provider_settings"] = provider_settings
        return {
            "propose_layer_fn": lambda **kw: SimpleNamespace(
                children=[],
                satisfied=True,
                reasoning_history=[],
                metadata={"proposal_source": "llm"},
            ),
            "review_layer_fn": lambda **kw: SimpleNamespace(
                updated_result=kw["current_layer_result"],
                coverage_ok=True,
                satisfied=True,
                strategy_used=kw["split_strategy"],
                review_notes=[],
            ),
            "max_depth": 2,
            "allow_review": True,
        }

    def fake_build_default_engines(engine_dir, *, provider_settings):
        return SimpleNamespace(), SimpleNamespace(), SimpleNamespace()

    def fake_run_ingest_workflow(**kwargs):
        tree = SemanticNode(
            title="Root",
            node_type="DOCUMENT_ROOT",
            total_content_pointers=[],
            child_nodes=[],
            metadata={"source": "synthetic_regression"},
        )
        return (
            SimpleNamespace(
                status="failure",
                run_id="run-1",
                final_state={
                    "parse_session": {"mode": "workflow_layered"},
                    "semantic_tree": tree.model_dump(),
                    "validation_report": {"overall_text_coverage": 0.0},
                    "workflow_errors": [{"kind": "validation", "reason": "coverage"}],
                },
            ),
            None,
        )

    monkeypatch.setattr(worker, "build_layerwise_llm_callbacks", fake_build_layerwise_llm_callbacks)
    monkeypatch.setattr(workflow_service, "build_default_engines", fake_build_default_engines)
    monkeypatch.setattr(workflow_service, "run_ingest_workflow", fake_run_ingest_workflow)

    result = worker.run_workflow_layered_parse(
        source_document_id="doc-2",
        title="Demo",
        raw_text="Alpha",
        provider_settings=WorkflowProviderSettings(
            parser=ProviderEndpointConfig(provider="fake", model="fake-model"),
        ),
        engine_dir=tmp_path / "engine",
    )

    assert result.workflow_status == "failure"
    assert result.graph_payload["nodes"]
    assert any(entry["stage"] == "workflow_layered_postparse_state_snapshot" for entry in result.layer_log)
    assert any(entry["stage"] == "workflow_layered_export_bundle_synthesized" for entry in result.layer_log)
    assert any(entry["stage"] == "workflow_layered_export_bundle_ready" for entry in result.layer_log)
    assert result.diagnostics["workflow_status"] == "failure"


def test_basic_sense_eval_ignores_disjoint_repeated_boilerplate_excerpts() -> None:
    graph_payload = {
        "nodes": [
            {
                "id": "n-1",
                "type": "entity",
                "metadata": {"semantic_node_type": "ENTITY", "level_from_root": 0},
                "mentions": [
                    {
                        "spans": [
                            {
                                "excerpt": "Repeated boilerplate sentence.",
                                "source_cluster_id": "cluster-a",
                                "start_char": 0,
                                "end_char": 30,
                            }
                        ]
                    }
                ],
            },
            {
                "id": "n-2",
                "type": "entity",
                "metadata": {"semantic_node_type": "ENTITY", "level_from_root": 1},
                "mentions": [
                    {
                        "spans": [
                            {
                                "excerpt": "Repeated boilerplate sentence.",
                                "source_cluster_id": "cluster-a",
                                "start_char": 40,
                                "end_char": 70,
                            }
                        ]
                    }
                ],
            },
            {
                "id": "n-3",
                "type": "entity",
                "metadata": {"semantic_node_type": "ENTITY", "level_from_root": 1},
                "mentions": [
                    {
                        "spans": [
                            {
                                "excerpt": "Repeated boilerplate sentence.",
                                "source_cluster_id": "cluster-b",
                                "start_char": 0,
                                "end_char": 30,
                            }
                        ]
                    }
                ],
            },
        ],
        "edges": [],
    }

    evaluation = worker._basic_sense_eval_from_graph_payload(
        graph_payload=graph_payload,
        diagnostics={"page_index": {"assignment_mode": "heuristic_deterministic"}},
    )

    assert evaluation["duplicate_excerpt_hits"] == 0
    assert evaluation["basic_sense_verdict"] in {"mixed", "good"}


def test_basic_sense_eval_counts_overlapping_duplicates_within_a_source_cluster() -> None:
    graph_payload = {
        "nodes": [
            {
                "id": "n-1",
                "type": "entity",
                "metadata": {"semantic_node_type": "ENTITY", "level_from_root": 0},
                "mentions": [
                    {
                        "spans": [
                            {
                                "excerpt": "Repeated boilerplate sentence.",
                                "source_cluster_id": "cluster-a",
                                "start_char": 0,
                                "end_char": 30,
                            }
                        ]
                    }
                ],
            },
            {
                "id": "n-2",
                "type": "entity",
                "metadata": {"semantic_node_type": "ENTITY", "level_from_root": 1},
                "mentions": [
                    {
                        "spans": [
                            {
                                "excerpt": "Repeated boilerplate sentence.",
                                "source_cluster_id": "cluster-a",
                                "start_char": 20,
                                "end_char": 50,
                            }
                        ]
                    }
                ],
            },
        ],
        "edges": [],
    }

    evaluation = worker._basic_sense_eval_from_graph_payload(
        graph_payload=graph_payload,
        diagnostics={"page_index": {"assignment_mode": "heuristic_deterministic"}},
    )

    assert evaluation["duplicate_excerpt_hits"] == 1
    assert evaluation["basic_sense_verdict"] in {"mixed", "good"}
