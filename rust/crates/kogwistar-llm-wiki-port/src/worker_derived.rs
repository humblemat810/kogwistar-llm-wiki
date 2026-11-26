use std::collections::BTreeSet;

use serde::Serialize;
use serde_json::{json, Value};

use crate::ids::stable_id;
use crate::models::JsonObject;

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct DistillationPlan {
    pub complete: bool,
    pub candidate_count: usize,
    pub selected_ids: Vec<String>,
    pub source_where: Value,
}

fn selected_candidate_ids(payload: &JsonObject) -> BTreeSet<String> {
    payload
        .get("maintenance_candidates")
        .and_then(Value::as_array)
        .map(|values| {
            values
                .iter()
                .filter_map(Value::as_object)
                .filter_map(|object| object.get("candidate_id"))
                .filter_map(Value::as_str)
                .map(str::trim)
                .filter(|value| !value.is_empty())
                .map(str::to_owned)
                .collect()
        })
        .unwrap_or_default()
}

fn and_where(clauses: Vec<Value>) -> Value {
    json!({"$and": clauses})
}

fn derived_source_where(
    workspace_id: &str,
    maintenance_mode: &str,
    selected_ids: &BTreeSet<String>,
) -> Value {
    let base = json!({"artifact_kind": "promoted_knowledge", "workspace_id": workspace_id});
    if maintenance_mode != "background" || selected_ids.is_empty() {
        return base;
    }
    and_where(vec![
        base,
        json!({"id": {"$in": selected_ids.iter().cloned().collect::<Vec<_>>()}}),
    ])
}

#[must_use]
pub fn distillation_plan(payload: &JsonObject, workspace_id: &str) -> DistillationPlan {
    let maintenance_mode = payload
        .get("maintenance_mode")
        .and_then(Value::as_str)
        .unwrap_or("request");
    let selected_ids = selected_candidate_ids(payload);
    let source_where = derived_source_where(workspace_id, maintenance_mode, &selected_ids);
    DistillationPlan {
        complete: maintenance_mode == "background" && selected_ids.is_empty(),
        candidate_count: selected_ids.len(),
        selected_ids: selected_ids.into_iter().collect(),
        source_where,
    }
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct DerivedNodePlan {
    pub id: String,
    pub label: String,
    pub metadata: Value,
}

fn source_node_ids(nodes: &[Value]) -> Vec<String> {
    let mut ids = nodes
        .iter()
        .map(|node| {
            node.get("id")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_owned()
        })
        .filter(|id| !id.is_empty())
        .collect::<Vec<_>>();
    ids.sort();
    ids.dedup();
    ids
}

fn source_digest_for_ids(ids: &[String]) -> String {
    let payload = json!({"source_node_ids": ids});
    let encoded = serde_json::to_string(&payload).expect("derived group payload");
    use sha2::{Digest, Sha256};
    let digest = Sha256::digest(encoded.as_bytes());
    digest.iter().map(|byte| format!("{byte:02x}")).collect()
}

#[allow(clippy::too_many_arguments)]
#[must_use]
pub fn build_derived_node_for_group(
    workspace_id: &str,
    label: &str,
    nodes: &[Value],
    created_at_ms: i64,
) -> DerivedNodePlan {
    let source_ids = source_node_ids(nodes);
    let derived_id = stable_id(
        "derived_knowledge",
        &[
            workspace_id,
            label,
            source_digest_for_ids(&source_ids).as_str(),
        ],
    )
    .to_string();
    DerivedNodePlan {
        id: derived_id,
        label: label.to_owned(),
        metadata: json!({
            "artifact_kind": "derived_knowledge",
            "workspace_id": workspace_id,
            "created_at_ms": created_at_ms,
            "source_node_ids": source_ids,
            "replaces_ids": source_ids,
            "derived_from": source_ids
        }),
    }
}
