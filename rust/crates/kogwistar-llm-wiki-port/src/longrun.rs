use std::path::PathBuf;

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
