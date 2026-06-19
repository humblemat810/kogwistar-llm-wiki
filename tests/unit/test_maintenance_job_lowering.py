from __future__ import annotations

from kogwistar_llm_wiki.maintenance_job_lowering import (
    build_review_patch_for_maintenance_job,
    maintenance_intent_for_job_kind,
)
from kogwistar_llm_wiki.maintenance_patches import MaintenanceOperationKind
from kogwistar_llm_wiki.maintenance_policy import GRAPH_PATCH_APPLY_KINDS, GRAPH_PATCH_PROPOSAL_KINDS


def test_graph_patch_job_kinds_lower_to_typed_review_patches() -> None:
    for maintenance_kind in sorted(GRAPH_PATCH_PROPOSAL_KINDS | GRAPH_PATCH_APPLY_KINDS):
        patch = build_review_patch_for_maintenance_job(
            workspace_id="demo",
            maintenance_kind=maintenance_kind,
            request_id=f"request:{maintenance_kind}",
            reason="awaiting graph patch proposal",
        )

        assert patch.intent == maintenance_intent_for_job_kind(maintenance_kind)
        assert [operation.kind for operation in patch.operations] == [
            MaintenanceOperationKind.REQUEST_REVIEW
        ]
        assert patch.scope.workspace_id == "demo"


def test_job_lowering_preserves_conversation_scope() -> None:
    patch = build_review_patch_for_maintenance_job(
        workspace_id="demo",
        maintenance_kind="conversation_promote_to_kg",
        request_id="request-1",
        reason="needs promotion review",
        conversation_id="conversation:demo:1",
    )

    assert patch.scope.scope_kind == "conversation"
    assert patch.scope.conversation_id == "conversation:demo:1"
