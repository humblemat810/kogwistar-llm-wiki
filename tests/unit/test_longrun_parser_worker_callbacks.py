from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from pathlib import Path

import pytest

from kg_doc_parser.workflow_ingest.providers import ProviderEndpointConfig, WorkflowProviderSettings
from kg_doc_parser.workflow_ingest import service as workflow_service
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
                final_state={"parse_session": {"mode": "workflow_layered"}},
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
    assert result.diagnostics["parse_session_mode"] == "workflow_layered"
