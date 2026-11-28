use kogwistar_llm_wiki_port::disambiguation::{
    DisambiguationArtifactStatus, DisambiguationCandidate, DisambiguationDecisionKind,
    DisambiguationEvidenceUpdate, DisambiguationResolutionSource, DisambiguationScoreBundle,
};
use serde_json::json;

#[test]
fn disambiguation_status_serialization_matches_python() {
    assert_eq!(
        serde_json::to_value(DisambiguationArtifactStatus::Pending).unwrap(),
        json!("pending")
    );
    assert_eq!(
        serde_json::to_value(DisambiguationArtifactStatus::Resolved).unwrap(),
        json!("resolved")
    );
    assert_eq!(
        serde_json::to_value(DisambiguationArtifactStatus::Superseded).unwrap(),
        json!("superseded")
    );
    assert_eq!(
        serde_json::to_value(DisambiguationArtifactStatus::Challenged).unwrap(),
        json!("challenged")
    );
}

#[test]
fn disambiguation_decision_kind_serialization_matches_python() {
    assert_eq!(
        serde_json::to_value(DisambiguationDecisionKind::SameEntity).unwrap(),
        json!("same_entity")
    );
    assert_eq!(
        serde_json::to_value(DisambiguationDecisionKind::DistinctEntities).unwrap(),
        json!("distinct_entities")
    );
    assert_eq!(
        serde_json::to_value(DisambiguationDecisionKind::Ambiguous).unwrap(),
        json!("ambiguous")
    );
}

#[test]
fn disambiguation_resolution_source_serialization_matches_python() {
    assert_eq!(
        serde_json::to_value(DisambiguationResolutionSource::None).unwrap(),
        json!("none")
    );
    assert_eq!(
        serde_json::to_value(DisambiguationResolutionSource::User).unwrap(),
        json!("user")
    );
    assert_eq!(
        serde_json::to_value(DisambiguationResolutionSource::Policy).unwrap(),
        json!("policy")
    );
}

#[test]
fn disambiguation_candidate_validation_rejects_fewer_than_two_entities() {
    let candidate = DisambiguationCandidate {
        artifact_id: "artifact-1".to_owned(),
        workspace_id: "ws-1".to_owned(),
        candidate_key: "contact-match:test".to_owned(),
        entity_ids: vec!["e1".to_owned()],
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
        metadata: Default::default(),
    };
    assert!(candidate.validate().is_err());
}

#[test]
fn disambiguation_candidate_validation_requires_non_empty_question() {
    let candidate = DisambiguationCandidate {
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
        question: "".to_owned(),
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
        metadata: Default::default(),
    };
    assert!(candidate.validate().is_err());
}

#[test]
fn disambiguation_candidate_validation_rejects_duplicate_entities() {
    let candidate = DisambiguationCandidate {
        artifact_id: "artifact-1".to_owned(),
        workspace_id: "ws-1".to_owned(),
        candidate_key: "contact-match:test".to_owned(),
        entity_ids: vec!["e1".to_owned(), "e1".to_owned()],
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
        metadata: Default::default(),
    };
    assert!(candidate.validate().is_err());
}

#[test]
fn disambiguation_candidate_validation_rejects_canonical_not_in_entities() {
    let candidate = DisambiguationCandidate {
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
        canonical_entity_id: Some("e3".to_owned()),
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
        metadata: Default::default(),
    };
    assert!(candidate.validate().is_err());
}

#[test]
fn disambiguation_candidate_validation_accepts_valid_candidate() {
    let candidate = DisambiguationCandidate {
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
        evidence_summary: "some evidence".to_owned(),
        canonical_entity_id: Some("e1".to_owned()),
        related_entity_ids: vec!["e2".to_owned()],
        replacement_question: None,
        source_document_ids: vec!["doc-1".to_owned()],
        source_span_ids: vec!["span-1".to_owned()],
        score_bundle: DisambiguationScoreBundle {
            merge_likelihood: 0.5,
            distinction_pressure: 0.3,
            review_priority: 0.8,
            usage_frequency: 0,
            answer_risk: 0.2,
        },
        metadata: Default::default(),
    };
    assert!(candidate.validate().is_ok());
}

#[test]
fn disambiguation_evidence_update_validation_rejects_both_flags() {
    let update = DisambiguationEvidenceUpdate {
        evidence_version: 1,
        question_is_concrete: true,
        resolves_equivalence: true,
        resolves_distinction: true,
        user_decision: None,
        policy_decision: None,
        candidate_group_size: None,
        replacement_question: None,
        evidence_summary: None,
        missing_evidence: Vec::new(),
        merge_likelihood: None,
        distinction_pressure: None,
        review_priority: None,
        decision_challenged: false,
        challenge_reason: None,
    };
    assert!(update.validate().is_err());
}

#[test]
fn disambiguation_evidence_update_validation_rejects_both_decisions() {
    let update = DisambiguationEvidenceUpdate {
        evidence_version: 1,
        question_is_concrete: true,
        resolves_equivalence: false,
        resolves_distinction: false,
        user_decision: Some(DisambiguationDecisionKind::SameEntity),
        policy_decision: Some(DisambiguationDecisionKind::DistinctEntities),
        candidate_group_size: None,
        replacement_question: None,
        evidence_summary: None,
        missing_evidence: Vec::new(),
        merge_likelihood: None,
        distinction_pressure: None,
        review_priority: None,
        decision_challenged: false,
        challenge_reason: None,
    };
    assert!(update.validate().is_err());
}

#[test]
fn disambiguation_score_bundle_defaults() {
    let bundle = DisambiguationScoreBundle {
        merge_likelihood: 0.0,
        distinction_pressure: 0.0,
        review_priority: 0.0,
        usage_frequency: 0,
        answer_risk: 0.0,
    };
    assert_eq!(bundle.merge_likelihood, 0.0);
    assert_eq!(bundle.answer_risk, 0.0);
}
