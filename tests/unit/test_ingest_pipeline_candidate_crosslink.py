from kogwistar.engine_core.models import Node


def test_candidate_link_is_created_in_background_lane(pipeline, ingest_request, seeded_kg_node):
    artifacts = pipeline.run(ingest_request)
    bg_nodes = pipeline.engines.conv_bg.get_nodes(node_type=Node, limit=100)
    candidate_ids = {str(node.id) for node in bg_nodes}
    assert set(artifacts.candidate_link_node_ids).issubset(candidate_ids)
    assert all(node.metadata.get("artifact_type") == "candidate_link" for node in bg_nodes)
