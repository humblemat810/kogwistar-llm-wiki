from __future__ import annotations

import time

from kogwistar.engine_core.models import Grounding, Node, Span

from kogwistar_llm_wiki import IngestPipeline, WorkbenchApi, WorkspaceNamespaces, build_in_memory_namespace_engines
from kogwistar_llm_wiki.utils import _temporary_namespace


def test_workbench_api_returns_serializable_bounded_lens():
    engines = build_in_memory_namespace_engines()
    try:
        api = WorkbenchApi(IngestPipeline(engines))
        payload = api.get_lens(
            {
                "workspace_id": "api-test",
                "query_text": "nothing here",
                "hop_limit": 0,
                "max_nodes": 2,
                "max_edges": 1,
            }
        )
        assert payload["workspace_id"] == "api-test"
        assert payload["nodes"] == []
        assert payload["completeness"] == "bounded"
    finally:
        engines.close()


def test_validate_proposal_re_resolves_with_anchor_and_pin_context(monkeypatch):
    engines = build_in_memory_namespace_engines()
    try:
        pipeline = IngestPipeline(engines)
        api = WorkbenchApi(pipeline)
        captured = {}
        original = pipeline.resolve_semantic_lens

        def resolve(request):
            captured["request"] = request
            return original(request)

        monkeypatch.setattr(pipeline, "resolve_semantic_lens", resolve)
        request = {
            "workspace_id": "api-test",
            "query_text": "nothing here",
            "explicit_anchor_ids": ["anchor-1"],
            "pinned_node_ids": ["pin-1"],
        }
        snapshot = api.get_lens(request)
        result = api.validate_proposal(
            {
                "request": {
                    **request,
                    "source_watermark": snapshot["source_watermark"],
                },
                "proposal": {
                    "lens_id": snapshot["lens_id"],
                    "source_watermark": snapshot["source_watermark"],
                    "operation": "no_change",
                    "target_ids": [],
                },
            }
        )
        assert result["accepted"] is True
        assert captured["request"].explicit_anchor_ids == ("anchor-1",)
        assert captured["request"].pinned_node_ids == ("pin-1",)
    finally:
        engines.close()


def test_validate_proposal_rejects_evidence_outside_the_scoped_lens():
    engines = build_in_memory_namespace_engines()
    try:
        workspace_id = "api-evidence-test"
        pipeline = IngestPipeline(engines)
        with _temporary_namespace(engines.kg, WorkspaceNamespaces(workspace_id).curated_kg_space):
            engines.kg.write.add_node(Node(
                id="n:visible",
                label="Visible evidence",
                type="entity",
                summary="Visible evidence",
                doc_id="doc:visible",
                mentions=[Grounding(spans=[Span.from_dummy_for_workflow("doc:visible")])],
                metadata={"workspace_id": workspace_id, "graph_space": "curated_kg"},
            ))
        api = WorkbenchApi(pipeline)
        request = {"workspace_id": workspace_id, "query_text": "visible", "max_nodes": 5}
        snapshot = api.get_lens(request)
        result = api.validate_proposal({
            "request": request,
            "proposal": {
                "lens_id": snapshot["lens_id"],
                "source_watermark": snapshot["source_watermark"],
                "operation": "review",
                "target_ids": ["n:visible"],
                "evidence_ids": ["fabricated-evidence"],
            },
        })
        assert result["accepted"] is False
        assert result["reason"] == "evidence_not_in_scoped_lens"
    finally:
        engines.close()


def test_codex_ask_uses_injected_listener_and_persists_turn():
    engines = build_in_memory_namespace_engines()
    try:
        pipeline = IngestPipeline(engines)
        observed = []

        def listener(request, snapshot):
            observed.append((request.query_text, snapshot.lens_id))
            return "listener: no grounded candidates need an edit"

        api = WorkbenchApi(pipeline, agent_responder=listener)
        response = api.ask(
            {
                "workspace_id": "api-listener-test",
                "query_text": "What should change?",
                "session_id": "browser-1",
                "mode": "codex",
            }
        )
        assert response["agent_status"] == "active"
        assert response["answer"]["text"] == "listener: no grounded candidates need an edit"
        assert observed == [("What should change?", response["snapshot"]["lens_id"])]
        history = api.get_history(workspace_id="api-listener-test", session_id="browser-1")
        assert len(history) == 1
        assert history[0]["question"] == "What should change?"
    finally:
        engines.close()


def test_background_codex_interaction_returns_pending_then_completed():
    engines = build_in_memory_namespace_engines()
    api = None
    try:
        def listener(request, snapshot, progress):
            progress()
            return f"background: {request.query_text}; lens={snapshot.lens_id}"

        api = WorkbenchApi(
            IngestPipeline(engines),
            agent_responder=listener,
            codex_worker_count=2,
        )
        pending = api.submit_interaction(
            {
                "workspace_id": "api-background-test",
                "query_text": "What is grounded?",
                "session_id": "browser-async",
                "mode": "codex",
            }
        )
        assert pending["status"] == "pending"

        deadline = time.monotonic() + 5
        result = None
        while time.monotonic() < deadline:
            result = api.get_interaction(
                workspace_id="api-background-test",
                interaction_id=str(pending["interaction_id"]),
            )
            if result and result["status"] != "pending":
                break
            time.sleep(0.01)
        assert result is not None
        assert result["status"] == "completed"
        assert result["response"]["agent_status"] == "active"
        assert result["response"]["answer"]["text"].startswith("background: What is grounded?")
        history = api.get_history(workspace_id="api-background-test", session_id="browser-async")
        assert len(history) == 1
    finally:
        if api is not None:
            api.close()
        engines.close()
