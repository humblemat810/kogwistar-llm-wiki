use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::models::JsonObject;

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq, Serialize, Deserialize)]
pub struct CrosslinkContextBudget {
    pub max_nodes: i64,
    pub max_edges: i64,
    pub max_tokens: i64,
    pub max_characters: i64,
}

fn integer_value(value: Option<&Value>, field: &str) -> Result<i64, String> {
    match value {
        Some(Value::Bool(_)) => Err(format!("{field} must be an integer")),
        Some(Value::Number(number)) => Ok(number.as_i64().unwrap_or(0)),
        Some(Value::String(value)) => value
            .trim()
            .parse::<i64>()
            .map_err(|_| format!("{field} must be an integer")),
        _ => Err(format!("{field} must be an integer")),
    }
}

impl CrosslinkContextBudget {
    pub fn from_payload(payload: &JsonObject) -> Result<Self, String> {
        let raw = payload
            .get("crosslink_context_budget")
            .or_else(|| payload.get("crosslink_context"));
        let source = raw.and_then(Value::as_object).map(|values| {
            values
                .iter()
                .map(|(key, value)| (key.clone(), value.clone()))
                .collect::<BTreeMap<_, _>>()
        });
        let source = source.unwrap_or_else(|| payload.clone());
        let names: [(&str, [&str; 3]); 4] = [
            (
                "max_nodes",
                ["max_nodes", "crosslink_context_max_nodes", ""],
            ),
            (
                "max_edges",
                ["max_edges", "crosslink_context_max_edges", ""],
            ),
            (
                "max_tokens",
                ["max_tokens", "crosslink_context_max_tokens", ""],
            ),
            (
                "max_characters",
                [
                    "max_characters",
                    "max_chars",
                    "crosslink_context_max_characters",
                ],
            ),
        ];
        let configured = names.iter().any(|(_, candidates)| {
            candidates
                .iter()
                .any(|name| !name.is_empty() && source.contains_key(*name))
        });
        if !configured {
            return Ok(Self::default());
        }
        let mut values = BTreeMap::new();
        for (field, candidates) in names {
            let default = match field {
                "max_nodes" => 16,
                "max_edges" => 24,
                _ => 0,
            };
            let value = candidates
                .iter()
                .filter(|name| !name.is_empty())
                .find_map(|name| source.get(*name))
                .map(|value| json!(value))
                .unwrap_or(Value::Null);
            let integer = if value.is_null() {
                default
            } else {
                integer_value(Some(&value), field)?
            };
            if integer < 0 {
                return Err(format!("{field} must be non-negative"));
            }
            values.insert(field.to_owned(), integer);
        }
        Ok(Self {
            max_nodes: values["max_nodes"],
            max_edges: values["max_edges"],
            max_tokens: values["max_tokens"],
            max_characters: values["max_characters"],
        })
    }
}

fn record_key(record: &Value) -> (String, String) {
    (
        record
            .get("context_kind")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_owned(),
        record
            .get("node_id")
            .or_else(|| record.get("edge_id"))
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_owned(),
    )
}

fn estimated_tokens(value: &Value) -> i64 {
    let encoded = serde_json::to_string(value).expect("context record");
    ((encoded.len() as f64 / 4.0).ceil()) as i64
}

#[must_use]
pub fn pack_crosslink_context(
    node_records: &[Value],
    edge_records: &[Value],
    budget: &CrosslinkContextBudget,
) -> Value {
    let mut nodes = node_records.to_vec();
    let mut edges = edge_records.to_vec();
    nodes.sort_by_key(record_key);
    edges.sort_by_key(record_key);
    let omitted_nodes = if budget.max_nodes > 0 {
        nodes.len().saturating_sub(budget.max_nodes as usize)
    } else {
        0
    };
    let omitted_edges = if budget.max_edges > 0 {
        edges.len().saturating_sub(budget.max_edges as usize)
    } else {
        0
    };
    if budget.max_nodes > 0 {
        nodes.truncate(budget.max_nodes as usize);
    }
    if budget.max_edges > 0 {
        edges.truncate(budget.max_edges as usize);
    }
    let mut kept: Vec<Value> = Vec::new();
    let mut used_characters = 0;
    for (kind, records) in [("node", nodes), ("edge", edges)] {
        for mut record in records {
            if let Some(object) = record.as_object_mut() {
                object.insert("context_kind".to_owned(), Value::from(kind));
            }
            let text = serde_json::to_string(&record).expect("crosslink context record");
            if budget.max_characters > 0
                && used_characters + text.len() as i64 > budget.max_characters
            {
                continue;
            }
            used_characters += text.len() as i64;
            kept.push(record);
        }
    }
    let nodes = kept
        .iter()
        .filter(|record| record.get("context_kind") == Some(&Value::from("node")))
        .cloned()
        .collect::<Vec<_>>();
    let edges = kept
        .iter()
        .filter(|record| record.get("context_kind") == Some(&Value::from("edge")))
        .cloned()
        .collect::<Vec<_>>();
    json!({
        "nodes": nodes,
        "edges": edges,
        "omitted_nodes": omitted_nodes,
        "omitted_edges": omitted_edges,
        "estimated_tokens": kept.iter().map(estimated_tokens).sum::<i64>(),
        "characters": used_characters,
        "budget": {
            "max_nodes": budget.max_nodes,
            "max_edges": budget.max_edges,
            "max_tokens": budget.max_tokens,
            "max_characters": budget.max_characters
        },
        "dropped_by_builder": {}
    })
}
