use std::collections::BTreeSet;

use serde::Serialize;
use serde_json::{json, Value};

use crate::maintenance_state::edge_ids;
use crate::models::JsonObject;

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct SelectionAudit {
    pub audit_key: String,
    pub conversation_id: String,
    pub inbox_id: String,
    pub sender_id: String,
    pub recipient_id: String,
    pub msg_type: String,
    pub purpose: String,
    pub payload: JsonObject,
}

fn selection_entity_is_accessible(metadata: Option<&Value>, workspace_id: &str) -> bool {
    let Some(metadata) = metadata.and_then(Value::as_object) else {
        return false;
    };
    let declared = metadata
        .get("workspace_id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .trim();
    if declared != workspace_id {
        return false;
    }
    let scope = metadata
        .get("acl_scope")
        .or_else(|| metadata.get("security_scope"))
        .and_then(Value::as_str)
        .unwrap_or_default()
        .trim();
    scope.is_empty()
}

fn metadata_text(metadata: Option<&Value>, keys: &[&str]) -> String {
    let Some(metadata) = metadata.and_then(Value::as_object) else {
        return String::new();
    };
    for key in keys {
        if let Some(value) = metadata.get(*key) {
            let text = match value {
                Value::String(value) => value.clone(),
                other => other.to_string(),
            };
            if !text.trim().is_empty() {
                return text;
            }
        }
    }
    String::new()
}

#[must_use]
pub fn selection_seed_ids(payload: &JsonObject, request_node_id: Option<&str>) -> BTreeSet<String> {
    let mut seed_ids = BTreeSet::new();
    if let Some(Value::Array(values)) = payload.get("seed_node_ids") {
        seed_ids.extend(
            values
                .iter()
                .filter_map(Value::as_str)
                .filter(|value| !value.is_empty())
                .map(str::to_owned),
        );
    }
    if let Some(continuation) = payload
        .get("maintenance_context")
        .and_then(Value::as_object)
    {
        if let Some(Value::Array(turns)) = continuation.get("turns") {
            if let Some(last_turn) = turns.last().and_then(Value::as_object) {
                if let Some(Value::Array(values)) = last_turn.get("next_seed_node_ids") {
                    seed_ids.extend(
                        values
                            .iter()
                            .filter_map(Value::as_str)
                            .filter(|value| !value.trim().is_empty())
                            .map(str::to_owned),
                    );
                }
            }
        }
        if let Some(Value::Array(values)) = continuation.get("compressed_node_ids") {
            seed_ids.extend(
                values
                    .iter()
                    .filter_map(Value::as_str)
                    .filter(|value| !value.trim().is_empty())
                    .map(str::to_owned),
            );
        }
    }
    if let Some(request_node_id) = request_node_id {
        if !request_node_id.trim().is_empty() {
            seed_ids.insert(request_node_id.to_owned());
        }
    }
    seed_ids
}

#[must_use]
pub fn filter_edges_to_visible_nodes(
    edges: &[Value],
    nodes: &[Value],
    workspace_id: &str,
) -> Vec<Value> {
    let node_ids = nodes
        .iter()
        .map(|node| {
            node.get("id")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_owned()
        })
        .collect::<BTreeSet<_>>();
    edges
        .iter()
        .filter(|edge| {
            let metadata = edge.get("metadata");
            selection_entity_is_accessible(metadata, workspace_id)
                && edge_ids(edge).is_subset(&node_ids)
        })
        .cloned()
        .collect()
}

#[must_use]
pub fn selection_topic_seeds(
    payload: &JsonObject,
    nodes: &[Value],
    seed_ids: &BTreeSet<String>,
) -> Vec<Value> {
    let topic = payload
        .get("topic")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .trim()
        .to_ascii_lowercase();
    if topic.is_empty() {
        return Vec::new();
    }
    let terms = topic
        .split_whitespace()
        .filter(|term| term.chars().count() > 2)
        .collect::<BTreeSet<_>>();
    if terms.is_empty() {
        return Vec::new();
    }
    nodes
        .iter()
        .filter(|node| {
            let node_id = node
                .get("id")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_owned();
            if node_id.is_empty() || seed_ids.contains(&node_id) {
                return false;
            }
            let searchable = format!(
                "{} {} {}",
                node.get("label")
                    .and_then(Value::as_str)
                    .unwrap_or_default(),
                node.get("summary")
                    .and_then(Value::as_str)
                    .unwrap_or_default(),
                node.get("metadata").cloned().unwrap_or(Value::Null)
            )
            .to_ascii_lowercase();
            terms.iter().any(|term| searchable.contains(term))
        })
        .cloned()
        .collect()
}

#[must_use]
pub fn selection_audit(
    workspace_id: &str,
    job_id: &str,
    request_node_id: &str,
    payload: &JsonObject,
    candidates: &[Value],
) -> SelectionAudit {
    let strategy = payload
        .get("selection_strategy")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    let candidates_json = serde_json::to_string(&candidates).expect("selection candidates");
    let audit_key = crate::ids::stable_id(
        "kogwistar_llm_wiki.maintenance.maintenance_selection",
        &[
            workspace_id,
            job_id,
            strategy.as_str(),
            candidates_json.as_str(),
        ],
    )
    .to_string();
    let payload_value = json!({
        "workspace_id": workspace_id,
        "job_id": job_id,
        "mode": payload.get("mode").and_then(Value::as_str).unwrap_or("request"),
        "topic": payload.get("topic").and_then(Value::as_str).unwrap_or(""),
        "selection_strategy": strategy,
        "candidates": candidates
    });
    let payload = payload_value
        .as_object()
        .expect("selection audit payload is an object")
        .iter()
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect();
    SelectionAudit {
        audit_key,
        conversation_id: format!("maintenance:{request_node_id}"),
        inbox_id: "inbox:worker:maintenance:audit".to_owned(),
        sender_id: "lane:worker:maintenance".to_owned(),
        recipient_id: "lane:worker:maintenance-audit".to_owned(),
        msg_type: "maintenance.selection".to_owned(),
        purpose: "internal".to_owned(),
        payload,
    }
}

#[must_use]
pub fn selection_entity_is_active_source_derivation(metadata: Option<&Value>) -> bool {
    let Some(metadata) = metadata.and_then(Value::as_object) else {
        return true;
    };
    let member_id = metadata_text(
        Some(&Value::Object(metadata.clone())),
        &["parse_generation_member_id", "generation_member_id"],
    );
    let generation_id = metadata_text(
        Some(&Value::Object(metadata.clone())),
        &["parse_generation_id", "generation_id"],
    );
    let source_document_id = metadata_text(
        Some(&Value::Object(metadata.clone())),
        &["source_document_id"],
    );
    if member_id.is_empty() && generation_id.is_empty() {
        return true;
    }
    !source_document_id.is_empty()
}
