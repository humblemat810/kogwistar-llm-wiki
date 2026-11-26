use serde::Serialize;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum DocumentGraphStatus {
    Seeded,
    Expanding,
    NeedsReview,
    Stable,
    Stale,
    Superseded,
}

impl DocumentGraphStatus {
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Seeded => "seeded",
            Self::Expanding => "expanding",
            Self::NeedsReview => "needs_review",
            Self::Stable => "stable",
            Self::Stale => "stale",
            Self::Superseded => "superseded",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum MaintenanceIntent {
    SeedDocument,
    SplitNode,
    MergeNodes,
    CorrectFact,
    DeriveSummary,
    DeriveEntity,
    DeriveCrosslinkCandidate,
    AddCrosslink,
    RetractCrosslink,
    RefreshSummary,
    PromoteCandidate,
    RetractPromotion,
    DistillToWisdom,
    RequestReview,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum MaintenancePatchStatus {
    Proposed,
    Validated,
    PartiallyAccepted,
    Applied,
    Rejected,
    NeedsReview,
    Retracted,
}

#[must_use]
pub fn graph_status_for_patch(
    patch_status: MaintenancePatchStatus,
    intent: MaintenanceIntent,
) -> DocumentGraphStatus {
    if matches!(
        patch_status,
        MaintenancePatchStatus::Rejected | MaintenancePatchStatus::NeedsReview
    ) {
        return DocumentGraphStatus::NeedsReview;
    }
    if patch_status == MaintenancePatchStatus::Retracted {
        return DocumentGraphStatus::Stale;
    }
    if matches!(
        intent,
        MaintenanceIntent::MergeNodes
            | MaintenanceIntent::CorrectFact
            | MaintenanceIntent::RefreshSummary
    ) {
        return DocumentGraphStatus::Superseded;
    }
    if matches!(
        intent,
        MaintenanceIntent::SplitNode
            | MaintenanceIntent::DeriveEntity
            | MaintenanceIntent::DeriveSummary
            | MaintenanceIntent::DeriveCrosslinkCandidate
    ) {
        return DocumentGraphStatus::Expanding;
    }
    if intent == MaintenanceIntent::SeedDocument {
        return DocumentGraphStatus::Seeded;
    }
    DocumentGraphStatus::Stable
}
