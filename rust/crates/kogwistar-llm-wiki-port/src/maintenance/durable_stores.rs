//! Durable maintenance stores: workspace filtering, metadata mapping,
//! semantic fingerprints, persisted budget state, and durable usage recovery.
//!
//! Mirrors Python contract `src/kogwistar_llm_wiki/maintenance/state.py`.
//! Pure over `serde_json::Value`, independent of the graph engine, so every
//! helper is unit-testable without a database.

use serde_json::{Map, Value};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, HashSet};

/// Budget keys safe to persist on a requeued maintenance job.
const PERSISTED_BUDGET_KEYS: &[&str] = &[
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

/// Chroma-compatible conjunction filter over metadata clauses.
#[must_use]
pub fn and_where(clauses: &[Map<String, Value>]) -> Value {
    let mut map = Map::new();
    map.insert(
        "$and".to_string(),
        Value::Array(clauses.iter().cloned().map(Value::Object).collect()),
    );
    Value::Object(map)
}

/// Metadata boundary check after namespace/ACL filtering.
///
/// Returns `true` when the node carries no declared workspace or when it
/// matches `workspace_id`.
#[must_use]
pub fn belongs_to_workspace(metadata: &Map<String, Value>, workspace_id: &str) -> bool {
    let declared = metadata
        .get("workspace_id")
        .and_then(Value::as_str)
        .unwrap_or("")
        .trim();
    declared.is_empty() || declared == workspace_id
}

/// Normalize graph metadata, merging a nested `"metadata"` JSON string.
#[must_use]
pub fn metadata_mapping(raw: &Map<String, Value>) -> Map<String, Value> {
    let mut metadata: Map<String, Value> = raw
        .iter()
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect();
    if let Some(Value::String(nested)) = metadata.get("metadata") {
        if !nested.is_empty() {
            if let Ok(parsed) = serde_json::from_str::<Map<String, Value>>(nested) {
                for (key, value) in parsed {
                    metadata.insert(key, value);
                }
            }
        }
    }
    metadata
}

/// All node IDs referenced by one edge.
#[must_use]
pub fn edge_ids(edge: &Map<String, Value>) -> HashSet<String> {
    let mut ids = HashSet::new();
    for field in ["source_ids", "target_ids"] {
        match edge.get(field) {
            Some(Value::String(value)) => {
                let trimmed = value.trim();
                if !trimmed.is_empty() {
                    ids.insert(trimmed.to_owned());
                }
            }
            Some(Value::Array(values)) => {
                for value in values {
                    if let Value::String(item) = value {
                        let trimmed = item.trim();
                        if !trimmed.is_empty() {
                            ids.insert(trimmed.to_owned());
                        }
                    }
                }
            }
            _ => {}
        }
    }
    ids
}

/// Graph extraction slice whose semantic output is fingerprinted.
#[derive(Clone, Debug)]
pub struct SemanticExtraction {
    pub nodes: Vec<Map<String, Value>>,
    pub edges: Vec<Map<String, Value>>,
}

fn canonical_json(value: &Value) -> String {
    serde_json::to_string(value).expect("value is serializable")
}

/// Normalize one node/edge: drop derivation IDs, keep stable fields.
fn normalize_item(item: &Map<String, Value>) -> Value {
    let mut normalized = item.clone();
    normalized.remove("id");
    if let Some(Value::Object(metadata)) = normalized.get_mut("metadata") {
        metadata.remove("parse_generation_event_id");
        metadata.remove("parse_generation_member_id");
    }
    Value::Object(normalized)
}

/// Fingerprint semantic output while excluding derivation event IDs.
#[must_use]
pub fn semantic_fingerprint(extraction: &SemanticExtraction) -> String {
    let mut nodes: Vec<String> = extraction
        .nodes
        .iter()
        .map(normalize_item)
        .map(|value| canonical_json(&value))
        .collect();
    let mut edges: Vec<String> = extraction
        .edges
        .iter()
        .map(normalize_item)
        .map(|value| canonical_json(&value))
        .collect();
    nodes.sort();
    edges.sort();
    let mut payload = BTreeMap::new();
    payload.insert(
        "nodes".to_string(),
        Value::Array(
            nodes
                .iter()
                .map(|item| Value::String(item.clone()))
                .collect(),
        ),
    );
    payload.insert(
        "edges".to_string(),
        Value::Array(
            edges
                .iter()
                .map(|item| Value::String(item.clone()))
                .collect(),
        ),
    );
    let encoded = serde_json::to_string(&payload).expect("payload is serializable");
    let mut hasher = Sha256::new();
    hasher.update(encoded.as_bytes());
    format!("{:x}", hasher.finalize())
}

/// Keep only JSON-safe cumulative budget fields on a requeued job.
#[must_use]
pub fn persisted_budget_state(state: &Map<String, Value>) -> Map<String, Value> {
    state
        .iter()
        .filter(|(key, _)| PERSISTED_BUDGET_KEYS.contains(&key.as_str()))
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect()
}

fn as_float(value: Option<&Value>) -> f64 {
    match value {
        Some(Value::Bool(_)) => 0.0,
        Some(Value::Number(number)) => number.as_f64().unwrap_or(0.0),
        Some(Value::String(text)) => text.trim().parse().unwrap_or(0.0),
        _ => 0.0,
    }
}

fn as_int(value: Option<&Value>) -> i64 {
    match value {
        Some(Value::Bool(_)) => 0,
        Some(Value::Number(number)) => number.as_i64().unwrap_or(0),
        Some(Value::String(text)) => text.trim().parse().unwrap_or(0),
        _ => 0,
    }
}

fn is_number(value: &Value) -> bool {
    matches!(value, Value::Number(_))
}

/// Budget default honoring Python falsy fallback: zero falls back to default.
fn budget_default(value: Option<&Value>, default: i64) -> i64 {
    match as_int(value) {
        0 => default,
        value => value.max(0),
    }
}

/// Recompute durable maintenance budget state from request budgets, recovered
/// usage, and optional fair-scheduling slice caps.
#[allow(clippy::too_many_arguments)]
#[must_use]
pub fn maintenance_budget_state(
    state: &Map<String, Value>,
    budgets: &Map<String, Value>,
    durable_usage: &Map<String, Value>,
    fair_scheduling: bool,
    maintenance_steps_per_slice: i64,
    maintenance_llm_calls_per_slice: i64,
    maintenance_seconds_per_slice: i64,
) -> Map<String, Value> {
    let mut next = state.clone();
    let token_limit = budget_default(budgets.get("max_tokens"), 10_000_000);
    let call_limit = budget_default(budgets.get("max_llm_calls"), 0);
    let step_limit = budget_default(budgets.get("max_steps"), 0);
    let time_limit_ms = (as_float(budgets.get("max_time_seconds")) * 1000.0) as i64;
    let cost_limit = as_float(budgets.get("max_cost_usd"));

    let used_keys = [
        "token_used",
        "call_used",
        "step_used",
        "time_used_ms",
        "cost_used",
    ];
    for key in used_keys {
        let current = next.get(key).cloned().unwrap_or(Value::Null);
        for (durable_key, durable_value) in durable_usage {
            if durable_key != key {
                continue;
            }
            if matches!(durable_value, Value::Bool(_)) || !is_number(durable_value) {
                continue;
            }
            let merged = as_float(Some(&current)).max(as_float(Some(durable_value)));
            next.insert(
                key.to_string(),
                if key == "cost_used" {
                    Value::from(merged)
                } else {
                    Value::from(merged as i64)
                },
            );
        }
    }
    next.insert("token_budget".to_string(), Value::from(token_limit));
    next.insert("call_budget".to_string(), Value::from(call_limit));
    next.insert("step_budget".to_string(), Value::from(step_limit));
    next.insert("time_budget_ms".to_string(), Value::from(time_limit_ms));
    next.insert("cost_budget".to_string(), Value::from(cost_limit));

    if fair_scheduling {
        let slice_limits = [
            ("step_budget", maintenance_steps_per_slice),
            ("call_budget", maintenance_llm_calls_per_slice),
            ("time_budget_ms", maintenance_seconds_per_slice * 1000),
        ];
        let request_keys = [
            ("step_budget", "request_step_budget"),
            ("call_budget", "request_call_budget"),
            ("time_budget_ms", "request_time_budget_ms"),
        ];
        let used_keys = [
            ("step_budget", "step_used"),
            ("call_budget", "call_used"),
            ("time_budget_ms", "time_used_ms"),
        ];
        for (limit_key, slice_limit) in slice_limits {
            if slice_limit == 0 {
                continue;
            }
            let request_key = request_keys
                .iter()
                .find(|(key, _)| *key == limit_key)
                .map(|(_, request)| *request)
                .unwrap_or("");
            let used_key = used_keys
                .iter()
                .find(|(key, _)| *key == limit_key)
                .map(|(_, used)| *used)
                .unwrap_or("");
            let request_limit = as_int(next.get(request_key));
            let slice_cap = as_int(next.get(used_key)) + slice_limit;
            next.insert(
                limit_key.to_string(),
                Value::from(if request_limit != 0 {
                    request_limit.min(slice_cap)
                } else {
                    slice_cap
                }),
            );
        }
    }
    next
}

/// Recover usage from authoritative event rows after a failed retry.
///
/// `rows` mirrors the event rows returned by `iter_entity_events`; the last
/// column of each row is the JSON payload.
#[must_use]
pub fn durable_maintenance_usage(
    _namespace: &str,
    rows: &[Vec<Value>],
    maintenance_job_id: &str,
) -> Map<String, Value> {
    let mut events: Vec<Map<String, Value>> = Vec::new();
    for row in rows {
        let Some(Value::String(payload_json)) = row.last() else {
            continue;
        };
        let Ok(payload) = serde_json::from_str::<Map<String, Value>>(payload_json) else {
            continue;
        };
        let Some(Value::Object(attribution)) = payload.get("attribution") else {
            continue;
        };
        if attribution
            .get("maintenance_job_id")
            .and_then(Value::as_str)
            != Some(maintenance_job_id)
        {
            continue;
        }
        if payload.get("artifact_kind") != Some(&Value::from("usage_event")) {
            continue;
        }
        events.push(payload);
    }
    let mut token_used = 0.0;
    let mut call_used = 0i64;
    let mut step_used = 0i64;
    let mut time_ms = 0.0;
    let mut total_cost = 0.0;
    for event in &events {
        let kind = event.get("kind").and_then(Value::as_str).unwrap_or("");
        let unit = event.get("unit").and_then(Value::as_str).unwrap_or("");
        let amount = as_float(event.get("amount"));
        if matches!(kind, "debit" | "token") && !matches!(unit, "call" | "llm_call" | "step" | "ms")
        {
            token_used += amount;
        }
        if unit == "call" || unit == "llm_call" {
            call_used += 1;
        }
        if unit == "step" {
            step_used += amount as i64;
        }
        if unit == "ms" {
            time_ms += amount;
        }
        if kind == "cost" {
            total_cost += amount;
        }
    }
    let mut result = Map::new();
    result.insert("token_used".to_string(), Value::from(token_used as i64));
    result.insert("call_used".to_string(), Value::from(call_used));
    result.insert("step_used".to_string(), Value::from(step_used));
    result.insert("time_used_ms".to_string(), Value::from(time_ms as i64));
    result.insert("cost_used".to_string(), Value::from(total_cost));
    result
}
