"""Tests for the lightweight review query helper.

The phase 6 slice keeps review artifacts in the background conversation lane
and exposes a thin query helper over the existing persisted metadata chain.
"""

from __future__ import annotations

from kogwistar.engine_core.models import Grounding, Node, Span

from kogwistar_llm_wiki.namespaces import WorkspaceNamespaces
from kogwistar_llm_wiki.utils import _temporary_namespace


def _sync_request(ingest_request, *, workspace_id: str, source_uri: str) -> object:
    """Build a sync-mode ingest request for the review query tests."""
    return ingest_request.model_copy(
        update={
            "workspace_id": workspace_id,
            "source_uri": source_uri,
            "promotion_mode": "sync",
        }
    )


def _span(doc_id: str) -> Span:
    """Create a tiny grounding span used for the synthetic lane-noise node."""
    return Span.model_validate(
        {
            "collection_page_url": f"document_collection/{doc_id}",
            "document_page_url": f"document/{doc_id}",
            "doc_id": doc_id,
            "insertion_method": "manual",
            "page_number": 1,
            "start_char": 0,
            "end_char": 1,
            "excerpt": "A",
            "context_before": "",
            "context_after": "B",
            "chunk_id": None,
            "source_cluster_id": None,
        }
    )


def _lane_noise_node(*, workspace_id: str, doc_id: str) -> Node:
    """Build a background conversation node that should be excluded by review queries."""
    return Node(
        label="Lane Noise",
        type="entity",
        summary="lane noise",
        doc_id=doc_id,
        mentions=[Grounding(spans=[_span(doc_id)])],
        metadata={
            "workspace_id": workspace_id,
            "artifact_kind": "lane_message",
            "conversation_lane": "background",
            "visibility": "internal",
        },
    )


def test_review_query_helper_returns_review_chain(pipeline, ingest_request):
    """The helper should recover the candidate, evidence pack, and promoted node chain."""
    request = _sync_request(
        ingest_request,
        workspace_id="review-helper-chain",
        source_uri="file:///contracts/review-helper-chain.txt",
    )
    artifacts = pipeline.run(request)
    review = pipeline.review_query_service
    ns = WorkspaceNamespaces(request.workspace_id)

    review_nodes = review.get_review_nodes(workspace_id=request.workspace_id)
    assert {node.metadata.get("artifact_kind") for node in review_nodes} == {
        "candidate_link",
        "promotion_candidate",
        "promotion_evidence_pack",
    }
    assert all(node.metadata.get("conversation_lane") == "background" for node in review_nodes)
    assert all(node.metadata.get("workspace_id") == request.workspace_id for node in review_nodes)

    candidate_links = review.get_candidate_links(workspace_id=request.workspace_id)
    candidates = review.get_promotion_candidates(
        workspace_id=request.workspace_id,
        candidate_link_id=artifacts.candidate_link_id,
    )
    evidence_packs = review.get_promotion_evidence_packs(
        workspace_id=request.workspace_id,
        candidate_link_id=artifacts.candidate_link_id,
    )
    chain = review.get_review_chain_for_promoted_node(
        workspace_id=request.workspace_id,
        promoted_node_id=artifacts.promoted_entity_id,
    )

    assert len(candidate_links) == 1
    assert candidate_links[0].id == artifacts.candidate_link_id
    assert len(candidates) == 1
    assert candidates[0].id == artifacts.promotion_candidate_id
    assert len(evidence_packs) == 1
    assert evidence_packs[0].metadata.get("candidate_link_id") == artifacts.candidate_link_id

    assert chain is not None
    assert chain.promoted_node.id == artifacts.promoted_entity_id
    assert chain.candidate_link is not None
    assert chain.candidate_link.id == artifacts.candidate_link_id
    assert chain.promotion_candidate is not None
    assert chain.promotion_candidate.id == artifacts.promotion_candidate_id
    assert chain.promotion_evidence_pack is not None
    assert chain.promotion_evidence_pack.metadata.get("candidate_link_id") == artifacts.candidate_link_id

    with _temporary_namespace(pipeline.engines.conversation, ns.conv_bg):
        lane_noise = _lane_noise_node(
            workspace_id=request.workspace_id,
            doc_id=pipeline._source_document_id(request),
        )
        pipeline.engines.conversation.write.add_node(lane_noise)

    filtered = review.get_review_nodes(workspace_id=request.workspace_id)
    assert all(node.metadata.get("artifact_kind") != "lane_message" for node in filtered)


def test_review_query_helper_is_workspace_scoped(pipeline, ingest_request):
    """Review queries must not leak artifacts across workspaces."""
    first = _sync_request(
        ingest_request,
        workspace_id="review-helper-a",
        source_uri="file:///contracts/review-helper-a.txt",
    )
    second = _sync_request(
        ingest_request,
        workspace_id="review-helper-b",
        source_uri="file:///contracts/review-helper-b.txt",
    )
    first_artifacts = pipeline.run(first)
    second_artifacts = pipeline.run(second)
    review = pipeline.review_query_service

    first_candidates = review.get_promotion_candidates(workspace_id=first.workspace_id)
    second_candidates = review.get_promotion_candidates(workspace_id=second.workspace_id)

    assert [node.id for node in first_candidates] == [first_artifacts.promotion_candidate_id]
    assert [node.id for node in second_candidates] == [second_artifacts.promotion_candidate_id]
    assert first_candidates[0].metadata.get("workspace_id") == first.workspace_id
    assert second_candidates[0].metadata.get("workspace_id") == second.workspace_id
