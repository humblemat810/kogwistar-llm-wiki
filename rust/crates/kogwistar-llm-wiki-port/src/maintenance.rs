use crate::models::JsonObject;
use serde_json::Value;

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
