from __future__ import annotations

from kogwistar.id_provider import stable_id

from ..maintenance.maintenance_patches import (
    MaintenanceIntent,
    MaintenanceOperationKind,
    MaintenancePatch,
    MaintenancePatchOperation,
    MaintenanceProvenance,
    MaintenanceScope,
)
from .disambiguation_contracts import (
    DisambiguationArtifactStatus,
    DisambiguationCandidate,
    DisambiguationDecisionKind,
    DisambiguationEvidenceUpdate,
    DisambiguationReconciliationResult,
    DisambiguationResolutionSource,
    DisambiguationScoreBundle,
)


def is_review_request_fresh(candidate: DisambiguationCandidate, latest_evidence_version: int) -> bool:
    return (
        candidate.artifact_status == DisambiguationArtifactStatus.PENDING
        and candidate.question_is_concrete
        and candidate.semantic_decision == DisambiguationDecisionKind.AMBIGUOUS
        and int(latest_evidence_version) == int(candidate.last_reconciled_evidence_version)
        and bool(str(candidate.question or "").strip())
    )


def _updated_score_bundle(
    candidate: DisambiguationCandidate,
    update: DisambiguationEvidenceUpdate,
) -> DisambiguationScoreBundle:
    bundle = candidate.score_bundle.model_copy()
    if update.merge_likelihood is not None:
        bundle.merge_likelihood = float(update.merge_likelihood)
    if update.distinction_pressure is not None:
        bundle.distinction_pressure = float(update.distinction_pressure)
    if update.review_priority is not None:
        bundle.review_priority = float(update.review_priority)
    return bundle


def reconcile_disambiguation_candidate(
    candidate: DisambiguationCandidate,
    update: DisambiguationEvidenceUpdate,
) -> DisambiguationReconciliationResult:
    next_candidate = candidate.model_copy(deep=True)
    next_candidate.last_reconciled_evidence_version = max(
        int(candidate.last_reconciled_evidence_version),
        int(update.evidence_version),
    )
    next_candidate.score_bundle = _updated_score_bundle(candidate, update)
    if update.evidence_summary is not None:
        next_candidate.evidence_summary = str(update.evidence_summary)
    if update.replacement_question is not None:
        next_candidate.replacement_question = str(update.replacement_question)
    if not update.question_is_concrete:
        next_candidate.question_is_concrete = False

    if update.candidate_group_size is not None and int(update.candidate_group_size) != len(candidate.entity_ids):
        next_candidate.artifact_status = DisambiguationArtifactStatus.SUPERSEDED
        next_candidate.resolution_source = DisambiguationResolutionSource.NEW_EVIDENCE
        next_candidate.semantic_decision = DisambiguationDecisionKind.ILL_FORMED_CANDIDATE
        next_candidate.question_is_concrete = False
        return DisambiguationReconciliationResult(
            candidate=next_candidate,
            status=next_candidate.artifact_status,
            resolution_source=next_candidate.resolution_source,
            semantic_decision=next_candidate.semantic_decision,
            should_surface_to_user=False,
            reason="candidate set changed and the old question is no longer well formed",
            new_evidence_version=int(update.evidence_version),
            replacement_question=next_candidate.replacement_question,
        )

    if update.decision_challenged:
        next_candidate.artifact_status = DisambiguationArtifactStatus.CHALLENGED
        next_candidate.resolution_source = DisambiguationResolutionSource.PRIOR_DECISION
        next_candidate.semantic_decision = (
            candidate.semantic_decision
            if candidate.semantic_decision != DisambiguationDecisionKind.AMBIGUOUS
            else DisambiguationDecisionKind.INSUFFICIENT_EVIDENCE
        )
        next_candidate.question_is_concrete = False
        return DisambiguationReconciliationResult(
            candidate=next_candidate,
            status=next_candidate.artifact_status,
            resolution_source=next_candidate.resolution_source,
            semantic_decision=next_candidate.semantic_decision,
            should_surface_to_user=False,
            reason=update.challenge_reason or "a prior decision now conflicts with newer evidence",
            new_evidence_version=int(update.evidence_version),
            challenge_required=True,
        )

    if update.resolves_equivalence:
        next_candidate.artifact_status = DisambiguationArtifactStatus.RESOLVED
        next_candidate.resolution_source = DisambiguationResolutionSource.NEW_EVIDENCE
        next_candidate.semantic_decision = DisambiguationDecisionKind.SAME_ENTITY
        return DisambiguationReconciliationResult(
            candidate=next_candidate,
            status=next_candidate.artifact_status,
            resolution_source=next_candidate.resolution_source,
            semantic_decision=next_candidate.semantic_decision,
            should_surface_to_user=False,
            reason="new evidence explicitly proves the entities are equivalent",
            new_evidence_version=int(update.evidence_version),
            replacement_question=next_candidate.replacement_question,
        )

    if update.resolves_distinction:
        next_candidate.artifact_status = DisambiguationArtifactStatus.RESOLVED
        next_candidate.resolution_source = DisambiguationResolutionSource.NEW_EVIDENCE
        next_candidate.semantic_decision = DisambiguationDecisionKind.DISTINCT_ENTITIES
        return DisambiguationReconciliationResult(
            candidate=next_candidate,
            status=next_candidate.artifact_status,
            resolution_source=next_candidate.resolution_source,
            semantic_decision=next_candidate.semantic_decision,
            should_surface_to_user=False,
            reason="new evidence explicitly proves the entities are distinct",
            new_evidence_version=int(update.evidence_version),
            replacement_question=next_candidate.replacement_question,
        )

    if update.user_decision is not None:
        next_candidate.artifact_status = DisambiguationArtifactStatus.RESOLVED
        next_candidate.resolution_source = DisambiguationResolutionSource.USER
        next_candidate.semantic_decision = update.user_decision
        return DisambiguationReconciliationResult(
            candidate=next_candidate,
            status=next_candidate.artifact_status,
            resolution_source=next_candidate.resolution_source,
            semantic_decision=next_candidate.semantic_decision,
            should_surface_to_user=False,
            reason="the user resolved the disambiguation question",
            new_evidence_version=int(update.evidence_version),
            replacement_question=next_candidate.replacement_question,
        )

    if update.policy_decision is not None:
        next_candidate.artifact_status = DisambiguationArtifactStatus.RESOLVED
        next_candidate.resolution_source = DisambiguationResolutionSource.POLICY
        next_candidate.semantic_decision = update.policy_decision
        return DisambiguationReconciliationResult(
            candidate=next_candidate,
            status=next_candidate.artifact_status,
            resolution_source=next_candidate.resolution_source,
            semantic_decision=next_candidate.semantic_decision,
            should_surface_to_user=False,
            reason="policy resolved the disambiguation question",
            new_evidence_version=int(update.evidence_version),
            replacement_question=next_candidate.replacement_question,
        )

    if not update.question_is_concrete:
        next_candidate.artifact_status = DisambiguationArtifactStatus.STALE
        next_candidate.resolution_source = DisambiguationResolutionSource.NEW_EVIDENCE
        next_candidate.semantic_decision = DisambiguationDecisionKind.AMBIGUOUS
        return DisambiguationReconciliationResult(
            candidate=next_candidate,
            status=next_candidate.artifact_status,
            resolution_source=next_candidate.resolution_source,
            semantic_decision=next_candidate.semantic_decision,
            should_surface_to_user=False,
            reason="evidence changed but the old question is no longer concrete enough to ask",
            new_evidence_version=int(update.evidence_version),
            replacement_question=next_candidate.replacement_question,
        )

    if update.missing_evidence:
        next_candidate.artifact_status = (
            DisambiguationArtifactStatus.NEEDS_MORE_EVIDENCE_COLLECTION
            if update.evidence_version >= candidate.last_reconciled_evidence_version
            else DisambiguationArtifactStatus.INSUFFICIENT_EVIDENCE
        )
        next_candidate.resolution_source = DisambiguationResolutionSource.NONE
        next_candidate.semantic_decision = DisambiguationDecisionKind.INSUFFICIENT_EVIDENCE
        next_candidate.replacement_question = (
            update.replacement_question or next_candidate.replacement_question
        )
        return DisambiguationReconciliationResult(
            candidate=next_candidate,
            status=next_candidate.artifact_status,
            resolution_source=next_candidate.resolution_source,
            semantic_decision=next_candidate.semantic_decision,
            should_surface_to_user=False,
            reason="evidence is still incomplete and more collection is needed",
            new_evidence_version=int(update.evidence_version),
            replacement_question=next_candidate.replacement_question,
        )

    next_candidate.artifact_status = DisambiguationArtifactStatus.PENDING
    next_candidate.resolution_source = DisambiguationResolutionSource.NONE
    next_candidate.semantic_decision = DisambiguationDecisionKind.AMBIGUOUS
    should_surface = is_review_request_fresh(next_candidate, int(update.evidence_version))
    return DisambiguationReconciliationResult(
        candidate=next_candidate,
        status=next_candidate.artifact_status,
        resolution_source=next_candidate.resolution_source,
        semantic_decision=next_candidate.semantic_decision,
        should_surface_to_user=should_surface,
        reason="candidate remains ambiguous but the question is still concrete",
        new_evidence_version=int(update.evidence_version),
        replacement_question=next_candidate.replacement_question,
    )


def _provenance_for_disambiguation(
    candidate: DisambiguationCandidate,
    *,
    maintenance_run_id: str,
    source_document_id: str | None,
    source_span_ids: tuple[str, ...],
    confidence: float,
) -> MaintenanceProvenance:
    return MaintenanceProvenance(
        source_document_id=source_document_id
        or (candidate.source_document_ids[0] if candidate.source_document_ids else None),
        source_span_ids=list(source_span_ids or candidate.source_span_ids),
        maintenance_run_id=maintenance_run_id,
        confidence=confidence,
    )


def build_disambiguation_patch(
    candidate: DisambiguationCandidate,
    result: DisambiguationReconciliationResult,
    *,
    maintenance_run_id: str,
    source_document_id: str | None = None,
    source_span_ids: tuple[str, ...] = (),
    confidence: float = 0.85,
) -> MaintenancePatch:
    scope = MaintenanceScope(workspace_id=candidate.workspace_id)
    provenance = _provenance_for_disambiguation(
        candidate,
        maintenance_run_id=maintenance_run_id,
        source_document_id=source_document_id,
        source_span_ids=source_span_ids,
        confidence=confidence,
    )

    if (
        result.status == DisambiguationArtifactStatus.RESOLVED
        and result.semantic_decision == DisambiguationDecisionKind.SAME_ENTITY
    ):
        canonical_entity_id = candidate.canonical_entity_id or candidate.entity_ids[0]
        related_entity_ids = tuple(
            entity_id
            for entity_id in candidate.entity_ids
            if entity_id != canonical_entity_id
        )
        operations = [
            MaintenancePatchOperation(
                operation_id=str(
                    f"ws:{candidate.workspace_id}:op:{stable_id('disambiguation_alias_edge', candidate.artifact_id, canonical_entity_id, related_entity_id)}"
                ),
                kind=MaintenanceOperationKind.ADD_EDGE,
                edge_id=str(
                    f"ws:{candidate.workspace_id}:edge:{stable_id('disambiguation_alias_edge_record', candidate.artifact_id, canonical_entity_id, related_entity_id)}"
                ),
                from_node_id=related_entity_id,
                to_node_id=canonical_entity_id,
                relation="alias_of",
                label="alias_of",
                provenance=provenance,
                properties={
                    "artifact_kind": "entity_disambiguation_decision",
                    "candidate_key": candidate.candidate_key,
                    "disambiguation_decision": result.semantic_decision.value,
                    "evidence_snapshot_id": candidate.evidence_snapshot_id,
                    "evidence_version": int(result.new_evidence_version),
                },
            )
            for related_entity_id in related_entity_ids
        ]
        intent = MaintenanceIntent.MERGE_NODES
    elif (
        result.status == DisambiguationArtifactStatus.RESOLVED
        and result.semantic_decision == DisambiguationDecisionKind.DISTINCT_ENTITIES
    ):
        left_entity_id = candidate.canonical_entity_id or candidate.entity_ids[0]
        operations = []
        for right_entity_id in candidate.entity_ids[1:]:
            operations.append(
                MaintenancePatchOperation(
                    operation_id=str(
                        f"ws:{candidate.workspace_id}:op:{stable_id('disambiguation_distinct_edge', candidate.artifact_id, left_entity_id, right_entity_id)}"
                    ),
                    kind=MaintenanceOperationKind.ADD_EDGE,
                    edge_id=str(
                        f"ws:{candidate.workspace_id}:edge:{stable_id('disambiguation_distinct_edge_record', candidate.artifact_id, left_entity_id, right_entity_id)}"
                    ),
                    from_node_id=left_entity_id,
                    to_node_id=right_entity_id,
                    relation="disambiguates_from",
                    label="disambiguates_from",
                    provenance=provenance,
                    properties={
                        "artifact_kind": "entity_disambiguation_decision",
                        "candidate_key": candidate.candidate_key,
                        "disambiguation_decision": result.semantic_decision.value,
                        "evidence_snapshot_id": candidate.evidence_snapshot_id,
                        "evidence_version": int(result.new_evidence_version),
                    },
                )
            )
        intent = MaintenanceIntent.CORRECT_FACT
    else:
        operations = [
            MaintenancePatchOperation(
                operation_id=str(
                    f"ws:{candidate.workspace_id}:op:{stable_id('disambiguation_review_request', candidate.artifact_id, result.new_evidence_version)}"
                ),
                kind=MaintenanceOperationKind.REQUEST_REVIEW,
                reason=result.reason,
            )
        ]
        intent = MaintenanceIntent.REQUEST_REVIEW

    return MaintenancePatch(
        patch_id=str(
            stable_id(
                "disambiguation_patch",
                candidate.artifact_id,
                result.semantic_decision.value,
                result.status.value,
                result.new_evidence_version,
            )
        ),
        intent=intent,
        scope=scope,
        operations=operations,
        rationale=result.reason,
    )


__all__ = [
    "DisambiguationArtifactStatus",
    "DisambiguationCandidate",
    "DisambiguationDecisionKind",
    "DisambiguationEvidenceUpdate",
    "DisambiguationReconciliationResult",
    "DisambiguationResolutionSource",
    "DisambiguationScoreBundle",
    "build_disambiguation_patch",
    "is_review_request_fresh",
    "reconcile_disambiguation_candidate",
]


