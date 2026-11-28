use std::collections::{BTreeMap, BTreeSet};

use serde::Serialize;
use serde_json::{json, Value};

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct BasicSenseEvaluation {
    pub basic_sense_score: f64,
    pub basic_sense_verdict: &'static str,
    pub coverage_ratio: f64,
    pub max_depth: i64,
    pub node_count: usize,
    pub node_type_diversity: usize,
    pub duplicate_excerpt_hits: usize,
    pub fallback_used: bool,
}

fn objects(value: Option<&Value>) -> Vec<&serde_json::Map<String, Value>> {
    value
        .and_then(Value::as_array)
        .map(Vec::as_slice)
        .unwrap_or_default()
        .iter()
        .filter_map(Value::as_object)
        .collect()
}

fn normalize_excerpt(value: Option<&Value>) -> String {
    value
        .and_then(Value::as_str)
        .unwrap_or_default()
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
}

fn truthy(value: Option<&Value>) -> bool {
    match value {
        Some(Value::Null) => false,
        Some(Value::Bool(value)) => *value,
        Some(Value::Number(value)) => value.as_f64().unwrap_or_default() != 0.0,
        Some(Value::String(value)) => !value.trim().is_empty(),
        Some(Value::Array(value)) => !value.is_empty(),
        Some(Value::Object(value)) => !value.is_empty(),
        None => false,
    }
}

fn round_ties_even(value: f64, digits: u32) -> f64 {
    let scale = 10_f64.powi(digits as i32);
    let scaled = value * scale;
    let floor = scaled.floor();
    let fraction = scaled - floor;
    let rounded = if (fraction - 0.5).abs() < f64::EPSILON {
        if floor % 2.0 == 0.0 {
            floor
        } else {
            floor + 1.0
        }
    } else {
        scaled.round()
    };
    rounded / scale
}

#[must_use]
pub fn basic_sense_eval_from_graph_payload(
    graph_payload: &Value,
    diagnostics: &Value,
) -> BasicSenseEvaluation {
    let nodes = objects(graph_payload.get("nodes"));
    let node_count = nodes.len();
    let mut node_types = BTreeSet::new();
    let mut excerpt_spans_by_cluster: BTreeMap<String, BTreeMap<String, Vec<(i64, i64)>>> =
        BTreeMap::new();
    let mut cluster_intervals: BTreeMap<String, Vec<(i64, i64)>> = BTreeMap::new();
    let mut max_depth = 0_i64;

    for node in nodes {
        let empty_metadata = serde_json::Map::new();
        let metadata = node
            .get("metadata")
            .and_then(Value::as_object)
            .unwrap_or(&empty_metadata);
        let node_type = metadata
            .get("semantic_node_type")
            .and_then(Value::as_str)
            .or_else(|| node.get("type").and_then(Value::as_str))
            .unwrap_or_default()
            .trim();
        if !node_type.is_empty() {
            node_types.insert(node_type.to_owned());
        }
        if let Some(level) = metadata.get("level_from_root").and_then(Value::as_i64) {
            max_depth = max_depth.max(level + 1);
        }
        for mention in objects(node.get("mentions")) {
            for span in objects(mention.get("spans")) {
                let excerpt = normalize_excerpt(span.get("excerpt"));
                let Some(source_cluster_id) = span.get("source_cluster_id") else {
                    continue;
                };
                if source_cluster_id.is_null() {
                    continue;
                }
                let Some(start_char) = span.get("start_char").and_then(Value::as_i64) else {
                    continue;
                };
                let Some(end_char) = span.get("end_char").and_then(Value::as_i64) else {
                    continue;
                };
                if end_char <= start_char {
                    continue;
                }
                let cluster_id = match source_cluster_id {
                    Value::String(value) => value.clone(),
                    value => value.to_string(),
                };
                let interval = (start_char.max(0), end_char.max(0));
                cluster_intervals
                    .entry(cluster_id.clone())
                    .or_default()
                    .push(interval);
                if !excerpt.is_empty() && excerpt != " " {
                    excerpt_spans_by_cluster
                        .entry(cluster_id)
                        .or_default()
                        .entry(excerpt)
                        .or_default()
                        .push(interval);
                }
            }
        }
    }

    let mut covered_total = 0_i64;
    let mut source_total = 0_i64;
    for intervals in cluster_intervals.values_mut() {
        if intervals.is_empty() {
            continue;
        }
        intervals.sort();
        let mut merged: Vec<(i64, i64)> = Vec::new();
        let mut current = intervals[0];
        for &(start_char, end_char) in intervals.iter().skip(1) {
            if start_char <= current.1 {
                current.1 = current.1.max(end_char);
            } else {
                merged.push(current);
                current = (start_char, end_char);
            }
        }
        merged.push(current);
        covered_total += merged.iter().map(|(start, end)| end - start).sum::<i64>();
        source_total += merged.iter().map(|(_, end)| *end).max().unwrap_or_default();
    }
    let coverage_ratio = if source_total == 0 {
        0.0
    } else {
        covered_total as f64 / source_total as f64
    };

    let mut duplicate_excerpt_hits = 0_usize;
    for excerpt_groups in excerpt_spans_by_cluster.values_mut() {
        for intervals in excerpt_groups.values_mut() {
            if intervals.len() <= 1 {
                continue;
            }
            intervals.sort();
            let mut merged_span_count = 0_usize;
            let mut current = intervals[0];
            for &(start_char, end_char) in intervals.iter().skip(1) {
                if start_char <= current.1 {
                    current.1 = current.1.max(end_char);
                } else {
                    merged_span_count += 1;
                    current = (start_char, end_char);
                }
            }
            merged_span_count += 1;
            duplicate_excerpt_hits += intervals.len().saturating_sub(merged_span_count);
        }
    }

    let page_index_diag = diagnostics
        .get("page_index")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    let assignment_mode = page_index_diag
        .get("assignment_mode")
        .and_then(Value::as_str)
        .or_else(|| diagnostics.get("assignment_mode").and_then(Value::as_str))
        .unwrap_or_default();
    let fallback_used = truthy(page_index_diag.get("fallback_reason"))
        || truthy(diagnostics.get("fallback_reason"))
        || assignment_mode == "deterministic_fallback"
        || truthy(page_index_diag.get("refine_excerpts_fallback"));

    let raw_score = coverage_ratio.min(1.0) * 45.0
        + max_depth.min(8) as f64 / 8.0 * 20.0
        + node_types.len().min(6) as f64 / 6.0 * 15.0
        + node_count.min(20) as f64 / 20.0 * 10.0
        - duplicate_excerpt_hits.min(5) as f64 * 7.0
        - if fallback_used { 10.0 } else { 0.0 };
    let score = round_ties_even(raw_score, 1).clamp(0.0, 100.0);
    let verdict = if score >= 70.0 && coverage_ratio >= 0.45 && duplicate_excerpt_hits == 0 {
        "good"
    } else if score >= 40.0 {
        "mixed"
    } else {
        "weak"
    };

    BasicSenseEvaluation {
        basic_sense_score: score,
        basic_sense_verdict: verdict,
        coverage_ratio: round_ties_even(coverage_ratio, 4),
        max_depth,
        node_count,
        node_type_diversity: node_types.len(),
        duplicate_excerpt_hits,
        fallback_used,
    }
}

#[must_use]
pub fn basic_sense_eval_value(graph_payload: &Value, diagnostics: &Value) -> Value {
    json!(basic_sense_eval_from_graph_payload(
        graph_payload,
        diagnostics
    ))
}
