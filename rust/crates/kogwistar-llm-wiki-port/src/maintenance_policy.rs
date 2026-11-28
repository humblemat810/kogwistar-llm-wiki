use serde::Serialize;

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
pub const GRAPH_PATCH_PROPOSAL_KINDS: [&str; 18] = [
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
    "entity_disambiguation_review",
    "entity_disambiguation_patch_proposal",
    "conversation_promote_to_kg",
    "graph_patch_review",
];
pub const DOCUMENT_PARSE_KINDS: [&str; 2] = ["document_parse_graph", "document_reparse_region"];
pub const GRAPH_PATCH_APPLY_KINDS: [&str; 1] = ["graph_patch_apply"];

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum MaintenanceWorkflowId {
    DerivedKnowledge,
    ExecutionWisdom,
    GraphPatchProposal,
    GraphPatchApply,
    MaintenanceObservation,
    CrosslinkGroup,
    MultimodalRetrieval,
}

impl MaintenanceWorkflowId {
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            Self::DerivedKnowledge => DERIVED_KNOWLEDGE_WORKFLOW_ID,
            Self::ExecutionWisdom => EXECUTION_WISDOM_WORKFLOW_ID,
            Self::GraphPatchProposal => GRAPH_PATCH_PROPOSAL_WORKFLOW_ID,
            Self::GraphPatchApply => GRAPH_PATCH_APPLY_WORKFLOW_ID,
            Self::MaintenanceObservation => MAINTENANCE_OBSERVATION_WORKFLOW_ID,
            Self::CrosslinkGroup => CROSSLINK_GROUP_WORKFLOW_ID,
            Self::MultimodalRetrieval => MULTIMODAL_RETRIEVAL_WORKFLOW_ID,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct MaintenancePolicyError {
    pub message: String,
}

impl std::fmt::Display for MaintenancePolicyError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "{}", self.message)
    }
}

impl std::error::Error for MaintenancePolicyError {}

#[must_use]
pub fn normalize_maintenance_kind(maintenance_kind: Option<&str>) -> String {
    maintenance_kind
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .unwrap_or("distill")
        .to_ascii_lowercase()
}

pub fn workflow_id_for_maintenance_kind(
    maintenance_kind: Option<&str>,
) -> Result<MaintenanceWorkflowId, MaintenancePolicyError> {
    let normalized = normalize_maintenance_kind(maintenance_kind);
    if CROSSLINK_GROUP_KINDS.contains(&normalized.as_str()) {
        return Ok(MaintenanceWorkflowId::CrosslinkGroup);
    }
    if DIRECT_MAINTENANCE_KINDS.contains(&normalized.as_str()) {
        return Err(MaintenancePolicyError {
            message: format!("'{normalized}' is a direct maintenance operation, not a workflow"),
        });
    }
    if normalized == "review_maintenance_subject" {
        return Ok(MaintenanceWorkflowId::MaintenanceObservation);
    }
    if EXECUTION_WISDOM_KINDS.contains(&normalized.as_str()) {
        return Ok(MaintenanceWorkflowId::ExecutionWisdom);
    }
    if GRAPH_PATCH_APPLY_KINDS.contains(&normalized.as_str()) {
        return Ok(MaintenanceWorkflowId::GraphPatchApply);
    }
    if GRAPH_PATCH_PROPOSAL_KINDS.contains(&normalized.as_str()) {
        return Ok(MaintenanceWorkflowId::GraphPatchProposal);
    }
    Ok(MaintenanceWorkflowId::DerivedKnowledge)
}

#[must_use]
pub fn is_execution_wisdom_kind(maintenance_kind: Option<&str>) -> bool {
    EXECUTION_WISDOM_KINDS.contains(&normalize_maintenance_kind(maintenance_kind).as_str())
}

#[must_use]
pub fn is_graph_patch_kind(maintenance_kind: Option<&str>) -> bool {
    let normalized = normalize_maintenance_kind(maintenance_kind);
    GRAPH_PATCH_PROPOSAL_KINDS.contains(&normalized.as_str())
        || GRAPH_PATCH_APPLY_KINDS.contains(&normalized.as_str())
}
