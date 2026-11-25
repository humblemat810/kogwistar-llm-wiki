use std::collections::BTreeSet;

use serde_json::Value;
use uuid::Uuid;

use crate::ids::stable_id;
use crate::ingest::source_lifecycle::DurableParseLimits;
use crate::models::JsonObject;
use crate::provenance::evidence_pack_digest_hash;

fn python_json_spaced(value: &Value) -> String {
    match value {
        Value::Null => "null".to_owned(),
        Value::Bool(value) => value.to_string(),
        Value::Number(value) => value.to_string(),
        Value::String(value) => serde_json::to_string(value).expect("JSON string"),
        Value::Array(values) => {
            let items: Vec<String> = values.iter().map(python_json_spaced).collect();
            format!("[{}]", items.join(", "))
        }
        Value::Object(values) => {
            let items: Vec<String> = values
                .iter()
                .map(|(key, value)| {
                    format!(
                        "{}: {}",
                        serde_json::to_string(key).expect("JSON key"),
                        python_json_spaced(value)
                    )
                })
                .collect();
            format!("{{{}}}", items.join(", "))
        }
    }
}

#[must_use]
pub fn maintenance_request_parameters_fingerprint(
    objective: Option<&str>,
    budgets: Option<&JsonObject>,
    topic: Option<&str>,
    seed_node_ids: &[&str],
    maintenance_context: Option<&JsonObject>,
    durable_parse_limits: &DurableParseLimits,
) -> String {
    let empty = JsonObject::new();
    let budgets_json = python_json_spaced(&Value::Object(
        budgets.unwrap_or(&empty).clone().into_iter().collect(),
    ));
    let context_json = python_json_spaced(&Value::Object(
        maintenance_context
            .unwrap_or(&empty)
            .clone()
            .into_iter()
            .collect(),
    ));
    let seed_ids_json = serde_json::to_string(&Value::Array(
        seed_node_ids
            .iter()
            .map(|seed_id| (*seed_id).to_owned())
            .collect::<BTreeSet<_>>()
            .into_iter()
            .map(Value::String)
            .collect(),
    ))
    .expect("JSON serializable seed IDs");
    let limits_json = serde_json::to_string(&durable_parse_limits.to_json(false))
        .expect("JSON serializable parse limits");
    stable_id(
        "kogwistar_llm_wiki.maintenance_request_parameters",
        &[
            objective.unwrap_or_default(),
            budgets_json.as_str(),
            topic.unwrap_or_default(),
            seed_ids_json.as_str(),
            context_json.as_str(),
            "",
            limits_json.as_str(),
        ],
    )
    .to_string()
}

#[must_use]
pub fn maintenance_request_node_id(
    workspace_id: &str,
    source_document_id: &str,
    maintenance_kind: &str,
    source_revision_id: &str,
    request_fingerprint: &str,
) -> Uuid {
    stable_id(
        "kogwistar_llm_wiki.maintenance_request",
        &[
            workspace_id,
            source_document_id,
            maintenance_kind,
            source_revision_id,
            request_fingerprint,
        ],
    )
}

#[must_use]
pub fn maintenance_request_lane_id(
    workspace_id: &str,
    source_document_id: &str,
    maintenance_kind: &str,
    source_revision_id: &str,
    request_fingerprint: &str,
) -> Uuid {
    stable_id(
        "kogwistar_llm_wiki.maintenance_request_lane",
        &[
            workspace_id,
            source_document_id,
            maintenance_kind,
            source_revision_id,
            request_fingerprint,
        ],
    )
}

#[must_use]
pub fn candidate_link_id(workspace_id: &str, source_document_id: &str) -> Uuid {
    stable_id(
        "kogwistar_llm_wiki.candidate_link",
        &[workspace_id, source_document_id],
    )
}

#[must_use]
pub fn promotion_candidate_id(workspace_id: &str, source_document_id: &str) -> Uuid {
    stable_id(
        "kogwistar_llm_wiki.promotion_candidate",
        &[workspace_id, source_document_id],
    )
}

#[must_use]
pub fn promotion_evidence_pack_digest(node_ids: &[&str], edge_ids: &[&str]) -> JsonObject {
    let mut node_ids: Vec<String> = node_ids.iter().map(|id| (*id).to_owned()).collect();
    node_ids.sort();
    let mut edge_ids: Vec<String> = edge_ids.iter().map(|id| (*id).to_owned()).collect();
    edge_ids.sort();
    let mut digest = JsonObject::from([
        (
            "node_ids".to_owned(),
            Value::Array(node_ids.into_iter().map(Value::String).collect()),
        ),
        (
            "edge_ids".to_owned(),
            Value::Array(edge_ids.into_iter().map(Value::String).collect()),
        ),
        (
            "depth".to_owned(),
            Value::String("parsed_graph_extraction".to_owned()),
        ),
        ("max_chars_per_item".to_owned(), Value::from(0)),
        ("max_total_chars".to_owned(), Value::from(0)),
    ]);
    let hash = evidence_pack_digest_hash(&digest);
    digest.insert("evidence_pack_hash".to_owned(), Value::String(hash));
    digest
}

#[must_use]
pub fn projection_request_id(workspace_id: &str, promoted_entity_id: &str) -> Uuid {
    stable_id(
        "kogwistar_llm_wiki.projection_request",
        &[workspace_id, promoted_entity_id],
    )
}

#[must_use]
pub fn projection_request_payload(
    workspace_id: &str,
    promoted_entity_id: &str,
    promotion_mode: &str,
) -> JsonObject {
    [
        ("workspace_id", Value::from(workspace_id)),
        ("promoted_entity_id", Value::from(promoted_entity_id)),
        ("promotion_mode", Value::from(promotion_mode)),
    ]
    .into_iter()
    .map(|(key, value)| (key.to_owned(), value))
    .collect()
}
