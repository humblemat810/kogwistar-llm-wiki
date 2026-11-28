use std::collections::BTreeSet;

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct DependencyEntity {
    pub id: String,
    #[serde(default)]
    pub metadata: Value,
    #[serde(default)]
    pub source_ids: Option<Vec<String>>,
    #[serde(default)]
    pub target_ids: Option<Vec<String>>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct DependencyInvalidationPlan {
    pub workspace_id: String,
    pub changed_entity_ids: Vec<String>,
    pub affected_entity_ids: Vec<String>,
    pub affected_source_document_ids: Vec<String>,
    pub follow_up_kinds: Vec<String>,
    pub projection_entity_ids: Vec<String>,
    pub skipped_cross_workspace_ids: Vec<String>,
    pub truncated: bool,
}

fn ids_from(value: Option<&Value>) -> BTreeSet<String> {
    match value {
        Some(Value::String(value)) => {
            let value = value.trim();
            if value.is_empty() {
                BTreeSet::new()
            } else {
                BTreeSet::from([value.to_owned()])
            }
        }
        Some(Value::Array(values)) => values
            .iter()
            .filter_map(Value::as_str)
            .map(str::trim)
            .filter(|item| !item.is_empty())
            .map(str::to_owned)
            .collect(),
        _ => BTreeSet::new(),
    }
}

fn metadata_string(metadata: &Value, key: &str) -> String {
    metadata
        .as_object()
        .and_then(|values| values.get(key))
        .and_then(Value::as_str)
        .unwrap_or_default()
        .trim()
        .to_owned()
}

fn metadata_ids(metadata: &Value, keys: &[&str]) -> BTreeSet<String> {
    let object = metadata.as_object();
    let mut ids = BTreeSet::new();
    for key in keys {
        ids.extend(ids_from(object.and_then(|values| values.get(*key))));
    }
    ids
}

fn follow_up_kinds(artifact_kind: &str) -> &'static [&'static str] {
    match artifact_kind {
        "candidate_link" | "crosslink" => &["document_propose_crosslinks"],
        "derived_summary" | "summary" => &["document_summarize_units"],
        "promotion_candidate" | "promotion_evidence_pack" => &["conversation_promote_to_kg"],
        _ => &[],
    }
}

#[allow(clippy::too_many_lines)]
pub fn plan_dependency_invalidation(
    workspace_id: &str,
    changed_entity_ids: &[String],
    nodes: &[DependencyEntity],
    edges: &[DependencyEntity],
    max_dependents: usize,
) -> Result<DependencyInvalidationPlan, String> {
    if max_dependents < 1 {
        return Err("max_dependents must be positive".to_owned());
    }
    let changed = changed_entity_ids
        .iter()
        .map(String::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
        .collect::<BTreeSet<_>>();
    let mut affected = BTreeSet::new();
    let mut source_ids = BTreeSet::new();
    let mut follow_ups = BTreeSet::new();
    let mut projection_entity_ids = BTreeSet::new();
    let mut skipped = BTreeSet::new();

    for entity in nodes.iter().chain(edges.iter()) {
        let item_id = entity.id.trim();
        let metadata = &entity.metadata;
        let mut references = metadata_ids(
            metadata,
            &[
                "depends_on_ids",
                "derived_from_ids",
                "lineage_node_ids",
                "lineage_edge_ids",
                "source_document_ids",
                "source_document_id",
                "parse_generation_member_id",
            ],
        );
        if entity.source_ids.is_some() || entity.target_ids.is_some() {
            references.extend(ids_from(
                entity
                    .source_ids
                    .as_ref()
                    .map(|values| json!(values))
                    .as_ref(),
            ));
            references.extend(ids_from(
                entity
                    .target_ids
                    .as_ref()
                    .map(|values| json!(values))
                    .as_ref(),
            ));
        }
        if item_id.is_empty() || (!changed.contains(item_id) && references.is_disjoint(&changed)) {
            continue;
        }
        if metadata_string(metadata, "workspace_id") != workspace_id {
            if !item_id.is_empty() {
                skipped.insert(item_id.to_owned());
            }
            continue;
        }
        let artifact_kind = metadata_string(metadata, "artifact_kind");
        if artifact_kind != "promoted_knowledge"
            && follow_up_kinds(artifact_kind.as_str()).is_empty()
        {
            continue;
        }
        affected.insert(item_id.to_owned());
        if artifact_kind == "promoted_knowledge" {
            projection_entity_ids.insert(item_id.to_owned());
        }
        source_ids.extend(metadata_ids(
            metadata,
            &["source_document_ids", "source_document_id"],
        ));
        follow_ups.extend(
            follow_up_kinds(artifact_kind.as_str())
                .iter()
                .map(|item| (*item).to_owned()),
        );
    }

    let ordered_affected = affected.into_iter().collect::<Vec<_>>();
    let truncated = ordered_affected.len() > max_dependents;
    let bounded_affected = ordered_affected
        .iter()
        .take(max_dependents)
        .cloned()
        .collect::<BTreeSet<_>>();
    let mut bounded_source_ids = BTreeSet::new();
    let mut bounded_projection_ids = BTreeSet::new();
    for entity in nodes.iter().chain(edges.iter()) {
        if !bounded_affected.contains(entity.id.trim()) {
            continue;
        }
        bounded_source_ids.extend(metadata_ids(
            &entity.metadata,
            &["source_document_ids", "source_document_id"],
        ));
        if projection_entity_ids.contains(entity.id.trim()) {
            bounded_projection_ids.insert(entity.id.clone());
        }
    }
    Ok(DependencyInvalidationPlan {
        workspace_id: workspace_id.to_owned(),
        changed_entity_ids: changed.into_iter().collect(),
        affected_entity_ids: bounded_affected.into_iter().collect(),
        affected_source_document_ids: bounded_source_ids.into_iter().collect(),
        follow_up_kinds: follow_ups.into_iter().collect(),
        projection_entity_ids: bounded_projection_ids.into_iter().collect(),
        skipped_cross_workspace_ids: skipped.into_iter().collect(),
        truncated,
    })
}

#[must_use]
pub fn dependency_invalidation_payload(plan: &DependencyInvalidationPlan) -> Value {
    json!({
        "workspace_id": plan.workspace_id,
        "changed_entity_ids": plan.changed_entity_ids,
        "affected_entity_ids": plan.affected_entity_ids,
        "affected_source_document_ids": plan.affected_source_document_ids,
        "follow_up_kinds": plan.follow_up_kinds,
        "projection_entity_ids": plan.projection_entity_ids,
        "skipped_cross_workspace_ids": plan.skipped_cross_workspace_ids,
        "truncated": plan.truncated
    })
}
