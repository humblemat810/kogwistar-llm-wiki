use std::collections::BTreeMap;

use serde_json::Value;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct LlmWikiTelemetry {
    pub service_name: String,
    pub packages_available: bool,
    pub runtime_enabled: Option<bool>,
    pub enabled: bool,
}

pub fn env_bool(environment: &BTreeMap<String, String>, name: &str, default: bool) -> bool {
    match environment.get(name) {
        Some(value) => matches!(
            value.trim().to_lowercase().as_str(),
            "1" | "true" | "yes" | "on"
        ),
        None => default,
    }
}

pub fn from_environment(environment: &BTreeMap<String, String>) -> LlmWikiTelemetry {
    let service_name = environment
        .get("LLM_WIKI_OTEL_SERVICE_NAME")
        .map(String::as_str)
        .unwrap_or("kogwistar-llm-wiki")
        .to_owned();
    let configured = env_bool(environment, "LLM_WIKI_OTEL_ENABLED", false);
    LlmWikiTelemetry {
        service_name,
        packages_available: true,
        runtime_enabled: None,
        enabled: configured,
    }
}

pub fn set_enabled(telemetry: &mut LlmWikiTelemetry, enabled: bool) {
    telemetry.runtime_enabled = Some(enabled);
    telemetry.enabled = enabled && telemetry.packages_available;
}

pub fn trace_exporter_endpoint(environment: &BTreeMap<String, String>) -> Option<String> {
    if let Some(endpoint) = environment.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT") {
        return Some(endpoint.to_owned());
    }
    let base_endpoint = environment.get("OTEL_EXPORTER_OTLP_ENDPOINT")?;
    let mut endpoint = base_endpoint.trim_end_matches('/').to_owned();
    if !endpoint.ends_with("/v1/traces") {
        endpoint.push_str("/v1/traces");
    }
    Some(endpoint)
}

pub fn event_attributes(event: &Value) -> BTreeMap<String, String> {
    let mut attributes = BTreeMap::new();
    if let Some(fields) = event.as_object() {
        for (key, value) in fields {
            if matches!(value, Value::String(_) | Value::Number(_) | Value::Bool(_)) {
                let serialized = match value {
                    Value::String(text) => text.clone(),
                    other => other.to_string(),
                };
                attributes.insert(format!("llm_wiki.{key}"), serialized);
            }
        }
    }
    attributes
}
