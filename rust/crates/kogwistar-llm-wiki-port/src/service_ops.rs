// T5.8 service ops - mirroring Python maintenance status + patch operation classification
use serde::{Deserialize, Serialize};

use crate::maintenance_patches::MaintenanceOperationKind;
use crate::maintenance_status::{
    graph_status_for_patch, DocumentGraphStatus, MaintenanceIntent, MaintenancePatchStatus,
};

/// Service operation category: how a maintenance op affects the graph lifecycle.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ServiceOpCategory {
    /// Operation seeds a new document/source into the graph.
    Seed,
    /// Operation expands the graph with new nodes/edges.
    Expand,
    /// Operation supersedes existing graph content.
    Supersede,
    /// Operation marks content as needing review.
    NeedsReview,
    /// Operation marks content as stale/superseded by retraction.
    Stale,
    /// Operation leaves graph in a stable state.
    Stable,
}

/// Classify a maintenance operation into a service op category.
/// Mirrors Python `maintenance_status.graph_status_for_patch` semantics.
pub fn classify_service_op(
    patch_status: MaintenancePatchStatus,
    intent: MaintenanceIntent,
) -> ServiceOpCategory {
    match graph_status_for_patch(patch_status, intent) {
        DocumentGraphStatus::Seeded => ServiceOpCategory::Seed,
        DocumentGraphStatus::Expanding => ServiceOpCategory::Expand,
        DocumentGraphStatus::Superseded => ServiceOpCategory::Supersede,
        DocumentGraphStatus::NeedsReview => ServiceOpCategory::NeedsReview,
        DocumentGraphStatus::Stale => ServiceOpCategory::Stale,
        DocumentGraphStatus::Stable => ServiceOpCategory::Stable,
    }
}

/// Whether a service op mutates graph state (vs read-only/noop).
pub fn is_graph_mutating(kind: MaintenanceOperationKind) -> bool {
    matches!(
        kind,
        MaintenanceOperationKind::AddNode
            | MaintenanceOperationKind::AddEdge
            | MaintenanceOperationKind::TombstoneNode
            | MaintenanceOperationKind::TombstoneEdge
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::maintenance_patches::MaintenanceOperationKind;

    #[test]
    fn classify_seed_document() {
        assert_eq!(
            classify_service_op(
                MaintenancePatchStatus::Applied,
                MaintenanceIntent::SeedDocument
            ),
            ServiceOpCategory::Seed
        );
    }

    #[test]
    fn classify_split_node_expands() {
        assert_eq!(
            classify_service_op(
                MaintenancePatchStatus::Applied,
                MaintenanceIntent::SplitNode
            ),
            ServiceOpCategory::Expand
        );
    }

    #[test]
    fn classify_rejected_needs_review() {
        assert_eq!(
            classify_service_op(
                MaintenancePatchStatus::Rejected,
                MaintenanceIntent::AddCrosslink
            ),
            ServiceOpCategory::NeedsReview
        );
    }

    #[test]
    fn classify_retracted_stale() {
        assert_eq!(
            classify_service_op(
                MaintenancePatchStatus::Retracted,
                MaintenanceIntent::RetractCrosslink
            ),
            ServiceOpCategory::Stale
        );
    }

    #[test]
    fn classify_correct_fact_supersedes() {
        assert_eq!(
            classify_service_op(
                MaintenancePatchStatus::Applied,
                MaintenanceIntent::CorrectFact
            ),
            ServiceOpCategory::Supersede
        );
    }

    #[test]
    fn is_graph_mutating_add_node() {
        assert!(is_graph_mutating(MaintenanceOperationKind::AddNode));
        assert!(is_graph_mutating(MaintenanceOperationKind::AddEdge));
        assert!(is_graph_mutating(MaintenanceOperationKind::TombstoneNode));
        assert!(is_graph_mutating(MaintenanceOperationKind::TombstoneEdge));
        assert!(!is_graph_mutating(MaintenanceOperationKind::Noop));
        assert!(!is_graph_mutating(MaintenanceOperationKind::RequestReview));
    }
}
