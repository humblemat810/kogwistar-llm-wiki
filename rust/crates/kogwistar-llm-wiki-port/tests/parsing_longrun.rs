use kogwistar_llm_wiki_port::longrun::{
    object_mapping, payload_int, payload_path, proposal_mode_summary,
};
use kogwistar_llm_wiki_port::models::JsonObject;
use serde_json::{json, Value};

fn map(value: serde_json::Value) -> JsonObject {
    value
        .as_object()
        .expect("JSON object")
        .iter()
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect()
}

#[test]
fn payload_helpers_match_python_contracts() {
    let payload = map(json!({
        "heartbeat_path": "heartbeat.json",
        "token_budget": 123,
        "max_retries": "4"
    }));

    assert_eq!(
        payload_path(&payload, "heartbeat_path").unwrap(),
        std::path::PathBuf::from("heartbeat.json")
    );
    assert_eq!(payload_int(&payload, "token_budget", 1).unwrap(), 123);
    assert_eq!(payload_int(&payload, "max_retries", 3).unwrap(), 4);
    assert_eq!(payload_int(&payload, "missing", 7).unwrap(), 7);
    assert_eq!(
        payload_path(&payload, "missing").unwrap_err(),
        "parser child payload field \"missing\" must be a path"
    );
    assert_eq!(
        payload_int(&map(json!({"flag": true})), "flag", 7).unwrap_err(),
        "parser child payload field \"flag\" must be an integer"
    );
}

#[test]
fn object_mapping_and_proposal_summary_match_python() {
    assert_eq!(
        object_mapping(&Value::from("not-object")),
        JsonObject::new()
    );
    assert_eq!(proposal_mode_summary(&JsonObject::new()), JsonObject::new());

    let state = map(json!({
        "current_layer_result": {
            "metadata": {
                "proposal_mode": "boundaries",
                "proposal_source": "provider",
                "boundary_proposed_count": 3,
                "boundary_rejected_count": 0,
                "unresolved_interval_count": 1,
                "provider_child_count": 2,
                "ignored": true
            }
        }
    }));
    let summary = proposal_mode_summary(&state);
    assert_eq!(
        serde_json::to_value(&summary).unwrap(),
        json!({
            "proposal_mode": "boundaries",
            "proposal_source": "provider",
            "boundary_proposed_count": 3,
            "boundary_rejected_count": 0,
            "unresolved_interval_count": 1,
            "provider_child_count": 2
        })
    );
}
