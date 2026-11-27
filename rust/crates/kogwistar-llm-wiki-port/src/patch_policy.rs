// T5.10 patch policy contracts - mirroring Python maintenance/maintenance_policy.py
pub const DERIVED_KNOWLEDGE_WORKFLOW_ID: &str = "maintenance.derived_knowledge.v1";
pub const EXECUTION_WISDOM_WORKFLOW_ID: &str = "maintenance.execution_wisdom.v1";
pub const GRAPH_PATCH_PROPOSAL_WORKFLOW_ID: &str = "maintenance.graph_patch_proposal.v1";
pub const GRAPH_PATCH_APPLY_WORKFLOW_ID: &str = "maintenance.graph_patch_apply.v1";
pub const MAINTENANCE_OBSERVATION_WORKFLOW_ID: &str = "maintenance.observation.v1";
pub const CROSSLINK_GROUP_WORKFLOW_ID: &str = "maintenance.crosslink_group.v1";
pub const MULTIMODAL_RETRIEVAL_WORKFLOW_ID: &str = "retrieval.multimodal_sidecar.v1";

pub const CROSSLINK_GROUP_KINDS: [&str; 4] = [
    "document_propose_crosslinks",
    "document_validate_crosslinks",
    "document_revalidate_crosslinks",
    "document_retract_crosslinks",
];
pub const DIRECT_MAINTENANCE_KINDS: [&str; 1] = ["entity_disambiguation_scan"];
pub const EXECUTION_WISDOM_KINDS: [&str; 5] = [
    "execution_wisdom",
    "history_wisdom",
    "distill_from_history",
    "derive_problem_solving_wisdom_from_history",
    "distill_to_wisdom",
];
pub const GRAPH_PATCH_PROPOSAL_KINDS: [&str; 14] = [
    "document_seed_graph",
    "document_parse_graph",
    "document_expand_parse_children",
    "document_reparse_region",
    "document_correct_parse_children",
    "document_summarize_units",
    "document_extract_entities",
    "document_propose_crosslinks",
    "document_validate_crosslinks",
    "document_revalidate_crosslinks",
    "document_retract_crosslinks",
    "document_detect_conflicts",
    "entity_merge_candidate",
    "entity_disambiguation_reconcile",
];
pub const DOCUMENT_PARSE_KINDS: [&str; 2] = ["document_parse_graph", "document_reparse_region"];
pub const GRAPH_PATCH_APPLY_KINDS: [&str; 1] = ["graph_patch_apply"];

fn contains<const N: usize>(kinds: [&str; N], value: &str) -> bool {
    kinds.contains(&value)
}

/// Mirrors Python `normalize_maintenance_kind`.
pub fn normalize_maintenance_kind(maintenance_kind: Option<&str>) -> String {
    maintenance_kind.unwrap_or("distill").trim().to_lowercase()
}

/// Mirrors Python `workflow_id_for_maintenance_kind`.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum PatchPolicyError {
    DirectMaintenanceOperation { kind: String },
}

impl std::fmt::Display for PatchPolicyError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::DirectMaintenanceOperation { kind } => {
                write!(
                    f,
                    "{kind} is a direct maintenance operation, not a workflow"
                )
            }
        }
    }
}

impl std::error::Error for PatchPolicyError {}

pub fn workflow_id_for_maintenance_kind(
    maintenance_kind: Option<&str>,
) -> Result<String, PatchPolicyError> {
    let normalized = normalize_maintenance_kind(maintenance_kind);
    if contains(CROSSLINK_GROUP_KINDS, &normalized) {
        return Ok(CROSSLINK_GROUP_WORKFLOW_ID.to_owned());
    }
    if contains(DIRECT_MAINTENANCE_KINDS, &normalized) {
        return Err(PatchPolicyError::DirectMaintenanceOperation { kind: normalized });
    }
    if normalized == "review_maintenance_subject" {
        return Ok(MAINTENANCE_OBSERVATION_WORKFLOW_ID.to_owned());
    }
    if contains(EXECUTION_WISDOM_KINDS, &normalized) {
        return Ok(EXECUTION_WISDOM_WORKFLOW_ID.to_owned());
    }
    if contains(GRAPH_PATCH_APPLY_KINDS, &normalized) {
        return Ok(GRAPH_PATCH_APPLY_WORKFLOW_ID.to_owned());
    }
    if contains(GRAPH_PATCH_PROPOSAL_KINDS, &normalized) {
        return Ok(GRAPH_PATCH_PROPOSAL_WORKFLOW_ID.to_owned());
    }
    Ok(DERIVED_KNOWLEDGE_WORKFLOW_ID.to_owned())
}

/// Mirrors Python `is_execution_wisdom_kind`.
pub fn is_execution_wisdom_kind(maintenance_kind: Option<&str>) -> bool {
    contains(
        EXECUTION_WISDOM_KINDS,
        &normalize_maintenance_kind(maintenance_kind),
    )
}

/// Mirrors Python `is_graph_patch_kind`.
pub fn is_graph_patch_kind(maintenance_kind: Option<&str>) -> bool {
    let normalized = normalize_maintenance_kind(maintenance_kind);
    contains(GRAPH_PATCH_PROPOSAL_KINDS, &normalized)
        || contains(GRAPH_PATCH_APPLY_KINDS, &normalized)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalize_matches_python() {
        assert_eq!(normalize_maintenance_kind(None), "distill");
        assert_eq!(normalize_maintenance_kind(Some("  Distill ")), "distill");
        assert_eq!(
            normalize_maintenance_kind(Some("ENTITY_MERGE_CANDIDATE")),
            "entity_merge_candidate"
        );
    }

    #[test]
    fn workflow_id_crosslink_group() {
        assert_eq!(
            workflow_id_for_maintenance_kind(Some("document_propose_crosslinks")).unwrap(),
            CROSSLINK_GROUP_WORKFLOW_ID
        );
    }

    #[test]
    fn workflow_id_direct_maintenance_errors() {
        assert!(matches!(
            workflow_id_for_maintenance_kind(Some("entity_disambiguation_scan")),
            Err(PatchPolicyError::DirectMaintenanceOperation { .. })
        ));
    }

    #[test]
    fn workflow_id_review_maintenance_subject() {
        assert_eq!(
            workflow_id_for_maintenance_kind(Some("review_maintenance_subject")).unwrap(),
            MAINTENANCE_OBSERVATION_WORKFLOW_ID
        );
    }

    #[test]
    fn workflow_id_execution_wisdom() {
        assert_eq!(
            workflow_id_for_maintenance_kind(Some("distill_to_wisdom")).unwrap(),
            EXECUTION_WISDOM_WORKFLOW_ID
        );
    }

    #[test]
    fn workflow_id_graph_patch_apply() {
        assert_eq!(
            workflow_id_for_maintenance_kind(Some("graph_patch_apply")).unwrap(),
            GRAPH_PATCH_APPLY_WORKFLOW_ID
        );
    }

    #[test]
    fn workflow_id_default_derived_knowledge() {
        assert_eq!(
            workflow_id_for_maintenance_kind(Some("unknown_kind")).unwrap(),
            DERIVED_KNOWLEDGE_WORKFLOW_ID
        );
    }

    #[test]
    fn is_execution_wisdom_kind_matches_python() {
        assert!(is_execution_wisdom_kind(Some("execution_wisdom")));
        assert!(!is_execution_wisdom_kind(Some("document_parse_graph")));
    }

    #[test]
    fn is_graph_patch_kind_matches_python() {
        assert!(is_graph_patch_kind(Some("document_seed_graph")));
        assert!(is_graph_patch_kind(Some("graph_patch_apply")));
        assert!(!is_graph_patch_kind(Some("execution_wisdom")));
    }
}
