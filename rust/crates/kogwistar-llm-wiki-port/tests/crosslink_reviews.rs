use kogwistar_llm_wiki_port::crosslink_proposals::CrosslinkReviewDecision;
use kogwistar_llm_wiki_port::crosslink_reviews::{
    normalize_review_batch, revalidation_payload, review_cas_request, review_event, review_outcome,
    CrosslinkReviewError,
};
use serde_json::{json, Value};

fn decision(artifact_id: &str, decision: &str, expected_version: i64) -> CrosslinkReviewDecision {
    CrosslinkReviewDecision {
        artifact_id: artifact_id.to_owned(),
        decision: decision.to_owned(),
        expected_version,
    }
}

#[test]
fn review_batch_requires_unique_decisions() {
    let decisions = vec![
        decision("artifact-1", "approve", 1),
        decision("artifact-1", "approve", 1),
    ];
    assert!(normalize_review_batch(&decisions)
        .unwrap_err()
        .to_string()
        .contains("only once"));
}

#[test]
fn review_outcome_conflicts_on_opposite_decision() {
    let decision = decision("artifact-1", "approve", 1);
    let existing: Value = json!({"decision": "reject"});
    assert!(matches!(
        review_outcome(&decision, Some(&existing)),
        Err(CrosslinkReviewError::Conflict)
    ));
    let same: Value = json!({"decision": "approve"});
    let outcome = review_outcome(&decision, Some(&same)).unwrap();
    assert_eq!(outcome.status, "already_decided");
}

#[test]
fn review_cas_request_rejects_stale_expected_version() {
    let decision = decision("artifact-1", "approve", 2);
    let request = review_cas_request("ws", &decision, 1);
    assert!(matches!(
        request.unwrap_err(),
        CrosslinkReviewError::Conflict
    ));
}

#[test]
fn review_cas_request_uses_python_projection_contract() {
    let decision = decision("artifact-1", "approve", 2);
    let request = review_cas_request("ws", &decision, 2).unwrap();
    assert_eq!(request.projection_ns, "ws:ws:projection_state");
    assert_eq!(
        request.projection_key,
        "crosslink_review_decision:artifact-1"
    );
    assert_eq!(request.expected_last_authoritative_seq, None);
    assert_eq!(request.expected_last_materialized_seq, None);
    assert_eq!(request.last_authoritative_seq, 2);
    assert_eq!(request.last_materialized_seq, 2);
    assert_eq!(request.projection_schema_version, 1);
    assert_eq!(request.materialization_status, "ready");
    assert_eq!(request.payload["decision"], "approve");
    assert_eq!(request.payload["expected_version"], 2);
}

#[test]
fn review_event_uses_python_wire_fields() {
    let decision = decision("artifact-1", "approve", 2);
    let event = review_event("ws", "actor", &decision).unwrap();
    assert_eq!(event["workspace_id"], json!("ws"));
    assert_eq!(event["artifact_id"], json!("artifact-1"));
    assert_eq!(event["actor_id"], json!("actor"));
}

#[test]
fn revalidation_payload_keeps_python_contract() {
    let metadata = json!({
        "source_document_id": "source-1",
        "source_revision_id": "revision-1",
        "source_digest": "digest-1",
        "revision_document_id": "revision-doc-1",
        "source_revision_fences": [{"source_document_id": "source-1"}],
        "parse_quality_status": "parsed"
    });
    let payload = revalidation_payload("ws", &metadata, "group-1", "job-1");
    assert_eq!(
        payload["maintenance_kind"],
        json!("document_validate_crosslinks")
    );
    assert_eq!(payload["mode"], json!("background"));
    assert_eq!(payload["crosslink_candidate_group_id"], json!("group-1"));
    assert_eq!(payload["accepted_confidence"], json!(1.0));
    assert_eq!(payload["parse_quality_status"], json!("parsed"));
}
