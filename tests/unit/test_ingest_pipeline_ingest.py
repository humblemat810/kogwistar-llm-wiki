from kogwistar.engine_core.models import Node


def test_run_creates_source_and_fragment_nodes_in_foreground(pipeline, ingest_request):
    artifacts = pipeline.run(ingest_request)
    nodes = pipeline.engines.conv_fg.get_nodes(node_type=Node, limit=100)
    ids = {str(node.id) for node in nodes}
    assert artifacts.source_node_id in ids
    assert any(node.metadata.get("artifact_type") == "fragment" for node in nodes)
