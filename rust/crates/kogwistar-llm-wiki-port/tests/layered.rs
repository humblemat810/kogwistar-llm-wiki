use kogwistar_llm_wiki_port::layered::{
    effective_run_id, engine_build_options, layer_event, layer_event_trace_message,
    ConversationPersistenceMode, LayeredParseResult,
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
fn conversation_modes_match_python() {
    assert_eq!(
        ConversationPersistenceMode::parse("single_stage").unwrap(),
        ConversationPersistenceMode::SingleStage
    );
    assert_eq!(
        ConversationPersistenceMode::parse("two_stage").unwrap(),
        ConversationPersistenceMode::TwoStage
    );
    assert_eq!(
        ConversationPersistenceMode::parse("other").unwrap_err(),
        "conversation_persistence_mode must be one of: single_stage, two_stage"
    );
}

#[test]
fn run_id_fallback_matches_python() {
    assert_eq!(effective_run_id(None, "source:1"), "parser:source:1");
    assert_eq!(effective_run_id(Some(""), "source:1"), "parser:source:1");
    assert_eq!(effective_run_id(Some("run:1"), "source:1"), "run:1");
}

#[test]
fn layer_events_and_trace_messages_match_python() {
    let extra = map(json!({"workflow_run_id": "run:1", "z": 2, "a": 1}));
    let event = layer_event(
        "workflow_layered_parse_start",
        123,
        "source:1",
        extra.clone(),
    );
    assert_eq!(
        serde_json::to_value(&event).unwrap(),
        json!({
            "stage": "workflow_layered_parse_start",
            "timestamp_ms": 123,
            "source_document_id": "source:1",
            "workflow_run_id": "run:1",
            "z": 2,
            "a": 1
        })
    );
    assert_eq!(
        layer_event_trace_message(&extra),
        "{\"a\": 1, \"workflow_run_id\": \"run:1\", \"z\": 2}"
    );
}

#[test]
fn engine_options_only_pass_explicit_two_stage() {
    let options = engine_build_options(json!({"provider": "ollama"}), "two_stage").unwrap();
    assert_eq!(
        options.conversation_persistence_mode,
        Some(ConversationPersistenceMode::TwoStage)
    );

    let options = engine_build_options(json!({"provider": "ollama"}), "single_stage").unwrap();
    assert_eq!(options.conversation_persistence_mode, None);
    assert!(engine_build_options(Value::Null, "other").is_err());
}

#[test]
fn layered_result_exposes_python_workflow_status() {
    let result = LayeredParseResult {
        graph_payload: json!({}),
        evaluation: json!({}),
        diagnostics: map(json!({"workflow_status": "ok"})),
        usage_summary: json!({}),
        usage_events: vec![],
        layer_log: vec![],
        semantic_tree: None,
        parser_source_map: None,
    };
    assert_eq!(result.workflow_status().as_deref(), Some("ok"));
}
