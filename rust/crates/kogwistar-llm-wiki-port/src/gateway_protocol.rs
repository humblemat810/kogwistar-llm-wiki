//! Small response-shaping helpers used by the agent gateway.

use serde_json::{json, Value};
use std::collections::HashMap;

#[must_use]
pub fn count_job_statuses(jobs: &[Value]) -> HashMap<String, usize> {
    let mut counts = HashMap::new();
    for job in jobs {
        let status = job
            .get("status")
            .and_then(Value::as_str)
            .unwrap_or("unknown")
            .to_string();
        *counts.entry(status).or_insert(0) += 1;
    }
    counts
}

#[must_use]
pub fn answer_text(result: &Value) -> String {
    let answer = result.get("answer");
    match answer {
        Some(Value::Object(answer)) => answer
            .get("text")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string(),
        Some(Value::String(text)) => text.clone(),
        Some(Value::Number(number)) => number.to_string(),
        Some(Value::Bool(value)) => value.to_string(),
        _ => String::new(),
    }
}

#[must_use]
pub fn request_id_from<G>(payload: &Value, prefix: &str, generator: G) -> String
where
    G: FnOnce() -> String,
{
    match payload.get("id").and_then(Value::as_str) {
        Some(id) if !id.is_empty() => id.to_string(),
        _ => format!("{prefix}_{}", generator()),
    }
}

#[must_use]
pub fn request_id(payload: &Value, prefix: &str) -> String {
    request_id_from(payload, prefix, || {
        uuid::Uuid::new_v4().simple().to_string()
    })
}

#[must_use]
pub fn a2a_task(interaction: &Value, standard: bool) -> Value {
    if interaction.get("status").is_some_and(Value::is_object) {
        let mut result = interaction.clone();
        let metadata_workspace = interaction
            .get("metadata")
            .and_then(|metadata| metadata.get("workspace_id"))
            .cloned()
            .unwrap_or_else(|| json!("default"));
        result
            .as_object_mut()
            .expect("status interaction is an object")
            .entry("contextId".to_string())
            .or_insert(metadata_workspace);
        return result;
    }

    let status = interaction
        .get("status")
        .and_then(Value::as_str)
        .unwrap_or("pending");
    let state = match (standard, status) {
        (_, "completed") => "completed",
        (_, "failed") => "failed",
        (true, "pending") => "submitted",
        (false, "pending") => "working",
        _ => status,
    };
    let text = interaction
        .get("response")
        .filter(|item| item.is_object())
        .map(answer_text)
        .unwrap_or_default();
    let mut result = json!({
        "id": interaction.get("interaction_id"),
        "contextId": interaction
            .get("context_id")
            .or_else(|| interaction.get("workspace_id"))
            .unwrap_or(&json!("default")),
        "status": {"state": state},
        "metadata": {"workspace_id": interaction.get("workspace_id")},
    });
    if !text.is_empty() {
        result["artifacts"] = json!([{"parts": [{"kind": "text", "text": text}]}]);
    }
    if let Some(error) = interaction.get("error").filter(|value| !value.is_null()) {
        result["status"] = json!({
            "state": "failed",
            "message": {
                "messageId": interaction.get("interaction_id"),
                "parts": [{"kind": "text", "text": error.to_string()}]
            }
        });
    }
    result
}

#[must_use]
pub fn jsonrpc_result(request_id: &Value, result: &Value) -> Value {
    json!({
        "jsonrpc": "2.0",
        "id": request_id,
        "result": result
    })
}

#[must_use]
pub fn jsonrpc_error(request_id: &Value, code: i32, message: &str, data: Option<&Value>) -> Value {
    let mut error = json!({
        "code": code,
        "message": message
    });
    if let Some(data) = data {
        error["data"] = data.clone();
    }
    json!({
        "jsonrpc": "2.0",
        "id": request_id,
        "error": error
    })
}
