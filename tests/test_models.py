from kogwistar_llm_wiki import IngestPipelineArtifacts, IngestPipelineRequest


def test_request_defaults():
    request = IngestPipelineRequest(
        workspace_id="demo",
        source_uri="file:///x.txt",
        title="X",
        raw_text="hello",
    )
    assert request.source_format == "text"
    assert request.parser_mode == "heuristic"


def test_artifacts_capture_promotion_state():
    artifacts = IngestPipelineArtifacts(
        source_document_id="source:1",
        maintenance_job_id="job:1",
        candidate_link_id="candidate:1",
        promotion_candidate_id="promotion:1",
        promoted_entity_id="kg:1",
    )
    assert artifacts.promoted_entity_id == "kg:1"
