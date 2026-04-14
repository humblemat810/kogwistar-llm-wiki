from kogwistar_llm_wiki import IngestPipelineRequest


def test_request_defaults():
    request = IngestPipelineRequest(
        workspace_id="demo",
        source_uri="file:///x.txt",
        title="X",
        raw_text="hello",
    )
    assert request.source_format == "text"
    assert request.parser_mode == "heuristic"
