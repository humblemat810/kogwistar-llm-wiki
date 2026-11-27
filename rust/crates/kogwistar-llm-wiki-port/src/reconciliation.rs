// T5.9 reconciliation - mirroring Python disambiguation/reconciliation.py
use crate::disambiguation::{
    DisambiguationArtifactStatus, DisambiguationCandidate, DisambiguationDecisionKind,
    DisambiguationEvidenceUpdate, DisambiguationReconciliationResult,
    DisambiguationResolutionSource,
};

/// Mirrors Python `is_review_request_fresh`.
pub fn is_review_request_fresh(
    candidate: &DisambiguationCandidate,
    latest_evidence_version: i64,
) -> bool {
    candidate.artifact_status == DisambiguationArtifactStatus::Pending
        && candidate.question_is_concrete
        && candidate.semantic_decision == DisambiguationDecisionKind::Ambiguous
        && latest_evidence_version == candidate.last_reconciled_evidence_version
        && !candidate.question.trim().is_empty()
}

/// Mirrors Python `_updated_score_bundle`.
fn updated_score_bundle(
    candidate: &DisambiguationCandidate,
    update: &DisambiguationEvidenceUpdate,
) -> crate::disambiguation::DisambiguationScoreBundle {
    let mut bundle = candidate.score_bundle.clone();
    if let Some(v) = update.merge_likelihood {
        bundle.merge_likelihood = v;
    }
    if let Some(v) = update.distinction_pressure {
        bundle.distinction_pressure = v;
    }
    if let Some(v) = update.review_priority {
        bundle.review_priority = v;
    }
    bundle
}

/// Mirrors Python `reconcile_disambiguation_candidate`.
pub fn reconcile_disambiguation_candidate(
    candidate: &DisambiguationCandidate,
    update: &DisambiguationEvidenceUpdate,
) -> DisambiguationReconciliationResult {
    let mut next_candidate = candidate.clone();
    let replacement_question = next_candidate.replacement_question.clone();
    next_candidate.last_reconciled_evidence_version = i64::max(
        candidate.last_reconciled_evidence_version,
        update.evidence_version,
    );
    next_candidate.score_bundle = updated_score_bundle(candidate, update);
    if let Some(summary) = &update.evidence_summary {
        next_candidate.evidence_summary = summary.clone();
    }
    if let Some(q) = &update.replacement_question {
        next_candidate.replacement_question = Some(q.clone());
    }
    if !update.question_is_concrete {
        next_candidate.question_is_concrete = false;
    }

    if let Some(group_size) = update.candidate_group_size {
        if group_size as usize != candidate.entity_ids.len() {
            next_candidate.artifact_status = DisambiguationArtifactStatus::Superseded;
            next_candidate.resolution_source = DisambiguationResolutionSource::NewEvidence;
            next_candidate.semantic_decision = DisambiguationDecisionKind::IllFormedCandidate;
            next_candidate.question_is_concrete = false;
            return DisambiguationReconciliationResult {
                status: next_candidate.artifact_status.clone(),
                resolution_source: next_candidate.resolution_source.clone(),
                semantic_decision: next_candidate.semantic_decision.clone(),
                should_surface_to_user: false,
                reason: "candidate set changed and the old question is no longer well formed"
                    .to_owned(),
                new_evidence_version: update.evidence_version,
                replacement_question: replacement_question.clone(),
                challenge_required: false,
                candidate: next_candidate,
            };
        }
    }

    if update.decision_challenged {
        next_candidate.artifact_status = DisambiguationArtifactStatus::Challenged;
        next_candidate.resolution_source = DisambiguationResolutionSource::PriorDecision;
        next_candidate.semantic_decision =
            if candidate.semantic_decision != DisambiguationDecisionKind::Ambiguous {
                candidate.semantic_decision.clone()
            } else {
                DisambiguationDecisionKind::InsufficientEvidence
            };
        next_candidate.question_is_concrete = false;
        return DisambiguationReconciliationResult {
            status: next_candidate.artifact_status.clone(),
            resolution_source: next_candidate.resolution_source.clone(),
            semantic_decision: next_candidate.semantic_decision.clone(),
            should_surface_to_user: false,
            reason: update
                .challenge_reason
                .as_deref()
                .unwrap_or("a prior decision now conflicts with newer evidence")
                .to_owned(),
            new_evidence_version: update.evidence_version,
            replacement_question: replacement_question.clone(),
            challenge_required: false,
            candidate: next_candidate,
        };
    }

    // Default: keep existing state, bump evidence version
    DisambiguationReconciliationResult {
        candidate: next_candidate,
        status: candidate.artifact_status.clone(),
        resolution_source: candidate.resolution_source.clone(),
        semantic_decision: candidate.semantic_decision.clone(),
        should_surface_to_user: false,
        reason: "evidence update recorded".to_owned(),
        new_evidence_version: update.evidence_version,
        replacement_question,
        challenge_required: false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::disambiguation::DisambiguationScoreBundle;
    use std::collections::BTreeMap;

    fn make_candidate() -> DisambiguationCandidate {
        DisambiguationCandidate {
            artifact_id: "artifact-1".to_owned(),
            workspace_id: "ws-1".to_owned(),
            candidate_key: "contact-match:test".to_owned(),
            entity_ids: vec!["e1".to_owned(), "e2".to_owned()],
            evidence_snapshot_id: "snap-1".to_owned(),
            evidence_cutoff_ms: 1000,
            last_reconciled_evidence_version: 0,
            artifact_status: DisambiguationArtifactStatus::Pending,
            resolution_source: DisambiguationResolutionSource::None,
            semantic_decision: DisambiguationDecisionKind::Ambiguous,
            question: "are e1 and e2 the same?".to_owned(),
            question_is_concrete: true,
            evidence_summary: "".to_owned(),
            canonical_entity_id: None,
            related_entity_ids: Vec::new(),
            replacement_question: None,
            source_document_ids: Vec::new(),
            source_span_ids: Vec::new(),
            score_bundle: DisambiguationScoreBundle {
                merge_likelihood: 0.5,
                distinction_pressure: 0.3,
                review_priority: 0.8,
                usage_frequency: 0,
                answer_risk: 0.2,
            },
            metadata: BTreeMap::new(),
        }
    }

    #[test]
    fn is_review_request_fresh_accepts_pending_concrete_ambiguous() {
        let cand = make_candidate();
        assert!(is_review_request_fresh(&cand, 0));
    }

    #[test]
    fn is_review_request_fresh_rejects_resolved() {
        let mut cand = make_candidate();
        cand.artifact_status = DisambiguationArtifactStatus::Resolved;
        assert!(!is_review_request_fresh(&cand, 0));
    }

    #[test]
    fn is_review_request_fresh_rejects_decided() {
        let mut cand = make_candidate();
        cand.semantic_decision = DisambiguationDecisionKind::SameEntity;
        assert!(!is_review_request_fresh(&cand, 0));
    }

    #[test]
    fn reconcile_bumps_evidence_version() {
        let cand = make_candidate();
        let update = DisambiguationEvidenceUpdate {
            evidence_version: 5,
            ..Default::default()
        };
        let result = reconcile_disambiguation_candidate(&cand, &update);
        assert_eq!(result.new_evidence_version, 5);
        assert_eq!(result.candidate.last_reconciled_evidence_version, 5);
    }

    #[test]
    fn reconcile_group_size_change_supersedes() {
        let cand = make_candidate();
        let update = DisambiguationEvidenceUpdate {
            evidence_version: 1,
            candidate_group_size: Some(99),
            ..Default::default()
        };
        let result = reconcile_disambiguation_candidate(&cand, &update);
        assert_eq!(result.status, DisambiguationArtifactStatus::Superseded);
        assert_eq!(
            result.semantic_decision,
            DisambiguationDecisionKind::IllFormedCandidate
        );
    }

    #[test]
    fn reconcile_challenge_marks_challenged() {
        let cand = make_candidate();
        let update = DisambiguationEvidenceUpdate {
            evidence_version: 1,
            decision_challenged: true,
            challenge_reason: Some("conflict".to_owned()),
            ..Default::default()
        };
        let result = reconcile_disambiguation_candidate(&cand, &update);
        assert_eq!(result.status, DisambiguationArtifactStatus::Challenged);
        assert_eq!(result.reason, "conflict");
    }
}
