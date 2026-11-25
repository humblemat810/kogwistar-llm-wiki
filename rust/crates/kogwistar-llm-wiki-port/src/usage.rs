use std::collections::{BTreeMap, BTreeSet};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::models::JsonObject;
pub use crate::providers::WorkflowProviderSettings;

#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct BudgetAttribution {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub workspace_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_document_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub operation_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub operation_kind: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub maintenance_job_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub dream_job_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub provider: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub model: Option<String>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct BudgetEvent {
    #[serde(default)]
    pub event_id: Option<String>,
    #[serde(default)]
    pub run_id: String,
    #[serde(default = "default_unknown")]
    pub source: String,
    #[serde(default = "default_unknown")]
    pub kind: String,
    pub amount: f64,
    #[serde(default)]
    pub unit: String,
    #[serde(default = "default_scope")]
    pub scope: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub ts_ms: Option<i64>,
    #[serde(default)]
    pub meta: JsonObject,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub attribution: Option<BudgetAttribution>,
}

fn default_unknown() -> String {
    "unknown".to_owned()
}

fn default_scope() -> String {
    "run".to_owned()
}

#[derive(Debug)]
pub enum BudgetContractError {
    Amount,
    Field(&'static str),
}

impl std::fmt::Display for BudgetContractError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Amount => write!(formatter, "budget event amount must be finite and >= 0"),
            Self::Field(name) => {
                write!(
                    formatter,
                    "parser child payload field {:?} must be an integer",
                    name
                )
            }
        }
    }
}

impl std::error::Error for BudgetContractError {}

pub fn budget_event_from_dict(payload: &Value) -> Result<BudgetEvent, BudgetContractError> {
    let event: BudgetEvent = serde_json::from_value(payload.clone())
        .map_err(|_| BudgetContractError::Field("budget_event"))?;
    if !event.amount.is_finite() || event.amount < 0.0 {
        return Err(BudgetContractError::Amount);
    }
    Ok(event)
}

#[must_use]
pub fn budget_event_to_dict(event: &BudgetEvent) -> Value {
    json!(event)
}

pub fn provider_call_count(events: &[BudgetEvent]) -> usize {
    events
        .iter()
        .filter_map(|event| event.meta.get("provider_run_id"))
        .filter(|provider_run_id| !provider_run_id.is_null())
        .map(ToString::to_string)
        .collect::<BTreeSet<_>>()
        .len()
}

#[derive(Clone, Debug, Default, PartialEq, Serialize)]
pub struct UsageSummary {
    pub input_tokens: u64,
    pub cached_input_tokens: u64,
    pub output_tokens: u64,
    pub total_tokens: u64,
    pub total_cost: f64,
    pub time_ms: u64,
    pub event_count: usize,
    pub event_counts: BTreeMap<String, u64>,
    pub by_unit: BTreeMap<String, u64>,
    pub cost_provenance: BTreeMap<String, u64>,
}

#[must_use]
pub fn summarize_budget_events(events: &[BudgetEvent]) -> UsageSummary {
    let mut summary = UsageSummary {
        event_count: events.len(),
        ..UsageSummary::default()
    };
    for event in events {
        let kind = event.kind.as_str();
        let unit = event.unit.as_str();
        let amount = event.amount;
        *summary.event_counts.entry(kind.to_owned()).or_default() += 1;
        if !unit.is_empty() {
            *summary.by_unit.entry(unit.to_owned()).or_default() += 1;
        }
        if kind == "cost" {
            if let Some(provenance) = event.meta.get("cost_provenance") {
                *summary
                    .cost_provenance
                    .entry(provenance.to_string())
                    .or_default() += 1;
            }
        }
        if (kind == "debit" || kind == "token") && unit == "input_tokens" {
            summary.input_tokens += amount as u64;
            summary.total_tokens += amount as u64;
        } else if (kind == "debit" || kind == "token") && unit == "output_tokens" {
            summary.output_tokens += amount as u64;
            summary.total_tokens += amount as u64;
        } else if (kind == "debit" || kind == "token") && unit == "cached_input_tokens" {
            summary.cached_input_tokens += amount as u64;
        } else if (kind == "debit" || kind == "token") && unit == "total_tokens" {
            summary.total_tokens += amount as u64;
        } else if kind == "cost" || unit == "total_cost" {
            summary.total_cost += amount;
        } else if unit == "ms" {
            summary.time_ms += amount as u64;
        }
    }
    summary.total_cost = (summary.total_cost * 1_000_000.0).round() / 1_000_000.0;
    summary
}

#[must_use]
pub fn summarize_budget_events_value(events: &[BudgetEvent]) -> Value {
    json!(summarize_budget_events(events))
}

#[must_use]
pub fn provider_config_summary_value(settings: &WorkflowProviderSettings) -> Value {
    crate::providers::provider_config_summary(settings)
}

pub fn summarize_provider_budget_events(
    events: &[BudgetEvent],
    settings: &WorkflowProviderSettings,
) -> Value {
    let provider_summary = crate::providers::provider_config_summary(settings);
    let Value::Object(mut summary) = provider_summary else {
        unreachable!("provider config summary is an object");
    };
    let Value::Object(base) = summarize_budget_events_value(events) else {
        unreachable!("usage summary is an object");
    };
    for (key, value) in base {
        summary.insert(key, value);
    }
    summary.insert(
        "llm_call_count".to_owned(),
        Value::from(provider_call_count(events)),
    );

    let cost_events: Vec<&BudgetEvent> = events
        .iter()
        .filter(|event| event.kind == "cost" || event.unit == "total_cost")
        .collect();
    let statuses: BTreeSet<String> = cost_events
        .iter()
        .filter_map(|event| {
            event
                .meta
                .get("cost_status")
                .and_then(Value::as_str)
                .filter(|value| !value.is_empty())
                .map(ToString::to_string)
        })
        .collect();
    if cost_events.is_empty()
        || (statuses.len() == 1 && statuses.contains("unavailable_missing_tokens"))
    {
        summary.insert("total_cost".to_owned(), Value::Null);
        summary.insert(
            "cost_status".to_owned(),
            Value::from("unavailable_missing_tokens"),
        );
        summary.insert("cost_source".to_owned(), Value::Null);
    } else if !statuses.is_empty() {
        let status = statuses.iter().cloned().collect::<Vec<_>>().join("+");
        let sources: Vec<String> = cost_events
            .iter()
            .filter_map(|event| {
                event
                    .meta
                    .get("cost_source")
                    .and_then(Value::as_str)
                    .filter(|value| !value.is_empty())
                    .map(ToString::to_string)
            })
            .collect::<BTreeSet<_>>()
            .into_iter()
            .collect();
        summary.insert("cost_status".to_owned(), Value::from(status));
        summary.insert(
            "cost_source".to_owned(),
            Value::Array(sources.into_iter().map(Value::from).collect()),
        );
    } else {
        summary.insert("cost_status".to_owned(), Value::from("provider_reported"));
        summary.insert("cost_source".to_owned(), Value::from("provider"));
    }
    Value::Object(summary)
}
