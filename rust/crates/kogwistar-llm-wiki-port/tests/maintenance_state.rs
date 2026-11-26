use kogwistar_llm_wiki_port::maintenance_state::{
    belongs_to_workspace, edge_ids, maintenance_budget_state, persisted_budget_state,
    semantic_fingerprint, MaintenanceBudgetInputs,
};
use serde_json::{json, Value};

#[test]
fn workspace_guard_is_fail_open_only_when_missing() {
    assert!(belongs_to_workspace(None, "workspace-a"));
    assert!(belongs_to_workspace(
        Some(&json!({"workspace_id": "workspace-a"})),
        "workspace-a"
    ));
    assert!(belongs_to_workspace(
        Some(&json!({"workspace_id": ""})),
        "workspace-a"
    ));
    assert!(!belongs_to_workspace(
        Some(&json!({"workspace_id": "workspace-b"})),
        "workspace-a"
    ));
}

#[test]
fn edge_ids_collect_source_and_target_endpoints() {
    let ids = edge_ids(&json!({
        "source_ids": ["node-1", "node-1"],
        "target_ids": "node-2"
    }));
    let actual = ids.iter().cloned().collect::<Vec<_>>();
    assert_eq!(actual, vec!["node-1", "node-2"]);
}

#[test]
fn persisted_budget_state_keeps_only_numeric_allowed_fields() {
    let actual = persisted_budget_state(Some(&json!({
        "token_used": 10,
        "token_budget": 20,
        "ignored": true,
        "raw_transcript": "secret"
    })));
    assert_eq!(actual, json!({"token_budget": 20, "token_used": 10}));
    assert_eq!(persisted_budget_state(Some(&Value::Null)), Value::Null);
}

#[test]
fn semantic_fingerprint_is_order_and_dynamic_id_insensitive() {
    let first = json!({
        "nodes": [
            {"id": "node-1", "metadata": {"parse_generation_member_id": "member-1"}},
            {"id": "node-2", "metadata": {}}
        ],
        "edges": []
    });
    let second = json!({
        "nodes": [
            {"id": "node-2", "metadata": {}},
            {"id": "node-1", "metadata": {"parse_generation_member_id": "member-2"}}
        ],
        "edges": []
    });
    assert_eq!(semantic_fingerprint(&first), semantic_fingerprint(&second));
    assert!(semantic_fingerprint(&first).len() == 64);
}

#[test]
fn maintenance_budget_state_builds_cumulative_ledger() {
    let payload: kogwistar_llm_wiki_port::models::JsonObject = serde_json::from_value(json!({
        "budgets": {
            "max_tokens": 100,
            "max_llm_calls": 3,
            "max_steps": 5,
            "max_time_seconds": 60,
            "max_cost_usd": 1.5
        }
    }))
    .unwrap();
    let state = maintenance_budget_state(&MaintenanceBudgetInputs {
        payload: &payload,
        fair_scheduling: false,
        maintenance_steps_per_slice: 2,
        maintenance_llm_calls_per_slice: 1,
        maintenance_seconds_per_slice: 10,
        durable_usage: None,
    });
    assert_eq!(state["token_budget"], json!(100));
    assert_eq!(state["call_budget"], json!(3));
    assert_eq!(state["step_budget"], json!(5));
    assert_eq!(state["time_budget_ms"], json!(60000));
    assert_eq!(state["cost_budget"], json!(1.5));
}
