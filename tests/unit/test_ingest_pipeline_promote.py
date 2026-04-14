def test_promote_creates_new_kg_edge_not_candidate_mutation(pipeline, ingest_request):
    artifacts = pipeline.run(ingest_request)
    kg_writes = pipeline.engines.kg.writes
    assert kg_writes
    assert kg_writes[0]["kind"] == "edge"
    assert kg_writes[0]["namespace"] == "ws:demo:kg"
    assert kg_writes[0]["promotion_candidate_id"] == artifacts.promotion_candidate_id
