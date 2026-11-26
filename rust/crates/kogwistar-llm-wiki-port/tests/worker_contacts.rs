use kogwistar_llm_wiki_port::worker_contacts::{
    bound_contact_scan_observations, contact_scan_trace, normalize_contact_scan_payload,
};
use serde_json::json;

#[test]
fn contact_scan_payload_is_acl_first_and_bounded() {
    let payload: kogwistar_llm_wiki_port::models::JsonObject = serde_json::from_value(json!({
        "workspace_id": "demo",
        "source_stream_ids": ["stream-b", "stream-a", "stream-a"]
    }))
    .unwrap();
    assert!(normalize_contact_scan_payload(&payload)
        .unwrap_err()
        .to_string()
        .contains("unique"));

    let payload: kogwistar_llm_wiki_port::models::JsonObject = serde_json::from_value(json!({
        "workspace_id": "demo",
        "source_stream_ids": ["stream-b", "stream-a"]
    }))
    .unwrap();
    let request = normalize_contact_scan_payload(&payload).unwrap();
    assert_eq!(request.workspace_id, "demo");
    assert_eq!(request.source_stream_ids, vec!["stream-b", "stream-a"]);
}

#[test]
fn contact_scan_observations_are_bounded_and_valid() {
    let observations = vec![
        json!({"entity_id": "person-a", "stream_id": "stream-a"}),
        json!({"entity_id": "person-b", "stream_id": "stream-b"}),
    ];
    assert!(bound_contact_scan_observations(&observations).is_ok());
    let invalid = vec![json!({"stream_id": "stream-a"})];
    assert!(bound_contact_scan_observations(&invalid)
        .unwrap_err()
        .to_string()
        .contains("invalid observation"));
}

#[test]
fn contact_scan_trace_uses_python_event_contract() {
    let trace = contact_scan_trace("demo", "job-1", &["candidate-1".to_owned()]);
    assert_eq!(
        trace["event"],
        json!("contact_disambiguation_scan_complete")
    );
    assert_eq!(trace["workspace_id"], json!("demo"));
    assert_eq!(trace["job_id"], json!("job-1"));
    assert_eq!(trace["persisted_candidate_count"], json!(1));
}
