use std::collections::BTreeMap;

use serde_json::json;

use kogwistar_llm_wiki_port::otel::{
    event_attributes, from_environment, set_enabled, trace_exporter_endpoint,
};

fn environment(values: &[(&str, &str)]) -> BTreeMap<String, String> {
    values
        .iter()
        .map(|(key, value)| ((*key).to_string(), (*value).to_string()))
        .collect()
}

#[test]
fn defaults_to_noop_service_name() {
    let telemetry = from_environment(&environment(&[]));
    assert_eq!(telemetry.service_name, "kogwistar-llm-wiki");
    assert!(!telemetry.enabled);
}

#[test]
fn toggles_runtime_enabled_without_provider_side_effects() {
    let mut telemetry = from_environment(&environment(&[("LLM_WIKI_OTEL_ENABLED", "true")]));
    assert!(telemetry.enabled);
    set_enabled(&mut telemetry, false);
    assert!(!telemetry.enabled);
    set_enabled(&mut telemetry, true);
    assert!(telemetry.enabled);
}

#[test]
fn resolves_trace_exporter_endpoint() {
    let direct = trace_exporter_endpoint(&environment(&[(
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "http://collector/v1/traces",
    )]));
    assert_eq!(direct.as_deref(), Some("http://collector/v1/traces"));
    let base = trace_exporter_endpoint(&environment(&[(
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "http://collector",
    )]));
    assert_eq!(base.as_deref(), Some("http://collector/v1/traces"));
}

#[test]
fn maps_scalar_event_attributes() {
    let attributes = event_attributes(&json!({
        "event": "ingest",
        "workspace_id": "ws",
        "attempts": 1,
        "duration_ms": 12.5,
        "ok": true,
        "ignored": [1],
    }));
    assert_eq!(
        attributes.get("llm_wiki.event").map(String::as_str),
        Some("ingest")
    );
    assert_eq!(
        attributes.get("llm_wiki.workspace_id").map(String::as_str),
        Some("ws")
    );
    assert_eq!(
        attributes.get("llm_wiki.attempts").map(String::as_str),
        Some("1")
    );
    assert_eq!(
        attributes.get("llm_wiki.duration_ms").map(String::as_str),
        Some("12.5")
    );
    assert_eq!(
        attributes.get("llm_wiki.ok").map(String::as_str),
        Some("true")
    );
    assert_eq!(attributes.get("llm_wiki.ignored"), None);
}
