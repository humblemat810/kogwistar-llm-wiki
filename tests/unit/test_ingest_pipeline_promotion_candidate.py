from typing import cast

from kogwistar.engine_core.models import Node
from kogwistar_llm_wiki import IngestPipeline


def test_promotion_candidate_is_separate_review_artifact(pipeline, ingest_request, seeded_kg_node):
    pipeline_c = cast(IngestPipeline, pipeline)
    artifacts = pipeline_c.run(ingest_request)
    workflow_nodes = pipeline_c.engines.workflow.get_nodes(node_type=Node, limit=100)
    promotion_nodes = [node for node in workflow_nodes if node.metadata.get("artifact_type") == "promotion_candidate"]
    ids = {str(node.id) for node in promotion_nodes}

    assert set(artifacts.promotion_candidate_node_ids).issubset(ids)
    assert promotion_nodes
    assert all(node.properties.get("accepted") is True for node in promotion_nodes)
    assert all(node.metadata.get("namespace") == f"ws:{ingest_request.workspace_id}:review" for node in promotion_nodes)
