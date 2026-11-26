use std::collections::BTreeMap;

use serde_json::{json, Value};

fn int_amount(value: Option<&Value>) -> i64 {
    value
        .and_then(Value::as_f64)
        .map(|value| value.trunc() as i64)
        .unwrap_or(0)
}

fn float_amount(value: Option<&Value>) -> f64 {
    value.and_then(Value::as_f64).unwrap_or(0.0)
}

fn meta_text(event: &Value, keys: &[&str], default: &str) -> String {
    for key in keys {
        if let Some(value) = event
            .get("meta")
            .and_then(Value::as_object)
            .and_then(|values| values.get(*key))
            .and_then(Value::as_str)
        {
            if !value.trim().is_empty() {
                return value.to_owned();
            }
        }
    }
    default.to_owned()
}

pub fn summarize_budget_events(events: &[Value]) -> Value {
    let mut input_tokens = 0;
    let mut cached_input_tokens = 0;
    let mut output_tokens = 0;
    let mut total_tokens = 0;
    let mut total_cost = 0.0;
    let mut time_ms = 0;
    let mut event_counts = BTreeMap::new();
    let mut by_unit = BTreeMap::new();
    let mut cost_provenance = BTreeMap::new();
    for event in events {
        let kind = event
            .get("kind")
            .and_then(Value::as_str)
            .unwrap_or("unknown")
            .to_owned();
        let unit = event
            .get("unit")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_owned();
        let amount = event.get("amount").cloned().unwrap_or(Value::Null);
        *event_counts.entry(kind.clone()).or_insert(0) += 1;
        if !unit.is_empty() {
            *by_unit.entry(unit.clone()).or_insert(0) += 1;
        }
        if kind == "cost" {
            if let Some(provenance) = event
                .get("meta")
                .and_then(Value::as_object)
                .and_then(|values| values.get("cost_provenance"))
                .and_then(Value::as_str)
            {
                *cost_provenance.entry(provenance.to_owned()).or_insert(0) += 1;
            }
        }
        if (kind == "debit" || kind == "token") && unit == "input_tokens" {
            input_tokens += int_amount(Some(&amount));
            total_tokens += int_amount(Some(&amount));
        } else if (kind == "debit" || kind == "token") && unit == "output_tokens" {
            output_tokens += int_amount(Some(&amount));
            total_tokens += int_amount(Some(&amount));
        } else if (kind == "debit" || kind == "token") && unit == "cached_input_tokens" {
            cached_input_tokens += int_amount(Some(&amount));
        } else if (kind == "debit" || kind == "token") && unit == "total_tokens" {
            total_tokens += int_amount(Some(&amount));
        } else if kind == "cost" || unit == "total_cost" {
            total_cost += float_amount(Some(&amount));
        } else if unit == "ms" {
            time_ms += int_amount(Some(&amount));
        }
    }
    json!({
        "input_tokens": input_tokens,
        "cached_input_tokens": cached_input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "total_cost": ((total_cost * 1_000_000.0).round() / 1_000_000.0),
        "time_ms": time_ms,
        "event_count": events.len(),
        "event_counts": event_counts,
        "by_unit": by_unit,
        "cost_provenance": cost_provenance
    })
}

#[must_use]
pub fn summarize_maintenance_costs(
    events: &[Value],
    default_maintenance_kind: &str,
    default_model: &str,
) -> Value {
    let mut grouped = BTreeMap::new();
    for event in events {
        let maintenance_kind = meta_text(
            event,
            &["maintenance_kind", "job_kind"],
            default_maintenance_kind,
        );
        let model = meta_text(event, &["model", "llm_model"], default_model);
        grouped
            .entry(format!("{maintenance_kind}|{model}"))
            .or_insert_with(Vec::new)
            .push(event.clone());
    }
    let mut summaries = serde_json::Map::new();
    for (key, events) in grouped {
        let (maintenance_kind, model) = key.split_once('|').unwrap_or((key.as_str(), ""));
        let mut summary = summarize_budget_events(&events);
        if let Some(object) = summary.as_object_mut() {
            object.insert("maintenance_kind".to_owned(), json!(maintenance_kind));
            object.insert("model".to_owned(), json!(model));
        }
        summaries.insert(key, summary);
    }
    Value::Object(summaries)
}
