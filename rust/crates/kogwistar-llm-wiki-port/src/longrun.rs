use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde_json::Value;

use crate::models::JsonObject;

pub fn object_mapping(value: &Value) -> JsonObject {
    match value {
        Value::Object(values) => values
            .iter()
            .map(|(key, item)| (key.clone(), item.clone()))
            .collect(),
        _ => JsonObject::new(),
    }
}

pub fn payload_path(payload: &JsonObject, key: &str) -> Result<PathBuf, String> {
    match payload.get(key) {
        Some(Value::String(value)) if !value.is_empty() => Ok(PathBuf::from(value)),
        _ => Err(format!(
            "parser child payload field {:?} must be a path",
            key
        )),
    }
}

pub fn payload_int(payload: &JsonObject, key: &str, default: i64) -> Result<i64, String> {
    match payload.get(key) {
        None => Ok(default),
        Some(Value::Bool(_)) => Err(format!(
            "parser child payload field {:?} must be an integer",
            key
        )),
        Some(Value::Number(value)) => value
            .as_i64()
            .ok_or_else(|| format!("parser child payload field {:?} must be an integer", key)),
        Some(Value::String(value)) => value
            .trim()
            .parse()
            .map_err(|_| format!("parser child payload field {:?} must be an integer", key)),
        _ => Err(format!(
            "parser child payload field {:?} must be an integer",
            key
        )),
    }
}

#[must_use]
pub fn proposal_mode_summary(final_state: &JsonObject) -> JsonObject {
    let empty = JsonObject::new();
    let current_layer_result = object_mapping(
        final_state
            .get("current_layer_result")
            .unwrap_or(&Value::Null),
    );
    let metadata = object_mapping(current_layer_result.get("metadata").unwrap_or(&Value::Null));
    if metadata.is_empty() {
        return empty;
    }

    let keys = [
        "proposal_mode",
        "proposal_source",
        "proposal_failure_reason",
        "boundary_proposed_count",
        "boundary_dropped_count",
        "boundary_accepted_count",
        "boundary_shifted_count",
        "boundary_rejected_count",
        "boundary_refinement_count",
        "boundary_refinement_attempts",
        "boundary_summary_count",
        "unresolved_interval_count",
        "provider_child_count",
    ];
    keys.iter()
        .filter_map(|key| {
            metadata
                .get(*key)
                .and_then(|value| (!value.is_null()).then(|| ((*key).to_owned(), value.clone())))
        })
        .collect()
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ChildPaths {
    pub heartbeat_path: PathBuf,
    pub result_path: PathBuf,
    pub failure_path: PathBuf,
    pub failure_payload_path: PathBuf,
    pub trace_path: PathBuf,
    pub dump_trace_path: Option<PathBuf>,
}

pub fn child_paths(payload: &JsonObject) -> Result<ChildPaths, String> {
    let heartbeat_path = payload_path(payload, "heartbeat_path")?;
    let result_path = payload_path(payload, "result_path")?;
    let failure_path = payload_path(payload, "failure_path")?;
    let failure_payload_path = match payload.get("failure_payload_path") {
        Some(Value::String(value)) if !value.is_empty() => PathBuf::from(value),
        _ => failure_path
            .parent()
            .unwrap_or_else(|| Path::new(""))
            .join("failure_payload.json"),
    };
    let trace_path = payload_path(payload, "trace_path")?;
    let dump_trace_path = match payload.get("dump_trace_path") {
        Some(Value::String(value)) if !value.is_empty() => Some(PathBuf::from(value)),
        _ => None,
    };
    Ok(ChildPaths {
        heartbeat_path,
        result_path,
        failure_path,
        failure_payload_path,
        trace_path,
        dump_trace_path,
    })
}

#[must_use]
pub fn env_flag_enabled(env: &BTreeMap<String, String>, names: &[&str], default: bool) -> bool {
    for name in names {
        if let Some(value) = env.get(*name) {
            return matches!(
                value.trim().to_ascii_lowercase().as_str(),
                "1" | "true" | "yes" | "on"
            );
        }
    }
    default
}

#[must_use]
pub fn live_trace_enabled(payload: &JsonObject, env: &BTreeMap<String, String>) -> bool {
    payload
        .get("live_trace")
        .and_then(Value::as_bool)
        .unwrap_or_default()
        || env_flag_enabled(
            env,
            &[
                "KOGWISTAR_LONGRUN_LIVE_TRACE",
                "KOGWISTAR_LLM_WIKI_LIVE_TRACE",
            ],
            env.get("KOGWISTAR_LLM_WIKI_LONGRUN")
                .map(|value| value.trim() == "1")
                .unwrap_or_default(),
        )
}

#[must_use]
pub fn trace_context(payload: &JsonObject) -> String {
    format!(
        "doc={} parser_lane={} provider={} model={} parser_workflow_run_id={}",
        python_string(payload.get("doc_id")),
        python_string(payload.get("parser_lane")),
        if python_string(payload.get("parser_provider")).is_empty() {
            "unknown".to_owned()
        } else {
            python_string(payload.get("parser_provider"))
        },
        if python_string(payload.get("parser_model")).is_empty() {
            "unknown".to_owned()
        } else {
            python_string(payload.get("parser_model"))
        },
        if python_string(payload.get("parser_workflow_run_id")).is_empty() {
            "unknown".to_owned()
        } else {
            python_string(payload.get("parser_workflow_run_id"))
        },
    )
}

pub fn heartbeat_payload(
    payload: &JsonObject,
    phase: &str,
    timestamp_ms: u64,
    pid: u32,
    extra: JsonObject,
) -> JsonObject {
    let mut value = JsonObject::from([
        ("phase".to_owned(), Value::from(phase)),
        ("timestamp_ms".to_owned(), Value::from(timestamp_ms)),
        (
            "parser_lane".to_owned(),
            python_string(payload.get("parser_lane")).into(),
        ),
        (
            "doc_id".to_owned(),
            python_string(payload.get("doc_id")).into(),
        ),
        (
            "parser_provider".to_owned(),
            python_string(payload.get("parser_provider")).into(),
        ),
        (
            "parser_model".to_owned(),
            python_string(payload.get("parser_model")).into(),
        ),
        (
            "parser_workflow_run_id".to_owned(),
            python_string(payload.get("parser_workflow_run_id")).into(),
        ),
        ("pid".to_owned(), Value::from(pid)),
    ]);
    for (key, item) in extra {
        value.insert(key, item);
    }
    value
}

#[must_use]
pub fn result_ok(diagnostics: &JsonObject) -> bool {
    let workflow_status = python_string(diagnostics.get("workflow_status"))
        .trim()
        .to_ascii_lowercase();
    !matches!(workflow_status.as_str(), "failure" | "failed" | "error")
}

fn python_string(value: Option<&Value>) -> String {
    match value {
        Some(Value::String(value)) => value.clone(),
        Some(Value::Null) => String::new(),
        Some(Value::Bool(true)) => "True".to_owned(),
        Some(Value::Bool(false)) => "False".to_owned(),
        Some(other) => other.to_string(),
        None => String::new(),
    }
}
