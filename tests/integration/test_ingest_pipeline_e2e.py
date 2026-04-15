from __future__ import annotations

from dataclasses import replace
import inspect
import os

import pytest

from kg_doc_parser.workflow_ingest.page_index import parse_page_index_document


def _supports_gemini_mode() -> bool:
    params = inspect.signature(parse_page_index_document).parameters
    return "llm_provider" in params and "model" in params


@pytest.mark.parametrize(
    ("parser_mode", "llm_provider", "llm_model"),
    [
        pytest.param("heuristic", None, None, id="heuristic"),
        pytest.param("ollama", "ollama", "gemma3:4b", id="ollama", marks=pytest.mark.ci_full),
        pytest.param(
            "gemini",
            "gemini",
            "gemini-2.5-flash",
            id="gemini",
            marks=pytest.mark.ci_full,
        ),
    ],
)
def test_ingest_pipeline_e2e(pipeline, ingest_request, parser_mode, llm_provider, llm_model):
    if parser_mode in {"ollama", "gemini"} and os.getenv("RUN_CI_FULL") != "1":
        pytest.skip("LLM text parsing paths are only enabled when RUN_CI_FULL=1")
    if parser_mode == "gemini" and not _supports_gemini_mode():
        pytest.skip("current kg-doc-parser build does not expose explicit gemini parser args yet")

    ingest_request = replace(
        ingest_request,
        parser_mode=parser_mode,
        llm_provider=llm_provider,
        llm_model=llm_model,
    )

    artifacts = pipeline.run(ingest_request)

    conversation_nodes = pipeline.engines.conversation.read.get_nodes(
        where={"workspace_id": ingest_request.workspace_id}
    )
    workflow_nodes = pipeline.engines.workflow.read.get_nodes(
        where={"workspace_id": ingest_request.workspace_id}
    )
    kg_nodes = pipeline.engines.kg.read.get_nodes(
        where={"workspace_id": ingest_request.workspace_id}
    )
    stored_document = pipeline.engines.conversation.backend.document_get(
        ids=[artifacts.source_document_id],
        include=["documents", "metadatas"],
    )

    assert stored_document["ids"] == [artifacts.source_document_id]
    assert pipeline.engines.conversation.read.get_nodes(where={"doc_id": artifacts.source_document_id})
    assert any(
        node.metadata.get("artifact_kind") == "candidate_link"
        and node.metadata.get("namespace") == "ws:demo:conv:bg"
        for node in conversation_nodes
    )
    assert any(
        node.metadata.get("artifact_kind") == "promotion_candidate"
        and node.metadata.get("namespace") == "ws:demo:conv:bg"
        and node.metadata.get("queue_state") == "pending"
        for node in conversation_nodes
    )
    assert any(
        node.metadata.get("artifact_kind") == "maintenance_job_request"
        and node.metadata.get("namespace") == "ws:demo:wf:maintenance"
        for node in workflow_nodes
    )
    assert not any(node.metadata.get("artifact_kind") == "promoted_knowledge" for node in kg_nodes)
    assert artifacts.promoted_entity_id is None

    snapshot = pipeline.build_projection_snapshot()
    assert snapshot.entities == []
