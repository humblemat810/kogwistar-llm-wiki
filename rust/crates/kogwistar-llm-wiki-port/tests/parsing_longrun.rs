use kogwistar_llm_wiki_port::longrun::{
    child_paths, env_flag_enabled, failure_payload, heartbeat_payload, live_trace_enabled,
    llm_call_count_from_events, object_mapping, payload_int, payload_path, proposal_mode_summary,
    result_ok, result_payload, trace_context,
};
use kogwistar_llm_wiki_port::models::JsonObject;
use serde_json::{json, Value};
use std::collections::BTreeMap;

fn map(value: serde_json::Value) -> JsonObject {
    value
        .as_object()
        .expect("JSON object")
        .iter()
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect()
}

#[test]
fn child_paths_match_python_contract() {
    let paths = child_paths(&map(json!({
        "heartbeat_path": "heartbeat.json",
        "result_path": "result.json",
        "failure_path": "failure.json",
        "trace_path": "trace.log",
        "dump_trace_path": "dump.log"
    })))
    .unwrap();

    assert_eq!(
        paths.heartbeat_path,
        std::path::PathBuf::from("heartbeat.json")
    );
    assert_eq!(paths.result_path, std::path::PathBuf::from("result.json"));
    assert_eq!(paths.failure_path, std::path::PathBuf::from("failure.json"));
    assert_eq!(
        paths.failure_payload_path,
        std::path::PathBuf::from("failure_payload.json")
    );
    assert_eq!(paths.trace_path, std::path::PathBuf::from("trace.log"));
    assert_eq!(
        paths.dump_trace_path,
        Some(std::path::PathBuf::from("dump.log"))
    );
    assert!(child_paths(&JsonObject::new()).is_err());
}

#[test]
fn live_trace_and_env_flags_match_python() {
    let env = BTreeMap::from([
        ("KOGWISTAR_LONGRUN_LIVE_TRACE".to_owned(), "true".to_owned()),
        ("KOGWISTAR_LLM_WIKI_LONGRUN".to_owned(), "1".to_owned()),
    ]);
    assert!(live_trace_enabled(&JsonObject::new(), &env));
    assert!(env_flag_enabled(
        &env,
        &["KOGWISTAR_LONGRUN_LIVE_TRACE"],
        false
    ));
    assert!(!env_flag_enabled(
        &BTreeMap::new(),
        &["KOGWISTAR_LONGRUN_LIVE_TRACE"],
        false
    ));
}

#[test]
fn trace_context_and_heartbeat_match_python() {
    let payload = map(json!({
        "doc_id": "doc:1",
        "parser_lane": "page_index",
        "parser_provider": "openai",
        "parser_model": "model:1",
        "parser_workflow_run_id": "run:1"
    }));
    assert_eq!(
        trace_context(&payload),
        "doc=doc:1 parser_lane=page_index provider=openai model=model:1 parser_workflow_run_id=run:1"
    );
    let heartbeat = heartbeat_payload(&payload, "started", 123, 456, JsonObject::new());
    assert_eq!(
        serde_json::to_value(&heartbeat).unwrap(),
        json!({
            "phase": "started",
            "timestamp_ms": 123,
            "parser_lane": "page_index",
            "doc_id": "doc:1",
            "parser_provider": "openai",
            "parser_model": "model:1",
            "parser_workflow_run_id": "run:1",
            "pid": 456
        })
    );
}

#[test]
fn result_status_matches_python_contract() {
    assert!(result_ok(&map(json!({"workflow_status": "ok"}))));
    assert!(result_ok(&map(json!({}))));
    assert!(!result_ok(&map(json!({"workflow_status": "failed"}))));
}

#[test]
fn result_payload_matches_python_contract() {
    let payload = map(json!({"parser_workflow_run_id": "run:1"}));
    let result = result_payload(
        &payload,
        "source:1",
        "page_index",
        "Title",
        &json!({"nodes": ["node:1"]}),
        &json!({"ok": true}),
        &json!({"llm_call_count": 1}),
        &[json!({"meta": {"provider_run_id": "run:1"}})],
        &map(json!({"workflow_status": "ok"})),
        Some("ok"),
        Some(&json!({"layer": "root"})),
    )
    .unwrap();

    assert_eq!(
        serde_json::to_value(&result).unwrap(),
        json!({
            "ok": true,
            "parser_workflow_run_id": "run:1",
            "parser_lane": "page_index",
            "title": "Title",
            "graph_payload": {"nodes": ["node:1"]},
            "evaluation": {"ok": true},
            "usage_summary": {"llm_call_count": 1},
            "usage_events": [{"meta": {"provider_run_id": "run:1"}}],
            "diagnostics": {"workflow_status": "ok"},
            "workflow_status": "ok",
            "layer_log": {"layer": "root"}
        })
    );

    let fallback = result_payload(
        &JsonObject::new(),
        "source:1",
        "workflow_layered",
        "Title",
        &json!({}),
        &json!({}),
        &json!({}),
        &[],
        &map(json!({})),
        None,
        None,
    )
    .unwrap();
    assert_eq!(fallback["parser_workflow_run_id"], json!("parser:source:1"));
    assert_eq!(fallback["workflow_status"], Value::Null);
    assert_eq!(fallback["layer_log"], Value::Null);
}

#[test]
fn failure_payload_and_llm_call_count_match_python() {
    let events = vec![
        json!({"meta": {"provider_run_id": "run:1"}}),
        json!({"meta": {"provider_run_id": "run:1"}}),
        json!({"meta": {"provider_run_id": ""}}),
        json!({"meta": {}}),
    ];
    assert_eq!(llm_call_count_from_events(&events), 1);

    let failure = failure_payload(
        "ValueError",
        "bad lane",
        "Traceback",
        std::path::Path::new("failure_payload.json"),
        &events,
    );
    assert_eq!(
        serde_json::to_value(&failure).unwrap(),
        json!({
            "ok": false,
            "error_type": "ValueError",
            "message": "bad lane",
            "traceback": "Traceback",
            "payload_path": "failure_payload.json",
            "usage_events": events,
            "llm_call_count": 1
        })
    );
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
