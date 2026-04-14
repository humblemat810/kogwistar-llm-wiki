def test_promotion_candidate_stays_out_of_workflow_storage(pipeline, ingest_request):
    artifacts = pipeline.run(ingest_request)

    workflow_candidates = pipeline.engines.workflow.read.get_nodes(
        where={"artifact_kind": "promotion_candidate"}
    )
    assert not workflow_candidates

    review_candidates = pipeline.engines.conversation.read.get_nodes(
        where={
            "workspace_id": ingest_request.workspace_id,
            "artifact_kind": "promotion_candidate",
            "namespace": "ws:demo:review",
        }
    )
    assert review_candidates
    assert {node.id for node in review_candidates} == {artifacts.promotion_candidate_id}
