from __future__ import annotations

from kogwistar_llm_wiki.disambiguation_service import DisambiguationService
from kogwistar_llm_wiki.entity_disambiguation import (
    DisambiguationArtifactStatus,
    DisambiguationCandidate,
    DisambiguationDecisionKind,
    DisambiguationResolutionSource,
    DisambiguationScoreBundle,
)
from kogwistar_llm_wiki.namespaces import WorkspaceNamespaces
from kogwistar_llm_wiki.utils import _temporary_namespace


def _candidate() -> DisambiguationCandidate:
    return DisambiguationCandidate(
        artifact_id="ws:demo:disambiguation:alice",
        workspace_id="demo",
        candidate_key="demo:alice",
        entity_ids=("ws:demo:entity:alice-1", "ws:demo:entity:alice-2"),
        evidence_snapshot_id="snapshot-1",
        evidence_cutoff_ms=1_700_000_000_000,
        question="Are these the same Alice?",
        evidence_summary="Shared label and neighborhood suggest possible aliasing.",
        canonical_entity_id="ws:demo:entity:alice-1",
        related_entity_ids=("ws:demo:entity:alice-2",),
        source_document_ids=("doc-1",),
        source_span_ids=("span-1",),
        score_bundle=DisambiguationScoreBundle(
            merge_likelihood=0.66,
            distinction_pressure=0.34,
            review_priority=0.72,
            usage_frequency=3,
            answer_risk=0.83,
        ),
    )


def test_service_records_user_answer_and_persists_decision_node(namespace_engines) -> None:
    service = DisambiguationService(namespace_engines)
    candidate = _candidate()

    record = service.record_user_answer(
        candidate,
        evidence_version=1,
        decision=DisambiguationDecisionKind.SAME_ENTITY,
        evidence_summary="The user confirms both names are the same person.",
    )

    assert record.reconciliation.status == DisambiguationArtifactStatus.RESOLVED
    assert record.reconciliation.resolution_source == DisambiguationResolutionSource.USER
    assert record.patch_intent == "merge_nodes"
    assert record.decision_kind == "same_entity"

    ns = WorkspaceNamespaces(candidate.workspace_id)
    with _temporary_namespace(namespace_engines.conversation, ns.conv_bg):
        nodes = namespace_engines.conversation.read.get_nodes(
            where={
                "workspace_id": candidate.workspace_id,
                "artifact_kind": "disambiguation_decision",
                "candidate_key": candidate.candidate_key,
            },
            limit=10,
        )

    assert len(nodes) == 1
    node = nodes[0]
    assert node.id == record.decision_node_id
    assert node.metadata.get("answer_source") == "user"
    assert node.metadata.get("resolution_source") == "user"
    assert node.metadata.get("semantic_decision") == "same_entity"


def test_service_records_reviewer_answer_and_challenge(namespace_engines) -> None:
    service = DisambiguationService(namespace_engines)
    candidate = _candidate()

    reviewed = service.record_reviewer_answer(
        candidate,
        evidence_version=2,
        decision=DisambiguationDecisionKind.DISTINCT_ENTITIES,
        evidence_summary="The reviewer found a hard identifier mismatch.",
    )
    challenged = service.challenge_decision(
        reviewed.candidate,
        evidence_version=3,
        challenge_reason="A later source contradicts the prior separation",
        evidence_summary="New evidence points to a single canonical entity.",
    )

    assert reviewed.reconciliation.resolution_source == DisambiguationResolutionSource.POLICY
    assert reviewed.patch_intent == "correct_fact"
    assert challenged.reconciliation.challenge_required is True
    assert challenged.reconciliation.status == DisambiguationArtifactStatus.CHALLENGED
    assert challenged.patch_intent == "request_review"

    ns = WorkspaceNamespaces(candidate.workspace_id)
    with _temporary_namespace(namespace_engines.conversation, ns.conv_bg):
        challenge_nodes = namespace_engines.conversation.read.get_nodes(
            where={
                "workspace_id": candidate.workspace_id,
                "artifact_kind": "disambiguation_decision_challenge",
                "candidate_key": candidate.candidate_key,
            },
            limit=10,
        )

    assert len(challenge_nodes) == 1
    assert challenge_nodes[0].metadata.get("challenge_reason") == "A later source contradicts the prior separation"

