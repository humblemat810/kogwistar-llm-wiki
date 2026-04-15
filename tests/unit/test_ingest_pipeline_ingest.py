from kg_doc_parser.workflow_ingest.page_index import parse_page_index_document


def test_run_invokes_kogwistar_ingest(pipeline, ingest_request, monkeypatch):
    ingest_calls = []

    original = pipeline.engines.conversation.persist_document_graph_extraction

    def spy_persist(*, doc_id, parsed, mode="append"):
        ingest_calls.append(
            {
                "doc_id": doc_id,
                "mode": mode,
                "node_count": len(parsed.nodes),
                "edge_count": len(parsed.edges),
            }
        )
        return original(doc_id=doc_id, parsed=parsed, mode=mode)

    monkeypatch.setattr(pipeline.engines.conversation, "persist_document_graph_extraction", spy_persist)
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
    assert ingest_calls
    assert ingest_calls[0]["doc_id"] == artifacts.source_document_id
    assert ingest_calls[0]["mode"] == "append"
    assert ingest_calls[0]["node_count"] > 0
