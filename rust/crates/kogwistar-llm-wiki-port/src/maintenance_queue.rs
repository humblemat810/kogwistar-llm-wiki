use serde::Serialize;
use serde_json::{json, Value};

use crate::maintenance_state::persisted_budget_state;
use crate::models::JsonObject;

const BUDGET_EXHAUSTION_MARKERS: [&str; 7] = [
    "exceeded wall-time budget",
    "exceeded wall time budget",
    "maintenance budget exhausted",
    "max_llm_calls budget exhausted",
    "max_tokens budget exhausted",
    "max_steps budget exhausted",
    "max_time_seconds budget exhausted",
];

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
pub enum MaintenanceQueueAction {
    Retry,
    Fail,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct MaintenanceFailure {
    pub action: MaintenanceQueueAction,
    pub reason: Option<&'static str>,
    pub error_text: String,
}

#[must_use]
pub fn budget_exhaustion_reason(error: &str) -> Option<&'static str> {
    let message = error.trim().to_ascii_lowercase();
    BUDGET_EXHAUSTION_MARKERS
        .iter()
        .find(|marker| message.contains(*marker))
        .copied()
}

#[must_use]
pub fn maintenance_failure_action(error: &str) -> MaintenanceFailure {
    match budget_exhaustion_reason(error) {
        Some(reason) => MaintenanceFailure {
            action: MaintenanceQueueAction::Fail,
            reason: Some(reason),
            error_text: format!("maintenance_budget_exhausted: {error}"),
        },
        None => MaintenanceFailure {
            action: MaintenanceQueueAction::Retry,
            reason: None,
            error_text: error.to_owned(),
        },
    }
}

#[must_use]
pub fn selection_result_payload(payload: &JsonObject) -> Value {
    json!({
        "selection_strategy": payload
            .get("selection_strategy")
            .and_then(Value::as_str)
            .unwrap_or_default(),
        "maintenance_candidates": payload
            .get("maintenance_candidates")
            .and_then(Value::as_array)
            .map(|candidates| candidates.iter().take(24).cloned().collect::<Vec<_>>())
            .unwrap_or_default()
    })
}

#[derive(Debug)]
pub enum RequeueSuspendedError {
    MissingFrontier,
    InvalidFrontier,
}

impl std::fmt::Display for RequeueSuspendedError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::MissingFrontier => {
                write!(
                    formatter,
                    "maintenance workflow suspended without a resumable frontier"
                )
            }
            Self::InvalidFrontier => write!(formatter, "invalid suspended frontier"),
        }
    }
}

impl std::error::Error for RequeueSuspendedError {}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct RequeueSuspendedJob {
    pub payload: JsonObject,
    pub continuation_run_id: String,
    pub suspended_node_id: String,
    pub suspended_token_id: String,
}

pub fn requeue_suspended_job(
    payload: &JsonObject,
    final_state: &Value,
    continuation_run_id: Option<&str>,
    budget_state: Option<&Value>,
) -> Result<RequeueSuspendedJob, RequeueSuspendedError> {
    let suspended = final_state
        .get("_rt_join")
        .and_then(|join| join.get("suspended"))
        .and_then(Value::as_array)
        .map(Vec::as_slice)
        .unwrap_or_default();
    let Some(frontier) = suspended.first() else {
        return Err(RequeueSuspendedError::MissingFrontier);
    };
    let Some(Value::Array(frontier)) = Some(frontier) else {
        return Err(RequeueSuspendedError::InvalidFrontier);
    };
    if frontier.len() < 4 {
        return Err(RequeueSuspendedError::InvalidFrontier);
    }
    let suspended_node_id = match frontier.first() {
        Some(Value::String(value)) if !value.trim().is_empty() => value.clone(),
        value => value
            .as_ref()
            .map(|value| value.to_string())
            .unwrap_or_default(),
    };
    let suspended_token_id = match frontier.get(2) {
        Some(Value::String(value)) if !value.trim().is_empty() => value.clone(),
        value => value
            .as_ref()
            .map(|value| value.to_string())
            .unwrap_or_default(),
    };
    let mut next_payload = payload.clone();
    if let Some(budget_state) = budget_state {
        next_payload.insert(
            "maintenance_budget_state".to_owned(),
            persisted_budget_state(Some(budget_state)),
        );
    }
    next_payload.insert(
        "continuation_run_id".to_owned(),
        Value::from(continuation_run_id.unwrap_or_default()),
    );
    next_payload.insert(
        "suspended_node_id".to_owned(),
        Value::from(suspended_node_id.clone()),
    );
    next_payload.insert(
        "suspended_token_id".to_owned(),
        Value::from(suspended_token_id.clone()),
    );
    Ok(RequeueSuspendedJob {
        payload: next_payload,
        continuation_run_id: continuation_run_id.unwrap_or_default().to_owned(),
        suspended_node_id,
        suspended_token_id,
    })
}

#[must_use]
pub fn requeue_trace_event(
    payload: &JsonObject,
    requeued: &RequeueSuspendedJob,
    timestamp_ms: u64,
) -> Value {
    json!({
        "event": "maintenance_job_requeued",
        "workspace_id": payload
            .get("workspace_id")
            .and_then(Value::as_str)
            .unwrap_or_default(),
        "source_document_id": payload
            .get("source_document_id")
            .and_then(Value::as_str)
            .unwrap_or_default(),
        "request_node_id": payload
            .get("request_node_id")
            .and_then(Value::as_str)
            .unwrap_or_default(),
        "continuation_run_id": requeued.continuation_run_id,
        "suspended_node_id": requeued.suspended_node_id,
        "suspended_token_id": requeued.suspended_token_id,
        "timestamp_ms": timestamp_ms
    })
}
