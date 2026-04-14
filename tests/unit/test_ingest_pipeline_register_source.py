from workflow_ingest.page_index import parse_page_index_document


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
    assert parser_calls == [
        {
            "document_id": artifacts.source_document_id,
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
