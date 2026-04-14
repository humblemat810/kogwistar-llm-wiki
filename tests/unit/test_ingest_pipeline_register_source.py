def test_run_registers_document_in_foreground(pipeline, ingest_request):
    artifacts = pipeline.run(ingest_request)
    docs = [w for w in pipeline.engines.conversation.writes if w["kind"] == "document"]
    assert docs
    assert artifacts.source_document_id == docs[0]["id"]
    assert docs[0]["namespace"] == "ws:demo:conv:fg"
