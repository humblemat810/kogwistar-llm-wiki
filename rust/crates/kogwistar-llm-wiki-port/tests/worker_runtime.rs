use kogwistar_llm_wiki_port::worker_runtime::{
    acknowledgement_trace, budget_exhaustion_reason, retry_or_fail_maintenance_job,
    selection_result_payload, BUDGET_EXHAUSTION_MARKERS,
};
use serde_json::json;

#[test]
fn budget_exhaustion_reason_matches_python_markers() {
    for marker in BUDGET_EXHAUSTION_MARKERS {
        assert_eq!(budget_exhaustion_reason(marker), Some(marker));
    }
    assert_eq!(budget_exhaustion_reason("provider request timed out"), None);
}

#[test]
fn exhausted_budget_is_terminal_but_transient_failure_retries() {
    let decision = retry_or_fail_maintenance_job(
        "budget-job",
        Some("claim-budget"),
        "durable parse expansion exceeded wall-time budget",
    );
    assert_eq!(decision.action, "failed");
    assert_eq!(
        decision.reason,
        Some("exceeded wall-time budget".to_owned())
    );
    assert_eq!(
        decision.error_text,
        Some(
            "maintenance_budget_exhausted: durable parse expansion exceeded wall-time budget"
                .to_owned()
        )
    );

    let decision = retry_or_fail_maintenance_job(
        "provider-timeout",
        Some("claim-provider"),
        "provider request timed out",
    );
    assert_eq!(decision.action, "retry");
    assert!(decision.reason.is_none());
}

#[test]
fn selection_payload_bounds_python_candidates() {
    let payload: kogwistar_llm_wiki_port::models::JsonObject = serde_json::from_value(json!({
        "selection_strategy": "connected_semantic_evidence_history",
        "maintenance_candidates": (0..30).map(|index| {
            json!({"candidate_id": format!("node-{index}")})
        }).collect::<Vec<_>>()
    }))
    .unwrap();
    let payload = selection_result_payload(&payload);
    assert_eq!(
        payload["selection_strategy"],
        json!("connected_semantic_evidence_history")
    );
    assert_eq!(
        payload["maintenance_candidates"].as_array().unwrap().len(),
        24
    );
}

#[test]
fn acknowledgement_trace_uses_python_event_contract() {
    let trace = acknowledgement_trace("ws", "job-1", 150, 123);
    assert_eq!(trace.event, "maintenance_job_acknowledged");
    assert_eq!(trace.workspace_id, "ws");
    assert_eq!(trace.job_id, "job-1");
    assert_eq!(trace.lease_seconds, 150);
    assert_eq!(trace.timestamp_ms, 123);
}
