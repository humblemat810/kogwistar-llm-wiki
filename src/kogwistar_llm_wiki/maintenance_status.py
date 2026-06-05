from __future__ import annotations

from enum import StrEnum

from .maintenance_patches import MaintenanceIntent, MaintenancePatchStatus


class DocumentGraphStatus(StrEnum):
    SEEDED = "seeded"
    EXPANDING = "expanding"
    NEEDS_REVIEW = "needs_review"
    STABLE = "stable"
    STALE = "stale"
    SUPERSEDED = "superseded"


def graph_status_for_patch(
    *,
    patch_status: MaintenancePatchStatus,
    intent: MaintenanceIntent,
) -> DocumentGraphStatus:
    if patch_status in {MaintenancePatchStatus.REJECTED, MaintenancePatchStatus.NEEDS_REVIEW}:
        return DocumentGraphStatus.NEEDS_REVIEW
    if patch_status == MaintenancePatchStatus.RETRACTED:
        return DocumentGraphStatus.STALE
    if intent in {MaintenanceIntent.MERGE_NODES, MaintenanceIntent.CORRECT_FACT, MaintenanceIntent.REFRESH_SUMMARY}:
        return DocumentGraphStatus.SUPERSEDED
    if intent in {
        MaintenanceIntent.SPLIT_NODE,
        MaintenanceIntent.DERIVE_ENTITY,
        MaintenanceIntent.DERIVE_SUMMARY,
        MaintenanceIntent.DERIVE_CROSSLINK_CANDIDATE,
    }:
        return DocumentGraphStatus.EXPANDING
    if intent == MaintenanceIntent.SEED_DOCUMENT:
        return DocumentGraphStatus.SEEDED
    return DocumentGraphStatus.STABLE
