from unittest.mock import Mock

from kogwistar_llm_wiki import IngestPipeline


def test_conversation_lanes_share_same_engine(pipeline):
    assert pipeline.engines.conversation is pipeline.engines.conversation


def test_pipeline_invokes_parser_and_persists_result(pipeline, ingest_request):
    parser = Mock(wraps=pipeline.parser)
    pipeline.parser = parser
    artifacts = pipeline.run(ingest_request)
    parser.assert_called_once()
    assert artifacts.source_document_id
