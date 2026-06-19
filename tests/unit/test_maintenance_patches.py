from __future__ import annotations

import pytest
from pydantic import ValidationError

from kogwistar_llm_wiki.maintenance_patches import (
    MaintenanceIntent,
    MaintenanceOperationKind,
    MaintenancePatch,
    MaintenancePatchOperation,
    MaintenanceProvenance,
    MaintenanceScope,
    validate_maintenance_patch,
)
from kogwistar_llm_wiki.maintenance_patch_apply import apply_maintenance_patch


def _provenance() -> MaintenanceProvenance:
    return MaintenanceProvenance(
        source_document_id="doc-1",
        source_span_ids=["span-1"],
        maintenance_run_id="run-1",
        confidence=0.91,
    )


def _cross_doc_provenance(confidence: float = 0.91) -> MaintenanceProvenance:
    return MaintenanceProvenance(
        source_document_id="doc-left",
        source_pointers=[
            {"doc_id": "doc-left", "start_char": 0, "end_char": 5},
            {"doc_id": "doc-right", "start_char": 6, "end_char": 11},
        ],
        maintenance_run_id="run-1",
        confidence=confidence,
    )


def _scope() -> MaintenanceScope:
    return MaintenanceScope(workspace_id="demo")


def test_maintenance_patch_accepts_add_tombstone_vocabulary() -> None:
    patch = MaintenancePatch(
        patch_id="patch-1",
        intent=MaintenanceIntent.CORRECT_FACT,
        scope=_scope(),
        operations=[
            MaintenancePatchOperation(
                operation_id="op-1",
                kind=MaintenanceOperationKind.TOMBSTONE_EDGE,
                target_id="ws:demo:edge:old",
                provenance=_provenance(),
                reason="old relation was incorrect",
            ),
            MaintenancePatchOperation(
                operation_id="op-2",
                kind=MaintenanceOperationKind.ADD_NODE,
                node_id="ws:demo:node:new",
                label="Corrected Fact",
                node_type="fact",
                supersedes_ids=["ws:demo:edge:old"],
                provenance=_provenance(),
            ),
            MaintenancePatchOperation(
                operation_id="op-3",
                kind=MaintenanceOperationKind.ADD_EDGE,
                edge_id="ws:demo:edge:new",
                from_node_id="ws:demo:node:new",
                to_node_id="ws:demo:node:source",
                relation="grounded_in",
                provenance=_provenance(),
            ),
        ],
    )

    report = validate_maintenance_patch(
        patch,
        active_node_ids={"ws:demo:node:source"},
        active_edge_ids={"ws:demo:edge:old"},
        namespace_prefix="ws:demo:",
    )

    assert report.valid is True
    assert {operation.kind for operation in patch.operations} == {
        MaintenanceOperationKind.TOMBSTONE_EDGE,
        MaintenanceOperationKind.ADD_NODE,
        MaintenanceOperationKind.ADD_EDGE,
    }


def test_maintenance_patch_models_are_strict_and_do_not_allow_update_ops() -> None:
    with pytest.raises(ValidationError):
        MaintenancePatchOperation.model_validate(
            {
                "operation_id": "op-1",
                "kind": "UPDATE_NODE",
                "node_id": "ws:demo:node:1",
                "provenance": _provenance().model_dump(),
            }
        )

    with pytest.raises(ValidationError):
        MaintenancePatch.model_validate(
            {
                "patch_id": "patch-1",
                "intent": "correct_fact",
                "scope": _scope().model_dump(),
                "operations": [],
                "unexpected": True,
            }
        )


def test_maintenance_patch_validation_reports_missing_provenance_and_endpoints() -> None:
    patch = MaintenancePatch(
        patch_id="patch-1",
        intent=MaintenanceIntent.DERIVE_ENTITY,
        scope=_scope(),
        operations=[
            MaintenancePatchOperation(
                operation_id="op-1",
                kind=MaintenanceOperationKind.ADD_EDGE,
                edge_id="ws:demo:edge:crosslink",
                from_node_id="ws:demo:node:left",
                to_node_id="ws:demo:node:right",
                relation="related_to",
            ),
        ],
    )

    report = validate_maintenance_patch(
        patch,
        active_node_ids={"ws:demo:node:left"},
        namespace_prefix="ws:demo:",
    )

    assert report.valid is False
    assert {issue.code for issue in report.issues} == {
        "missing_provenance",
        "missing_edge_endpoint",
    }


def test_maintenance_patch_validation_reports_idempotency_and_tombstone_target_errors() -> None:
    patch = MaintenancePatch(
        patch_id="patch-1",
        intent=MaintenanceIntent.CORRECT_FACT,
        scope=_scope(),
        operations=[
            MaintenancePatchOperation(
                operation_id="op-1",
                kind=MaintenanceOperationKind.TOMBSTONE_EDGE,
                target_id="ws:demo:edge:missing",
                provenance=_provenance(),
            ),
            MaintenancePatchOperation(
                operation_id="op-1",
                kind=MaintenanceOperationKind.ADD_NODE,
                node_id="outside:node:1",
                provenance=_provenance(),
            ),
        ],
    )

    report = validate_maintenance_patch(
        patch,
        active_edge_ids={"ws:demo:edge:active"},
        namespace_prefix="ws:demo:",
    )

    assert report.valid is False
    assert {issue.code for issue in report.issues} == {
        "duplicate_operation_id",
        "missing_tombstone_target",
        "namespace_scope_violation",
    }


def test_maintenance_patch_validation_requires_supersession_lineage() -> None:
    patch = MaintenancePatch(
        patch_id="patch-1",
        intent=MaintenanceIntent.CORRECT_FACT,
        scope=_scope(),
        operations=[
            MaintenancePatchOperation(
                operation_id="op-1",
                kind=MaintenanceOperationKind.ADD_NODE,
                node_id="ws:demo:node:new",
                supersedes_ids=["ws:demo:node:old"],
                provenance=_provenance(),
            ),
        ],
    )

    report = validate_maintenance_patch(
        patch,
        active_node_ids={"ws:demo:node:old"},
        namespace_prefix="ws:demo:",
    )

    assert report.valid is False
    assert {issue.code for issue in report.issues} == {"unpaired_supersession"}


class _FakeRead:
    def __init__(self, engine: "_FakeEngine") -> None:
        self.engine = engine

    def get_nodes(self, ids=None, resolve_mode="active_only", **_kwargs):
        nodes = list(self.engine.nodes.values())
        if ids is not None:
            wanted = set(ids)
            nodes = [node for node in nodes if node.id in wanted]
        if resolve_mode != "include_tombstones":
            nodes = [
                node
                for node in nodes
                if (node.metadata or {}).get("lifecycle_status", "active") == "active"
            ]
        return nodes

    def get_edges(self, ids=None, resolve_mode="active_only", **_kwargs):
        edges = list(self.engine.edges.values())
        if ids is not None:
            wanted = set(ids)
            edges = [edge for edge in edges if edge.id in wanted]
        if resolve_mode != "include_tombstones":
            edges = [
                edge
                for edge in edges
                if (edge.metadata or {}).get("lifecycle_status", "active") == "active"
            ]
        return edges


class _FakeWrite:
    def __init__(self, engine: "_FakeEngine") -> None:
        self.engine = engine

    def add_node(self, node) -> None:
        self.engine.node_add_calls.append(node.id)
        self.engine.nodes[node.id] = node

    def add_edge(self, edge) -> None:
        self.engine.edge_add_calls.append(edge.id)
        self.engine.edges[edge.id] = edge


class _FakeEngine:
    def __init__(self) -> None:
        self.nodes = {}
        self.edges = {}
        self.node_add_calls = []
        self.edge_add_calls = []
        self.tombstone_node_calls = []
        self.tombstone_edge_calls = []
        self.read = _FakeRead(self)
        self.write = _FakeWrite(self)

    def tombstone_node(self, node_id: str, **_kwargs) -> bool:
        self.tombstone_node_calls.append(node_id)
        node = self.nodes.get(node_id)
        if node is None:
            return False
        node.metadata["lifecycle_status"] = "tombstoned"
        return True

    def tombstone_edge(self, edge_id: str, **_kwargs) -> bool:
        self.tombstone_edge_calls.append(edge_id)
        edge = self.edges.get(edge_id)
        if edge is None:
            return False
        edge.metadata["lifecycle_status"] = "tombstoned"
        return True


def test_apply_maintenance_patch_is_idempotent_for_retry() -> None:
    engine = _FakeEngine()
    seed_patch = MaintenancePatch(
        patch_id="patch-seed",
        intent=MaintenanceIntent.SEED_DOCUMENT,
        scope=_scope(),
        operations=[
            MaintenancePatchOperation(
                operation_id="seed-source",
                kind=MaintenanceOperationKind.ADD_NODE,
                node_id="ws:demo:node:source",
                label="Source",
                provenance=_provenance(),
            )
        ],
    )
    seed_result = apply_maintenance_patch(engine, seed_patch, namespace_prefix="ws:demo:")
    assert seed_result.status == "applied"

    patch = MaintenancePatch(
        patch_id="patch-1",
        intent=MaintenanceIntent.ADD_CROSSLINK,
        scope=_scope(),
        operations=[
            MaintenancePatchOperation(
                operation_id="op-node",
                kind=MaintenanceOperationKind.ADD_NODE,
                node_id="ws:demo:node:new",
                label="New",
                provenance=_provenance(),
            ),
            MaintenancePatchOperation(
                operation_id="op-edge",
                kind=MaintenanceOperationKind.ADD_EDGE,
                edge_id="ws:demo:edge:new",
                from_node_id="ws:demo:node:source",
                to_node_id="ws:demo:node:new",
                relation="related_to",
                properties={"crosslink_status": "accepted"},
                provenance=_cross_doc_provenance(confidence=0.9),
            ),
        ],
    )

    first = apply_maintenance_patch(engine, patch, namespace_prefix="ws:demo:")
    second = apply_maintenance_patch(engine, patch, namespace_prefix="ws:demo:")

    assert first.status == "applied"
    assert second.status == "applied"
    assert [result.status for result in first.operation_results] == ["applied", "applied"]
    assert [result.status for result in second.operation_results] == ["skipped", "skipped"]
    assert engine.node_add_calls.count("ws:demo:node:new") == 1
    assert engine.edge_add_calls.count("ws:demo:edge:new") == 1


def test_apply_maintenance_patch_tombstones_superseded_edge() -> None:
    engine = _FakeEngine()
    seed = MaintenancePatch(
        patch_id="patch-seed",
        intent=MaintenanceIntent.ADD_CROSSLINK,
        scope=_scope(),
        operations=[
            MaintenancePatchOperation(
                operation_id="op-left",
                kind=MaintenanceOperationKind.ADD_NODE,
                node_id="ws:demo:node:left",
                provenance=_provenance(),
            ),
            MaintenancePatchOperation(
                operation_id="op-right",
                kind=MaintenanceOperationKind.ADD_NODE,
                node_id="ws:demo:node:right",
                provenance=_provenance(),
            ),
            MaintenancePatchOperation(
                operation_id="op-edge-old",
                kind=MaintenanceOperationKind.ADD_EDGE,
                edge_id="ws:demo:edge:old",
                from_node_id="ws:demo:node:left",
                to_node_id="ws:demo:node:right",
                relation="related_to",
                properties={"crosslink_status": "accepted"},
                provenance=_cross_doc_provenance(confidence=0.9),
            ),
        ],
    )
    apply_maintenance_patch(engine, seed, namespace_prefix="ws:demo:")
    patch = MaintenancePatch(
        patch_id="patch-correct",
        intent=MaintenanceIntent.CORRECT_FACT,
        scope=_scope(),
        operations=[
            MaintenancePatchOperation(
                operation_id="op-tombstone",
                kind=MaintenanceOperationKind.TOMBSTONE_EDGE,
                target_id="ws:demo:edge:old",
                provenance=_provenance(),
                reason="incorrect relation",
            ),
            MaintenancePatchOperation(
                operation_id="op-replacement",
                kind=MaintenanceOperationKind.ADD_EDGE,
                edge_id="ws:demo:edge:new",
                from_node_id="ws:demo:node:left",
                to_node_id="ws:demo:node:right",
                relation="contradicts",
                supersedes_ids=["ws:demo:edge:old"],
                provenance=_provenance(),
            ),
        ],
    )

    result = apply_maintenance_patch(engine, patch, namespace_prefix="ws:demo:")

    assert result.status == "applied"
    assert engine.edges["ws:demo:edge:old"].metadata["lifecycle_status"] == "tombstoned"
    assert engine.tombstone_edge_calls == ["ws:demo:edge:old"]


def test_crosslink_candidate_requires_two_sided_evidence_and_candidate_status() -> None:
    valid = MaintenancePatch(
        patch_id="patch-crosslink-candidate",
        intent=MaintenanceIntent.DERIVE_CROSSLINK_CANDIDATE,
        scope=_scope(),
        operations=[
            MaintenancePatchOperation(
                operation_id="op-crosslink",
                kind=MaintenanceOperationKind.ADD_EDGE,
                edge_id="ws:demo:edge:candidate",
                from_node_id="ws:demo:node:left",
                to_node_id="ws:demo:node:right",
                relation="related_to",
                properties={"crosslink_status": "candidate"},
                provenance=_cross_doc_provenance(confidence=0.55),
            )
        ],
    )
    report = validate_maintenance_patch(
        valid,
        active_node_ids={"ws:demo:node:left", "ws:demo:node:right"},
        namespace_prefix="ws:demo:",
    )

    assert report.valid is True

    invalid = valid.model_copy(
        deep=True,
        update={
            "operations": [
                valid.operations[0].model_copy(
                    deep=True,
                    update={
                        "properties": {"crosslink_status": "accepted"},
                        "provenance": MaintenanceProvenance(
                            source_document_id="doc-left",
                            source_pointers=[{"doc_id": "doc-left"}],
                            maintenance_run_id="run-1",
                            confidence=0.3,
                        ),
                    },
                )
            ]
        },
    )
    invalid_report = validate_maintenance_patch(
        invalid,
        active_node_ids={"ws:demo:node:left", "ws:demo:node:right"},
        namespace_prefix="ws:demo:",
    )

    assert invalid_report.valid is False
    assert {issue.code for issue in invalid_report.issues} == {
        "invalid_crosslink_status",
        "missing_crosslink_two_sided_evidence",
        "crosslink_confidence_below_threshold",
    }


def test_crosslink_acceptance_and_retraction_flow_is_append_and_tombstone() -> None:
    engine = _FakeEngine()
    seed = MaintenancePatch(
        patch_id="patch-crosslink-seed",
        intent=MaintenanceIntent.SEED_DOCUMENT,
        scope=_scope(),
        operations=[
            MaintenancePatchOperation(
                operation_id="op-left",
                kind=MaintenanceOperationKind.ADD_NODE,
                node_id="ws:demo:node:left",
                provenance=_provenance(),
            ),
            MaintenancePatchOperation(
                operation_id="op-right",
                kind=MaintenanceOperationKind.ADD_NODE,
                node_id="ws:demo:node:right",
                provenance=_provenance(),
            ),
        ],
    )
    apply_maintenance_patch(engine, seed, namespace_prefix="ws:demo:")
    accept = MaintenancePatch(
        patch_id="patch-crosslink-accept",
        intent=MaintenanceIntent.ADD_CROSSLINK,
        scope=_scope(),
        operations=[
            MaintenancePatchOperation(
                operation_id="op-accepted",
                kind=MaintenanceOperationKind.ADD_EDGE,
                edge_id="ws:demo:edge:accepted",
                from_node_id="ws:demo:node:left",
                to_node_id="ws:demo:node:right",
                relation="supports",
                properties={"crosslink_status": "accepted"},
                provenance=_cross_doc_provenance(confidence=0.9),
            )
        ],
    )
    accept_result = apply_maintenance_patch(engine, accept, namespace_prefix="ws:demo:")

    assert accept_result.status == "applied"
    assert engine.edges["ws:demo:edge:accepted"].metadata["crosslink_status"] == "accepted"

    retract = MaintenancePatch(
        patch_id="patch-crosslink-retract",
        intent=MaintenanceIntent.RETRACT_CROSSLINK,
        scope=_scope(),
        operations=[
            MaintenancePatchOperation(
                operation_id="op-retract",
                kind=MaintenanceOperationKind.TOMBSTONE_EDGE,
                target_id="ws:demo:edge:accepted",
                provenance=_cross_doc_provenance(confidence=0.95),
                reason="later evidence showed the relation was stale",
            )
        ],
    )
    retract_result = apply_maintenance_patch(engine, retract, namespace_prefix="ws:demo:")

    assert retract_result.status == "applied"
    assert engine.edges["ws:demo:edge:accepted"].metadata["lifecycle_status"] == "tombstoned"
    assert engine.tombstone_edge_calls == ["ws:demo:edge:accepted"]


def test_maintenance_scope_keeps_conversation_lanes_explicit() -> None:
    with pytest.raises(ValidationError):
        MaintenanceScope(workspace_id="demo", scope_kind="conversation")

    scope = MaintenanceScope(
        workspace_id="demo",
        scope_kind="conversation",
        conversation_id="conversation:demo:1",
    )

    assert scope.scope_kind == "conversation"
