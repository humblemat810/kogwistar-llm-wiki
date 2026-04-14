def test_ingest_pipeline_e2e(pipeline, ingest_request):
    artifacts = pipeline.run(ingest_request)
    assert artifacts.promoted_edge_id

    conversation_writes = pipeline.engines.conversation.writes
    workflow_writes = pipeline.engines.workflow.writes
    kg_writes = pipeline.engines.kg.writes

    assert any(w["kind"] == "document" for w in conversation_writes)
    assert any(w["kind"] == "candidate_link" for w in conversation_writes)
    assert any(w["kind"] == "promotion_candidate" for w in conversation_writes)
    assert any(w["kind"] == "maintenance_job_request" for w in workflow_writes)
    assert any(w["kind"] == "edge" for w in kg_writes)
