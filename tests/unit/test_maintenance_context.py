from __future__ import annotations

import json

import pytest

from kogwistar_llm_wiki.ingest_pipeline import IngestPipeline
from kogwistar_llm_wiki.maintenance.maintenance_context import (
    MAX_MAINTENANCE_CONTEXT_TOKENS,
    append_maintenance_round,
    bound_maintenance_context,
    maintenance_execution_active,
    maintenance_execution_context,
)
from kogwistar_llm_wiki.maintenance.maintenance_control import (
    configured_default_request_max_rounds,
)


def test_default_request_rounds_allow_one_follow_up_and_are_configurable(monkeypatch) -> None:
    monkeypatch.delenv("LLM_WIKI_MAINTENANCE_DEFAULT_REQUEST_MAX_ROUNDS", raising=False)
    assert configured_default_request_max_rounds() == 2
    monkeypatch.setenv("LLM_WIKI_MAINTENANCE_DEFAULT_REQUEST_MAX_ROUNDS", "4")
    assert configured_default_request_max_rounds() == 4


def test_default_request_rounds_reject_invalid_values(monkeypatch) -> None:
    monkeypatch.setenv("LLM_WIKI_MAINTENANCE_DEFAULT_REQUEST_MAX_ROUNDS", "0")
    with pytest.raises(ValueError, match="between 1 and 100"):
        configured_default_request_max_rounds()


def test_maintenance_execution_context_is_scoped() -> None:
    assert maintenance_execution_active() is False
    with maintenance_execution_context():
        assert maintenance_execution_active() is True
    assert maintenance_execution_active() is False


def test_maintenance_execution_cannot_create_recursive_request() -> None:
    pipeline = object.__new__(IngestPipeline)
    with maintenance_execution_context(), pytest.raises(RuntimeError, match="cannot create a new maintenance request"):
        pipeline.create_maintenance_request(
            request=object(),  # type: ignore[arg-type]
            source_document_id="source-1",
            namespace="maintenance",
        )


def test_maintenance_execution_cannot_enqueue_recursive_job() -> None:
    pipeline = object.__new__(IngestPipeline)
    with maintenance_execution_context(), pytest.raises(
        RuntimeError, match="cannot enqueue a new maintenance job"
    ):
        pipeline._enqueue_maintenance_job(
            request=object(),  # type: ignore[arg-type]
            request_node_id="request-1",
            source_document_id="source-1",
            namespace="maintenance",
        )


def test_maintenance_context_is_structured_and_bounded() -> None:
    context = bound_maintenance_context(
        {
            "raw_transcript": "must not cross the maintenance boundary",
            "compressed_summary": "A" * 20_000,
            "turns": [
                {
                    "round": number,
                    "summary": "B" * 2_000,
                    "touched_node_ids": [f"node-{number}"],
                    "next_seed_node_ids": [f"seed-{number}"],
                    "selection_reasons": [
                        {"candidate_id": f"node-{number}", "reason": "connected_neighbor", "score": 0.5}
                    ],
                }
                for number in range(12)
            ],
        }
    )

    assert "raw_transcript" not in context
    assert len(context["turns"]) <= 10
    assert len(json.dumps(context, separators=(",", ":"))) <= MAX_MAINTENANCE_CONTEXT_TOKENS
    assert context["truncated"] is True


def test_maintenance_context_appends_round_without_losing_prior_state() -> None:
    context = append_maintenance_round(
        {"compressed_node_ids": ["seed-1"]},
        round_number=3,
        summary="Expanded to the two-hop neighborhood.",
        touched_node_ids=["node-1", "node-2"],
        next_seed_node_ids=["node-2"],
        hop_limit=2,
        selection_reasons=[{"candidate_id": "node-2", "reason": "connected_neighbor", "score": 0.8}],
    )

    assert context["compressed_node_ids"] == ["seed-1"]
    assert context["turns"][-1]["round"] == 3
    assert context["turns"][-1]["hop_limit"] == 2
