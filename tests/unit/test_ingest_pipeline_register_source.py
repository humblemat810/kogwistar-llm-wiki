from typing import cast

from kogwistar_llm_wiki import IngestPipeline


def test_run_registers_document_in_foreground(pipeline, ingest_request):
    pipeline_c = cast(IngestPipeline, pipeline)
    artifacts = pipeline_c.run(ingest_request)
    documents = pipeline_c.engines.conv_fg.backend.document_query(limit=50)
    assert documents
    assert artifacts.source_document_id == documents["ids"][0][0]
