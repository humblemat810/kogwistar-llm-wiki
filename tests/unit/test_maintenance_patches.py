from __future__ import annotations

from contextlib import contextmanager

import pytest
from kogwistar.engine_core import AtomicMutationCapability
from kogwistar.engine_core.models import Grounding, Node, Span
from pydantic import ValidationError

import kogwistar_llm_wiki.maintenance.maintenance_patch_apply as patch_apply
from kogwistar_llm_wiki.maintenance.maintenance_patch_apply import (
    apply_maintenance_patch,
)
from kogwistar_llm_wiki.maintenance.maintenance_patches import (
    MaintenanceIntent,
    MaintenanceOperationKind,
    MaintenancePatch,
    MaintenancePatchOperation,
    MaintenanceProvenance,
    MaintenanceScope,
    validate_maintenance_patch,
)


def _provenance(doc_id: str = "doc-1") -> MaintenanceProvenance:
    return MaintenanceProvenance(
        source_document_id=doc_id,
        source_pointers=[{"doc_id": doc_id, "start_char": 0, "end_char": 8}],
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


@pytest.mark.parametrize(
    "intent",
    [
        MaintenanceIntent.DERIVE_CROSSLINK_CANDIDATE,
        MaintenanceIntent.ADD_CROSSLINK,
    ],
)
def test_crosslink_edge_serialization_keeps_both_grounding_spans(
    intent: MaintenanceIntent,
) -> None:
    operation = MaintenancePatchOperation(
        operation_id="add-crosslink",
        kind=MaintenanceOperationKind.ADD_EDGE,
        edge_id="ws:demo:edge:derived",
        from_node_id="ws:demo:node:left",
        to_node_id="ws:demo:node:right",
        relation="used_by",
        provenance=MaintenanceProvenance(
            source_document_id="doc-left",
            source_pointers=[
                {"doc_id": "doc-left", "start_char": 0, "end_char": 5, "excerpt": "alpha"},
                {"doc_id": "doc-right", "start_char": 6, "end_char": 11, "excerpt": "omega"},
            ],
            maintenance_run_id="run-crosslink",
            confidence=0.9,
        ),
    )
    patch = MaintenancePatch(
        patch_id="patch-crosslink-spans",
        intent=intent,
        scope=_scope(),
        operations=[operation],
    )

    edge = patch_apply._edge_from_operation(patch, operation)

    assert [
        (span.doc_id, span.start_char, span.end_char, span.excerpt)
        for span in edge.iter_span()
    ] == [
        ("doc-left", 0, 5, "alpha"),
        ("doc-right", 6, 11, "omega"),
    ]


def _scope() -> MaintenanceScope:
    return MaintenanceScope(workspace_id="demo")


def test_maintenance_cannot_tombstone_raw_source_fact() -> None:
    engine = _FakeEngine()
    raw_node = Node(
        id="raw-source-1",
        label="Original document",
        type="entity",
        summary="The exact source snapshot",
        doc_id="doc-1",
        mentions=[Grounding(spans=[Span.from_dummy_for_conversation("raw-source-1")])],
        metadata={
            "artifact_kind": "source_revision",
            "graph_space": "source",
            "source_raw_text": "The original spelling is preserved.",
        },
    )
    engine.nodes[raw_node.id] = raw_node
    patch = MaintenancePatch(
        patch_id="patch-raw-source",
        intent=MaintenanceIntent.CORRECT_FACT,
        scope=_scope(),
        operations=[
            MaintenancePatchOperation(
                operation_id="op-tombstone-raw-source",
                kind=MaintenanceOperationKind.TOMBSTONE_NODE,
                target_id=raw_node.id,
                reason="maintenance guessed the source had a typo",
                provenance=_provenance(),
            )
        ],
    )

    result = apply_maintenance_patch(engine, patch, active_node_ids={raw_node.id})

    assert result.status == "rejected"
    assert any(issue.code == "raw_fact_mutation_forbidden" for issue in result.validation.issues)
    assert engine.tombstone_node_calls == []
    assert raw_node.metadata.get("lifecycle_status") == "active"
    assert raw_node.metadata["source_raw_text"] == "The original spelling is preserved."


def test_maintenance_can_add_interpretation_and_support_edge_to_raw_fact() -> None:
    engine = _FakeEngine()
    raw_node = Node(
        id="raw-source-2",
        label="Original statement",
        type="entity",
        summary="A user-authored statement",
        doc_id="conversation-1",
        mentions=[Grounding(spans=[Span.from_dummy_for_conversation("raw-source-2")])],
        metadata={"artifact_kind": "lane_message", "conversation_id": "conversation-1"},
    )
    engine.nodes[raw_node.id] = raw_node
    patch = MaintenancePatch(
        patch_id="patch-interpretation",
        intent=MaintenanceIntent.DERIVE_ENTITY,
        scope=_scope(),
        operations=[
            MaintenancePatchOperation(
                operation_id="op-add-interpretation",
                kind=MaintenanceOperationKind.ADD_NODE,
                node_id="ws:demo:interpretation:1",
                label="Corrected interpretation guess",
                node_type="interpretation_guess",
                properties={"interpretation_status": "inferred"},
                provenance=_provenance("conversation-1"),
            ),
            MaintenancePatchOperation(
                operation_id="op-supports-interpretation",
                kind=MaintenanceOperationKind.ADD_EDGE,
                edge_id="ws:demo:edge:interpretation-1",
                from_node_id="ws:demo:interpretation:1",
                to_node_id=raw_node.id,
                relation="interprets",
                reason="Possible correction without changing the original statement",
                provenance=_provenance("conversation-1"),
            ),
        ],
    )

    result = apply_maintenance_patch(
        engine,
        patch,
        active_node_ids={raw_node.id},
        namespace_prefix="ws:demo:",
    )

    assert result.status == "applied"
    assert engine.tombstone_node_calls == []
    assert engine.nodes[raw_node.id].summary == "A user-authored statement"
    assert "ws:demo:interpretation:1" in engine.nodes
    assert engine.nodes["ws:demo:interpretation:1"].metadata["node_type"] == "interpretation_guess"
    assert "ws:demo:edge:interpretation-1" in engine.edges


def test_generic_patch_rechecks_acl_before_tombstoning_existing_entity(monkeypatch) -> None:
    engine = _FakeEngine()
    protected = Node(
        id="ws:demo:node:protected",
        label="Protected",
        type="entity",
        summary="protected",
        doc_id="doc-1",
        mentions=[Grounding(spans=[Span.from_dummy_for_conversation("protected")])],
        metadata={"workspace_id": "demo", "acl_scope": "private:finance"},
    )
    engine.nodes[protected.id] = protected
    monkeypatch.setattr(patch_apply, "can_access_security_scope", lambda _scope: False)

    patch = MaintenancePatch(
        patch_id="patch-acl-revoked",
        intent=MaintenanceIntent.CORRECT_FACT,
        scope=_scope(),
        operations=[
            MaintenancePatchOperation(
                operation_id="tombstone-protected",
                kind=MaintenanceOperationKind.TOMBSTONE_NODE,
                target_id=protected.id,
                reason="stale interpretation",
                provenance=_provenance(),
            )
        ],
    )

    result = apply_maintenance_patch(engine, patch, active_node_ids={protected.id})

    assert result.status == "rejected"
    assert any(issue.code == "acl_scope_denied" for issue in result.validation.issues)
    assert engine.tombstone_node_calls == []


def test_generic_patch_rechecks_acl_inside_uow_before_mutation() -> None:
    """A revoke observed after UoW entry must reject before lifecycle writes."""

    class RevokedAfterUowRead(_FakeRead):
        def get_nodes(self, ids=None, resolve_mode="active_only", **kwargs):
            if self.engine.in_uow:
                return []
            return super().get_nodes(ids=ids, resolve_mode=resolve_mode, **kwargs)

    class UowEngine(_FakeEngine):
        def __init__(self) -> None:
            super().__init__()
            self.in_uow = False
            self.read = RevokedAfterUowRead(self)
            self.raw_read = _FakeRead(self)

        @contextmanager
        def uow(self):
            self.in_uow = True
            try:
                yield None
            finally:
                self.in_uow = False

    engine = UowEngine()
    target = Node(
        id="ws:demo:node:revoked",
        label="Revoked",
        type="entity",
        summary="target",
        doc_id="doc-1",
        mentions=[Grounding(spans=[Span.from_dummy_for_conversation("revoked")])],
        metadata={"workspace_id": "demo"},
    )
    engine.nodes[target.id] = target
    patch = MaintenancePatch(
        patch_id="patch-revoked-in-uow",
        intent=MaintenanceIntent.CORRECT_FACT,
        scope=_scope(),
        operations=[
            MaintenancePatchOperation(
                operation_id="tombstone-revoked",
                kind=MaintenanceOperationKind.TOMBSTONE_NODE,
                target_id=target.id,
                reason="revoke between preflight and mutation",
                provenance=_provenance(),
            )
        ],
    )

    result = apply_maintenance_patch(engine, patch, active_node_ids={target.id})

    assert result.status == "rejected"
    assert any(issue.code == "acl_target_unavailable" for issue in result.validation.issues)
    assert engine.tombstone_node_calls == []


def test_generic_patch_rejects_acl_hidden_target_even_when_callers_supply_active_ids() -> None:
    """Lifecycle writes must not bypass an ACL-filtered graph read."""

    class AclFilteredRead(_FakeRead):
        def get_nodes(self, ids=None, resolve_mode="active_only", **_kwargs):
            nodes = super().get_nodes(ids=ids, resolve_mode=resolve_mode)
            return [node for node in nodes if node.id != "ws:demo:node:hidden"]

    engine = _FakeEngine()
    hidden = Node(
        id="ws:demo:node:hidden",
        label="Hidden",
        type="entity",
        summary="not visible to this maintenance principal",
        doc_id="doc-1",
        mentions=[Grounding(spans=[Span.from_dummy_for_conversation("hidden")])],
        metadata={"workspace_id": "demo"},
    )
    engine.nodes[hidden.id] = hidden
    engine.read = AclFilteredRead(engine)
    engine.raw_read = _FakeRead(engine)
    patch = MaintenancePatch(
        patch_id="patch-hidden-acl-target",
        intent=MaintenanceIntent.CORRECT_FACT,
        scope=_scope(),
        operations=[
            MaintenancePatchOperation(
                operation_id="tombstone-hidden",
                kind=MaintenanceOperationKind.TOMBSTONE_NODE,
                target_id=hidden.id,
                reason="must not mutate an inaccessible target",
                provenance=_provenance(),
            )
        ],
    )

    result = apply_maintenance_patch(engine, patch, active_node_ids={hidden.id})

    assert result.status == "rejected"
    assert any(issue.code == "acl_target_unavailable" for issue in result.validation.issues)
    assert engine.tombstone_node_calls == []


def test_acl_hidden_target_check_supports_legacy_readers_without_resolve_mode() -> None:
    """The final authorization gate must retain compatibility with legacy readers."""

    class LegacyAclFilteredRead:
        def get_nodes(self, ids=None):
            return []

        def get_edges(self, ids=None):
            return []

    engine = _FakeEngine()
    engine.read = LegacyAclFilteredRead()
    patch = MaintenancePatch(
        patch_id="patch-legacy-hidden-acl-target",
        intent=MaintenanceIntent.CORRECT_FACT,
        scope=_scope(),
        operations=[
            MaintenancePatchOperation(
                operation_id="tombstone-hidden",
                kind=MaintenanceOperationKind.TOMBSTONE_NODE,
                target_id="ws:demo:node:hidden",
                reason="must not mutate an inaccessible target",
                provenance=_provenance(),
            )
        ],
    )

    result = apply_maintenance_patch(
        engine,
        patch,
        active_node_ids={"ws:demo:node:hidden"},
    )

    assert result.status == "rejected"
    assert any(issue.code == "acl_target_unavailable" for issue in result.validation.issues)
    assert engine.tombstone_node_calls == []


def test_add_node_cannot_overwrite_acl_hidden_identifier() -> None:
    """ADD_NODE is an upsert at storage level, not authority to replace hidden data."""

    class AclFilteredRead(_FakeRead):
        def get_nodes(self, ids=None, resolve_mode="active_only", **_kwargs):
            nodes = super().get_nodes(ids=ids, resolve_mode=resolve_mode)
            return [node for node in nodes if node.id != "ws:demo:node:hidden"]

    engine = _FakeEngine()
    hidden = Node(
        id="ws:demo:node:hidden",
        label="Hidden",
        type="entity",
        summary="must not be replaced",
        doc_id="doc-1",
        mentions=[Grounding(spans=[Span.from_dummy_for_conversation("hidden")])],
        metadata={"workspace_id": "demo"},
    )
    engine.nodes[hidden.id] = hidden
    engine.read = AclFilteredRead(engine)
    engine.raw_read = _FakeRead(engine)
    patch = MaintenancePatch(
        patch_id="patch-hidden-upsert",
        intent=MaintenanceIntent.DERIVE_ENTITY,
        scope=_scope(),
        operations=[
            MaintenancePatchOperation(
                operation_id="replace-hidden",
                kind=MaintenanceOperationKind.ADD_NODE,
                node_id=hidden.id,
                label="Replacement",
                provenance=_provenance(),
            )
        ],
    )

    result = apply_maintenance_patch(engine, patch, namespace_prefix="ws:demo:")

    assert result.status == "rejected"
    assert any(issue.code == "acl_target_unavailable" for issue in result.validation.issues)
    assert hidden.id not in engine.node_add_calls
    assert engine.nodes[hidden.id].summary == "must not be replaced"


def test_add_node_fails_closed_without_raw_existence_check() -> None:
    """An adapter must prove an ADD_NODE ID is new before allowing an upsert."""

    engine = _FakeEngine()
    del engine.raw_read
    patch = MaintenancePatch(
        patch_id="patch-unverifiable-add-node",
        intent=MaintenanceIntent.DERIVE_ENTITY,
        scope=_scope(),
        operations=[
            MaintenancePatchOperation(
                operation_id="new-node",
                kind=MaintenanceOperationKind.ADD_NODE,
                node_id="ws:demo:node:new",
                label="New",
                provenance=_provenance(),
            )
        ],
    )

    result = apply_maintenance_patch(engine, patch, namespace_prefix="ws:demo:")

    assert result.status == "rejected"
    assert any(
        issue.code == "acl_new_target_unverifiable" for issue in result.validation.issues
    )
    assert "ws:demo:node:new" not in engine.node_add_calls


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
    assert patch.requires_atomic_replacement is True


def test_non_atomic_backend_rejects_replacement_before_any_write() -> None:
    engine = _FakeEngine()
    engine.atomic_mutation_capability = AtomicMutationCapability(
        mode="eventual",
        reason="physical cleanup is deferred",
    )
    existing = Node(
        id="ws:demo:node:old",
        label="Old interpretation",
        type="entity",
        summary="old",
        doc_id="doc-1",
        mentions=[Grounding(spans=[Span.from_dummy_for_conversation("old")])],
        metadata={"workspace_id": "demo", "graph_space": "curated_kg"},
    )
    engine.nodes[existing.id] = existing
    patch = MaintenancePatch(
        patch_id="patch-eventual-replacement",
        intent=MaintenanceIntent.CORRECT_FACT,
        scope=_scope(),
        operations=[
            MaintenancePatchOperation(
                operation_id="tombstone-old",
                kind=MaintenanceOperationKind.TOMBSTONE_NODE,
                target_id=existing.id,
                provenance=_provenance(),
                reason="replace interpretation",
            ),
            MaintenancePatchOperation(
                operation_id="add-new",
                kind=MaintenanceOperationKind.ADD_NODE,
                node_id="ws:demo:node:new",
                label="New interpretation",
                node_type="fact",
                supersedes_ids=[existing.id],
                provenance=_provenance(),
            ),
        ],
    )
    result = apply_maintenance_patch(engine, patch, namespace_prefix="ws:demo:")
    assert result.status == "rejected"
    assert any(issue.code == "atomic_mutation_required" for issue in result.validation.issues)
    assert engine.tombstone_node_calls == []
    assert existing.metadata.get("lifecycle_status") != "tombstoned"
    assert "ws:demo:node:new" not in engine.nodes


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
    def __init__(self, engine: _FakeEngine) -> None:
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
    def __init__(self, engine: _FakeEngine) -> None:
        self.engine = engine

    def add_node(self, node) -> None:
        self.engine.node_add_calls.append(node.id)
        self.engine.nodes[node.id] = node

    def add_edge(self, edge) -> None:
        self.engine.edge_add_calls.append(edge.id)
        self.engine.edges[edge.id] = edge


class _FakeEngine:
    def __init__(self) -> None:
        self.atomic_mutation_capability = AtomicMutationCapability(
            mode="atomic",
            reason="test transaction",
        )
        self.nodes = {}
        self.edges = {}
        self.node_add_calls = []
        self.edge_add_calls = []
        self.tombstone_node_calls = []
        self.tombstone_edge_calls = []
        self.read = _FakeRead(self)
        self.raw_read = self.read
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


def test_partial_additive_patch_retry_preserves_applied_operations() -> None:
    engine = _FakeEngine()
    existing = Node(
        id="ws:demo:node:existing",
        label="Existing",
        type="entity",
        summary="existing",
        doc_id="doc-1",
        mentions=[Grounding(spans=[Span.from_dummy_for_conversation("existing")])],
        metadata={"workspace_id": "demo"},
    )
    engine.nodes[existing.id] = existing
    original_tombstone = engine.tombstone_node
    fail_once = True

    def fail_first_tombstone(node_id: str, **kwargs) -> bool:
        nonlocal fail_once
        if fail_once:
            fail_once = False
            engine.tombstone_node_calls.append(node_id)
            return False
        return original_tombstone(node_id, **kwargs)

    engine.tombstone_node = fail_first_tombstone
    patch = MaintenancePatch(
        patch_id="patch-partial-retry",
        intent=MaintenanceIntent.DERIVE_ENTITY,
        scope=_scope(),
        operations=[
            MaintenancePatchOperation(
                operation_id="add-derived",
                kind=MaintenanceOperationKind.ADD_NODE,
                node_id="ws:demo:node:derived",
                label="Derived",
                provenance=_provenance(),
            ),
            MaintenancePatchOperation(
                operation_id="tombstone-existing",
                kind=MaintenanceOperationKind.TOMBSTONE_NODE,
                target_id=existing.id,
                reason="replace stale interpretation",
                provenance=_provenance(),
            ),
        ],
    )

    first = apply_maintenance_patch(engine, patch, namespace_prefix="ws:demo:")
    second = apply_maintenance_patch(engine, patch, namespace_prefix="ws:demo:")

    assert first.status.value == "needs_review"
    assert first.applied_count == 1
    assert second.status.value == "applied"
    assert second.skipped_count == 1
    assert second.applied_count == 1
    assert engine.node_add_calls.count("ws:demo:node:derived") == 1
    assert engine.tombstone_node_calls.count(existing.id) == 2


def test_apply_maintenance_patch_rejects_invalid_character_source_pointer() -> None:
    engine = _FakeEngine()
    patch = MaintenancePatch(
        patch_id="patch-bad-pointer",
        intent=MaintenanceIntent.SEED_DOCUMENT,
        scope=_scope(),
        operations=[
            MaintenancePatchOperation(
                operation_id="op-node",
                kind=MaintenanceOperationKind.ADD_NODE,
                node_id="ws:demo:node:new",
                label="New",
                provenance=MaintenanceProvenance(
                    source_document_id="doc-1",
                    source_pointers=[
                        {
                            "doc_id": "doc-1",
                            "start_char": 20,
                            "end_char": 5,
                            "excerpt": "bad",
                        }
                    ],
                    maintenance_run_id="run-1",
                    confidence=0.8,
                ),
            )
        ],
    )

    result = apply_maintenance_patch(engine, patch, namespace_prefix="ws:demo:", emit_artifact=False)

    assert result.status == "rejected"
    assert any(issue.code == "invalid_source_pointer" for issue in result.validation.issues)
    assert engine.node_add_calls == []


def test_apply_maintenance_patch_keeps_document_only_provenance_unverified() -> None:
    engine = _FakeEngine()
    patch = MaintenancePatch(
        patch_id="patch-doc-only",
        intent=MaintenanceIntent.SEED_DOCUMENT,
        scope=_scope(),
        operations=[
            MaintenancePatchOperation(
                operation_id="op-node",
                kind=MaintenanceOperationKind.ADD_NODE,
                node_id="ws:demo:node:new",
                label="New",
                provenance=MaintenanceProvenance(
                    source_document_id="doc-1",
                    source_pointers=[{"doc_id": "doc-1"}],
                    maintenance_run_id="run-1",
                    confidence=0.8,
                ),
            )
        ],
    )

    result = apply_maintenance_patch(engine, patch, namespace_prefix="ws:demo:")

    assert result.status == "applied"
    span = engine.nodes["ws:demo:node:new"].mentions[0].spans[0]
    assert span.doc_id == "doc-1"
    assert span.start_char == 0
    assert span.end_char == 1
    assert span.verification.is_verified is False


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
        "missing_crosslink_character_span",
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


def test_crosslink_retraction_cannot_mutate_source_native_edge() -> None:
    engine = _FakeEngine()
    seed = MaintenancePatch(
        patch_id="patch-source-native",
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
            MaintenancePatchOperation(
                operation_id="op-source-edge",
                kind=MaintenanceOperationKind.ADD_EDGE,
                edge_id="ws:demo:edge:source-native",
                from_node_id="ws:demo:node:left",
                to_node_id="ws:demo:node:right",
                relation="has_child",
                properties={"source_native": True, "edge_kind": "has_child"},
                provenance=_provenance(),
            ),
        ],
    )
    apply_maintenance_patch(engine, seed, namespace_prefix="ws:demo:")

    retract = MaintenancePatch(
        patch_id="patch-source-native-retract",
        intent=MaintenanceIntent.RETRACT_CROSSLINK,
        scope=_scope(),
        operations=[
            MaintenancePatchOperation(
                operation_id="op-retract-source-edge",
                kind=MaintenanceOperationKind.TOMBSTONE_EDGE,
                target_id="ws:demo:edge:source-native",
                provenance=_provenance(),
                reason="maintenance must not rewrite source structure",
            )
        ],
    )

    result = apply_maintenance_patch(engine, retract, namespace_prefix="ws:demo:")

    assert result.status == "rejected"
    assert any(issue.code == "derived_crosslink_required" for issue in result.validation.issues)
    assert engine.tombstone_edge_calls == []
    assert engine.edges["ws:demo:edge:source-native"].metadata.get("lifecycle_status") != "tombstoned"


def test_maintenance_scope_keeps_conversation_lanes_explicit() -> None:
    with pytest.raises(ValidationError):
        MaintenanceScope(workspace_id="demo", scope_kind="conversation")

    scope = MaintenanceScope(
        workspace_id="demo",
        scope_kind="conversation",
        conversation_id="conversation:demo:1",
    )

    assert scope.scope_kind == "conversation"
