def test_source_document_id_is_deterministic(pipeline, ingest_request):
    first = pipeline._source_document_id(ingest_request)
    second = pipeline._source_document_id(ingest_request)
    assert first == second
