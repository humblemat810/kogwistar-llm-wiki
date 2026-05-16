from kogwistar_llm_wiki.namespaces import WorkspaceNamespaces


def test_promote_creates_kg_visible_node_not_workflow_artifact(pipeline, ingest_request):
    ingest_request = ingest_request.model_copy(update={"promotion_mode": "sync"})
    ns = WorkspaceNamespaces(ingest_request.workspace_id)
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

    promotion_evidence_packs = pipeline.engines.conversation.read.get_nodes(
        where={
            "workspace_id": ingest_request.workspace_id,
            "namespace": ns.conv_bg,
            "artifact_kind": "promotion_evidence_pack",
            "candidate_link_id": artifacts.candidate_link_id,
        }
    )
    assert len(promotion_evidence_packs) == 1
    evidence_pack = promotion_evidence_packs[0]
    assert evidence_pack.metadata.get("evidence_role") == "promotion"
    assert evidence_pack.metadata.get("created_from") == "parsed_graph_extraction"
    assert evidence_pack.metadata.get("evidence_pack_hash")
    assert evidence_pack.metadata.get("node_ids")
    assert evidence_pack.metadata.get("edge_ids") is not None

    promoted = kg_nodes[0]
    assert promoted.metadata.get("promotion_candidate_id") == artifacts.promotion_candidate_id
    assert promoted.metadata.get("promotion_evidence_pack_id") == str(evidence_pack.id)
    assert promoted.metadata.get("promotion_evidence_pack_digest")
    assert promoted.metadata.get("promotion_decision_reason") == "explicit promotion approval accepted by default policy"
    assert promoted.metadata.get("promotion_decision_metadata", {}).get("promotion_approved") is True

    workflow_nodes = pipeline.engines.workflow.read.get_nodes(
        where={"artifact_kind": "promoted_knowledge"}
    )
    assert not workflow_nodes


def test_repeated_sync_ingest_converges_to_same_artifact_ids(pipeline, ingest_request):
    ingest_request = ingest_request.model_copy(update={"promotion_mode": "sync"})
    ns = WorkspaceNamespaces(ingest_request.workspace_id)

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

    packs = pipeline.engines.conversation.read.get_nodes(
        where={
            "workspace_id": ingest_request.workspace_id,
            "namespace": ns.conv_bg,
            "artifact_kind": "promotion_evidence_pack",
        }
    )
    assert len(packs) == 1
