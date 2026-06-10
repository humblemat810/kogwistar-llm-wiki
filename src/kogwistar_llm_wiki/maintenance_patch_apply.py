from __future__ import annotations

from contextlib import nullcontext
from typing import Any, Protocol, runtime_checkable

from kogwistar.engine_core.models import Edge, Grounding, MentionVerification, Node, Span
from kogwistar.engine_core import GraphKnowledgeEngine
from kogwistar.id_provider import stable_id
from kogwistar.typing_interfaces import WriteLike
from pydantic import BaseModel, ConfigDict, Field

from .maintenance_patches import (
    MaintenanceOperationKind,
    MaintenancePatch,
    MaintenancePatchStatus,
    MaintenancePatchValidationReport,
    validate_maintenance_patch,
)
from .maintenance_status import graph_status_for_patch
from .namespaces import WorkspaceNamespaces
from .models import NamespaceEngines
from .utils import _temporary_namespace


JsonScalar = str | int | float | bool | None


@runtime_checkable
class _MaintenanceReadLike(Protocol):
    def get_nodes(
        self,
        ids: list[str] | None = None,
        where: dict[str, Any] | None = None,
        resolve_mode: str | None = None,
    ) -> list[Any]: ...

    def get_edges(
        self,
        ids: list[str] | None = None,
        where: dict[str, Any] | None = None,
        resolve_mode: str | None = None,
    ) -> list[Any]: ...


class _MaintenanceEngineLike(Protocol):
    read: _MaintenanceReadLike
    write: WriteLike

    def uow(self): ...

    def tombstone_node(self, node_id: str, **kw: Any) -> bool: ...
    def tombstone_edge(self, edge_id: str, **kw: Any) -> bool: ...


class MaintenancePatchOperationApplyResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str
    kind: MaintenanceOperationKind
    status: str
    entity_id: str | None = None
    message: str | None = None


class MaintenancePatchApplyResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patch_id: str
    status: MaintenancePatchStatus
    validation: MaintenancePatchValidationReport
    operation_results: list[MaintenancePatchOperationApplyResult] = Field(default_factory=list)
    artifact_id: str | None = None

    @property
    def applied_count(self) -> int:
        return sum(1 for item in self.operation_results if item.status == "applied")

    @property
    def skipped_count(self) -> int:
        return sum(1 for item in self.operation_results if item.status == "skipped")

    @property
    def failed_count(self) -> int:
        return sum(1 for item in self.operation_results if item.status == "failed")


def apply_maintenance_patch(
    engine: GraphKnowledgeEngine,
    patch: MaintenancePatch,
    *,
    active_node_ids: set[str] | None = None,
    active_edge_ids: set[str] | None = None,
    namespace_prefix: str | None = None,
    emit_artifact: bool = True,
) -> MaintenancePatchApplyResult:
    """Validate and apply a maintenance patch through kogwistar graph primitives.

    Backends with a real ``engine.uow()`` get one coherent unit of work. Other
    backends still get deterministic, idempotent operation-level application.
    """

    active_node_ids = set(active_node_ids or _read_active_ids(engine, "node"))
    active_edge_ids = set(active_edge_ids or _read_active_ids(engine, "edge"))
    validation = validate_maintenance_patch(
        patch,
        active_node_ids=active_node_ids,
        active_edge_ids=active_edge_ids,
        namespace_prefix=namespace_prefix,
    )
    if not validation.valid:
        artifact_id = _emit_patch_artifact(engine, patch, validation, [], MaintenancePatchStatus.REJECTED) if emit_artifact else None
        return MaintenancePatchApplyResult(
            patch_id=patch.patch_id,
            status=MaintenancePatchStatus.REJECTED,
            validation=validation,
            artifact_id=artifact_id,
        )

    operation_results: list[MaintenancePatchOperationApplyResult] = []
    status = MaintenancePatchStatus.APPLIED
    try:
        with _engine_uow(engine):
            for operation in patch.operations:
                result = _apply_operation(engine, patch, operation)
                operation_results.append(result)
                if result.status == "failed":
                    status = MaintenancePatchStatus.NEEDS_REVIEW
        artifact_id = _emit_patch_artifact(engine, patch, validation, operation_results, status) if emit_artifact else None
    except Exception as exc:
        status = MaintenancePatchStatus.NEEDS_REVIEW
        operation_results.append(
            MaintenancePatchOperationApplyResult(
                operation_id="patch",
                kind=MaintenanceOperationKind.REQUEST_REVIEW,
                status="failed",
                message=str(exc),
            )
        )
        artifact_id = _emit_patch_artifact(engine, patch, validation, operation_results, status) if emit_artifact else None

    return MaintenancePatchApplyResult(
        patch_id=patch.patch_id,
        status=status,
        validation=validation,
        operation_results=operation_results,
        artifact_id=artifact_id,
    )


def apply_maintenance_patch_for_scope(
    engines: NamespaceEngines,
    patch: MaintenancePatch,
    *,
    namespace_prefix: str | None = None,
    emit_artifact: bool = True,
) -> MaintenancePatchApplyResult:
    """Apply a patch to the graph space implied by its maintenance scope."""

    ns = WorkspaceNamespaces(patch.scope.workspace_id)
    if patch.scope.scope_kind == "workspace":
        with _temporary_namespace(engines.kg, ns.curated_kg_space):
            return apply_maintenance_patch(
                engines.kg,
                patch,
                namespace_prefix=namespace_prefix,
                emit_artifact=emit_artifact,
            )

    with _temporary_namespace(engines.conversation, ns.conv_bg):
        return apply_maintenance_patch(
            engines.conversation,
            patch,
            namespace_prefix=namespace_prefix,
            emit_artifact=emit_artifact,
        )


def _engine_uow(engine: _MaintenanceEngineLike):
    uow = getattr(engine, "uow", None)
    if callable(uow):
        return uow()
    return nullcontext()


def _read_active_ids(engine: _MaintenanceEngineLike, kind: str) -> set[str]:
    read = getattr(engine, "read", engine)
    if kind == "node":
        getter = getattr(read, "get_nodes", None)
    else:
        getter = getattr(read, "get_edges", None)
    if not callable(getter):
        return set()
    try:
        items = getter(resolve_mode="active_only")
    except TypeError:
        items = getter()
    return {str(getattr(item, "id")) for item in items if getattr(item, "id", None)}


def _exists(engine: _MaintenanceEngineLike, kind: str, entity_id: str, *, include_tombstones: bool = False) -> bool:
    read = getattr(engine, "read", engine)
    getter = getattr(read, "get_nodes" if kind == "node" else "get_edges", None)
    if not callable(getter):
        return False
    try:
        items = getter(ids=[entity_id], resolve_mode="include_tombstones" if include_tombstones else "active_only")
    except TypeError:
        items = getter(ids=[entity_id])
    return any(str(getattr(item, "id", "")) == entity_id for item in items)


def _is_tombstoned(engine: _MaintenanceEngineLike, kind: str, entity_id: str) -> bool:
    read = getattr(engine, "read", engine)
    getter = getattr(read, "get_nodes" if kind == "node" else "get_edges", None)
    if not callable(getter):
        return False
    try:
        items = getter(ids=[entity_id], resolve_mode="include_tombstones")
    except TypeError:
        items = getter(ids=[entity_id])
    for item in items:
        if str(getattr(item, "id", "")) == entity_id:
            return str((getattr(item, "metadata", {}) or {}).get("lifecycle_status") or "active") == "tombstoned"
    return False


def _apply_operation(
    engine: _MaintenanceEngineLike,
    patch: MaintenancePatch,
    operation: "MaintenancePatchOperation",
) -> MaintenancePatchOperationApplyResult:
    if operation.kind == MaintenanceOperationKind.NOOP:
        return MaintenancePatchOperationApplyResult(operation_id=operation.operation_id, kind=operation.kind, status="skipped")
    if operation.kind == MaintenanceOperationKind.REQUEST_REVIEW:
        return MaintenancePatchOperationApplyResult(
            operation_id=operation.operation_id,
            kind=operation.kind,
            status="skipped",
            message=operation.reason,
        )
    if operation.kind == MaintenanceOperationKind.ADD_NODE:
        node_id = str(operation.node_id)
        if _exists(engine, "node", node_id, include_tombstones=True):
            return MaintenancePatchOperationApplyResult(operation_id=operation.operation_id, kind=operation.kind, status="skipped", entity_id=node_id)
        _write(engine).add_node(_node_from_operation(patch, operation))
        return MaintenancePatchOperationApplyResult(operation_id=operation.operation_id, kind=operation.kind, status="applied", entity_id=node_id)
    if operation.kind == MaintenanceOperationKind.ADD_EDGE:
        edge_id = str(operation.edge_id)
        if _exists(engine, "edge", edge_id, include_tombstones=True):
            return MaintenancePatchOperationApplyResult(operation_id=operation.operation_id, kind=operation.kind, status="skipped", entity_id=edge_id)
        _write(engine).add_edge(_edge_from_operation(patch, operation))
        return MaintenancePatchOperationApplyResult(operation_id=operation.operation_id, kind=operation.kind, status="applied", entity_id=edge_id)
    if operation.kind == MaintenanceOperationKind.TOMBSTONE_NODE:
        target_id = str(operation.tombstone_target_id)
        if _is_tombstoned(engine, "node", target_id):
            return MaintenancePatchOperationApplyResult(operation_id=operation.operation_id, kind=operation.kind, status="skipped", entity_id=target_id)
        ok = bool(engine.tombstone_node(target_id, reason=operation.reason or patch.rationale or patch.intent.value, deleted_by=operation.provenance.maintenance_run_id if operation.provenance else None))
        return MaintenancePatchOperationApplyResult(operation_id=operation.operation_id, kind=operation.kind, status="applied" if ok else "failed", entity_id=target_id)
    if operation.kind == MaintenanceOperationKind.TOMBSTONE_EDGE:
        target_id = str(operation.tombstone_target_id)
        if _is_tombstoned(engine, "edge", target_id):
            return MaintenancePatchOperationApplyResult(operation_id=operation.operation_id, kind=operation.kind, status="skipped", entity_id=target_id)
        ok = bool(engine.tombstone_edge(target_id, reason=operation.reason or patch.rationale or patch.intent.value, deleted_by=operation.provenance.maintenance_run_id if operation.provenance else None))
        return MaintenancePatchOperationApplyResult(operation_id=operation.operation_id, kind=operation.kind, status="applied" if ok else "failed", entity_id=target_id)
    return MaintenancePatchOperationApplyResult(operation_id=operation.operation_id, kind=operation.kind, status="failed", message="unsupported operation kind")


def _write(engine: _MaintenanceEngineLike) -> WriteLike:
    return getattr(engine, "write", engine)


def _node_from_operation(patch: MaintenancePatch, operation: "MaintenancePatchOperation") -> Node:
    metadata = _operation_metadata(patch, operation)
    return Node(
        id=operation.node_id,
        label=operation.label or operation.node_id,
        type="entity",
        summary=operation.reason or operation.label or operation.node_id,
        doc_id=_source_document_id(operation),
        properties=operation.properties or None,
        mentions=[Grounding(spans=[_span_from_operation(operation)])],
        metadata=metadata,
    )


def _edge_from_operation(patch: MaintenancePatch, operation: "MaintenancePatchOperation") -> Edge:
    metadata = _operation_metadata(patch, operation)
    return Edge(
        id=operation.edge_id,
        label=operation.label or operation.relation or operation.edge_id,
        type="relationship",
        summary=operation.reason or operation.label or operation.relation or operation.edge_id,
        doc_id=_source_document_id(operation),
        source_ids=[str(operation.from_node_id)],
        target_ids=[str(operation.to_node_id)],
        relation=str(operation.relation),
        source_edge_ids=[],
        target_edge_ids=[],
        properties=operation.properties or None,
        mentions=[_span_from_operation(operation)],
        metadata=metadata,
    )


def _operation_metadata(patch: MaintenancePatch, operation: "MaintenancePatchOperation") -> dict[str, JsonScalar]:
    provenance = operation.provenance
    metadata: dict[str, JsonScalar] = {
        "artifact_kind": "maintenance_patch_operation",
        "workspace_id": patch.scope.workspace_id,
        "scope_kind": patch.scope.scope_kind,
        "conversation_id": patch.scope.conversation_id,
        "thread_id": patch.scope.thread_id,
        "maintenance_intent": patch.intent.value,
        "patch_id": patch.patch_id,
        "operation_id": operation.operation_id,
        "operation_kind": operation.kind.value,
        "maintenance_run_id": provenance.maintenance_run_id if provenance else None,
        "confidence": provenance.confidence if provenance else None,
        "supersedes_ids": ",".join(operation.supersedes_ids),
    }
    for key in (
        "crosslink_status",
        "review_status",
        "left_source_document_id",
        "right_source_document_id",
        "promotion_target_scope",
        "promoted_from_scope",
        "source_conversation_id",
        "source_thread_id",
    ):
        value = operation.properties.get(key)
        if isinstance(value, (str, int, float, bool)) or value is None:
            metadata[key] = value
    return {key: value for key, value in metadata.items() if value not in (None, "")}


def _source_document_id(operation: "MaintenancePatchOperation") -> str:
    provenance = operation.provenance
    if provenance and provenance.source_document_id:
        return provenance.source_document_id
    return "maintenance"


def _span_from_operation(operation: "MaintenancePatchOperation") -> Span:
    provenance = operation.provenance
    pointer: dict[str, str | int | float | bool | None] = {}
    if provenance and provenance.source_pointers:
        pointer = dict(provenance.source_pointers[0])
    doc_id = str(pointer.get("doc_id") or _source_document_id(operation))
    start_char = int(pointer.get("start_char") or 0)
    end_char = int(pointer.get("end_char") or max(start_char + 1, 1))
    excerpt = str(pointer.get("excerpt") or "")
    return Span(
        collection_page_url=str(pointer.get("collection_page_url") or f"maintenance/{doc_id}"),
        document_page_url=str(pointer.get("document_page_url") or f"maintenance/{doc_id}"),
        doc_id=doc_id,
        insertion_method="maintenance_patch",
        page_number=int(pointer.get("page_number") or 1),
        start_char=start_char,
        end_char=end_char,
        excerpt=excerpt,
        context_before=str(pointer.get("context_before") or ""),
        context_after=str(pointer.get("context_after") or ""),
        verification=MentionVerification(method="system", is_verified=True, score=provenance.confidence if provenance else 1.0, notes="maintenance patch provenance"),
    )


def _emit_patch_artifact(
    engine: _MaintenanceEngineLike,
    patch: MaintenancePatch,
    validation: MaintenancePatchValidationReport,
    operation_results: list[MaintenancePatchOperationApplyResult],
    status: MaintenancePatchStatus,
) -> str | None:
    artifact_id = f"maintenance_patch_artifact:{stable_id('maintenance_patch_artifact', patch.patch_id, status.value)}"
    if _exists(engine, "node", artifact_id, include_tombstones=True):
        return artifact_id
    issue_codes = ",".join(issue.code for issue in validation.issues)
    metadata: dict[str, JsonScalar] = {
        "artifact_kind": "maintenance_patch_artifact",
        "workspace_id": patch.scope.workspace_id,
        "scope_kind": patch.scope.scope_kind,
        "conversation_id": patch.scope.conversation_id,
        "thread_id": patch.scope.thread_id,
        "patch_id": patch.patch_id,
        "patch_status": status.value,
        "graph_status": graph_status_for_patch(
            patch_status=status,
            intent=patch.intent,
        ).value,
        "maintenance_intent": patch.intent.value,
        "operation_count": len(patch.operations),
        "applied_count": sum(1 for item in operation_results if item.status == "applied"),
        "skipped_count": sum(1 for item in operation_results if item.status == "skipped"),
        "failed_count": sum(1 for item in operation_results if item.status == "failed"),
        "validation_issue_codes": issue_codes,
    }
    node = Node(
        id=artifact_id,
        label=f"Maintenance Patch {status.value}: {patch.patch_id}",
        type="entity",
        summary=patch.rationale or f"Maintenance patch {patch.patch_id} {status.value}",
        doc_id=patch.patch_id,
        mentions=[Grounding(spans=[Span.from_dummy_for_workflow(patch.patch_id)])],
        metadata={key: value for key, value in metadata.items() if value not in (None, "")},
    )
    _write(engine).add_node(node)
    return artifact_id
