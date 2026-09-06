from __future__ import annotations

from kogwistar_llm_wiki.maintenance_patch_apply import apply_maintenance_patch_for_scope
from kogwistar_llm_wiki.maintenance_patches import (
    MaintenanceIntent,
    MaintenanceOperationKind,
    MaintenancePatch,
    MaintenancePatchOperation,
    MaintenanceProvenance,
    MaintenanceScope,
)
from kogwistar_llm_wiki.namespaces import WorkspaceNamespaces
from kogwistar_llm_wiki.utils import _temporary_namespace


def _provenance(doc_id: str = "conversation:demo:message:1") -> MaintenanceProvenance:
    return MaintenanceProvenance(
        source_document_id=doc_id,
        source_pointers=[{"doc_id": doc_id, "start_char": 0, "end_char": 8}],
        maintenance_run_id="run-scope",
        confidence=0.88,
    )


def test_conversation_scoped_patch_does_not_leak_to_workspace_kg(pipeline) -> None:
    workspace_id = "scope-isolation"
    conversation_id = "conversation:scope-isolation:lane-1"
    ns = WorkspaceNamespaces(workspace_id)
    patch = MaintenancePatch(
        patch_id="patch-conversation-temp",
        intent=MaintenanceIntent.DERIVE_ENTITY,
        scope=MaintenanceScope(
            workspace_id=workspace_id,
            scope_kind="conversation",
            conversation_id=conversation_id,
        ),
        operations=[
            MaintenancePatchOperation(
                operation_id="op-temp-fact",
                kind=MaintenanceOperationKind.ADD_NODE,
                node_id="ws:scope-isolation:conversation:node:temp-fact",
                label="Temporary fact",
                properties={"review_status": "candidate"},
                provenance=_provenance(),
            )
        ],
    )

    result = apply_maintenance_patch_for_scope(
        pipeline.engines,
        patch,
        namespace_prefix="ws:scope-isolation:",
    )

    assert result.status == "applied"
    with _temporary_namespace(pipeline.engines.conversation, ns.conv_bg):
        conversation_nodes = pipeline.engines.conversation.read.get_nodes(
            ids=["ws:scope-isolation:conversation:node:temp-fact"],
        )
    with _temporary_namespace(pipeline.engines.kg, ns.curated_kg_space):
        workspace_nodes = pipeline.engines.kg.read.get_nodes(
            ids=["ws:scope-isolation:conversation:node:temp-fact"],
        )

    assert len(conversation_nodes) == 1
    assert conversation_nodes[0].metadata.get("scope_kind") == "conversation"
    assert conversation_nodes[0].metadata.get("conversation_id") == conversation_id
    assert workspace_nodes == []


def test_conversation_promotion_patch_adds_separate_workspace_fact_with_provenance(pipeline) -> None:
    workspace_id = "scope-promotion"
    conversation_id = "conversation:scope-promotion:lane-1"
    ns = WorkspaceNamespaces(workspace_id)
    promote = MaintenancePatch(
        patch_id="patch-promote-conversation-fact",
        intent=MaintenanceIntent.PROMOTE_CANDIDATE,
        scope=MaintenanceScope(workspace_id=workspace_id),
        operations=[
            MaintenancePatchOperation(
                operation_id="op-promoted-fact",
                kind=MaintenanceOperationKind.ADD_NODE,
                node_id="ws:scope-promotion:kg:node:promoted-fact",
                label="Promoted fact",
                properties={
                    "promotion_target_scope": "workspace",
                    "promoted_from_scope": "conversation",
                    "source_conversation_id": conversation_id,
                },
                provenance=_provenance("conversation:scope-promotion:message:1"),
            )
        ],
    )

    result = apply_maintenance_patch_for_scope(
        pipeline.engines,
        promote,
        namespace_prefix="ws:scope-promotion:",
    )

    assert result.status == "applied"
    with _temporary_namespace(pipeline.engines.kg, ns.curated_kg_space):
        promoted_nodes = pipeline.engines.kg.read.get_nodes(
            ids=["ws:scope-promotion:kg:node:promoted-fact"],
        )
    with _temporary_namespace(pipeline.engines.conversation, ns.conv_bg):
        conversation_nodes = pipeline.engines.conversation.read.get_nodes(
            ids=["ws:scope-promotion:kg:node:promoted-fact"],
        )

    assert len(promoted_nodes) == 1
    promoted_metadata = promoted_nodes[0].metadata
    assert promoted_metadata.get("scope_kind") == "workspace"
    assert promoted_metadata.get("promoted_from_scope") == "conversation"
    assert promoted_metadata.get("source_conversation_id") == conversation_id
    assert conversation_nodes == []


def test_thread_scoped_patch_rejects_a_node_from_another_conversation(pipeline) -> None:
    workspace_id = "scope-thread-guard"
    conversation_id = "conversation:scope-thread-guard:one"
    source_patch = MaintenancePatch(
        patch_id="patch-conversation-source",
        intent=MaintenanceIntent.DERIVE_ENTITY,
        scope=MaintenanceScope(
            workspace_id=workspace_id,
            scope_kind="conversation",
            conversation_id=conversation_id,
        ),
        operations=[MaintenancePatchOperation(
            operation_id="op-add",
            kind=MaintenanceOperationKind.ADD_NODE,
            node_id="conversation:node:one",
            label="Conversation one node",
            provenance=_provenance(),
        )],
    )
    assert apply_maintenance_patch_for_scope(pipeline.engines, source_patch).status == "applied"
    thread_patch = MaintenancePatch(
        patch_id="patch-thread-cross-scope",
        intent=MaintenanceIntent.CORRECT_FACT,
        scope=MaintenanceScope(
            workspace_id=workspace_id,
            scope_kind="thread",
            thread_id="thread:scope-thread-guard:one",
        ),
        operations=[MaintenancePatchOperation(
            operation_id="op-tombstone",
            kind=MaintenanceOperationKind.TOMBSTONE_NODE,
            target_id="conversation:node:one",
            reason="must not cross logical scope",
            provenance=_provenance(),
        )],
    )
    result = apply_maintenance_patch_for_scope(pipeline.engines, thread_patch)
    assert result.status == "rejected"
    assert any(issue.code == "scope_entity_mismatch" for issue in result.validation.issues)


def test_thread_scoped_patch_can_mutate_its_own_thread_entity(pipeline) -> None:
    workspace_id = "scope-thread-own"
    thread_id = "thread:scope-thread-own:one"
    create = MaintenancePatch(
        patch_id="patch-thread-own-create",
        intent=MaintenanceIntent.DERIVE_ENTITY,
        scope=MaintenanceScope(workspace_id=workspace_id, scope_kind="thread", thread_id=thread_id),
        operations=[MaintenancePatchOperation(
            operation_id="op-add-thread-node",
            kind=MaintenanceOperationKind.ADD_NODE,
            node_id="thread:node:own",
            label="Thread-owned node",
            provenance=_provenance(),
        )],
    )
    assert apply_maintenance_patch_for_scope(pipeline.engines, create).status == "applied"
    tombstone = MaintenancePatch(
        patch_id="patch-thread-own-tombstone",
        intent=MaintenanceIntent.CORRECT_FACT,
        scope=MaintenanceScope(workspace_id=workspace_id, scope_kind="thread", thread_id=thread_id),
        operations=[MaintenancePatchOperation(
            operation_id="op-tombstone-thread-node",
            kind=MaintenanceOperationKind.TOMBSTONE_NODE,
            target_id="thread:node:own",
            reason="thread-local correction",
            provenance=_provenance(),
        )],
    )
    assert apply_maintenance_patch_for_scope(pipeline.engines, tombstone).status == "applied"
