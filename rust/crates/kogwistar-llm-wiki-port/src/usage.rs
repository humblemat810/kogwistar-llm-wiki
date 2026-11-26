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

#[derive(Debug)]
pub enum BudgetError {
    Exhausted(String),
    Invalid(&'static str),
}

impl std::fmt::Display for BudgetError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Exhausted(message) => write!(formatter, "{message}"),
            Self::Invalid(message) => write!(formatter, "{message}"),
        }
    }
}

impl std::error::Error for BudgetError {}

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

fn state_i64(state: &JsonObject, key: &str) -> i64 {
    state
        .get(key)
        .and_then(Value::as_f64)
        .map(|value| value as i64)
        .unwrap_or_default()
}

fn state_f64(state: &JsonObject, key: &str) -> f64 {
    state.get(key).and_then(Value::as_f64).unwrap_or_default()
}

fn set_state_i64(state: &mut JsonObject, key: &str, value: i64) {
    state.insert(key.to_owned(), Value::from(value));
}

fn set_state_f64(state: &mut JsonObject, key: &str, value: f64) {
    if let Some(number) = serde_json::Number::from_f64(value) {
        state.insert(key.to_owned(), Value::Number(number));
    }
}

fn metadata_with_reason(reason: &str, meta: Option<&JsonObject>) -> JsonObject {
    let mut value = JsonObject::from([("reason".to_owned(), Value::from(reason))]);
    if let Some(meta) = meta {
        for (key, item) in meta {
            value.insert(key.clone(), item.clone());
        }
    }
    value
}

fn state_text(state: &JsonObject, key: &str, default: &'static str) -> String {
    state
        .get(key)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
        .map(ToString::to_string)
        .unwrap_or_else(|| default.to_owned())
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct StateBackedBudgetLedger {
    pub state: JsonObject,
    pub events: Vec<BudgetEvent>,
    ceilings: BTreeMap<String, f64>,
}

impl StateBackedBudgetLedger {
    #[must_use]
    pub fn new(state: JsonObject) -> Self {
        Self {
            state,
            events: Vec::new(),
            ceilings: BTreeMap::new(),
        }
    }

    pub fn set_ceilings(&mut self, ceilings: Option<&BTreeMap<String, f64>>) {
        let Some(ceilings) = ceilings else {
            return;
        };
        for (key, value) in ceilings {
            let current = self.ceilings.get(key);
            let bounded = current.map_or(*value, |current| current.min(*value));
            self.ceilings.insert(key.clone(), bounded);
        }
    }

    fn bounded_limit(&self, key: &str) -> f64 {
        let raw = if key == "cost_budget" {
            state_f64(&self.state, key)
        } else {
            state_i64(&self.state, key) as f64
        };
        match self.ceilings.get(key) {
            Some(ceiling) if raw > 0.0 => raw.min(*ceiling),
            Some(ceiling) => *ceiling,
            None => raw,
        }
    }

    #[must_use]
    pub fn total(&self) -> i64 {
        self.bounded_limit("token_budget") as i64
    }

    #[must_use]
    pub fn used(&self) -> i64 {
        state_i64(&self.state, "token_used")
    }

    #[must_use]
    pub fn remaining(&self) -> i64 {
        (self.total() - self.used()).max(0)
    }

    #[must_use]
    pub fn time_budget_ms(&self) -> i64 {
        self.bounded_limit("time_budget_ms") as i64
    }

    #[must_use]
    pub fn time_used_ms(&self) -> i64 {
        state_i64(&self.state, "time_used_ms")
    }

    #[must_use]
    pub fn rate_limit(&self) -> i64 {
        self.bounded_limit("rate_limit") as i64
    }

    #[must_use]
    pub fn rate_used(&self) -> i64 {
        state_i64(&self.state, "rate_used")
    }

    #[must_use]
    pub fn rate_window_ms(&self) -> i64 {
        self.bounded_limit("rate_window_ms") as i64
    }

    #[must_use]
    pub fn rate_window_started_ms(&self) -> i64 {
        state_i64(&self.state, "rate_window_started_ms")
    }

    #[must_use]
    pub fn rate_window_ready_ms(&self) -> Option<i64> {
        if self.rate_limit() == 0
            || self.rate_window_ms() == 0
            || self.rate_window_started_ms() == 0
        {
            return None;
        }
        Some(self.rate_window_started_ms() + self.rate_window_ms())
    }

    #[must_use]
    pub fn step_budget(&self) -> i64 {
        self.bounded_limit("step_budget") as i64
    }

    #[must_use]
    pub fn step_used(&self) -> i64 {
        state_i64(&self.state, "step_used")
    }

    #[must_use]
    pub fn call_budget(&self) -> i64 {
        self.bounded_limit("call_budget") as i64
    }

    #[must_use]
    pub fn call_used(&self) -> i64 {
        state_i64(&self.state, "call_used")
    }

    #[must_use]
    pub fn cost_budget(&self) -> f64 {
        self.bounded_limit("cost_budget")
    }

    #[must_use]
    pub fn cost_used(&self) -> f64 {
        state_f64(&self.state, "cost_used")
    }

    pub fn debit_step(
        &mut self,
        amount: i64,
        reason: Option<&str>,
        run_id: &str,
        attribution: Option<&BudgetAttribution>,
    ) -> Result<(), BudgetError> {
        let amount = amount.max(0);
        if amount < 0 {
            return Err(BudgetError::Invalid("step amount must be >= 0"));
        }
        let next_used = self.step_used() + amount;
        if self.step_budget() != 0 && next_used > self.step_budget() {
            return Err(BudgetError::Exhausted(format!(
                "step budget exhausted: used={} total={}",
                self.step_used(),
                self.step_budget()
            )));
        }
        set_state_i64(&mut self.state, "step_used", next_used);
        self.events.push(BudgetEvent {
            run_id: run_id.to_owned(),
            source: "runtime".to_owned(),
            kind: "debit".to_owned(),
            amount: amount as f64,
            unit: "step".to_owned(),
            scope: state_text(&self.state, "budget_scope", "run"),
            ts_ms: None,
            meta: JsonObject::from([(
                "reason".to_owned(),
                Value::from(reason.unwrap_or("workflow_step")),
            )]),
            event_id: None,
            attribution: attribution.cloned(),
        });
        Ok(())
    }

    pub fn debit_call(
        &mut self,
        amount: i64,
        reason: Option<&str>,
        run_id: &str,
        attribution: Option<&BudgetAttribution>,
    ) -> Result<(), BudgetError> {
        let amount = amount.max(0);
        if amount < 0 {
            return Err(BudgetError::Invalid("call amount must be >= 0"));
        }
        let next_used = self.call_used() + amount;
        if self.call_budget() != 0 && next_used > self.call_budget() {
            return Err(BudgetError::Exhausted(format!(
                "call budget exhausted: used={} total={}",
                self.call_used(),
                self.call_budget()
            )));
        }
        set_state_i64(&mut self.state, "call_used", next_used);
        self.events.push(BudgetEvent {
            run_id: run_id.to_owned(),
            source: "runtime".to_owned(),
            kind: "debit".to_owned(),
            amount: amount as f64,
            unit: "call".to_owned(),
            scope: state_text(&self.state, "budget_scope", "run"),
            ts_ms: None,
            meta: JsonObject::from([(
                "reason".to_owned(),
                Value::from(reason.unwrap_or("llm_call")),
            )]),
            event_id: None,
            attribution: attribution.cloned(),
        });
        Ok(())
    }

    fn refresh_rate_window(&mut self, amount: i64, now_ms: i64) -> Result<(), BudgetError> {
        let limit = self.rate_limit();
        let window_ms = self.rate_window_ms();
        if limit == 0 {
            return Ok(());
        }
        let mut used = self.rate_used();
        let mut window_started_ms = self.rate_window_started_ms();
        if window_ms != 0
            && window_started_ms != 0
            && now_ms.saturating_sub(window_started_ms) >= window_ms
        {
            window_started_ms = now_ms;
            used = 0;
        }
        if window_ms != 0 && window_started_ms == 0 {
            window_started_ms = now_ms;
        }
        if limit != 0 && used + amount > limit {
            return Err(BudgetError::Exhausted(format!(
                "rate budget exhausted: used={} total={}",
                used, limit
            )));
        }
        set_state_i64(&mut self.state, "rate_used", used + amount);
        set_state_i64(&mut self.state, "rate_window_started_ms", window_started_ms);
        Ok(())
    }

    #[allow(clippy::too_many_arguments)]
    pub fn debit(
        &mut self,
        amount: i64,
        reason: &str,
        source: &str,
        run_id: &str,
        unit: Option<&str>,
        attribution: Option<&BudgetAttribution>,
        event_id: Option<&str>,
        meta: Option<&JsonObject>,
    ) -> Result<(), BudgetError> {
        let amount = amount.max(0);
        if amount < 0 {
            return Err(BudgetError::Invalid("amount must be >= 0"));
        }
        let next_used = self.used() + amount;
        if next_used > self.total() {
            self.events.push(BudgetEvent {
                run_id: run_id.to_owned(),
                source: source.to_owned(),
                kind: "exhausted".to_owned(),
                amount: amount as f64,
                unit: unit
                    .map(ToString::to_string)
                    .unwrap_or_else(|| state_text(&self.state, "budget_kind", "token")),
                scope: state_text(&self.state, "budget_scope", "run"),
                ts_ms: None,
                meta: metadata_with_reason(reason, meta),
                event_id: event_id.map(ToString::to_string),
                attribution: attribution.cloned(),
            });
            return Err(BudgetError::Exhausted(format!(
                "budget exhausted: used={} total={}",
                self.used(),
                self.total()
            )));
        }
        let now_ms = state_i64(&self.state, "now_ms");
        self.refresh_rate_window(amount, now_ms)?;
        set_state_i64(&mut self.state, "token_used", next_used);
        self.events.push(BudgetEvent {
            run_id: run_id.to_owned(),
            source: source.to_owned(),
            kind: "debit".to_owned(),
            amount: amount as f64,
            unit: unit
                .map(ToString::to_string)
                .unwrap_or_else(|| state_text(&self.state, "budget_kind", "token")),
            scope: state_text(&self.state, "budget_scope", "run"),
            ts_ms: None,
            meta: metadata_with_reason(reason, meta),
            event_id: event_id.map(ToString::to_string),
            attribution: attribution.cloned(),
        });
        Ok(())
    }

    #[allow(clippy::too_many_arguments)]
    pub fn debit_time(
        &mut self,
        amount_ms: i64,
        reason: &str,
        source: &str,
        run_id: &str,
        attribution: Option<&BudgetAttribution>,
        event_id: Option<&str>,
        meta: Option<&JsonObject>,
    ) -> Result<(), BudgetError> {
        let amount_ms = amount_ms.max(0);
        if amount_ms < 0 {
            return Err(BudgetError::Invalid("amount_ms must be >= 0"));
        }
        let next_used = self.time_used_ms() + amount_ms;
        if self.time_budget_ms() != 0 && next_used > self.time_budget_ms() {
            self.events.push(BudgetEvent {
                run_id: run_id.to_owned(),
                source: source.to_owned(),
                kind: "exhausted".to_owned(),
                amount: amount_ms as f64,
                unit: "ms".to_owned(),
                scope: state_text(&self.state, "budget_scope", "run"),
                ts_ms: None,
                meta: metadata_with_reason(reason, meta),
                event_id: event_id.map(ToString::to_string),
                attribution: attribution.cloned(),
            });
            return Err(BudgetError::Exhausted(format!(
                "time budget exhausted: used={} total={}",
                self.time_used_ms(),
                self.time_budget_ms()
            )));
        }
        set_state_i64(&mut self.state, "time_used_ms", next_used);
        self.events.push(BudgetEvent {
            run_id: run_id.to_owned(),
            source: source.to_owned(),
            kind: "debit".to_owned(),
            amount: amount_ms as f64,
            unit: "ms".to_owned(),
            scope: state_text(&self.state, "budget_scope", "run"),
            ts_ms: None,
            meta: metadata_with_reason(reason, meta),
            event_id: event_id.map(ToString::to_string),
            attribution: attribution.cloned(),
        });
        Ok(())
    }

    pub fn ingest(&mut self, event: BudgetEvent) -> Result<(), BudgetError> {
        if (event.kind == "debit" || event.kind == "token") && event.unit != "ms" {
            self.debit(
                event.amount as i64,
                event
                    .meta
                    .get("reason")
                    .and_then(Value::as_str)
                    .filter(|value| !value.is_empty())
                    .unwrap_or(event.kind.as_str()),
                event.source.as_str(),
                event.run_id.as_str(),
                Some(event.unit.as_str()),
                event.attribution.as_ref(),
                event.event_id.as_deref(),
                Some(&event.meta),
            )?;
            return Ok(());
        }
        if event.kind == "time" || event.unit == "ms" {
            self.debit_time(
                event.amount as i64,
                event
                    .meta
                    .get("reason")
                    .and_then(Value::as_str)
                    .filter(|value| !value.is_empty())
                    .unwrap_or(event.kind.as_str()),
                event.source.as_str(),
                event.run_id.as_str(),
                event.attribution.as_ref(),
                event.event_id.as_deref(),
                Some(&event.meta),
            )?;
            return Ok(());
        }
        if event.kind == "cost" {
            let next_cost_used = self.cost_used() + event.amount;
            set_state_f64(&mut self.state, "cost_used", next_cost_used);
            self.events.push(event);
            return Ok(());
        }
        self.events.push(event);
        Ok(())
    }

    #[must_use]
    pub fn should_suspend_for_budget(&self) -> bool {
        self.total() != 0 && self.used() >= self.total()
            || self.time_budget_ms() != 0 && self.time_used_ms() >= self.time_budget_ms()
            || self.rate_limit() != 0 && self.rate_used() >= self.rate_limit()
            || self.step_budget() != 0 && self.step_used() >= self.step_budget()
            || self.call_budget() != 0 && self.call_used() >= self.call_budget()
            || self.cost_budget() > 0.0 && self.cost_used() >= self.cost_budget()
    }
}
