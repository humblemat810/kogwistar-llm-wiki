def test_ingest_pipeline_smoke(pipeline, ingest_request, seeded_kg_node):
    artifacts = pipeline.run(ingest_request)
    assert artifacts.source_document_id
    assert artifacts.source_node_id
    assert artifacts.maintenance_request_id
