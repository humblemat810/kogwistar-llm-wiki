def test_promotion_candidate_is_separate_review_artifact(pipeline, ingest_request):
    artifacts = pipeline.run(ingest_request)
    writes = pipeline.engines.conversation.writes
    promotion_candidates = [w for w in writes if w["kind"] == "promotion_candidate"]
    assert promotion_candidates
    assert promotion_candidates[0]["namespace"] == "ws:demo:review"
    assert artifacts.promotion_candidate_id == promotion_candidates[0]["id"]
