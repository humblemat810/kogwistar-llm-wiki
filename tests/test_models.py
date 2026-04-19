from kogwistar_llm_wiki import IngestPipelineArtifacts, IngestPipelineRequest


def test_request_defaults():
    # Pydantic validates keyword arguments
    request = IngestPipelineRequest(
        workspace_id="demo",
        source_uri="file:///x.txt",
        title="X",
        raw_text="hello",
    )
    assert request.source_format == "text"
    assert request.parser_mode == "heuristic"


def test_request_slicing():
    # Verify that we can create slices
    from pydantic_extension.model_slicing import ModeSlicingMixin
    
    # IngestPipelineRequest["dto"] should include the marked fields
    DtoSlice = IngestPipelineRequest["dto"]
    assert "workspace_id" in DtoSlice.model_fields
    assert "raw_text" in DtoSlice.model_fields
    
    # Verify it behaves like a Pydantic model
    dto = DtoSlice(
        workspace_id="demo",
        source_uri="file:///x.txt",
        title="X",
        raw_text="hello",
    )
    assert dto.workspace_id == "demo"


def test_artifacts_capture_promotion_state():
    artifacts = IngestPipelineArtifacts(
        source_document_id="source:1",
        maintenance_job_id="job:1",
        candidate_link_id="candidate:1",
        promotion_candidate_id="promotion:1",
        promoted_entity_id="kg:1",
    )
    assert artifacts.promoted_entity_id == "kg:1"
