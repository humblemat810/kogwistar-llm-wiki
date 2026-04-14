from kogwistar.engine_core.models import Edge, Node


def test_ingest_pipeline_end_to_end(pipeline, ingest_request, seeded_kg_node):
    artifacts = pipeline.run(ingest_request)

    foreground_nodes = pipeline.engines.conv_fg.get_nodes(node_type=Node, limit=100)
    workflow_nodes = pipeline.engines.workflow.get_nodes(node_type=Node, limit=100)
    background_nodes = pipeline.engines.conv_bg.get_nodes(node_type=Node, limit=100)
    promotion_nodes = [node for node in workflow_nodes if node.metadata.get("artifact_type") == "promotion_candidate"]
    kg_edges = pipeline.engines.kg.get_edges(edge_type=Edge, limit=100)

    assert any(str(node.id) == artifacts.source_node_id for node in foreground_nodes)
    assert any(str(node.id) == artifacts.maintenance_request_id for node in workflow_nodes)
    assert set(artifacts.candidate_link_node_ids).issubset({str(node.id) for node in background_nodes})
    assert set(artifacts.promotion_candidate_node_ids).issubset({str(node.id) for node in promotion_nodes})
    assert set(artifacts.promoted_edge_ids).issubset({str(edge.id) for edge in kg_edges})

    snapshot = pipeline.build_projection_snapshot()
    assert snapshot.entities
    assert any(rel.relation_type == "related_to" for entity in snapshot.entities for rel in entity.relationships)
