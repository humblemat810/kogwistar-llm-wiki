from kogwistar_llm_wiki.namespaces import WorkspaceNamespaces


def test_promotion_candidate_stays_out_of_workflow_storage(pipeline, ingest_request):
    artifacts = pipeline.run(ingest_request)
    ns = WorkspaceNamespaces(ingest_request.workspace_id)

    workflow_candidates = pipeline.engines.workflow.read.get_nodes(
        where={"artifact_kind": "promotion_candidate"}
    )
    assert not workflow_candidates

    background_candidates = pipeline.engines.conversation.read.get_nodes(
        where={
            "workspace_id": ingest_request.workspace_id,
            "artifact_kind": "promotion_candidate",
            "namespace": "ws:demo:conv:bg",
            "queue_state": "pending",
        }
    )
    assert background_candidates
    assert {node.id for node in background_candidates} == {artifacts.promotion_candidate_id}

    promotion_evidence_packs = pipeline.engines.conversation.read.get_nodes(
        where={
            "workspace_id": ingest_request.workspace_id,
            "namespace": ns.conv_bg,
            "artifact_kind": "promotion_evidence_pack",
            "candidate_link_id": artifacts.candidate_link_id,
        }
    )
    assert len(promotion_evidence_packs) == 1
    pack = promotion_evidence_packs[0]
    candidate = background_candidates[0]
    assert candidate.metadata.get("promotion_evidence_pack_id") == str(pack.id)
    assert candidate.metadata.get("promotion_evidence_pack_digest")
    assert candidate.metadata.get("lineage_node_ids") == [
        pipeline._source_document_id(ingest_request),
        artifacts.candidate_link_id,
    ]
    assert candidate.metadata.get("lineage_edge_ids") == []
