def test_promote_creates_kg_visible_node_not_workflow_artifact(pipeline, ingest_request):
    ingest_request = ingest_request.model_copy(update={"promotion_mode": "sync"})
    artifacts = pipeline.run(ingest_request)

    kg_nodes = pipeline.engines.kg.read.get_nodes(
        where={
            "workspace_id": ingest_request.workspace_id,
            "projection_visible": True,
            "artifact_kind": "promoted_knowledge",
        }
    )
    assert kg_nodes
    assert {node.id for node in kg_nodes} == {artifacts.promoted_entity_id}

    workflow_nodes = pipeline.engines.workflow.read.get_nodes(
        where={"artifact_kind": "promoted_knowledge"}
    )
    assert not workflow_nodes
