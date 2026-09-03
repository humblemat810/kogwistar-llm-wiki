from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from kogwistar_llm_wiki.agent_gateway import AgentGateway, _fetch_source_text
from kogwistar_llm_wiki.workbench_api import WorkbenchApi
from kogwistar_llm_wiki.worker import _durable_maintenance_usage, _maintenance_budget_state
from kogwistar.runtime import BudgetAttribution, BudgetEvent, budget_event_to_dict


def test_agent_capability_fake_payload_flow(pipeline):
    gateway = AgentGateway(WorkbenchApi(pipeline))
    source_uri = "https://example.test/rl.txt"
    payload = {
        "workspace_id": "agent-flow",
        "source_uri": source_uri,
        "title": "Reinforcement Learning Notes",
        "raw_text": "Reinforcement learning uses rewards to train an agent.",
        "provenance_policy": "optional",
    }

    ingested = gateway.ingest(payload)
    source_id = ingested["artifacts"]["source_document_id"]
    assert ingested["status"] == "ingested"

    inspected = gateway.source({"workspace_id": "agent-flow", "source_uri": source_uri})
    assert inspected["exists"] is True
    assert inspected["source_document_id"] == source_id
    assert inspected["revision"] is not None
    assert inspected["readiness"]
    assert inspected["metadata"]["provenance_policy"] == "optional"
    assert "source_raw_text" not in inspected["metadata"]
    assert "source_raw_text" not in inspected["revision"]["metadata"]

    search = gateway.search({"workspace_id": "agent-flow", "query_text": "reinforcement learning"})
    assert "nodes" in search
    hypergraph = gateway.hypergraph_search({"workspace_id": "agent-flow", "query_text": "rewards"})
    assert "hyperedges" in hypergraph

    maintained = gateway.maintain(
        {
            "workspace_id": "agent-flow",
            "topic": "reinforcement learning",
            "objective": "check cross-links",
            "max_time_seconds": 30,
            "max_llm_calls": 2,
            "max_tokens": 1000,
            "max_cost_usd": 0.25,
            "max_steps": 3,
        }
    )
    assert maintained["status"] == "queued"
    assert maintained["job_ids"]
    assert maintained["budgets"]["max_llm_calls"] == 2

    status = gateway.status({"workspace_id": "agent-flow"})
    assert status["health"]["ready"] is True
    assert status["sources"]["count"] >= 1
    assert status["maintenance"]["counts"]["pending"] >= 1

    reingest_payload = dict(payload)
    reingest_payload.pop("source_uri")
    reingest_payload.update(
        {
            "source_document_id": source_id,
            "raw_text": "Reinforcement learning uses rewards and value estimates to train an agent.",
        }
    )
    reingested = gateway.reingest(reingest_payload)
    assert reingested["status"] == "reingested"
    assert reingested["artifacts"]["source_document_id"] == source_id

    gateway.api.close()
    pipeline.engines.close()


def test_maintain_rejects_unknown_explicit_source_ids_without_partial_enqueue(pipeline):
    gateway = AgentGateway(WorkbenchApi(pipeline))
    with pytest.raises(ValueError, match="do not resolve"):
        gateway.maintain(
            {
                "workspace_id": "agent-maintenance",
                "source_document_ids": ["missing-source"],
                "objective": "inspect it",
            }
        )
    gateway.api.close()
    pipeline.engines.close()


def test_required_provenance_rejects_missing_or_mismatched_evidence(pipeline):
    gateway = AgentGateway(WorkbenchApi(pipeline))
    with pytest.raises(ValueError, match="required provenance"):
        gateway.ingest(
            {
                "workspace_id": "agent-provenance",
                "source_uri": "https://example.test/source.txt",
                "raw_text": "Grounded source text.",
                "provenance_policy": "required",
            }
        )
    with pytest.raises(ValueError, match="does not match"):
        gateway.ingest(
            {
                "workspace_id": "agent-provenance",
                "source_uri": "https://example.test/source.txt",
                "raw_text": "Grounded source text.",
                "provenance_policy": "required",
                "provenance": {
                    "workspace_id": "other-workspace",
                    "source_uri": "https://example.test/source.txt",
                },
            }
        )
    with pytest.raises(ValueError, match="must include workspace_id"):
        gateway.ingest(
            {
                "workspace_id": "agent-provenance",
                "source_uri": "https://example.test/source.txt",
                "raw_text": "Grounded source text.",
                "provenance_policy": "required",
                "provenance": {"note": "fabricated"},
            }
        )
    valid = gateway._source_request(
        {
            "workspace_id": "agent-provenance",
            "source_uri": "https://example.test/source.txt",
            "raw_text": "Grounded source text.",
            "provenance_policy": "required",
            "provenance": {
                "workspace_id": "agent-provenance",
                "source_uri": "https://example.test/source.txt",
            },
        }
    )
    assert valid.provenance == {
        "workspace_id": "agent-provenance",
        "source_uri": "https://example.test/source.txt",
    }
    with pytest.raises(ValueError, match="cannot be resolved"):
        gateway._source_request(
            {
                "workspace_id": "agent-provenance",
                "source_uri": "https://example.test/source.txt",
                "raw_text": "Grounded source text.",
                "provenance_policy": "optional",
                "provenance": {
                    "workspace_id": "agent-provenance",
                    "source_uri": "https://example.test/source.txt",
                    "source_revision_id": "unknown-revision",
                },
            }
        )
    with pytest.raises(ValueError, match="does not match request source identity"):
        gateway._source_request(
            {
                "workspace_id": "agent-provenance",
                "source_uri": "https://example.test/source.txt",
                "raw_text": "Grounded source text.",
                "provenance_policy": "optional",
                "provenance": {
                    "workspace_id": "agent-provenance",
                    "source_uri": "https://example.test/source.txt",
                    "source_document_id": "wrong-source-id",
                },
            }
        )
    disabled = gateway.ingest(
        {
            "workspace_id": "agent-provenance",
            "source_uri": "https://example.test/disabled.txt",
            "raw_text": "Unattributed user-provided source text.",
            "provenance_policy": "disabled",
        }
    )
    assert disabled["request"]["provenance_policy"] == "disabled"
    gateway.api.close()
    pipeline.engines.close()


def test_agent_gateway_exposes_only_semantic_capability_names(pipeline):
    gateway = AgentGateway(WorkbenchApi(pipeline))
    assert gateway.mcp_tool_names() == (
        "query", "search", "ingest", "source", "reingest", "maintain",
        "status", "hypergraph_search", "history", "propose", "confirm",
    )
    assert set(gateway.mcp_tool_names()) == set(gateway.mcp_tool_descriptions())
    gateway.api.close()
    pipeline.engines.close()


def test_maintenance_request_budget_survives_fair_slice_requeue():
    first = _maintenance_budget_state(
        {
            "budgets": {
                "max_tokens": 1000,
                "max_llm_calls": 4,
                "max_steps": 6,
                "max_time_seconds": 10,
                "max_cost_usd": 0.5,
            }
        },
        fair_scheduling=True,
        maintenance_steps_per_slice=2,
        maintenance_llm_calls_per_slice=1,
        maintenance_seconds_per_slice=3,
    )
    first.update({"token_used": 100, "call_used": 1, "step_used": 2, "time_used_ms": 3000, "cost_used": 0.1})
    second = _maintenance_budget_state(
        {
            "budgets": {
                "max_tokens": 1000,
                "max_llm_calls": 4,
                "max_steps": 6,
                "max_time_seconds": 10,
                "max_cost_usd": 0.5,
            },
            "maintenance_budget_state": first,
        },
        fair_scheduling=True,
        maintenance_steps_per_slice=2,
        maintenance_llm_calls_per_slice=1,
        maintenance_seconds_per_slice=3,
    )
    assert second["token_budget"] == 1000
    assert second["call_budget"] == 2
    assert second["step_budget"] == 4
    assert second["time_budget_ms"] == 6000
    assert second["call_used"] == 1
    assert second["cost_budget"] == 0.5


def test_http_source_fetch_requires_allowlist_and_enforces_response_limit(monkeypatch):
    with pytest.raises(ValueError, match="allowlisted"):
        _fetch_source_text("http://127.0.0.1/source.txt")

    class Response:
        headers = {"Content-Length": "4"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return b"test"

    class Opener:
        def open(self, _request, *, timeout):
            assert timeout == 30
            return Response()

    monkeypatch.setenv("LLM_WIKI_SOURCE_FETCH_ALLOWED_HOSTS", "example.test")
    monkeypatch.setenv("LLM_WIKI_SOURCE_FETCH_MAX_BYTES", "4")
    monkeypatch.setattr("kogwistar_llm_wiki.agent_gateway.urllib_request.build_opener", lambda *_args: Opener())
    assert _fetch_source_text("https://example.test/source.txt") == "test"


def test_agent_ingest_rejects_local_source_uri_even_with_raw_text(pipeline):
    gateway = AgentGateway(WorkbenchApi(pipeline))
    for source_uri in ("file:///secret.txt", "C:/secret.txt", "C:\\secret.txt", "secret.txt"):
        with pytest.raises(ValueError, match="source_uri"):
            gateway.ingest(
                {
                    "workspace_id": "agent-uri-policy",
                    "source_uri": source_uri,
                    "raw_text": "user supplied text",
                }
            )
    gateway.api.close()
    pipeline.engines.close()


def test_reingest_rejects_conflicting_source_identity_selectors():
    gateway = AgentGateway(SimpleNamespace())
    gateway._source_documents = lambda _workspace_id: [  # type: ignore[method-assign]
        {"id": "source-a", "metadata": {"source_uri": "https://example.test/a"}, "content": "A"},
        {"id": "source-b", "metadata": {"source_uri": "https://example.test/b"}, "content": "B"},
    ]
    with pytest.raises(ValueError, match="different sources"):
        gateway._load_source_request(
            workspace_id="w",
            source_document_id="source-a",
            source_uri="https://example.test/b",
        )


def test_reingest_preserves_existing_source_policy_when_not_overridden():
    gateway = AgentGateway(SimpleNamespace())
    gateway.source = lambda _arguments: {  # type: ignore[method-assign]
        "exists": True,
        "source_uri": "https://example.test/source.txt",
        "metadata": {
            "title": "Existing title",
            "source_format": "markdown",
            "operation_mode": "hybrid",
            "parser_mode": "layered",
            "parser_lane": "workflow_layered",
            "promotion_mode": "review",
            "provenance_policy": "required",
        },
        "revisions": [],
    }
    captured: dict[str, object] = {}

    def capture(arguments, *, reingest):
        captured.update(arguments)
        captured["reingest"] = reingest
        return {"status": "reingested"}

    gateway._ingest = capture  # type: ignore[method-assign]
    gateway.reingest(
        {
            "workspace_id": "w",
            "source_document_id": "source-1",
            "raw_text": "new source text",
            "title": "",
            "provenance_policy": None,
        }
    )
    assert captured["source_uri"] == "https://example.test/source.txt"
    assert captured["title"] == "Existing title"
    assert captured["operation_mode"] == "hybrid"
    assert captured["parser_mode"] == "layered"
    assert captured["parser_lane"] == "workflow_layered"
    assert captured["promotion_mode"] == "review"
    assert captured["provenance_policy"] == "required"
    assert captured["reingest"] is True


def test_maintenance_jobs_include_done_rows_and_surface_queue_errors():
    done = SimpleNamespace(
        job_id="job-done",
        entity_id="source-a",
        job_kind="maintenance_job:distill",
        payload={"source_document_id": "source-a"},
    )

    class Queue:
        def list(self, *, status, **_kwargs):
            if status == "DONE":
                return [done]
            return []

    class ErrorQueue(Queue):
        def list(self, *, status, **_kwargs):
            if status == "PENDING":
                raise RuntimeError("queue unavailable")
            return super().list(status=status)

    class Api:
        pipeline = SimpleNamespace(
            namespaces_for=lambda _workspace_id: SimpleNamespace(maintenance_jobs="jobs"),
            engines=SimpleNamespace(conversation=SimpleNamespace(jobs=Queue())),
        )

    gateway = AgentGateway(Api())
    jobs = gateway._maintenance_jobs("w")
    assert jobs[0]["status"] == "completed"
    gateway.api.pipeline.engines.conversation.jobs = ErrorQueue()
    errors: list[str] = []
    assert gateway._maintenance_jobs("w", errors=errors) == [
        {
            "job_id": "job-done",
            "status": "completed",
            "entity_id": "source-a",
            "job_kind": "maintenance_job:distill",
            "payload": {"source_document_id": "source-a"},
        }
    ]
    assert errors == ["RuntimeError: queue unavailable"]


def test_status_marks_maintenance_unavailable_instead_of_reporting_empty(monkeypatch):
    class BrokenQueue:
        def list(self, **_kwargs):
            raise RuntimeError("maintenance store offline")

    class Api:
        pipeline = SimpleNamespace(
            namespaces_for=lambda _workspace_id: SimpleNamespace(maintenance_jobs="jobs"),
            engines=SimpleNamespace(conversation=SimpleNamespace(jobs=BrokenQueue())),
        )

        def readiness(self):
            return {"ready": True}

    gateway = AgentGateway(Api())
    gateway._source_documents = lambda _workspace_id: []  # type: ignore[method-assign]
    monkeypatch.setattr(
        "kogwistar_llm_wiki.agent_gateway.build_workspace_quality_report",
        lambda *_args, **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr("kogwistar_llm_wiki.agent_gateway.asdict", lambda _value: {})
    status = gateway.status({"workspace_id": "w"})
    assert status["maintenance"]["available"] is False
    assert status["maintenance"]["errors"] == [
        "RuntimeError: maintenance store offline",
        "RuntimeError: maintenance store offline",
        "RuntimeError: maintenance store offline",
        "RuntimeError: maintenance store offline",
    ]


def test_agent_result_limits_are_server_bounded():
    gateway = AgentGateway(SimpleNamespace(get_lens=lambda payload: payload, ask=lambda payload: payload))
    result = gateway.search(
        {
            "workspace_id": "w",
            "query_text": "q",
            "hop_limit": 999,
            "max_nodes": 999999,
            "max_edges": 999999,
            "max_hyperedges": 999999,
        }
    )
    assert result["hop_limit"] == 8
    assert result["max_nodes"] == 500
    assert result["max_edges"] == 2000
    assert result["max_hyperedges"] == 250
    bounded = gateway.search(
        {
            "workspace_id": "w",
            "query_text": "q",
            "hop_limit": -3,
            "max_nodes": -1,
        }
    )
    assert bounded["hop_limit"] == 0
    assert bounded["max_nodes"] == 0


def test_maintenance_rejects_malformed_budget_values():
    gateway = AgentGateway(SimpleNamespace())
    with pytest.raises(ValueError, match="max_steps"):
        gateway.maintain(
            {
                "workspace_id": "w",
                "topic": "topic",
                "max_steps": "many",
            }
        )
    with pytest.raises(ValueError, match="max_cost_usd"):
        gateway.maintain(
            {
                "workspace_id": "w",
                "topic": "topic",
                "max_cost_usd": True,
            }
        )


def test_fetch_source_rejects_credentials_fragments_and_redirects(monkeypatch):
    for source_uri in (
        "https://user:pass@example.test/source.txt",
        "https://example.test/source.txt#fragment",
    ):
        with pytest.raises(ValueError, match="credential-free"):
            _fetch_source_text(source_uri)

    class Opener:
        def open(self, *_args, **_kwargs):
            raise ValueError("source_uri redirects are not permitted")

    monkeypatch.setenv("LLM_WIKI_SOURCE_FETCH_ALLOWED_HOSTS", "example.test")
    monkeypatch.setattr(
        "kogwistar_llm_wiki.agent_gateway.urllib_request.build_opener",
        lambda *_args: Opener(),
    )
    with pytest.raises(ValueError, match="redirects"):
        _fetch_source_text("https://example.test/source.txt")


def test_durable_maintenance_usage_recovers_failed_attempt_budget():
    job_id = "maintenance-job-1"
    attribution = BudgetAttribution(
        workspace_id="w",
        source_document_id="source-a",
        maintenance_job_id=job_id,
    )
    events = [
        BudgetEvent(run_id="run-1", source="runtime", kind="debit", amount=1, unit="call", attribution=attribution),
        BudgetEvent(run_id="run-1", source="runtime", kind="debit", amount=2, unit="step", attribution=attribution),
        BudgetEvent(run_id="run-1", source="runtime", kind="debit", amount=120, unit="token", attribution=attribution),
        BudgetEvent(run_id="run-1", source="runtime", kind="time", amount=30, unit="ms", attribution=attribution),
        BudgetEvent(run_id="run-1", source="runtime", kind="cost", amount=0.02, unit="total_cost", attribution=attribution),
    ]

    class Meta:
        def iter_entity_events(self, **_kwargs):
            for index, event in enumerate(events, start=1):
                payload = budget_event_to_dict(event)
                payload["artifact_kind"] = "usage_event"
                yield index, f"event-{index}", "usage_event", f"event-{index}", json.dumps(payload)

    usage = _durable_maintenance_usage(
        Meta(), namespace="usage", maintenance_job_id=job_id
    )
    assert usage == {
        "token_used": 120,
        "call_used": 1,
        "step_used": 2,
        "time_used_ms": 30,
        "cost_used": 0.02,
    }
    state = _maintenance_budget_state(
        {
            "budgets": {"max_tokens": 1000, "max_llm_calls": 4, "max_steps": 6, "max_time_seconds": 10, "max_cost_usd": 0.5}
        },
        fair_scheduling=False,
        maintenance_steps_per_slice=0,
        maintenance_llm_calls_per_slice=0,
        maintenance_seconds_per_slice=0,
        durable_usage=usage,
    )
    assert state["token_used"] == 120
    assert state["call_used"] == 1
    assert state["step_used"] == 2
    assert state["time_used_ms"] == 30
    assert state["cost_used"] == 0.02
