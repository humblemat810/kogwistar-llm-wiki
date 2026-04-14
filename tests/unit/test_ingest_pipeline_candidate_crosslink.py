def test_candidate_link_is_created_in_background_lane(pipeline, ingest_request):
    artifacts = pipeline.run(ingest_request)
    candidate_links = pipeline.engines.conversation.read.get_nodes(
        where={
            "workspace_id": ingest_request.workspace_id,
            "artifact_kind": "candidate_link",
            "conversation_lane": "background",
            "namespace": "ws:demo:conv:bg",
        }
    )
    assert candidate_links
    assert {node.id for node in candidate_links} == {artifacts.candidate_link_id}
