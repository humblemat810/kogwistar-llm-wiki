use crate::models::JsonObject;
use serde_json::{json, Value};

pub const MAX_MAINTENANCE_CONTEXT_TOKENS: usize = 10_000;
const MAX_TURNS: usize = 10;
const MAX_IDS: usize = 64;
const MAX_REASON_ENTRIES: usize = 24;

pub const DEFAULT_DOCUMENT_MAINTENANCE_PLAN: [&str; 6] = [
    "document_seed_graph",
    "document_parse_graph",
    "document_expand_parse_children",
    "review_maintenance_subject",
    "document_propose_crosslinks",
    "document_validate_crosslinks",
];

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MaintenancePlanDecision {
    pub current_kind: String,
    pub next_kind: Option<String>,
    pub current_index: i64,
    pub next_index: i64,
    pub reason: &'static str,
}

impl MaintenancePlanDecision {
    #[must_use]
    pub fn should_continue(&self) -> bool {
        self.next_kind.is_some()
    }
}

#[must_use]
pub fn normalize_maintenance_plan(value: Option<&Value>) -> Vec<String> {
    let Some(Value::Array(values)) = value else {
        return Vec::new();
    };
    values
        .iter()
        .map(python_string_value)
        .filter(|item| !item.trim().is_empty())
        .map(|item| item.trim().to_owned())
        .collect()
}

pub fn decide_next_maintenance_phase(
    payload: &JsonObject,
    completed_kind: &str,
) -> MaintenancePlanDecision {
    let mut plan = normalize_maintenance_plan(payload.get("maintenance_plan"));
    let durable_layered_parse = payload
        .get("durable_layered_parse")
        .and_then(Value::as_bool)
        .unwrap_or_default();
    if !durable_layered_parse {
        plan.retain(|kind| kind != "document_expand_parse_children");
    }

    let current_kind = python_string(payload.get("completed_kind"));
    let current_kind = if current_kind.trim().is_empty() {
        completed_kind.to_owned()
    } else {
        current_kind.trim().to_owned()
    };
    let current_index = as_int(payload.get("maintenance_phase_index"));
    let completed_rounds = as_int(payload.get("maintenance_round"));
    let max_rounds = as_int(payload.get("maintenance_max_rounds"));

    if payload
        .get("maintenance_stop_requested")
        .and_then(Value::as_bool)
        .unwrap_or_default()
    {
        return MaintenancePlanDecision {
            current_kind,
            next_kind: None,
            current_index,
            next_index: current_index,
            reason: "stop_requested",
        };
    }
    if max_rounds > 0 && completed_rounds + 1 >= max_rounds {
        return MaintenancePlanDecision {
            current_kind,
            next_kind: None,
            current_index,
            next_index: current_index,
            reason: "max_rounds_reached",
        };
    }
    if plan.is_empty() {
        return if max_rounds > 0 && completed_rounds < max_rounds {
            MaintenancePlanDecision {
                next_kind: Some(current_kind.clone()),
                current_index,
                next_index: current_index + 1,
                reason: "next_bounded_round_available",
                current_kind,
            }
        } else {
            MaintenancePlanDecision {
                current_kind,
                next_kind: None,
                current_index,
                next_index: current_index,
                reason: "no_plan",
            }
        };
    }

    let plan_index = plan
        .iter()
        .position(|kind| *kind == current_kind)
        .map(|position| position as i64)
        .unwrap_or(current_index);
    let next_index = current_index.max(plan_index) + 1;
    if next_index >= plan.len() as i64 {
        return MaintenancePlanDecision {
            current_kind,
            next_kind: None,
            current_index: plan_index,
            next_index,
            reason: "plan_complete",
        };
    }
    MaintenancePlanDecision {
        current_kind,
        next_kind: Some(plan[next_index as usize].clone()),
        current_index: plan_index,
        next_index,
        reason: "next_phase_available",
    }
}

fn as_int(value: Option<&Value>) -> i64 {
    match value {
        Some(Value::Number(number)) => number.as_i64().unwrap_or_default(),
        Some(Value::String(value)) => value.parse().unwrap_or_default(),
        Some(Value::Bool(true)) => 0,
        _ => 0,
    }
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

fn python_string_value(value: &Value) -> String {
    python_string(Some(value))
}

#[must_use]
pub fn bound_maintenance_context(value: Option<&JsonObject>) -> JsonObject {
    let empty = JsonObject::new();
    let value = value.unwrap_or(&empty);
    let mut turns: Vec<JsonObject> = Vec::new();
    if let Some(Value::Array(values)) = value.get("turns") {
        for item in values
            .iter()
            .rev()
            .take(MAX_TURNS)
            .collect::<Vec<_>>()
            .into_iter()
            .rev()
        {
            if let Some(turn) = bounded_turn(item) {
                turns.push(turn);
            }
        }
    }
    let mut result = JsonObject::from([
        ("version".to_owned(), Value::from(1)),
        (
            "turns".to_owned(),
            Value::Array(turns.iter().map(|turn| json_object(turn.clone())).collect()),
        ),
        (
            "compressed_summary".to_owned(),
            Value::from(bounded_string(value.get("compressed_summary"), 4_000)),
        ),
        (
            "compressed_node_ids".to_owned(),
            Value::Array(bounded_ids(value.get("compressed_node_ids"))),
        ),
        (
            "compressed_edge_ids".to_owned(),
            Value::Array(bounded_ids(value.get("compressed_edge_ids"))),
        ),
        (
            "truncated".to_owned(),
            Value::from(truthy(value.get("truncated"))),
        ),
    ]);
    if python_string(value.get("compressed_summary")).len() > 4_000 {
        result.insert("truncated".to_owned(), Value::Bool(true));
    }

    while context_json_len(&result) > MAX_MAINTENANCE_CONTEXT_TOKENS {
        if let Some(Value::Array(values)) = result.get_mut("turns") {
            if !values.is_empty() {
                values.remove(0);
                result.insert("truncated".to_owned(), Value::Bool(true));
                continue;
            }
        }
        if let Some(Value::String(summary)) = result.get("compressed_summary") {
            let summary = summary.clone();
            if !summary.is_empty() {
                let new_len = summary.len().saturating_sub(256);
                result.insert(
                    "compressed_summary".to_owned(),
                    Value::String(summary[..new_len].to_owned()),
                );
                result.insert("truncated".to_owned(), Value::Bool(true));
                continue;
            }
        }
        for key in ["compressed_node_ids", "compressed_edge_ids"] {
            if let Some(Value::Array(values)) = result.get_mut(key) {
                if values.len() >= 8 {
                    values.truncate(values.len() - 8);
                } else {
                    values.clear();
                }
                result.insert("truncated".to_owned(), Value::Bool(true));
            }
        }
        if context_json_len(&result) <= MAX_MAINTENANCE_CONTEXT_TOKENS {
            break;
        }
    }
    result
}

#[allow(clippy::too_many_arguments)]
#[must_use]
pub fn append_maintenance_round(
    value: Option<&JsonObject>,
    round_number: i64,
    summary: &str,
    touched_node_ids: &[String],
    touched_edge_ids: &[String],
    next_seed_node_ids: &[String],
    hop_limit: i64,
    selection_reasons: &[Value],
) -> JsonObject {
    let mut context = bound_maintenance_context(value);
    if let Some(Value::Array(turns)) = context.get_mut("turns") {
        turns.push(json!({
            "round": round_number.max(0),
            "summary": summary,
            "touched_node_ids": touched_node_ids,
            "touched_edge_ids": touched_edge_ids,
            "next_seed_node_ids": next_seed_node_ids,
            "hop_limit": hop_limit,
            "selection_reasons": selection_reasons
        }));
    }
    bound_maintenance_context(Some(&context))
}

fn bounded_turn(value: &Value) -> Option<JsonObject> {
    let Value::Object(object) = value else {
        return None;
    };
    let reason_values = match object.get("selection_reasons") {
        Some(Value::Array(values)) => values.iter().take(MAX_REASON_ENTRIES).collect::<Vec<_>>(),
        _ => Vec::new(),
    };
    let reasons = reason_values
        .into_iter()
        .filter_map(|reason| {
            let Value::Object(reason) = reason else {
                return None;
            };
            Some(JsonObject::from([
                (
                    "candidate_id".to_owned(),
                    Value::from(bounded_string(reason.get("candidate_id"), 256)),
                ),
                (
                    "reason".to_owned(),
                    Value::from(bounded_string(reason.get("reason"), 64)),
                ),
                ("score".to_owned(), numeric_or_null(reason.get("score"))),
            ]))
        })
        .collect::<Vec<_>>();
    let bounded_ids = |key: &str| Value::Array(bounded_ids(object.get(key)));
    Some(JsonObject::from([
        (
            "round".to_owned(),
            Value::from(bounded_integer(object.get("round")).max(0)),
        ),
        (
            "summary".to_owned(),
            Value::from(bounded_string(object.get("summary"), 2_000)),
        ),
        (
            "touched_node_ids".to_owned(),
            bounded_ids("touched_node_ids"),
        ),
        (
            "touched_edge_ids".to_owned(),
            bounded_ids("touched_edge_ids"),
        ),
        (
            "next_seed_node_ids".to_owned(),
            bounded_ids("next_seed_node_ids"),
        ),
        (
            "hop_limit".to_owned(),
            Value::from(bounded_integer(object.get("hop_limit")).clamp(0, 8)),
        ),
        (
            "selection_reasons".to_owned(),
            Value::Array(reasons.into_iter().map(json_object).collect()),
        ),
    ]))
}

fn bounded_integer(value: Option<&Value>) -> i64 {
    match value {
        Some(Value::Number(number)) => number.as_i64().unwrap_or_default(),
        Some(Value::Bool(true)) => 0,
        Some(Value::String(value)) => value.trim().parse().unwrap_or_default(),
        _ => 0,
    }
}

fn bounded_string(value: Option<&Value>, limit: usize) -> String {
    python_string(value).trim().chars().take(limit).collect()
}

fn bounded_ids(value: Option<&Value>) -> Vec<Value> {
    let Some(Value::Array(values)) = value else {
        return Vec::new();
    };
    let mut result: Vec<Value> = Vec::new();
    for item in values {
        let item_id = bounded_string(Some(item), 256);
        if !item_id.is_empty() && !result.contains(&Value::String(item_id.clone())) {
            result.push(Value::String(item_id));
        }
        if result.len() >= MAX_IDS {
            break;
        }
    }
    result
}

fn numeric_or_null(value: Option<&Value>) -> Value {
    match value {
        Some(Value::Number(number)) => Value::Number(number.clone()),
        _ => Value::Null,
    }
}

fn truthy(value: Option<&Value>) -> bool {
    matches!(value, Some(Value::Bool(true)))
}

fn context_json_len(value: &JsonObject) -> usize {
    json_object(value.clone()).to_string().len()
}

#[must_use]
fn json_object(value: JsonObject) -> Value {
    Value::Object(value.into_iter().collect())
}

pub mod durable_stores;

pub use durable_stores::*;
