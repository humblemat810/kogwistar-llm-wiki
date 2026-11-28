use crate::ids::stable_id;
use crate::maintenance_patches::{
    MaintenanceOperationKind, MaintenancePatch, MaintenancePatchOperation, MaintenanceScope,
};
use crate::maintenance_policy::normalize_maintenance_kind;
use crate::maintenance_status::MaintenanceIntent;

#[must_use]
pub fn maintenance_intent_for_job_kind(maintenance_kind: Option<&str>) -> MaintenanceIntent {
    match normalize_maintenance_kind(maintenance_kind).as_str() {
        "document_seed_graph" => MaintenanceIntent::SeedDocument,
        "document_parse_graph" => MaintenanceIntent::SplitNode,
        "document_expand_parse_children" => MaintenanceIntent::SplitNode,
        "document_reparse_region" => MaintenanceIntent::SplitNode,
        "document_correct_parse_children" => MaintenanceIntent::CorrectFact,
        "document_summarize_units" => MaintenanceIntent::DeriveSummary,
        "document_extract_entities" => MaintenanceIntent::DeriveEntity,
        "document_propose_crosslinks" => MaintenanceIntent::DeriveCrosslinkCandidate,
        "document_validate_crosslinks" => MaintenanceIntent::AddCrosslink,
        "document_revalidate_crosslinks" => MaintenanceIntent::DeriveCrosslinkCandidate,
        "document_retract_crosslinks" => MaintenanceIntent::RetractCrosslink,
        "document_detect_conflicts" => MaintenanceIntent::RequestReview,
        "entity_merge_candidate" => MaintenanceIntent::RequestReview,
        "entity_disambiguation_scan" => MaintenanceIntent::RequestReview,
        "entity_disambiguation_reconcile" => MaintenanceIntent::RequestReview,
        "entity_disambiguation_review" => MaintenanceIntent::RequestReview,
        "entity_disambiguation_patch_proposal" => MaintenanceIntent::RequestReview,
        "conversation_promote_to_kg" => MaintenanceIntent::PromoteCandidate,
        "graph_patch_review" => MaintenanceIntent::RequestReview,
        "graph_patch_apply" => MaintenanceIntent::RequestReview,
        _ => MaintenanceIntent::DeriveSummary,
    }
}

#[allow(clippy::too_many_arguments)]
#[must_use]
pub fn build_review_patch_for_maintenance_job(
    workspace_id: &str,
    maintenance_kind: Option<&str>,
    request_id: &str,
    reason: &str,
    conversation_id: Option<&str>,
    thread_id: Option<&str>,
) -> MaintenancePatch {
    let normalized = normalize_maintenance_kind(maintenance_kind);
    let scope_kind = if thread_id.is_some_and(|value| !value.trim().is_empty()) {
        "thread"
    } else if conversation_id.is_some_and(|value| !value.trim().is_empty()) {
        "conversation"
    } else {
        "workspace"
    };
    let patch_id = format!(
        "maintenance_job_review:{}",
        stable_id(
            "maintenance_job_review_patch",
            &[workspace_id, normalized.as_str(), request_id]
        )
    );
    let operation_id = format!(
        "review:{}",
        stable_id(
            "maintenance_job_review_operation",
            &[request_id, normalized.as_str()]
        )
    );
    MaintenancePatch {
        patch_id,
        intent: maintenance_intent_for_job_kind(Some(normalized.as_str())),
        scope: MaintenanceScope {
            workspace_id: workspace_id.to_owned(),
            scope_kind: scope_kind.to_owned(),
            conversation_id: conversation_id.map(str::to_owned),
            thread_id: thread_id.map(str::to_owned),
        },
        operations: vec![MaintenancePatchOperation {
            operation_id,
            kind: MaintenanceOperationKind::RequestReview,
            target_id: None,
            node_id: None,
            edge_id: None,
            from_node_id: None,
            to_node_id: None,
            relation: None,
            label: None,
            node_type: None,
            properties: serde_json::json!({}),
            supersedes_ids: Vec::new(),
            provenance: None,
            reason: Some(reason.to_owned()),
        }],
        status: "proposed".to_owned(),
        rationale: Some(format!(
            "Maintenance job '{normalized}' requires proposal/review before graph application."
        )),
        requires_atomic_group: false,
    }
}
