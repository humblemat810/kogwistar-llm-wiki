use kogwistar_llm_wiki_port::crosslink_proposals::{
    crosslink_patch_requires_atomic_replacement, validate_crosslink_critic_response,
    validate_crosslink_proposal_response, validate_crosslink_review_decision,
    CrosslinkCriticResponse, CrosslinkEvidence, CrosslinkProposalResponse, CrosslinkReviewDecision,
};
use serde_json::json;

#[test]
fn evidence_requires_sha256_revision_digest() {
    let evidence = CrosslinkEvidence {
        evidence_id: "e1".to_owned(),
        node_id: "n1".to_owned(),
        source_document_id: "source".to_owned(),
        source_revision_id: "revision".to_owned(),
        revision_document_id: "revision-doc".to_owned(),
        source_digest: "not-a-digest".to_owned(),
        start_char: 0,
        end_char: 1,
        excerpt: "x".to_owned(),
    };
    assert!(evidence.validate().unwrap_err().contains("SHA-256"));
}

#[test]
fn proposal_contract_rejects_duplicate_group_and_supersession_ids() {
    let operation = json!({
        "left_evidence_id": "left",
        "right_evidence_id": "right",
        "relation": "references",
        "rationale": "both sources support this",
        "supersedes_edge_id": "edge-1"
    });
    let response: CrosslinkProposalResponse = serde_json::from_value(json!({
        "groups": [{
            "group_id": "same",
            "rationale": "a group",
            "operations": [
                operation.clone(),
                {
                    "left_evidence_id": "left",
                    "right_evidence_id": "right",
                    "relation": "mentions",
                    "rationale": "both sources support this",
                    "supersedes_edge_id": "edge-1"
                }
            ]
        }]
    }))
    .unwrap();
    assert!(validate_crosslink_proposal_response(&response)
        .unwrap_err()
        .contains("supersede each edge at most once"));
}

#[test]
fn critic_and_review_contracts_are_bounded() {
    let critic = CrosslinkCriticResponse {
        verdict: "approve".to_owned(),
        explanation: "The excerpts support the relation.".to_owned(),
        evidence_ids: vec!["evidence-1".to_owned()],
    };
    assert!(validate_crosslink_critic_response(&critic).is_ok());
    let decision = CrosslinkReviewDecision {
        artifact_id: "review-1".to_owned(),
        decision: "approve".to_owned(),
        expected_version: 2,
    };
    assert!(validate_crosslink_review_decision(&decision).is_ok());
}

#[test]
fn patch_atomicity_is_carried_into_patch_contract() {
    assert!(crosslink_patch_requires_atomic_replacement(
        true,
        &[json!({"kind": "ADD_EDGE"})]
    ));
    assert!(crosslink_patch_requires_atomic_replacement(
        false,
        &[json!({"supersedes_ids": ["edge-1"]})]
    ));
    assert!(!crosslink_patch_requires_atomic_replacement(
        false,
        &[json!({"supersedes_ids": []})]
    ));
}
