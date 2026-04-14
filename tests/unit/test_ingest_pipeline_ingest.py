def test_run_creates_source_and_fragment_nodes_in_foreground(pipeline, ingest_request):
    pipeline.run(ingest_request)
    writes = pipeline.engines.conversation.writes
    assert any(w["kind"] == "document" and w["namespace"] == "ws:demo:conv:fg" for w in writes)
    assert any(w["kind"] == "fragment" and w["namespace"] == "ws:demo:conv:fg" for w in writes)
