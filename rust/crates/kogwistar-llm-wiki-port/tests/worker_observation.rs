use kogwistar_llm_wiki_port::worker_observation::{
    redacted_frame_payload, schedule_observation_continuation,
};
use serde_json::json;

#[test]
fn redacted_frame_persistence_removes_transient_source_excerpts() {
    let frame: kogwistar_llm_wiki_port::models::JsonObject = serde_json::from_value(json!({
        "source_context": [{
            "id": "source-node-1",
            "source_evidence_status": "span_verified",
            "source_excerpt": "private source excerpt"
        }],
        "source_evidence_required": true
    }))
    .unwrap();
    let payload = redacted_frame_payload(&frame);
    assert_eq!(
        payload["source_context"][0]["source_evidence_status"],
        json!("span_verified")
    );
    assert!(payload["source_context"][0].get("source_excerpt").is_none());
}

#[test]
fn observation_continuation_requeues_same_job_once_with_bounded_context() {
    let payload: kogwistar_llm_wiki_port::models::JsonObject = serde_json::from_value(json!({
        "subject_id": "ws:demo:child",
        "parent_subject_id": "ws:demo:parent",
        "maintenance_round": 0,
        "maintenance_max_rounds": 2
    }))
    .unwrap();
    let decision =
        schedule_observation_continuation(&payload, "demo", "request-1", "review_parent", true);
    assert!(decision.scheduled);
    let next_payload = decision.next_payload.unwrap();
    assert_eq!(
        next_payload["maintenance_kind"],
        json!("review_maintenance_subject")
    );
    assert_eq!(next_payload["subject_id"], json!("ws:demo:parent"));
    assert_eq!(next_payload["maintenance_round"], json!(1));
    assert_eq!(
        next_payload["observation_continuation_scheduled"],
        json!(true)
    );

    let next_payload: kogwistar_llm_wiki_port::models::JsonObject =
        serde_json::from_value(json!(next_payload)).unwrap();
    let repeat = schedule_observation_continuation(
        &next_payload,
        "demo",
        "request-1",
        "review_parent",
        true,
    );
    assert!(!repeat.scheduled);
}
