use serde::Serialize;
use serde_json::{json, Value};

use crate::models::JsonObject;

pub const MAX_CONTACT_SCAN_STREAMS: usize = 64;
pub const MAX_CONTACT_SCAN_OBSERVATIONS: usize = 250;
pub const MAX_CONTACT_SCAN_CANDIDATES: usize = 500;

#[derive(Clone, Debug, PartialEq)]
pub struct ContactScanRequest {
    pub workspace_id: String,
    pub source_stream_ids: Vec<String>,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct ContactScanError {
    pub message: String,
    pub kind: &'static str,
}

impl std::fmt::Display for ContactScanError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "{}", self.message)
    }
}

impl std::error::Error for ContactScanError {}

fn error(kind: &'static str, message: &str) -> ContactScanError {
    ContactScanError {
        message: message.to_owned(),
        kind,
    }
}

pub fn normalize_contact_scan_payload(
    payload: &JsonObject,
) -> Result<ContactScanRequest, ContactScanError> {
    let workspace_id = payload
        .get("workspace_id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .trim();
    if workspace_id.is_empty() {
        return Err(error(
            "value",
            "contact scan workspace does not match its maintenance job",
        ));
    }
    let Some(Value::Array(raw_stream_ids)) = payload.get("source_stream_ids") else {
        return Err(error(
            "value",
            "contact scan requires authorized source_stream_ids",
        ));
    };
    if raw_stream_ids.is_empty() {
        return Err(error(
            "value",
            "contact scan requires authorized source_stream_ids",
        ));
    }
    if raw_stream_ids.len() > MAX_CONTACT_SCAN_STREAMS {
        return Err(error("value", "contact scan exceeds source stream limit"));
    }
    let mut stream_ids = Vec::new();
    for value in raw_stream_ids {
        let Some(value) = value.as_str() else {
            return Err(error(
                "value",
                "contact scan source_stream_ids must be non-empty strings",
            ));
        };
        let stream_id = value.trim();
        if stream_id.is_empty() {
            return Err(error(
                "value",
                "contact scan source_stream_ids must be non-empty strings",
            ));
        }
        stream_ids.push(stream_id.to_owned());
    }
    let unique = stream_ids
        .iter()
        .cloned()
        .collect::<std::collections::BTreeSet<_>>();
    if unique.len() != stream_ids.len() {
        return Err(error(
            "value",
            "contact scan source_stream_ids must be unique",
        ));
    }
    Ok(ContactScanRequest {
        workspace_id: workspace_id.to_owned(),
        source_stream_ids: stream_ids,
    })
}

pub fn bound_contact_scan_observations(
    observations: &[Value],
) -> Result<Vec<Value>, ContactScanError> {
    if observations.len() > MAX_CONTACT_SCAN_OBSERVATIONS {
        return Err(error("value", "contact scan exceeds observation limit"));
    }
    for observation in observations {
        if observation
            .get("entity_id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .is_empty()
        {
            return Err(error(
                "type",
                "contact observation provider returned an invalid observation",
            ));
        }
        if observation
            .get("stream_id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .is_empty()
        {
            return Err(error(
                "type",
                "contact observation provider returned an invalid observation",
            ));
        }
    }
    Ok(observations.to_vec())
}

#[must_use]
pub fn contact_scan_trace(workspace_id: &str, job_id: &str, persisted_ids: &[String]) -> Value {
    json!({
        "event": "contact_disambiguation_scan_complete",
        "workspace_id": workspace_id,
        "job_id": job_id,
        "persisted_candidate_count": persisted_ids.len(),
        "persisted_ids": persisted_ids
    })
}
