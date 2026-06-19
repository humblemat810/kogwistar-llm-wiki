from __future__ import annotations

from kogwistar.id_provider import stable_id

from .maintenance_patches import (
    MaintenanceIntent,
    MaintenanceOperationKind,
    MaintenancePatch,
    MaintenancePatchOperation,
    MaintenanceScope,
)
from .maintenance_policy import normalize_maintenance_kind


JOB_KIND_INTENTS: dict[str, MaintenanceIntent] = {
    "document_seed_graph": MaintenanceIntent.SEED_DOCUMENT,
    "document_expand_parse_children": MaintenanceIntent.SPLIT_NODE,
    "document_correct_parse_children": MaintenanceIntent.CORRECT_FACT,
    "document_summarize_units": MaintenanceIntent.DERIVE_SUMMARY,
    "document_extract_entities": MaintenanceIntent.DERIVE_ENTITY,
    "document_propose_crosslinks": MaintenanceIntent.DERIVE_CROSSLINK_CANDIDATE,
    "document_validate_crosslinks": MaintenanceIntent.ADD_CROSSLINK,
    "document_retract_crosslinks": MaintenanceIntent.RETRACT_CROSSLINK,
    "document_detect_conflicts": MaintenanceIntent.REQUEST_REVIEW,
    "conversation_promote_to_kg": MaintenanceIntent.PROMOTE_CANDIDATE,
    "graph_patch_review": MaintenanceIntent.REQUEST_REVIEW,
    "graph_patch_apply": MaintenanceIntent.REQUEST_REVIEW,
}


def maintenance_intent_for_job_kind(maintenance_kind: str | None) -> MaintenanceIntent:
    return JOB_KIND_INTENTS.get(
        normalize_maintenance_kind(maintenance_kind),
        MaintenanceIntent.DERIVE_SUMMARY,
    )


def build_review_patch_for_maintenance_job(
    *,
    workspace_id: str,
    maintenance_kind: str,
    request_id: str,
    reason: str,
    conversation_id: str | None = None,
    thread_id: str | None = None,
) -> MaintenancePatch:
    """Lower a maintenance job into a strict review patch when no graph ops exist yet."""

    scope_kind = "workspace"
    if thread_id:
        scope_kind = "thread"
    elif conversation_id:
        scope_kind = "conversation"
    scope = MaintenanceScope(
        workspace_id=workspace_id,
        scope_kind=scope_kind,
        conversation_id=conversation_id,
        thread_id=thread_id,
    )
    normalized = normalize_maintenance_kind(maintenance_kind)
    return MaintenancePatch(
        patch_id=f"maintenance_job_review:{stable_id('maintenance_job_review_patch', workspace_id, normalized, request_id)}",
        intent=maintenance_intent_for_job_kind(normalized),
        scope=scope,
        operations=[
            MaintenancePatchOperation(
                operation_id=f"review:{stable_id('maintenance_job_review_operation', request_id, normalized)}",
                kind=MaintenanceOperationKind.REQUEST_REVIEW,
                reason=reason,
            )
        ],
        rationale=f"Maintenance job {normalized!r} requires proposal/review before graph application.",
    )
