use serde::Serialize;
use serde_json::{json, Value};

use crate::models::JsonObject;

pub const BUDGET_EXHAUSTION_MARKERS: [&str; 7] = [
    "exceeded wall-time budget",
    "exceeded wall time budget",
    "maintenance budget exhausted",
    "max_llm_calls budget exhausted",
    "max_tokens budget exhausted",
    "max_steps budget exhausted",
    "max_time_seconds budget exhausted",
];

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct RetryOrFailDecision {
    pub action: &'static str,
    pub reason: Option<String>,
    pub error_text: Option<String>,
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
pub fn retry_or_fail_maintenance_job(
    job_id: &str,
    claim_token: Option<&str>,
    error: &str,
) -> RetryOrFailDecision {
    if let Some(reason) = budget_exhaustion_reason(error) {
        return RetryOrFailDecision {
            action: "failed",
            reason: Some(reason.to_owned()),
            error_text: Some(format!("maintenance_budget_exhausted: {error}")),
        };
    }
    let _ = (job_id, claim_token);
    RetryOrFailDecision {
        action: "retry",
        reason: None,
        error_text: None,
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

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct AcknowledgementTrace {
    pub event: &'static str,
    pub workspace_id: String,
    pub job_id: String,
    pub lease_seconds: u64,
    pub timestamp_ms: u64,
}

#[must_use]
pub fn acknowledgement_trace(
    workspace_id: &str,
    job_id: &str,
    lease_seconds: u64,
    timestamp_ms: u64,
) -> AcknowledgementTrace {
    AcknowledgementTrace {
        event: "maintenance_job_acknowledged",
        workspace_id: workspace_id.to_owned(),
        job_id: job_id.to_owned(),
        lease_seconds,
        timestamp_ms,
    }
}
