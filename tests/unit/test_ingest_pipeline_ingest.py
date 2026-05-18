from kg_doc_parser.workflow_ingest.page_index import parse_page_index_document
from kogwistar_llm_wiki.namespaces import WorkspaceNamespaces
from kogwistar_llm_wiki.utils import _temporary_namespace


def test_run_invokes_kogwistar_ingest(pipeline, ingest_request, monkeypatch):
    ingest_calls = {"source": [], "compat": []}

    original_source = pipeline.engines.kg.persist_document_graph_extraction
    original_compat = pipeline.engines.conversation.persist_document_graph_extraction

    def spy_source(*, doc_id, parsed, mode="append"):
        ingest_calls["source"].append(
            {
                "doc_id": doc_id,
                "mode": mode,
                "node_count": len(parsed.nodes),
                "edge_count": len(parsed.edges),
            }
        )
        return original_source(doc_id=doc_id, parsed=parsed, mode=mode)

    def spy_compat(*, doc_id, parsed, mode="append"):
        ingest_calls["compat"].append(
            {
                "doc_id": doc_id,
                "mode": mode,
                "node_count": len(parsed.nodes),
                "edge_count": len(parsed.edges),
            }
        )
        return original_compat(doc_id=doc_id, parsed=parsed, mode=mode)

    monkeypatch.setattr(pipeline.engines.kg, "persist_document_graph_extraction", spy_source)
    monkeypatch.setattr(pipeline.engines.conversation, "persist_document_graph_extraction", spy_compat)
    monkeypatch.setattr(
        pipeline,
        "parser",
        lambda *, document_id, title, raw_text, source_format, mode: parse_page_index_document(
            document_id=document_id,
            title=title,
            raw_text=raw_text,
            source_format=source_format,
            mode=mode,
        ),
    )

    artifacts = pipeline.run(ingest_request)
    assert ingest_calls["source"]
    assert ingest_calls["compat"]
    assert ingest_calls["source"][0]["doc_id"] == artifacts.source_document_id
    assert ingest_calls["compat"][0]["doc_id"] == artifacts.source_document_id
    assert ingest_calls["source"][0]["mode"] == "append"
    assert ingest_calls["compat"][0]["mode"] == "append"
    assert ingest_calls["source"][0]["node_count"] > 0
    assert ingest_calls["compat"][0]["node_count"] > 0

    ns = WorkspaceNamespaces(ingest_request.workspace_id)
    with _temporary_namespace(pipeline.engines.kg, ns.source_space):
        source_nodes = pipeline.engines.kg.read.get_nodes(
            where={"doc_id": artifacts.source_document_id, "graph_space": "source"}
        )
    with _temporary_namespace(pipeline.engines.conversation, ns.conv_fg):
        compat_nodes = pipeline.engines.conversation.read.get_nodes(
            where={"doc_id": artifacts.source_document_id}
        )
    assert source_nodes
    assert compat_nodes
    assert all(node.metadata.get("graph_space") == "source" for node in source_nodes)
    assert all(node.metadata.get("source_document_id") == artifacts.source_document_id for node in source_nodes)
