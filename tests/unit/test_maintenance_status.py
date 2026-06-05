from __future__ import annotations

from kogwistar_llm_wiki.maintenance_patches import MaintenanceIntent, MaintenancePatchStatus
from kogwistar_llm_wiki.maintenance_status import DocumentGraphStatus, graph_status_for_patch


def test_graph_status_for_patch_covers_document_lifecycle() -> None:
    assert graph_status_for_patch(
        patch_status=MaintenancePatchStatus.APPLIED,
        intent=MaintenanceIntent.SEED_DOCUMENT,
    ) == DocumentGraphStatus.SEEDED
    assert graph_status_for_patch(
        patch_status=MaintenancePatchStatus.APPLIED,
        intent=MaintenanceIntent.SPLIT_NODE,
    ) == DocumentGraphStatus.EXPANDING
    assert graph_status_for_patch(
        patch_status=MaintenancePatchStatus.NEEDS_REVIEW,
        intent=MaintenanceIntent.ADD_CROSSLINK,
    ) == DocumentGraphStatus.NEEDS_REVIEW
    assert graph_status_for_patch(
        patch_status=MaintenancePatchStatus.APPLIED,
        intent=MaintenanceIntent.ADD_CROSSLINK,
    ) == DocumentGraphStatus.STABLE
    assert graph_status_for_patch(
        patch_status=MaintenancePatchStatus.RETRACTED,
        intent=MaintenanceIntent.RETRACT_CROSSLINK,
    ) == DocumentGraphStatus.STALE
    assert graph_status_for_patch(
        patch_status=MaintenancePatchStatus.APPLIED,
        intent=MaintenanceIntent.CORRECT_FACT,
    ) == DocumentGraphStatus.SUPERSEDED
