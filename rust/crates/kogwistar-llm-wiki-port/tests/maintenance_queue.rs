use kogwistar_llm_wiki_port::maintenance_queue::{
    budget_exhaustion_reason, maintenance_failure_action, requeue_suspended_job,
    requeue_trace_event, selection_result_payload, MaintenanceFailure, MaintenanceQueueAction,
};
use serde_json::{json, Value};

fn map(value: serde_json::Value) -> kogwistar_llm_wiki_port::models::JsonObject {
    value
        .as_object()
        .expect("JSON object")
        .iter()
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect()
}

#[test]
fn budget_exhaustion_reason_matches_python_markers() {
    assert_eq!(
        budget_exhaustion_reason("durable parse expansion exceeded wall-time budget"),
        Some("exceeded wall-time budget")
    );
    assert_eq!(
        budget_exhaustion_reason("MAX_LLM_CALLS budget exhausted"),
        Some("max_llm_calls budget exhausted")
    );
    assert_eq!(budget_exhaustion_reason("provider request timed out"), None);
}

#[test]
fn failure_action_terminalizes_budget_but_retries_transient() {
    assert_eq!(
        maintenance_failure_action("provider request timed out"),
        MaintenanceFailure {
            action: MaintenanceQueueAction::Retry,
            reason: None,
            error_text: "provider request timed out".to_owned()
        }
    );
    assert_eq!(
        maintenance_failure_action("durable parse expansion exceeded wall-time budget"),
        MaintenanceFailure {
            action: MaintenanceQueueAction::Fail,
            reason: Some("exceeded wall-time budget"),
            error_text:
                "maintenance_budget_exhausted: durable parse expansion exceeded wall-time budget"
                    .to_owned()
        }
    );
}

#[test]
fn selection_payload_bounds_python_candidates() {
    let mut payload = map(json!({
        "selection_strategy": "connected_semantic_evidence_history",
        "maintenance_candidates": [0, 1, 2, 3, 4]
    }));
    let mut result = selection_result_payload(&payload);
    assert_eq!(
        result["maintenance_candidates"].as_array().map(Vec::len),
        Some(5)
    );

    let mut candidates = vec![];
    for index in 0..30 {
        candidates.push(json!(index));
    }
    payload.insert(
        "maintenance_candidates".to_owned(),
        Value::Array(candidates),
    );
    result = selection_result_payload(&payload);
    assert_eq!(
        result["maintenance_candidates"].as_array().map(Vec::len),
        Some(24)
    );
}

#[test]
fn requeue_payload_carries_python_checkpoint_frontier() {
    let payload = map(json!({
        "source_document_id": "doc-1",
        "maintenance_kind": "document_seed_graph"
    }));
    let final_state = json!({
        "_rt_join": {
            "suspended": [["node-2", 0, "token-2", null]]
        }
    });
    let requeued =
        requeue_suspended_job(&payload, &final_state, Some("maintenance-run-1"), None).unwrap();

    assert_eq!(
        requeued.payload,
        map(json!({
            "source_document_id": "doc-1",
            "maintenance_kind": "document_seed_graph",
            "continuation_run_id": "maintenance-run-1",
            "suspended_node_id": "node-2",
            "suspended_token_id": "token-2"
        }))
    );
    assert_eq!(
        requeue_trace_event(&payload, &requeued, 123),
        json!({
            "event": "maintenance_job_requeued",
            "workspace_id": "",
            "source_document_id": "doc-1",
            "request_node_id": "",
            "continuation_run_id": "maintenance-run-1",
            "suspended_node_id": "node-2",
            "suspended_token_id": "token-2",
            "timestamp_ms": 123
        })
    );
}

#[test]
fn requeue_payload_filters_python_budget_state() {
    let payload = map(json!({"source_document_id": "doc-1"}));
    let final_state = json!({
        "_rt_join": {
            "suspended": [["node-2", 0, "token-2", null]]
        }
    });
    let budget_state = json!({
        "token_budget": 10,
        "token_used": 3,
        "cost_used": 0.25,
        "ignored": "no",
        "flag": true
    });
    let requeued =
        requeue_suspended_job(&payload, &final_state, Some("run:1"), Some(&budget_state)).unwrap();

    assert_eq!(
        requeued.payload["maintenance_budget_state"],
        json!({
            "token_budget": 10,
            "token_used": 3,
            "cost_used": 0.25
        })
    );
}

#[test]
fn requeue_rejects_missing_or_invalid_frontier() {
    let payload = map(json!({}));
    assert!(requeue_suspended_job(&payload, &json!({}), None, None)
        .unwrap_err()
        .to_string()
        .contains("without a resumable frontier"));

    let final_state = json!({"_rt_join": {"suspended": [["node"]] }});
    assert!(requeue_suspended_job(&payload, &final_state, None, None)
        .unwrap_err()
        .to_string()
        .contains("invalid suspended frontier"));
}
