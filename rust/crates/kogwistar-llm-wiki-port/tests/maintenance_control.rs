use kogwistar_llm_wiki_port::maintenance_control::{
    configured_default_request_max_rounds, corrupt_control_state, MaintenanceControlState,
};
use serde_json::json;

#[test]
fn default_request_max_rounds_reads_and_validates_environment() {
    std::env::set_var("LLM_WIKI_MAINTENANCE_DEFAULT_REQUEST_MAX_ROUNDS", "4");
    assert_eq!(configured_default_request_max_rounds().unwrap(), 4);
    std::env::set_var("LLM_WIKI_MAINTENANCE_DEFAULT_REQUEST_MAX_ROUNDS", "0");
    assert!(configured_default_request_max_rounds()
        .unwrap_err()
        .contains("between 1 and 100"));
    std::env::set_var("LLM_WIKI_MAINTENANCE_DEFAULT_REQUEST_MAX_ROUNDS", "101");
    assert!(configured_default_request_max_rounds()
        .unwrap_err()
        .contains("between 1 and 100"));
    std::env::remove_var("LLM_WIKI_MAINTENANCE_DEFAULT_REQUEST_MAX_ROUNDS");
}

#[test]
fn control_state_maps_python_wire_fields() {
    std::env::remove_var("LLM_WIKI_MAINTENANCE_PROFILE");
    std::env::remove_var("LLM_WIKI_MAINTENANCE_MODEL_CLASS");
    std::env::remove_var("LLM_WIKI_MAINTENANCE_PROFILE_LADDER");
    std::env::remove_var("LLM_WIKI_MAINTENANCE_ENABLED");
    std::env::remove_var("LLM_WIKI_MAINTENANCE_REQUEST_ENABLED");
    std::env::remove_var("LLM_WIKI_MAINTENANCE_BACKGROUND_ENABLED");
    let state = MaintenanceControlState::from_mapping(Some(&json!({
        "request_enabled": false,
        "background_enabled": true,
        "enabled": true,
        "profile": "budgeted",
        "profile_ladder": [{"name": "primary", "provider": "codex", "profile": "high"}],
        "budget": {"daily": {"output_tokens": 20}},
        "spend": {"daily": {"output_tokens": 5}},
        "deferred_cycle": true,
        "status_reason": "operator",
        "updated_at_ms": 123,
        "updated_by": "test"
    })))
    .unwrap();
    assert!(!state.request_enabled);
    assert!(state.background_enabled);
    assert_eq!(state.profile, "budgeted");
    assert!(state.profile_ladder_configured);
    assert_eq!(state.budget["daily"]["output_tokens"], 20.0);
    assert_eq!(state.spend["daily"]["output_tokens"], 5.0);
}

#[test]
fn corrupt_control_fails_closed() {
    let state = corrupt_control_state().unwrap();
    assert!(!state.enabled);
    assert_eq!(state.status_reason, "configured");
}

#[test]
fn changed_state_uses_python_fields_and_clock() {
    let state = MaintenanceControlState::default_state();
    let next = state.changed_with_clock(
        json!({
            "request_enabled": false,
            "background_enabled": true,
            "status_reason": "blocked_context_window",
            "updated_by": "maintenance-context-guard"
        }),
        1234,
    );
    assert!(!next.request_enabled);
    assert!(next.background_enabled);
    assert_eq!(next.status_reason, "blocked_context_window");
    assert_eq!(next.updated_by, "maintenance-context-guard");
    assert_eq!(next.updated_at_ms, 1234);
}
