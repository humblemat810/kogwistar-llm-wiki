from kogwistar.engine_core.models import Edge


def test_promote_creates_new_kg_edge_not_candidate_mutation(pipeline, ingest_request, seeded_kg_node):
    artifacts = pipeline.run(ingest_request)
    edges = pipeline.engines.kg.get_edges(edge_type=Edge, limit=100)
    ids = {str(edge.id) for edge in edges}
    assert set(artifacts.promoted_edge_ids).issubset(ids)
    assert all(edge.relation == "related_to" for edge in edges)
