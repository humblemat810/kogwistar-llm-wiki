use serde::{Deserialize, Serialize};
use serde_json::Value;

use crate::diagnostics::summarize_semantic_tree;
use crate::models::JsonObject;
use crate::provenance::python_ascii_json;

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ParseStatisticsRecord {
    pub created_at_ms: i64,
    pub workspace_id: String,
    pub source_document_id: String,
    pub source_uri: String,
    pub title: String,
    pub parser_lane: String,
    pub parser_mode: String,
    pub proposal_mode: Option<String>,
    pub provider: Option<String>,
    pub model: Option<String>,
    pub document_length_chars: usize,
    pub document_length_words: usize,
    pub parse_runtime_ms: i64,
    pub tree_depth: usize,
    pub tree_node_count: usize,
    pub tree_leaf_count: usize,
    pub node_count: usize,
    pub edge_count: usize,
    pub basic_sense_score: Option<f64>,
    pub basic_sense_verdict: Option<String>,
    pub coverage_ratio: Option<f64>,
    pub fallback_used: Option<bool>,
    pub retry_used: Option<bool>,
    pub status: String,
    pub details_json: String,
}

fn object_records(value: Option<&Value>) -> Vec<&serde_json::Map<String, Value>> {
    value
        .and_then(Value::as_array)
        .map(Vec::as_slice)
        .unwrap_or_default()
        .iter()
        .filter_map(Value::as_object)
        .collect()
}

fn optional_float(value: Option<&Value>) -> Option<f64> {
    match value {
        Some(Value::Number(number)) => number.as_f64(),
        Some(Value::String(value)) => value.trim().parse().ok(),
        Some(Value::Bool(value)) => Some(if *value { 1.0 } else { 0.0 }),
        _ => None,
    }
}

fn optional_bool(value: Option<&Value>) -> Option<bool> {
    match value {
        Some(Value::Bool(value)) => Some(*value),
        Some(Value::Number(number)) => Some(number.as_f64().unwrap_or_default() != 0.0),
        Some(Value::String(value)) if value.trim().eq_ignore_ascii_case("true") => Some(true),
        Some(Value::String(value)) if value.trim().eq_ignore_ascii_case("false") => Some(false),
        _ => None,
    }
}

#[allow(clippy::too_many_arguments)]
#[must_use]
pub fn build_parse_statistics_record(
    created_at_ms: i64,
    workspace_id: &str,
    source_document_id: &str,
    source_uri: &str,
    title: &str,
    raw_text: &str,
    parser_lane: &str,
    parser_mode: &str,
    proposal_mode: Option<&str>,
    provider: Option<&str>,
    model: Option<&str>,
    parse_runtime_ms: i64,
    semantic_tree: &Value,
    graph_payload: Option<&Value>,
    diagnostics: Option<&Value>,
    evaluation: Option<&Value>,
    status: &str,
) -> ParseStatisticsRecord {
    let empty = Value::Object(serde_json::Map::new());
    let graph_payload = graph_payload.unwrap_or(&empty);
    let diagnostics = diagnostics.unwrap_or(&empty);
    let evaluation = evaluation.unwrap_or(&empty);
    let graph_nodes = object_records(graph_payload.get("nodes"));
    let graph_edges = object_records(graph_payload.get("edges"));
    let diagnostics_dict =
        serde_json::from_value::<JsonObject>(diagnostics.clone()).unwrap_or_default();
    let evaluation_dict =
        serde_json::from_value::<JsonObject>(evaluation.clone()).unwrap_or_default();
    let details = serde_json::json!({
        "diagnostics": diagnostics_dict,
        "evaluation": evaluation_dict,
        "graph_node_ids": graph_nodes
            .iter()
            .map(|node| node.get("id").and_then(Value::as_str).unwrap_or_default().to_owned())
            .collect::<Vec<_>>(),
        "graph_edge_ids": graph_edges
            .iter()
            .map(|edge| edge.get("id").and_then(Value::as_str).unwrap_or_default().to_owned())
            .collect::<Vec<_>>(),
        "parser_lane": parser_lane,
        "proposal_mode": proposal_mode,
        "provider": provider,
        "model": model,
    });
    let tree_summary = summarize_semantic_tree(semantic_tree);
    ParseStatisticsRecord {
        created_at_ms,
        workspace_id: workspace_id.to_owned(),
        source_document_id: source_document_id.to_owned(),
        source_uri: source_uri.to_owned(),
        title: title.to_owned(),
        parser_lane: parser_lane.to_owned(),
        parser_mode: parser_mode.to_owned(),
        proposal_mode: proposal_mode.map(str::to_owned),
        provider: provider.map(str::to_owned),
        model: model.map(str::to_owned),
        document_length_chars: raw_text.chars().count(),
        document_length_words: raw_text.split_whitespace().count(),
        parse_runtime_ms,
        tree_depth: tree_summary.tree_depth,
        tree_node_count: tree_summary.tree_node_count,
        tree_leaf_count: tree_summary.tree_leaf_count,
        node_count: graph_nodes.len(),
        edge_count: graph_edges.len(),
        basic_sense_score: optional_float(evaluation_dict.get("basic_sense_score")),
        basic_sense_verdict: evaluation_dict
            .get("basic_sense_verdict")
            .filter(|value| !value.is_null())
            .and_then(Value::as_str)
            .map(str::to_owned),
        coverage_ratio: optional_float(evaluation_dict.get("coverage_ratio")),
        fallback_used: optional_bool(evaluation_dict.get("fallback_used")),
        retry_used: optional_bool(diagnostics_dict.get("retry_used")),
        status: status.to_owned(),
        details_json: python_ascii_json(&details),
    }
}
