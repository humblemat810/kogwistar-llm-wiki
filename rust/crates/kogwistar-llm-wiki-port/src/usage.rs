use std::collections::{BTreeMap, BTreeSet};

use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};

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

#[derive(Clone, Debug, Default, PartialEq, Serialize)]
pub struct TokenPricing {
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub input_per_1k: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub output_per_1k: Option<f64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cached_input_per_1k: Option<f64>,
    #[serde(default)]
    pub source: String,
}

impl TokenPricing {
    pub fn new(
        input_per_1k: Option<f64>,
        output_per_1k: Option<f64>,
        cached_input_per_1k: Option<f64>,
        source: Option<&str>,
    ) -> Result<Self, String> {
        for (name, rate) in [
            ("input_per_1k", input_per_1k),
            ("cached_input_per_1k", cached_input_per_1k),
            ("output_per_1k", output_per_1k),
        ] {
            if rate.is_some_and(|rate| rate < 0.0) {
                return Err(format!("{name} must be non-negative"));
            }
        }
        Ok(Self {
            input_per_1k,
            output_per_1k,
            cached_input_per_1k,
            source: source.unwrap_or("unavailable").to_owned(),
        })
    }

    #[must_use]
    pub fn configured(&self) -> bool {
        self.input_per_1k.is_some()
            || self.output_per_1k.is_some()
            || self.cached_input_per_1k.is_some()
    }
}

const REFERENCE_TOKEN_PRICING: [(&str, &str, f64, f64, f64, &str); 4] = [
    (
        "openai",
        "gpt-5-mini",
        0.00025,
        0.000025,
        0.002,
        "reference:openai-public-standard:gpt-5-mini",
    ),
    (
        "azure",
        "gpt-5-mini",
        0.00025,
        0.000025,
        0.002,
        "reference:openai-public-standard:gpt-5-mini:azure-estimate",
    ),
    (
        "openai",
        "gpt-5-nano",
        0.00005,
        0.000005,
        0.0004,
        "reference:openai-public-standard:gpt-5-nano",
    ),
    (
        "azure",
        "gpt-5-nano",
        0.00005,
        0.000005,
        0.0004,
        "reference:openai-public-standard:gpt-5-nano:azure-estimate",
    ),
];

pub fn resolve_token_pricing(
    provider: &str,
    model: &str,
    env: &BTreeMap<String, String>,
) -> Result<TokenPricing, String> {
    let prefixes = [
        "KOGWISTAR_LONGRUN_PARSER",
        "KOGWISTAR_PARSER",
        "KG_DOC_PARSER",
        "KOGWISTAR_LLM",
    ];
    let mut rates: BTreeMap<&str, (f64, String)> = BTreeMap::new();
    for (suffix, name) in [
        ("INPUT_COST_PER_1K_TOKENS", "input_per_1k"),
        ("OUTPUT_COST_PER_1K_TOKENS", "output_per_1k"),
        ("CACHED_INPUT_COST_PER_1K_TOKENS", "cached_input_per_1k"),
    ] {
        for prefix in prefixes {
            let full_name = format!("{prefix}_{suffix}");
            let Some(raw) = env.get(&full_name) else {
                continue;
            };
            if raw.trim().is_empty() {
                continue;
            }
            let value = raw
                .trim()
                .parse::<f64>()
                .map_err(|_| format!("{full_name} must be a non-negative number"))?;
            if value < 0.0 {
                return Err(format!("{full_name} must be a non-negative number"));
            }
            rates.insert(name, (value, full_name));
            break;
        }
    }

    let reference =
        REFERENCE_TOKEN_PRICING
            .iter()
            .find(|(reference_provider, reference_model, _, _, _, _)| {
                reference_provider.eq_ignore_ascii_case(provider)
                    && reference_model.eq_ignore_ascii_case(model)
            });
    let source = rates
        .get("input_per_1k")
        .or_else(|| rates.get("output_per_1k"))
        .or_else(|| rates.get("cached_input_per_1k"))
        .map(|(_, name)| name.clone());
    if source.is_none() {
        if let Some((_, _, input, cached, output, reference_source)) = reference {
            return TokenPricing::new(
                Some(*input),
                Some(*output),
                Some(*cached),
                Some(reference_source),
            );
        }
    }
    let fallback_source = source.unwrap_or_else(|| format!("unavailable:{provider}/{model}"));
    TokenPricing::new(
        rates.get("input_per_1k").map(|(rate, _)| *rate),
        rates.get("output_per_1k").map(|(rate, _)| *rate),
        rates.get("cached_input_per_1k").map(|(rate, _)| *rate),
        Some(fallback_source.as_str()),
    )
}

fn mapping(value: Option<&Value>) -> Option<&Map<String, Value>> {
    value.and_then(Value::as_object)
}

fn add_candidates(value: Option<&Value>, candidates: &mut Vec<Map<String, Value>>) {
    if let Some(mapping_value) = mapping(value) {
        candidates.push(mapping_value.clone());
        for key in ["usage_metadata", "token_usage", "usage"] {
            if let Some(nested) = mapping(mapping_value.get(key)) {
                candidates.push(nested.clone());
            }
        }
    }
}

fn usage_candidates(response: &Value) -> Vec<Map<String, Value>> {
    let mut candidates = Vec::new();
    add_candidates(response.get("llm_output"), &mut candidates);
    add_candidates(response.get("response_metadata"), &mut candidates);
    if let Some(Value::Array(groups)) = response.get("generations") {
        for group in groups {
            if let Some(generations) = group.as_array() {
                for generation in generations {
                    add_candidates(
                        generation
                            .get("message")
                            .and_then(|message| message.get("usage_metadata")),
                        &mut candidates,
                    );
                    add_candidates(
                        generation
                            .get("message")
                            .and_then(|message| message.get("response_metadata")),
                        &mut candidates,
                    );
                    add_candidates(generation.get("generation_info"), &mut candidates);
                }
            }
        }
    }
    for candidate in candidates.clone() {
        for key in [
            "prompt_tokens_details",
            "input_token_details",
            "input_tokens_details",
        ] {
            if let Some(nested) = mapping(candidate.get(key)) {
                candidates.push(nested.clone());
            }
        }
    }
    candidates
}

#[must_use]
pub fn extract_provider_usage(response: &Value) -> BTreeMap<String, f64> {
    let aliases: BTreeMap<&str, [&str; 3]> = BTreeMap::from([
        (
            "input_tokens",
            ["input_tokens", "prompt_tokens", "input_token_count"],
        ),
        (
            "output_tokens",
            ["output_tokens", "completion_tokens", "output_token_count"],
        ),
        ("total_tokens", ["total_tokens", "token_count", ""]),
        ("total_cost", ["total_cost", "cost", "cost_usd"]),
        (
            "cached_input_tokens",
            ["cached_input_tokens", "cached_tokens", ""],
        ),
    ]);
    let mut usage: BTreeMap<String, f64> = BTreeMap::new();
    for candidate in usage_candidates(response) {
        for (normalized, keys) in &aliases {
            if usage.contains_key(*normalized) {
                continue;
            }
            for key in keys {
                if key.is_empty() {
                    continue;
                }
                if let Some(Value::Number(value)) = candidate.get(*key) {
                    let value = value.as_f64().unwrap_or_default();
                    if value >= 0.0 {
                        usage.insert((*normalized).to_owned(), value);
                        break;
                    }
                }
            }
        }
    }
    if !usage.contains_key("total_tokens") {
        if let (Some(input), Some(output)) = (usage.get("input_tokens"), usage.get("output_tokens"))
        {
            usage.insert("total_tokens".to_owned(), input + output);
        }
    }
    usage
}

pub fn estimate_token_cost_usd(
    usage: &Value,
    pricing: &TokenPricing,
) -> Option<(f64, &'static str)> {
    let numeric = |key: &str| {
        usage
            .get(key)
            .and_then(Value::as_f64)
            .map(|value| value.max(0.0))
            .unwrap_or_default()
    };
    let input_tokens = numeric("input_tokens");
    let cached_tokens = input_tokens.min(numeric("cached_input_tokens"));
    let mut components = Vec::new();
    let mut has_missing_rate = false;
    for (tokens, rate) in [
        (input_tokens - cached_tokens, pricing.input_per_1k),
        (
            cached_tokens,
            pricing.cached_input_per_1k.or(pricing.input_per_1k),
        ),
        (numeric("output_tokens"), pricing.output_per_1k),
    ] {
        if tokens <= 0.0 {
            continue;
        }
        match rate {
            None => has_missing_rate = true,
            Some(rate) => components.push(tokens / 1000.0 * rate),
        }
    }
    if components.is_empty() {
        return None;
    }
    let total = components.iter().sum::<f64>();
    Some((
        (total * 100_000_000.0).round() / 100_000_000.0,
        if has_missing_rate {
            "estimated_partial"
        } else {
            "estimated"
        },
    ))
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
