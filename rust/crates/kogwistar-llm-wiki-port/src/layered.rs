use serde::Serialize;
use serde_json::Value;

use crate::models::JsonObject;
use crate::provenance::python_spaced_json;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ConversationPersistenceMode {
    SingleStage,
    TwoStage,
}

impl ConversationPersistenceMode {
    pub fn parse(value: &str) -> Result<Self, String> {
        match value {
            "single_stage" => Ok(Self::SingleStage),
            "two_stage" => Ok(Self::TwoStage),
            _ => Err(
                "conversation_persistence_mode must be one of: single_stage, two_stage".to_owned(),
            ),
        }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct LayeredParseResult {
    pub graph_payload: Value,
    pub evaluation: Value,
    pub diagnostics: JsonObject,
    pub usage_summary: Value,
    pub usage_events: Vec<Value>,
    pub layer_log: Vec<LayerEvent>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub semantic_tree: Option<Value>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub parser_source_map: Option<JsonObject>,
}

impl LayeredParseResult {
    #[must_use]
    pub fn workflow_status(&self) -> Option<String> {
        self.diagnostics
            .get("workflow_status")
            .and_then(Value::as_str)
            .map(str::to_owned)
    }
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct LayerEvent {
    pub stage: String,
    pub timestamp_ms: u64,
    pub source_document_id: String,
    #[serde(flatten)]
    pub extra: JsonObject,
}

#[must_use]
pub fn effective_run_id(run_id: Option<&str>, source_document_id: &str) -> String {
    run_id
        .filter(|value| !value.trim().is_empty())
        .unwrap_or(&format!("parser:{source_document_id}"))
        .to_owned()
}

#[must_use]
pub fn layer_event(
    stage: &str,
    timestamp_ms: u64,
    source_document_id: &str,
    extra: JsonObject,
) -> LayerEvent {
    LayerEvent {
        stage: stage.to_owned(),
        timestamp_ms,
        source_document_id: source_document_id.to_owned(),
        extra,
    }
}

#[must_use]
pub fn layer_event_trace_message(extra: &JsonObject) -> String {
    python_spaced_json(&Value::Object(extra.clone().into_iter().collect()))
}

#[derive(Clone, Debug, PartialEq)]
pub struct EngineBuildOptions {
    pub provider_settings: Value,
    pub conversation_persistence_mode: Option<ConversationPersistenceMode>,
}

pub fn engine_build_options(
    provider_settings: Value,
    conversation_persistence_mode: &str,
) -> Result<EngineBuildOptions, String> {
    let mode = ConversationPersistenceMode::parse(conversation_persistence_mode)?;
    Ok(EngineBuildOptions {
        provider_settings,
        conversation_persistence_mode: match mode {
            ConversationPersistenceMode::SingleStage => None,
            ConversationPersistenceMode::TwoStage => Some(mode),
        },
    })
}
