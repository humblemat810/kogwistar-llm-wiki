from kogwistar_llm_wiki.models import IngestPipelineArtifacts, IngestPipelineRequest


def test_ingest_pipeline_request_defaults():
    request = IngestPipelineRequest(
        workspace_id="ws1",
        source_uri="file:///tmp/doc.txt",
        title="Doc",
        raw_text="hello",
    )
    assert request.source_format == "text"
    assert request.parser_mode == "heuristic"
    assert request.auto_accept_threshold == 0.95


def test_ingest_pipeline_artifacts_shape():
    artifacts = IngestPipelineArtifacts(
        source_document_id="doc-1",
        source_node_id="node-1",
        maintenance_request_id="job-1",
        candidate_link_node_ids=("cand-1",),
        promotion_candidate_node_ids=("prom-1",),
        promoted_edge_ids=("edge-1",),
    )
    assert artifacts.promoted_edge_ids == ("edge-1",)
