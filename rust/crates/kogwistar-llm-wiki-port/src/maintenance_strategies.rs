use serde::Serialize;

use crate::maintenance_policy::{
    normalize_maintenance_kind, EXECUTION_WISDOM_KINDS, GRAPH_PATCH_APPLY_KINDS,
    GRAPH_PATCH_PROPOSAL_KINDS,
};

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum MaintenanceStrategyKind {
    ExecutionWisdom,
    GraphPatchApply,
    MaintenanceObservation,
    ContactDisambiguationScan,
    CrosslinkLifecycle,
    DocumentParse,
    DocumentExpandParseChildren,
    GraphPatchProposal,
    RuntimeWorkflow,
}

impl MaintenanceStrategyKind {
    #[must_use]
    pub fn name(self) -> &'static str {
        match self {
            Self::ExecutionWisdom => "execution_wisdom",
            Self::GraphPatchApply => "graph_patch_apply",
            Self::MaintenanceObservation => "maintenance_observation",
            Self::ContactDisambiguationScan => "contact_disambiguation_scan",
            Self::CrosslinkLifecycle => "crosslink_lifecycle",
            Self::DocumentParse => "document_parse",
            Self::DocumentExpandParseChildren => "document_expand_parse_children",
            Self::GraphPatchProposal => "graph_patch_proposal",
            Self::RuntimeWorkflow => "runtime_workflow",
        }
    }
}

#[must_use]
pub fn maintenance_strategy_kind(maintenance_kind: Option<&str>) -> MaintenanceStrategyKind {
    let normalized = normalize_maintenance_kind(maintenance_kind);
    if EXECUTION_WISDOM_KINDS.contains(&normalized.as_str()) {
        return MaintenanceStrategyKind::ExecutionWisdom;
    }
    if GRAPH_PATCH_APPLY_KINDS.contains(&normalized.as_str()) {
        return MaintenanceStrategyKind::GraphPatchApply;
    }
    if normalized == "review_maintenance_subject" {
        return MaintenanceStrategyKind::MaintenanceObservation;
    }
    if normalized == "entity_disambiguation_scan" {
        return MaintenanceStrategyKind::ContactDisambiguationScan;
    }
    if [
        "document_propose_crosslinks",
        "document_validate_crosslinks",
        "document_revalidate_crosslinks",
        "document_retract_crosslinks",
    ]
    .contains(&normalized.as_str())
    {
        return MaintenanceStrategyKind::CrosslinkLifecycle;
    }
    if normalized == "document_parse_graph" {
        return MaintenanceStrategyKind::DocumentParse;
    }
    if normalized == "document_expand_parse_children" || normalized == "document_reparse_region" {
        return MaintenanceStrategyKind::DocumentExpandParseChildren;
    }
    if GRAPH_PATCH_PROPOSAL_KINDS.contains(&normalized.as_str()) {
        return MaintenanceStrategyKind::GraphPatchProposal;
    }
    MaintenanceStrategyKind::RuntimeWorkflow
}
