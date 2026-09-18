from __future__ import annotations

import json

from kg_doc_parser.workflow_ingest.page_index import parse_page_index_document

from kogwistar_llm_wiki.configuration.workspace import WorkspaceNamespaces
from kogwistar_llm_wiki.utils import _temporary_namespace


def _doc_metadata(row: dict) -> dict:
    payload = row.get("metadata")
    if isinstance(payload, str) and payload:
        return json.loads(payload)
    if isinstance(payload, dict):
        return payload
    return {}


def test_run_registers_source_and_invokes_parser(pipeline, ingest_request, monkeypatch):
    parser_calls = []

    def spy_parser(*, document_id, title, raw_text, source_format, mode):
        parser_calls.append(
            {
                "document_id": document_id,
                "title": title,
                "source_format": source_format,
                "mode": mode,
            }
        )
        return parse_page_index_document(
            document_id=document_id,
            title=title,
            raw_text=raw_text,
            source_format=source_format,
            mode=mode,
        )

    monkeypatch.setattr(pipeline, "parser", spy_parser)

    artifacts = pipeline.run(ingest_request)
    assert artifacts.source_document_id
    revision = pipeline.source_revision(
        request=ingest_request,
        source_document_id=artifacts.source_document_id,
    )
    assert parser_calls == [
        {
            "document_id": revision.revision_document_id,
            "title": ingest_request.title,
            "source_format": ingest_request.source_format,
            "mode": ingest_request.parser_mode,
        }
    ]

    stored = pipeline.engines.conversation.backend.document_get(
        ids=[artifacts.source_document_id],
        include=["documents", "metadatas"],
    )
    assert stored["ids"] == [artifacts.source_document_id]
    assert stored["metadatas"][0]["doc_id"] == artifacts.source_document_id

    source_stored = pipeline.engines.kg.backend.document_get(
        ids=[artifacts.source_document_id],
        include=["documents", "metadatas"],
    )
    assert source_stored["ids"] == [artifacts.source_document_id]
    assert source_stored["metadatas"][0]["doc_id"] == artifacts.source_document_id
    assert _doc_metadata(source_stored["metadatas"][0])["graph_space"] == "source"

    with _temporary_namespace(
        pipeline.engines.kg,
        WorkspaceNamespaces(ingest_request.workspace_id).source_space,
    ):
        revision_nodes = pipeline.engines.kg.read.get_nodes(
            where={
                "artifact_kind": "source_revision",
                "source_document_id": artifacts.source_document_id,
            }
        )
        readiness_nodes = pipeline.engines.kg.read.get_nodes(
            where={
                "artifact_kind": "source_readiness",
                "source_document_id": artifacts.source_document_id,
            }
        )
    assert revision_nodes
    assert readiness_nodes
    assert all(node.doc_id == revision.revision_document_id for node in revision_nodes)
    assert all(node.doc_id == revision.revision_document_id for node in readiness_nodes)
    assert all(
        node.mentions[0].spans[0].doc_id == revision.revision_document_id
        for node in [*revision_nodes, *readiness_nodes]
    )

    conv_meta = _doc_metadata(stored["metadatas"][0])
    assert conv_meta["graph_space"] == "source"
    assert conv_meta["legacy_namespace"] == "ws:demo:conv:fg"
