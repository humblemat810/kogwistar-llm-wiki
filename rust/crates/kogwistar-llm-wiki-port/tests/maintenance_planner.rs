use kogwistar_llm_wiki_port::maintenance::{
    decide_next_maintenance_phase, normalize_maintenance_plan, DEFAULT_DOCUMENT_MAINTENANCE_PLAN,
};
use kogwistar_llm_wiki_port::models::JsonObject;
use serde_json::{json, Value};

fn payload(value: serde_json::Value) -> JsonObject {
    value
        .as_object()
        .expect("JSON object")
        .iter()
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect()
}

#[test]
fn planner_advances_one_phase_at_a_time() {
    let state = payload(json!({
        "maintenance_plan": DEFAULT_DOCUMENT_MAINTENANCE_PLAN,
        "maintenance_phase_index": 0,
        "maintenance_round": 0
    }));

    let decision = decide_next_maintenance_phase(&state, "document_seed_graph");
    assert!(decision.should_continue());
    assert_eq!(decision.next_kind.as_deref(), Some("document_parse_graph"));
    assert_eq!(decision.next_index, 1);
}

#[test]
fn planner_stops_when_plan_complete_or_stop_requested() {
    let plan = Value::Array(
        DEFAULT_DOCUMENT_MAINTENANCE_PLAN
            .iter()
            .map(|kind| Value::from(*kind))
            .collect(),
    );
    let complete = payload(json!({
        "maintenance_plan": plan,
        "maintenance_phase_index": 5
    }));
    let stopped = payload(json!({
        "maintenance_plan": DEFAULT_DOCUMENT_MAINTENANCE_PLAN,
        "maintenance_stop_requested": true
    }));

    assert_eq!(
        decide_next_maintenance_phase(&complete, "document_validate_crosslinks").reason,
        "plan_complete"
    );
    assert_eq!(
        decide_next_maintenance_phase(&stopped, "document_seed_graph").reason,
        "stop_requested"
    );
}

#[test]
fn planner_stops_at_round_limit_and_repeats_unplanned_work() {
    let limited = payload(json!({
        "maintenance_plan": DEFAULT_DOCUMENT_MAINTENANCE_PLAN,
        "maintenance_phase_index": 0,
        "maintenance_round": 2,
        "maintenance_max_rounds": 2
    }));
    let repeat = payload(json!({
        "maintenance_round": 3,
        "maintenance_max_rounds": 10
    }));

    assert_eq!(
        decide_next_maintenance_phase(&limited, "document_seed_graph").reason,
        "max_rounds_reached"
    );
    let repeated = decide_next_maintenance_phase(&repeat, "document_propose_crosslinks");
    assert_eq!(
        repeated.next_kind.as_deref(),
        Some("document_propose_crosslinks")
    );
    assert_eq!(repeated.next_index, 1);
    assert_eq!(repeated.reason, "next_bounded_round_available");
}

#[test]
fn plan_normalizes_strings_without_mutation() {
    let plan = json!([" seed ", "", "document_parse_graph"]);
    assert_eq!(
        normalize_maintenance_plan(Some(&plan)),
        vec!["seed".to_owned(), "document_parse_graph".to_owned()]
    );
    assert_eq!(normalize_maintenance_plan(None), Vec::<String>::new());
}
