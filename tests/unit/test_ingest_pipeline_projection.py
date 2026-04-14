def test_projection_reads_kg_visible_state_only(pipeline, ingest_request, seeded_kg_node):
    pipeline.run(ingest_request)
    snapshot = pipeline.build_projection_snapshot()
    assert snapshot.entities
    assert "Payment Terms" in {entity.title for entity in snapshot.entities}
