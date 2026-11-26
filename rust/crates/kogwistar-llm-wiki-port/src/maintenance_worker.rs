use serde_json::{json, Value};

use crate::ids::stable_id;
use crate::models::JsonObject;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MaintenanceRunContext<'a> {
    pub workspace_id: &'a str,
    pub request_node_id: &'a str,
    pub job_id: &'a str,
    pub maintenance_kind: &'a str,
    pub worker_id: &'a str,
    pub claim_attempts: i64,
}

fn as_int(value: Option<&Value>, default: i64) -> i64 {
    match value {
        Some(Value::Number(number)) => number.as_i64().unwrap_or_else(|| {
            number
                .as_f64()
                .map(|value| value.trunc() as i64)
                .unwrap_or(default)
        }),
        Some(Value::String(value)) => value.parse().unwrap_or(default),
        Some(Value::Bool(true)) => default,
        Some(Value::Bool(false)) => default,
        _ => default,
    }
}

fn first_truthy<'a>(values: &[Option<&'a Value>]) -> Option<&'a Value> {
    values
        .iter()
        .flatten()
        .find(|value| match value {
            Value::Null | Value::Bool(false) => false,
            Value::String(value) => !value.is_empty(),
            Value::Array(values) => !values.is_empty(),
            Value::Object(values) => !values.is_empty(),
            _ => true,
        })
        .copied()
}

#[must_use]
pub fn expected_revisions_from_payload(payload: &JsonObject) -> Option<JsonObject> {
    let Value::Object(raw) = payload.get("expected_revisions")? else {
        return None;
    };
    let mut normalized = JsonObject::new();
    for (entity_id, revision) in raw {
        if entity_id.is_empty() {
            let mut invalid = JsonObject::new();
            invalid.insert("__invalid_expected_revision_key__".to_owned(), Value::Null);
            return Some(invalid);
        }
        if revision.is_boolean() {
            normalized.insert(entity_id.clone(), Value::Null);
            continue;
        }
        let normalized_revision = match revision {
            Value::String(_) | Value::Null => revision.clone(),
            Value::Number(number) => match number.as_i64() {
                Some(value) => Value::from(value),
                None => Value::from(number.as_f64().unwrap_or_default().trunc() as i64),
            },
            _ => Value::Null,
        };
        normalized.insert(entity_id.clone(), normalized_revision);
    }
    Some(normalized)
}

#[must_use]
pub fn expected_revisions_from_mapping(raw: Option<&Value>) -> Option<JsonObject> {
    let Value::Object(raw) = raw? else {
        return None;
    };
    let payload = JsonObject::from([("expected_revisions".to_owned(), Value::Object(raw.clone()))]);
    expected_revisions_from_payload(&payload)
}

#[must_use]
pub fn maintenance_run_identity(
    payload: &JsonObject,
    context: &MaintenanceRunContext<'_>,
) -> JsonObject {
    let claim_attempts = Value::from(context.claim_attempts);
    let attempt = as_int(
        first_truthy(&[
            payload.get("maintenance_attempt"),
            payload.get("attempt_count"),
            if context.claim_attempts == 0 {
                None
            } else {
                Some(&claim_attempts)
            },
        ]),
        1,
    );
    let run_id = payload
        .get("maintenance_run_id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .trim();
    let run_id = if run_id.is_empty() {
        stable_id(
            "maintenance_run",
            &[
                context.workspace_id,
                context.request_node_id,
                context.job_id,
                &attempt.to_string(),
            ],
        )
        .to_string()
    } else {
        run_id.to_owned()
    };
    let identity = json!({
        "maintenance_run_id": run_id,
        "workspace_id": context.workspace_id,
        "request_node_id": context.request_node_id,
        "job_id": context.job_id,
        "worker_id": context.worker_id,
        "attempt": attempt,
        "maintenance_kind": context.maintenance_kind,
        "workflow_version": "crosslink-groups.v1"
    });
    identity
        .as_object()
        .expect("run identity is a JSON object")
        .iter()
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect()
}
