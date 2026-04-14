def test_candidate_link_is_created_in_background_lane(pipeline, ingest_request):
    pipeline.run(ingest_request)
    writes = pipeline.engines.conversation.writes
    assert any(w["kind"] == "candidate_link" and w["namespace"] == "ws:demo:conv:bg" for w in writes)
