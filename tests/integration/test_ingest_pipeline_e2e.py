def test_ingest_pipeline_e2e(pipeline, ingest_request):
    artifacts = pipeline.run(ingest_request)

    conversation_nodes = pipeline.engines.conversation.read.get_nodes(
        where={"workspace_id": ingest_request.workspace_id}
    )
    workflow_nodes = pipeline.engines.workflow.read.get_nodes(
        where={"workspace_id": ingest_request.workspace_id}
    )
    kg_nodes = pipeline.engines.kg.read.get_nodes(
        where={"workspace_id": ingest_request.workspace_id}
    )

    stored_document = pipeline.engines.conversation.backend.document_get(
        ids=[artifacts.source_document_id],
        include=["documents", "metadatas"],
    )

    assert stored_document["ids"] == [artifacts.source_document_id]
    assert pipeline.engines.conversation.read.get_nodes(where={"doc_id": artifacts.source_document_id})
    assert any(node.metadata.get("artifact_kind") == "candidate_link" and node.metadata.get("namespace") == "ws:demo:conv:bg" for node in conversation_nodes)
    assert any(node.metadata.get("artifact_kind") == "promotion_candidate" and node.metadata.get("namespace") == "ws:demo:review" for node in conversation_nodes)
    assert any(node.metadata.get("artifact_kind") == "maintenance_job_request" and node.metadata.get("namespace") == "ws:demo:wf:maintenance" for node in workflow_nodes)
    assert any(node.metadata.get("artifact_kind") == "promoted_knowledge" and node.metadata.get("projection_visible") for node in kg_nodes)
    assert artifacts.promoted_entity_id in {node.id for node in kg_nodes}

    snapshot = pipeline.build_projection_snapshot()
    assert {entity.title for entity in snapshot.entities} == {node.label for node in kg_nodes if node.metadata.get("projection_visible")}
