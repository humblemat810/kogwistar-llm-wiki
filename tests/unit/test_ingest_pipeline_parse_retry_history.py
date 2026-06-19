from __future__ import annotations

from types import SimpleNamespace

from kogwistar_llm_wiki.namespaces import WorkspaceNamespaces
from kogwistar_llm_wiki.utils import _temporary_namespace


def _parse_result(*, title: str, diagnostics: dict[str, object]) -> object:
    return SimpleNamespace(
        semantic_tree=SimpleNamespace(title=title),
        diagnostics=diagnostics,
    )


def test_parse_retry_history_persists_compact_background_artifact(pipeline, ingest_request):
    request = ingest_request.model_copy(
        update={
            "workspace_id": "parse-history-workspace",
            "source_uri": "file:///contracts/parse-history-retry.txt",
            "raw_text": "# Parse Retry\n\nIntro\n\n## Retry Section\nBody\n",
            "source_format": "markdown",
            "parser_mode": "ollama",
        }
    )
    source_document_id = pipeline._source_document_id(request)
    parse_result = _parse_result(
        title=request.title,
        diagnostics={
            "parser_lane": "page_index",
            "page_index": {
                "assignment_mode": "llm_flat_assignment_retry",
                "final_outcome": "assignment_retry_success",
                "assignment_attempt_count": 2,
                "assignment_retry_used": True,
                "assignment_retry_succeeded": True,
                "structure_retry_used": False,
                "structure_retry_succeeded": False,
                "retry_used": True,
                "retry_succeeded": True,
                "assignment_validation_errors": ["heading structure was flattened"],
                "structure_validation_errors": [],
                "first_validation_errors": ["heading structure was flattened"],
                "retry_validation_errors": [],
                "validation_errors": [],
                "fallback_reason": None,
                "workflow_run_id": "run-17",
                "workflow_status": "succeeded",
                "retry_prompt_summary": "repair heading hierarchy",
            },
        },
    )

    node_id = pipeline.create_parse_retry_history(
        request=request,
        source_document_id=source_document_id,
        parse_result=parse_result,
        namespace=WorkspaceNamespaces(request.workspace_id).conv_bg,
    )

    assert node_id is not None
    with _temporary_namespace(pipeline.engines.conversation, WorkspaceNamespaces(request.workspace_id).conv_bg):
        nodes = pipeline.engines.conversation.read.get_nodes(where={"artifact_kind": "parse_retry_history"})

    assert len(nodes) == 1
    metadata = dict(nodes[0].metadata or {})
    assert metadata["workspace_id"] == request.workspace_id
    assert metadata["source_document_id"] == source_document_id
    assert metadata["parser_lane"] == "page_index"
    assert metadata["final_outcome"] == "assignment_retry_success"
    assert metadata["assignment_retry_used"] is True
    assert metadata["assignment_retry_succeeded"] is True
    assert metadata["structure_retry_used"] is False
    assert metadata["structure_retry_succeeded"] is False
    assert metadata["retry_used"] is True
    assert metadata["retry_succeeded"] is True
    assert metadata["assignment_attempt_count"] == 2
    assert metadata["assignment_validation_errors"] == ["heading structure was flattened"]
    assert metadata["structure_validation_errors"] == []
    assert metadata["first_validation_errors"] == ["heading structure was flattened"]


def test_parse_retry_history_records_structure_retry_phase(pipeline, ingest_request):
    request = ingest_request.model_copy(
        update={
            "workspace_id": "parse-history-structure",
            "source_uri": "file:///contracts/parse-history-structure.txt",
            "raw_text": "# Parse Retry\n\nIntro\n\n## Retry Section\nBody\n",
            "source_format": "markdown",
            "parser_mode": "ollama",
        }
    )
    source_document_id = pipeline._source_document_id(request)
    parse_result = _parse_result(
        title=request.title,
        diagnostics={
            "parser_lane": "page_index",
            "page_index": {
                "assignment_mode": "llm_flat_assignment_structure_retry",
                "final_outcome": "structure_retry_success",
                "assignment_attempt_count": 2,
                "assignment_retry_used": False,
                "assignment_retry_succeeded": False,
                "structure_retry_used": True,
                "structure_retry_succeeded": True,
                "retry_used": True,
                "retry_succeeded": True,
                "assignment_validation_errors": [],
                "structure_validation_errors": ["block 1 excerpt is too broad"],
                "first_validation_errors": [],
                "retry_validation_errors": [],
                "validation_errors": [],
                "fallback_reason": None,
                "structure_retry_prompt_summary": "repair tree shape",
            },
        },
    )

    node_id = pipeline.create_parse_retry_history(
        request=request,
        source_document_id=source_document_id,
        parse_result=parse_result,
        namespace=WorkspaceNamespaces(request.workspace_id).conv_bg,
    )

    assert node_id is not None
    with _temporary_namespace(pipeline.engines.conversation, WorkspaceNamespaces(request.workspace_id).conv_bg):
        nodes = pipeline.engines.conversation.read.get_nodes(where={"artifact_kind": "parse_retry_history"})

    assert len(nodes) == 1
    metadata = dict(nodes[0].metadata or {})
    assert metadata["workspace_id"] == request.workspace_id
    assert metadata["source_document_id"] == source_document_id
    assert metadata["final_outcome"] == "structure_retry_success"
    assert metadata["assignment_retry_used"] is False
    assert metadata["structure_retry_used"] is True
    assert metadata["structure_retry_succeeded"] is True
    assert metadata["assignment_validation_errors"] == []
    assert metadata["structure_validation_errors"] == ["block 1 excerpt is too broad"]


def test_parse_retry_history_skips_clean_first_pass(pipeline, ingest_request):
    request = ingest_request.model_copy(
        update={
            "workspace_id": "parse-history-clean",
            "source_uri": "file:///contracts/parse-history-clean.txt",
            "raw_text": "# Parse Clean\n\nIntro\n",
            "source_format": "markdown",
            "parser_mode": "ollama",
        }
    )
    source_document_id = pipeline._source_document_id(request)
    parse_result = _parse_result(
        title=request.title,
        diagnostics={
            "parser_lane": "page_index",
            "page_index": {
                "assignment_mode": "llm_flat_assignment",
                "final_outcome": "first_pass_success",
                "assignment_attempt_count": 1,
                "assignment_retry_used": False,
                "assignment_retry_succeeded": False,
                "structure_retry_used": False,
                "structure_retry_succeeded": False,
                "retry_used": False,
                "retry_succeeded": False,
                "assignment_validation_errors": [],
                "structure_validation_errors": [],
                "first_validation_errors": [],
                "retry_validation_errors": [],
                "validation_errors": [],
                "fallback_reason": None,
            },
        },
    )

    node_id = pipeline.create_parse_retry_history(
        request=request,
        source_document_id=source_document_id,
        parse_result=parse_result,
        namespace=WorkspaceNamespaces(request.workspace_id).conv_bg,
    )

    assert node_id is None
    with _temporary_namespace(pipeline.engines.conversation, WorkspaceNamespaces(request.workspace_id).conv_bg):
        nodes = pipeline.engines.conversation.read.get_nodes(where={"artifact_kind": "parse_retry_history"})

    assert nodes == []
