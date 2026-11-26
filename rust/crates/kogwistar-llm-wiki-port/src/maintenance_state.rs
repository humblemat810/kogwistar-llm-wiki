use std::collections::BTreeSet;

use serde_json::{json, Value};
use sha2::{Digest, Sha256};

use crate::models::JsonObject;

#[must_use]
pub fn and_where(clauses: &[(&str, Value)]) -> Value {
    json!({
        "$and": clauses
            .iter()
            .map(|(key, value)| json!({ *key: value }))
            .collect::<Vec<_>>()
    })
}

#[must_use]
pub fn belongs_to_workspace(metadata: Option<&Value>, workspace_id: &str) -> bool {
    let Some(metadata) = metadata.and_then(Value::as_object) else {
        return true;
    };
    let declared = metadata
        .get("workspace_id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .trim();
    declared.is_empty() || declared == workspace_id
}

#[must_use]
pub fn edge_ids(edge: &Value) -> BTreeSet<String> {
    let mut ids = BTreeSet::new();
    for field in ["source_ids", "target_ids"] {
        match edge.get(field) {
            Some(Value::String(value)) if !value.trim().is_empty() => {
                ids.insert(value.clone());
            }
            Some(Value::Array(values)) => {
                ids.extend(
                    values
                        .iter()
                        .filter_map(Value::as_str)
                        .filter(|value| !value.trim().is_empty())
                        .map(str::to_owned),
                );
            }
            _ => {}
        }
    }
    ids
}

#[must_use]
pub fn persisted_budget_state(state: Option<&Value>) -> Value {
    const ALLOWED: [&str; 15] = [
        "token_budget",
        "token_used",
        "step_budget",
        "step_used",
        "call_budget",
        "call_used",
        "time_budget_ms",
        "time_used_ms",
        "cost_budget",
        "cost_used",
        "request_token_budget",
        "request_step_budget",
        "request_call_budget",
        "request_time_budget_ms",
        "request_cost_budget",
    ];
    let Some(Value::Object(values)) = state else {
        return Value::Null;
    };
    Value::Object(
        values
            .iter()
            .filter(|(key, value)| {
                ALLOWED.contains(&key.as_str()) && value.is_number() && !value.is_boolean()
            })
            .map(|(key, value)| (key.clone(), value.clone()))
            .collect(),
    )
}

#[must_use]
pub fn semantic_fingerprint(extraction: &Value) -> String {
    let mut payload = extraction.clone();
    for key in ["nodes", "edges"] {
        let Some(values) = payload.get_mut(key).and_then(Value::as_array_mut) else {
            continue;
        };
        let mut normalized = values
            .iter()
            .map(|item| {
                let mut item = item.clone();
                if let Some(Value::Object(object)) = Some(&mut item) {
                    object.remove("id");
                    if let Some(Value::Object(metadata)) = object.get_mut("metadata") {
                        metadata.remove("parse_generation_event_id");
                        metadata.remove("parse_generation_member_id");
                    }
                }
                item
            })
            .collect::<Vec<_>>();
        normalized.sort_by_cached_key(|item| serde_json::to_string(item).unwrap_or_default());
        payload[key] = Value::Array(normalized);
    }
    let encoded = serde_json::to_string(&payload).expect("semantic fingerprint payload");
    let digest = Sha256::digest(encoded.as_bytes());
    digest.iter().map(|byte| format!("{byte:02x}")).collect()
}

pub struct MaintenanceBudgetInputs<'a> {
    pub payload: &'a JsonObject,
    pub fair_scheduling: bool,
    pub maintenance_steps_per_slice: i64,
    pub maintenance_llm_calls_per_slice: i64,
    pub maintenance_seconds_per_slice: i64,
    pub durable_usage: Option<&'a JsonObject>,
}

fn int_value(value: Option<&Value>, default: i64) -> i64 {
    match value {
        Some(Value::Number(number)) => number.as_i64().unwrap_or(default),
        Some(Value::String(value)) => value.parse().unwrap_or(default),
        Some(Value::Bool(true)) => 1,
        Some(Value::Bool(false)) => 0,
        _ => default,
    }
}

fn float_value(value: Option<&Value>, default: f64) -> f64 {
    match value {
        Some(Value::Number(number)) => number.as_f64().unwrap_or(default),
        Some(Value::String(value)) => value.parse().unwrap_or(default),
        Some(Value::Bool(_)) | None => default,
        _ => default,
    }
}

fn insert_number(state: &mut JsonObject, key: &str, value: Value) {
    state.insert(key.to_owned(), value);
}

#[must_use]
pub fn maintenance_budget_state(inputs: &MaintenanceBudgetInputs<'_>) -> JsonObject {
    let mut state = inputs
        .payload
        .get("maintenance_budget_state")
        .and_then(Value::as_object)
        .map(|values| {
            values
                .iter()
                .map(|(key, value)| (key.clone(), value.clone()))
                .collect()
        })
        .unwrap_or_default();
    let budgets = inputs
        .payload
        .get("budgets")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    let token_limit = int_value(budgets.get("max_tokens"), 10_000_000);
    let call_limit = int_value(budgets.get("max_llm_calls"), 0);
    let step_limit = int_value(budgets.get("max_steps"), 0);
    let time_limit_ms = (float_value(budgets.get("max_time_seconds"), 0.0) * 1000.0) as i64;
    let cost_limit = float_value(budgets.get("max_cost_usd"), 0.0);
    insert_number(&mut state, "request_token_budget", Value::from(token_limit));
    insert_number(&mut state, "request_call_budget", Value::from(call_limit));
    insert_number(&mut state, "request_step_budget", Value::from(step_limit));
    insert_number(
        &mut state,
        "request_time_budget_ms",
        Value::from(time_limit_ms),
    );
    insert_number(&mut state, "request_cost_budget", Value::from(cost_limit));
    let mut used: JsonObject = [
        "token_used",
        "call_used",
        "step_used",
        "time_used_ms",
        "cost_used",
    ]
    .iter()
    .map(|key| {
        (
            (*key).to_owned(),
            state.get(*key).cloned().unwrap_or(Value::from(0)),
        )
    })
    .collect();
    if let Some(durable_usage) = inputs.durable_usage {
        for (key, value) in durable_usage {
            let Some(current) = used.get_mut(key) else {
                continue;
            };
            if value.is_boolean() || !value.is_number() {
                continue;
            }
            let value = value.as_f64().unwrap_or(0.0);
            let merged = current.as_f64().unwrap_or(0.0).max(value);
            if key == "cost_used" {
                *current = Value::from(merged);
                state.insert(key.clone(), Value::from(merged));
            } else {
                *current = Value::from(merged.trunc() as i64);
                state.insert(key.clone(), Value::from(merged.trunc() as i64));
            }
        }
    }
    insert_number(&mut state, "token_budget", Value::from(token_limit));
    insert_number(&mut state, "call_budget", Value::from(call_limit));
    insert_number(&mut state, "step_budget", Value::from(step_limit));
    insert_number(&mut state, "time_budget_ms", Value::from(time_limit_ms));
    insert_number(&mut state, "cost_budget", Value::from(cost_limit));
    if inputs.fair_scheduling {
        for (limit_key, slice_limit, request_key, used_key) in [
            (
                "step_budget",
                inputs.maintenance_steps_per_slice,
                "request_step_budget",
                "step_used",
            ),
            (
                "call_budget",
                inputs.maintenance_llm_calls_per_slice,
                "request_call_budget",
                "call_used",
            ),
            (
                "time_budget_ms",
                inputs.maintenance_seconds_per_slice * 1000,
                "request_time_budget_ms",
                "time_used_ms",
            ),
        ] {
            if slice_limit == 0 {
                continue;
            }
            let request_limit = int_value(state.get(request_key), 0);
            let slice_cap = int_value(used.get(used_key), 0) + slice_limit;
            let value = if request_limit == 0 {
                slice_cap
            } else {
                request_limit.min(slice_cap)
            };
            insert_number(&mut state, limit_key, Value::from(value));
        }
    }
    state
}
