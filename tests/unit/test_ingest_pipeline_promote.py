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


def test_repeated_sync_ingest_converges_to_same_artifact_ids(pipeline, ingest_request):
    ingest_request = ingest_request.model_copy(update={"promotion_mode": "sync"})

    first = pipeline.run(ingest_request)
    second = pipeline.run(ingest_request)

    assert second.candidate_link_id == first.candidate_link_id
    assert second.promotion_candidate_id == first.promotion_candidate_id
    assert second.promoted_entity_id == first.promoted_entity_id

    kg_nodes = pipeline.engines.kg.read.get_nodes(
        where={
            "workspace_id": ingest_request.workspace_id,
            "artifact_kind": "promoted_knowledge",
        }
    )
    assert [str(node.id) for node in kg_nodes] == [first.promoted_entity_id]
