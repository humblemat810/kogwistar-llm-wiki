def test_ingest_pipeline_smoke(pipeline, ingest_request):
    artifacts = pipeline.run(ingest_request)
    assert artifacts.source_document_id
    assert artifacts.maintenance_job_id
    assert artifacts.candidate_link_id
