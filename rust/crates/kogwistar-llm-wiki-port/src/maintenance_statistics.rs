use std::collections::BTreeMap;

use serde::Serialize;
use serde_json::{json, Value};

#[derive(Clone, Debug, Default, PartialEq, Serialize)]
pub struct MaintenanceDocumentStats {
    pub source_document_id: String,
    pub attempt_count: i64,
    pub completed_count: i64,
    pub suspended_count: i64,
    pub requeued_count: i64,
    pub blocked_count: i64,
    pub failed_count: i64,
    pub duration_ms_total: i64,
    pub duration_ms_max: i64,
    pub operation_categories: BTreeMap<String, i64>,
    pub workers: Vec<String>,
    pub derived_node_count: i64,
    pub source_node_count: i64,
    pub parse_count: i64,
    pub parse_duration_ms_total: i64,
    pub parsed_node_count: i64,
    pub parsed_edge_count: i64,
    pub llm_call_count: i64,
    pub failure_reasons: BTreeMap<String, i64>,
    pub average_duration_ms: f64,
}

fn int_value(value: Option<&Value>, default: i64) -> i64 {
    match value {
        Some(Value::Number(number)) => number.as_i64().unwrap_or(default),
        Some(Value::String(value)) => value.parse().unwrap_or(default),
        Some(Value::Bool(true)) => 1,
        Some(Value::Bool(false)) => 0,
        _ => default,
    }
}

fn string_value(value: Option<&Value>, default: &str) -> String {
    value.and_then(Value::as_str).unwrap_or(default).to_owned()
}

#[must_use]
pub fn operation_category(maintenance_kind: &str) -> String {
    let value = if maintenance_kind.is_empty() {
        "unknown"
    } else {
        maintenance_kind
    }
    .to_ascii_lowercase();
    if value.contains("seed") {
        return "source_seeding".to_owned();
    }
    if value.contains("parse") || value.contains("extract") {
        return "breakdown".to_owned();
    }
    if ["link", "crosslink", "edge"]
        .iter()
        .any(|token| value.contains(token))
    {
        return "linking".to_owned();
    }
    if ["split", "breakdown", "decompose"]
        .iter()
        .any(|token| value.contains(token))
    {
        return "breakdown".to_owned();
    }
    if ["merge", "combine", "consolidate"]
        .iter()
        .any(|token| value.contains(token))
    {
        return "combining".to_owned();
    }
    if ["repair", "correct", "fix"]
        .iter()
        .any(|token| value.contains(token))
    {
        return "correction".to_owned();
    }
    if ["distill", "derive", "wisdom"]
        .iter()
        .any(|token| value.contains(token))
    {
        return "distillation".to_owned();
    }
    value
}

fn count(value: &mut BTreeMap<String, i64>, key: String, amount: i64) {
    *value.entry(key).or_insert(0) += amount;
}

pub fn build_maintenance_statistics(trace_rows: &[Value]) -> Value {
    let mut documents = BTreeMap::new();
    let mut totals = BTreeMap::new();
    let mut failure_hotspots = BTreeMap::new();
    let mut operation_totals = BTreeMap::new();

    for row in trace_rows {
        let event = string_value(row.get("event"), "");
        let attributed = row
            .get("source_document_id")
            .map(|value| !value.as_str().unwrap_or_default().is_empty())
            .unwrap_or_default();
        if !attributed
            && ![
                "maintenance_job_start",
                "maintenance_runtime_attempt_start",
                "maintenance_runtime_attempt_complete",
                "maintenance_job_requeued",
                "maintenance_job_blocked",
                "maintenance_runtime_attempt_failed",
                "maintenance_graph_effect",
            ]
            .contains(&event.as_str())
        {
            continue;
        }
        let doc_id = string_value(row.get("source_document_id"), "unattributed");
        let item = documents
            .entry(doc_id)
            .or_insert_with(|| MaintenanceDocumentStats {
                source_document_id: string_value(row.get("source_document_id"), "unattributed"),
                ..Default::default()
            });
        let worker_id = string_value(row.get("worker_id"), "");
        if !worker_id.is_empty() && !item.workers.contains(&worker_id) {
            item.workers.push(worker_id);
        }
        let category = operation_category(string_value(row.get("maintenance_kind"), "").as_str());
        if event == "maintenance_runtime_attempt_start" {
            count(&mut item.operation_categories, category.clone(), 1);
            count(&mut operation_totals, category, 1);
            item.attempt_count += 1;
            count(&mut totals, "attempt_count".to_owned(), 1);
        } else if event == "maintenance_runtime_attempt_complete" {
            let status = string_value(row.get("runtime_status"), "unknown");
            let duration = int_value(row.get("duration_ms"), 0);
            item.duration_ms_total += duration;
            item.duration_ms_max = item.duration_ms_max.max(duration);
            if ["success", "succeeded", "completed", "finished"].contains(&status.as_str()) {
                item.completed_count += 1;
            }
            if status == "suspended" {
                item.suspended_count += 1;
            }
            count(&mut totals, format!("status_{status}"), 1);
        } else if event == "maintenance_job_requeued" {
            item.requeued_count += 1;
            count(&mut totals, "requeued_count".to_owned(), 1);
        } else if event == "maintenance_job_blocked" {
            let reason = string_value(row.get("reason"), "unknown");
            item.blocked_count += 1;
            count(&mut item.failure_reasons, reason.clone(), 1);
            count(&mut failure_hotspots, format!("blocked:{reason}"), 1);
            count(&mut totals, "blocked_count".to_owned(), 1);
        } else if event == "maintenance_runtime_attempt_failed" {
            count(&mut item.operation_categories, category.clone(), 1);
            count(&mut operation_totals, category, 1);
            let reason = string_value(
                row.get("error_type").or_else(|| row.get("error")),
                "unknown",
            );
            item.failed_count += 1;
            count(&mut item.failure_reasons, reason.clone(), 1);
            count(&mut failure_hotspots, format!("failed:{reason}"), 1);
            count(&mut totals, "failed_count".to_owned(), 1);
        } else if event == "maintenance_graph_effect" {
            count(&mut item.operation_categories, category.clone(), 1);
            count(&mut operation_totals, category, 1);
            let derived_nodes = int_value(row.get("derived_node_count"), 0);
            let source_nodes = int_value(row.get("source_node_count"), 0);
            item.derived_node_count += derived_nodes;
            item.source_node_count += source_nodes;
            count(&mut totals, "derived_node_count".to_owned(), derived_nodes);
            count(&mut totals, "source_node_count".to_owned(), source_nodes);
        } else if event == "maintenance_parse_complete" {
            count(&mut item.operation_categories, category.clone(), 1);
            count(&mut operation_totals, category, 1);
            item.parse_count += 1;
            item.parse_duration_ms_total += int_value(row.get("duration_ms"), 0);
            item.parsed_node_count += int_value(row.get("node_count"), 0);
            item.parsed_edge_count += int_value(row.get("edge_count"), 0);
            let llm_calls = int_value(row.get("llm_call_count"), 0);
            item.llm_call_count += llm_calls;
            count(&mut totals, "parse_count".to_owned(), 1);
            count(&mut totals, "llm_call_count".to_owned(), llm_calls);
        } else if event == "maintenance_parse_failed" {
            count(&mut item.operation_categories, category.clone(), 1);
            count(&mut operation_totals, category, 1);
            let reason = string_value(
                row.get("error_type").or_else(|| row.get("error")),
                "unknown",
            );
            count(&mut item.failure_reasons, reason.clone(), 1);
            count(&mut failure_hotspots, format!("parse_failed:{reason}"), 1);
            item.failed_count += 1;
            count(&mut totals, "failed_count".to_owned(), 1);
        }
    }

    for item in documents.values_mut() {
        item.workers.sort();
        item.average_duration_ms = if item.attempt_count == 0 {
            0.0
        } else {
            ((item.duration_ms_total as f64) / (item.attempt_count as f64) * 100.0).round() / 100.0
        };
    }
    json!({
        "document_count": documents.len(),
        "documents": documents,
        "operation_totals": operation_totals,
        "failure_hotspots": failure_hotspots,
        "totals": totals
    })
}
