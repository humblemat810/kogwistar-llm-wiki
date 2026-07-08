from __future__ import annotations

from kogwistar_llm_wiki.entity_disambiguation import (
    DisambiguationArtifactStatus,
    DisambiguationCandidate,
    DisambiguationDecisionKind,
    DisambiguationEvidenceUpdate,
    DisambiguationResolutionSource,
    DisambiguationScoreBundle,
    build_disambiguation_patch,
    is_review_request_fresh,
    reconcile_disambiguation_candidate,
)
from kogwistar_llm_wiki.maintenance_patches import (
    MaintenanceIntent,
    MaintenanceOperationKind,
    validate_maintenance_patch,
)


def _candidate() -> DisambiguationCandidate:
    return DisambiguationCandidate(
        artifact_id="ws:demo:disambiguation:john-smith",
        workspace_id="demo",
        candidate_key="demo:john-smith",
        entity_ids=("ws:demo:entity:john-1", "ws:demo:entity:john-2"),
        evidence_snapshot_id="snapshot-1",
        evidence_cutoff_ms=1_700_000_000_000,
        question="Are these the same John Smith?",
        evidence_summary="Two records share a label and district but differ on role.",
        canonical_entity_id="ws:demo:entity:john-1",
        related_entity_ids=("ws:demo:entity:john-2",),
        source_document_ids=("doc-1",),
        source_span_ids=("span-1",),
        score_bundle=DisambiguationScoreBundle(
            merge_likelihood=0.58,
            distinction_pressure=0.42,
            review_priority=0.76,
            usage_frequency=4,
            answer_risk=0.81,
        ),
    )


def test_review_request_freshness_depends_on_current_evidence_version() -> None:
    candidate = _candidate()

    assert is_review_request_fresh(candidate, 0) is True

    updated = reconcile_disambiguation_candidate(
        candidate,
        DisambiguationEvidenceUpdate(
            evidence_version=1,
            missing_evidence=["hard identifier"],
            replacement_question="Are these the same John Smith, given the new source?",
        ),
    )

    assert updated.status == DisambiguationArtifactStatus.NEEDS_MORE_EVIDENCE_COLLECTION
    assert updated.should_surface_to_user is False
    assert updated.replacement_question == "Are these the same John Smith, given the new source?"
    assert is_review_request_fresh(updated.candidate, 1) is False


def test_new_evidence_resolves_equivalence_into_alias_patch() -> None:
    candidate = _candidate()

    result = reconcile_disambiguation_candidate(
        candidate,
        DisambiguationEvidenceUpdate(
            evidence_version=2,
            resolves_equivalence=True,
            evidence_summary="The later source says the two names refer to the same person.",
        ),
    )
    patch = build_disambiguation_patch(
        candidate,
        result,
        maintenance_run_id="run-1",
        source_document_id="doc-2",
        source_span_ids=("span-2",),
    )

    assert result.status == DisambiguationArtifactStatus.RESOLVED
    assert result.resolution_source == DisambiguationResolutionSource.NEW_EVIDENCE
    assert result.semantic_decision == DisambiguationDecisionKind.SAME_ENTITY
    assert patch.intent == MaintenanceIntent.MERGE_NODES
    assert [operation.kind for operation in patch.operations] == [MaintenanceOperationKind.ADD_EDGE]
    operation = patch.operations[0]
    assert operation.relation == "alias_of"
    assert operation.from_node_id == "ws:demo:entity:john-2"
    assert operation.to_node_id == "ws:demo:entity:john-1"

    report = validate_maintenance_patch(
        patch,
        active_node_ids={"ws:demo:entity:john-1", "ws:demo:entity:john-2"},
        namespace_prefix="ws:demo:",
    )
    assert report.valid is True


def test_new_evidence_resolves_distinction_into_distinctness_patch() -> None:
    candidate = _candidate()

    result = reconcile_disambiguation_candidate(
        candidate,
        DisambiguationEvidenceUpdate(
            evidence_version=3,
            resolves_distinction=True,
            evidence_summary="The new source explicitly says the two people are not related.",
        ),
    )
    patch = build_disambiguation_patch(
        candidate,
        result,
        maintenance_run_id="run-2",
        source_document_id="doc-3",
        source_span_ids=("span-3",),
    )

    assert result.status == DisambiguationArtifactStatus.RESOLVED
    assert result.resolution_source == DisambiguationResolutionSource.NEW_EVIDENCE
    assert result.semantic_decision == DisambiguationDecisionKind.DISTINCT_ENTITIES
    assert patch.intent == MaintenanceIntent.CORRECT_FACT
    assert [operation.kind for operation in patch.operations] == [MaintenanceOperationKind.ADD_EDGE]
    operation = patch.operations[0]
    assert operation.relation == "disambiguates_from"
    assert operation.from_node_id == "ws:demo:entity:john-1"
    assert operation.to_node_id == "ws:demo:entity:john-2"

    report = validate_maintenance_patch(
        patch,
        active_node_ids={"ws:demo:entity:john-1", "ws:demo:entity:john-2"},
        namespace_prefix="ws:demo:",
    )
    assert report.valid is True


def test_new_evidence_supersedes_when_candidate_group_changes() -> None:
    candidate = _candidate()

    result = reconcile_disambiguation_candidate(
        candidate,
        DisambiguationEvidenceUpdate(
            evidence_version=4,
            candidate_group_size=3,
            question_is_concrete=False,
            replacement_question="Does this refer to three related records instead of two?",
        ),
    )
    patch = build_disambiguation_patch(
        candidate,
        result,
        maintenance_run_id="run-3",
    )

    assert result.status == DisambiguationArtifactStatus.SUPERSEDED
    assert result.should_surface_to_user is False
    assert result.semantic_decision == DisambiguationDecisionKind.ILL_FORMED_CANDIDATE
    assert patch.intent == MaintenanceIntent.REQUEST_REVIEW
    assert [operation.kind for operation in patch.operations] == [MaintenanceOperationKind.REQUEST_REVIEW]


def test_new_evidence_can_challenge_a_previous_decision_without_overwriting_history() -> None:
    candidate = _candidate().model_copy(
        update={
            "artifact_status": DisambiguationArtifactStatus.RESOLVED,
            "resolution_source": DisambiguationResolutionSource.NEW_EVIDENCE,
            "semantic_decision": DisambiguationDecisionKind.SAME_ENTITY,
        }
    )

    result = reconcile_disambiguation_candidate(
        candidate,
        DisambiguationEvidenceUpdate(
            evidence_version=5,
            decision_challenged=True,
            challenge_reason="the newer source identifies distinct people",
        ),
    )
    patch = build_disambiguation_patch(
        candidate,
        result,
        maintenance_run_id="run-4",
    )

    assert result.status == DisambiguationArtifactStatus.CHALLENGED
    assert result.resolution_source == DisambiguationResolutionSource.PRIOR_DECISION
    assert result.challenge_required is True
    assert patch.intent == MaintenanceIntent.REQUEST_REVIEW


def test_policy_or_user_resolution_maps_to_the_matching_resolution_source() -> None:
    candidate = _candidate()

    result = reconcile_disambiguation_candidate(
        candidate,
        DisambiguationEvidenceUpdate(
            evidence_version=6,
            policy_decision=DisambiguationDecisionKind.SAME_ENTITY,
        ),
    )
    patch = build_disambiguation_patch(
        candidate,
        result,
        maintenance_run_id="run-5",
    )

    assert result.status == DisambiguationArtifactStatus.RESOLVED
    assert result.resolution_source == DisambiguationResolutionSource.POLICY
    assert result.semantic_decision == DisambiguationDecisionKind.SAME_ENTITY
    assert patch.intent == MaintenanceIntent.MERGE_NODES
