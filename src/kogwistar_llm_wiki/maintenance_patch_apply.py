from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, nullcontext
from typing import Protocol, cast, runtime_checkable

from kogwistar.engine_core import (
    AtomicMutationCapability,
    GraphKnowledgeEngine,
    get_atomic_mutation_capability,
)
from kogwistar.engine_core.models import (
    Edge,
    Grounding,
    MentionVerification,
    Node,
    Span,
)
from kogwistar.id_provider import stable_id
from kogwistar.typing_interfaces import WriteLike
from kogwistar.utils import source_pointer_has_character_span, validate_source_pointer
from pydantic import BaseModel, ConfigDict, Field

from .maintenance_patches import (
    MaintenanceOperationKind,
    MaintenancePatch,
    MaintenancePatchOperation,
    MaintenancePatchStatus,
    MaintenancePatchValidationIssue,
    MaintenancePatchValidationReport,
    validate_maintenance_patch,
)
from .maintenance_status import graph_status_for_patch
from .models import NamespaceEngines
from .namespaces import WorkspaceNamespaces
from .utils import _temporary_namespace

JsonScalar = str | int | float | bool | None
EngineUnitOfWork = AbstractContextManager[object | None]
MaintenanceEntity = Node | Edge


_IMMUTABLE_RAW_ARTIFACT_KINDS = frozenset(
    {
        "source_document",
        "source_revision",
        "source_readiness",
        "source_map_seed",
        "lane_message",
    }
)


class _StaleMaintenancePatch(Exception):
    def __init__(self, issues: list[MaintenancePatchValidationIssue]) -> None:
        super().__init__("maintenance patch targets changed after proposal")
        self.issues = issues


class _AtomicPatchOperationFailed(Exception):
    def __init__(
        self,
        operation_results: list[MaintenancePatchOperationApplyResult],
        failed: MaintenancePatchOperationApplyResult,
    ) -> None:
        super().__init__(failed.message or "atomic maintenance operation failed")
        self.operation_results = operation_results
        self.failed = failed


@runtime_checkable
class _MaintenanceReadLike(Protocol):
    def get_nodes(
        self,
        ids: list[str] | None = None,
        where: dict[str, object] | None = None,
        resolve_mode: str | None = None,
    ) -> list[Node]: ...

    def get_edges(
        self,
        ids: list[str] | None = None,
        where: dict[str, object] | None = None,
        resolve_mode: str | None = None,
    ) -> list[Edge]: ...


class _MaintenanceEngineLike(Protocol):
    read: _MaintenanceReadLike
    write: WriteLike

    def uow(self) -> EngineUnitOfWork: ...

    def tombstone_node(self, node_id: str, **kw: object) -> bool: ...
    def tombstone_edge(self, edge_id: str, **kw: object) -> bool: ...


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
    expected_revisions: Mapping[str, str | int | None] | None = None,
    protected_raw_node_ids: set[str] | None = None,
    protected_raw_edge_ids: set[str] | None = None,
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
    raw_fact_issues = _immutable_raw_fact_issues(
        engine,
        patch,
        protected_raw_node_ids=protected_raw_node_ids,
        protected_raw_edge_ids=protected_raw_edge_ids,
    )
    if raw_fact_issues:
        validation = MaintenancePatchValidationReport(
            valid=False,
            issues=[*validation.issues, *raw_fact_issues],
        )
    if not validation.valid:
        artifact_id = _emit_patch_artifact(engine, patch, validation, [], MaintenancePatchStatus.REJECTED) if emit_artifact else None
        return MaintenancePatchApplyResult(
            patch_id=patch.patch_id,
            status=MaintenancePatchStatus.REJECTED,
            validation=validation,
            artifact_id=artifact_id,
        )

    if patch.requires_atomic_replacement:
        capability = _atomic_mutation_capability(engine)
        if not capability.supports_atomic_mutation:
            validation = MaintenancePatchValidationReport(
                valid=False,
                issues=[
                    *validation.issues,
                    MaintenancePatchValidationIssue(
                        code="atomic_mutation_required",
                        message=(
                            "replacement patches require a backend transaction; "
                            f"use a ParseView activation path for {capability.mode} "
                            f"backend ({capability.reason})"
                        ),
                    ),
                ],
            )
            artifact_id = (
                _emit_patch_artifact(
                    engine,
                    patch,
                    validation,
                    [],
                    MaintenancePatchStatus.REJECTED,
                )
                if emit_artifact
                else None
            )
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
            stale_issues = _stale_revision_issues(engine, expected_revisions or {})
            if stale_issues:
                raise _StaleMaintenancePatch(stale_issues)
            for operation in patch.operations:
                result = _apply_operation(engine, patch, operation)
                operation_results.append(result)
                if result.status == "failed":
                    if patch.requires_atomic_replacement:
                        raise _AtomicPatchOperationFailed(operation_results[:-1], result)
                    status = MaintenancePatchStatus.NEEDS_REVIEW
        artifact_id = _emit_patch_artifact(engine, patch, validation, operation_results, status) if emit_artifact else None
    except _StaleMaintenancePatch as exc:
        validation = MaintenancePatchValidationReport(valid=False, issues=[*validation.issues, *exc.issues])
        artifact_id = _emit_patch_artifact(engine, patch, validation, operation_results, MaintenancePatchStatus.REJECTED) if emit_artifact else None
        return MaintenancePatchApplyResult(
            patch_id=patch.patch_id,
            status=MaintenancePatchStatus.REJECTED,
            validation=validation,
            operation_results=operation_results,
            artifact_id=artifact_id,
        )
    except _AtomicPatchOperationFailed as exc:
        operation_results = [
            item.model_copy(update={"status": "rolled_back"})
            for item in exc.operation_results
        ]
        operation_results.append(exc.failed)
        status = MaintenancePatchStatus.NEEDS_REVIEW
        artifact_id = (
            _emit_patch_artifact(engine, patch, validation, operation_results, status)
            if emit_artifact
            else None
        )
        return MaintenancePatchApplyResult(
            patch_id=patch.patch_id,
            status=status,
            validation=validation,
            operation_results=operation_results,
            artifact_id=artifact_id,
        )
    except Exception as exc:  # noqa: BLE001 - malformed maintenance operations become review items
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
    expected_revisions: Mapping[str, str | int | None] | None = None,
) -> MaintenancePatchApplyResult:
    """Apply a patch to the graph space implied by its maintenance scope."""

    ns = WorkspaceNamespaces(patch.scope.workspace_id)
    protected_raw_node_ids, protected_raw_edge_ids = _raw_fact_ids_for_workspace(
        engines,
        ns,
        patch,
    )
    if patch.scope.scope_kind == "workspace":
        with _temporary_namespace(engines.kg, ns.curated_kg_space):
            return apply_maintenance_patch(
                engines.kg,
                patch,
                namespace_prefix=namespace_prefix,
                emit_artifact=emit_artifact,
                expected_revisions=expected_revisions,
                protected_raw_node_ids=protected_raw_node_ids,
                protected_raw_edge_ids=protected_raw_edge_ids,
            )

    with _temporary_namespace(engines.conversation, ns.conv_bg):
        scope_issues = _non_workspace_scope_issues(engines.conversation, patch)
        if scope_issues:
            validation = MaintenancePatchValidationReport(valid=False, issues=scope_issues)
            artifact_id = _emit_patch_artifact(
                engines.conversation,
                patch,
                validation,
                [],
                MaintenancePatchStatus.REJECTED,
            ) if emit_artifact else None
            return MaintenancePatchApplyResult(
                patch_id=patch.patch_id,
                status=MaintenancePatchStatus.REJECTED,
                validation=validation,
                artifact_id=artifact_id,
            )
        return apply_maintenance_patch(
            engines.conversation,
            patch,
            namespace_prefix=namespace_prefix,
            emit_artifact=emit_artifact,
            expected_revisions=expected_revisions,
            protected_raw_node_ids=protected_raw_node_ids,
            protected_raw_edge_ids=protected_raw_edge_ids,
        )


def _immutable_raw_fact_issues(
    engine: _MaintenanceEngineLike,
    patch: MaintenancePatch,
    *,
    protected_raw_node_ids: set[str] | None = None,
    protected_raw_edge_ids: set[str] | None = None,
) -> list[MaintenancePatchValidationIssue]:
    """Reject maintenance attempts to rewrite raw source or conversation facts."""

    protected_nodes = set(protected_raw_node_ids or set())
    protected_edges = set(protected_raw_edge_ids or set())
    node_targets = {
        str(operation.tombstone_target_id)
        for operation in patch.operations
        if operation.kind == MaintenanceOperationKind.TOMBSTONE_NODE and operation.tombstone_target_id
    }
    edge_targets = {
        str(operation.tombstone_target_id)
        for operation in patch.operations
        if operation.kind == MaintenanceOperationKind.TOMBSTONE_EDGE and operation.tombstone_target_id
    }
    for entity in _read_entities_by_ids(engine, "node", node_targets):
        if _is_immutable_raw_fact(entity):
            protected_nodes.add(str(entity.id))
    for entity in _read_entities_by_ids(engine, "edge", edge_targets):
        if _is_immutable_raw_fact(entity):
            protected_edges.add(str(entity.id))

    issues: list[MaintenancePatchValidationIssue] = []
    for operation in patch.operations:
        target_id = operation.tombstone_target_id
        if operation.kind == MaintenanceOperationKind.TOMBSTONE_NODE and target_id in protected_nodes:
            issues.append(
                MaintenancePatchValidationIssue(
                    operation_id=operation.operation_id,
                    code="raw_fact_mutation_forbidden",
                    message=(
                        "maintenance cannot tombstone a raw source or conversation fact; "
                        "add a grounded interpretation node and support edge instead"
                    ),
                )
            )
        elif operation.kind == MaintenanceOperationKind.TOMBSTONE_EDGE and target_id in protected_edges:
            issues.append(
                MaintenancePatchValidationIssue(
                    operation_id=operation.operation_id,
                    code="raw_fact_mutation_forbidden",
                    message=(
                        "maintenance cannot tombstone an edge belonging to a raw source or "
                        "conversation fact"
                    ),
                )
            )
    return issues


def _raw_fact_ids_for_workspace(
    engines: NamespaceEngines,
    ns: WorkspaceNamespaces,
    patch: MaintenancePatch,
) -> tuple[set[str], set[str]]:
    """Find immutable targets even when they live outside the patch destination space."""

    node_targets = {
        str(operation.tombstone_target_id)
        for operation in patch.operations
        if operation.kind == MaintenanceOperationKind.TOMBSTONE_NODE and operation.tombstone_target_id
    }
    edge_targets = {
        str(operation.tombstone_target_id)
        for operation in patch.operations
        if operation.kind == MaintenanceOperationKind.TOMBSTONE_EDGE and operation.tombstone_target_id
    }
    protected_nodes: set[str] = set()
    protected_edges: set[str] = set()
    locations = (
        (engines.kg, ns.source_space),
        (engines.conversation, ns.conv_fg),
        (engines.conversation, ns.conv_bg),
    )
    for engine, namespace in locations:
        with _temporary_namespace(engine, namespace):
            for entity in _read_entities_by_ids(engine, "node", node_targets):
                if _is_immutable_raw_fact(entity):
                    protected_nodes.add(str(entity.id))
            for entity in _read_entities_by_ids(engine, "edge", edge_targets):
                if _is_immutable_raw_fact(entity):
                    protected_edges.add(str(entity.id))
    return protected_nodes, protected_edges


def _read_entities_by_ids(
    engine: _MaintenanceEngineLike,
    kind: str,
    ids: set[str],
) -> list[MaintenanceEntity]:
    if not ids:
        return []
    read = getattr(engine, "read", engine)
    getter = getattr(read, "get_nodes" if kind == "node" else "get_edges", None)
    if not callable(getter):
        return []
    try:
        return list(getter(ids=sorted(ids), resolve_mode="include_tombstones"))
    except TypeError:
        return list(getter(ids=sorted(ids)))


def _is_immutable_raw_fact(entity: MaintenanceEntity) -> bool:
    metadata = dict(getattr(entity, "metadata", None) or {})
    artifact_kind = str(metadata.get("artifact_kind") or "").strip().lower()
    return (
        artifact_kind in _IMMUTABLE_RAW_ARTIFACT_KINDS
        or artifact_kind.startswith("source_")
        or str(metadata.get("graph_space") or "").strip().lower() == "source"
        or metadata.get("raw_fact") is True
    )


def _non_workspace_scope_issues(
    engine: _MaintenanceEngineLike,
    patch: MaintenancePatch,
) -> list[MaintenancePatchValidationIssue]:
    """Prevent a scoped patch from mutating entities owned by another scope."""

    if patch.scope.scope_kind == "workspace":
        return []
    metadata_key = "conversation_id" if patch.scope.scope_kind == "conversation" else "thread_id"
    expected_value = getattr(patch.scope, metadata_key)
    referenced_node_ids: set[str] = set()
    referenced_edge_ids: set[str] = set()
    for operation in patch.operations:
        if operation.kind == MaintenanceOperationKind.ADD_EDGE:
            referenced_node_ids.update(
                entity_id
                for entity_id in (operation.from_node_id, operation.to_node_id)
                if entity_id
            )
        elif operation.kind == MaintenanceOperationKind.TOMBSTONE_NODE and operation.tombstone_target_id:
            referenced_node_ids.add(operation.tombstone_target_id)
        elif operation.kind == MaintenanceOperationKind.TOMBSTONE_EDGE and operation.tombstone_target_id:
            referenced_edge_ids.add(operation.tombstone_target_id)

    issues: list[MaintenancePatchValidationIssue] = []
    nodes = {node.id: node for node in engine.read.get_nodes(ids=sorted(referenced_node_ids), resolve_mode="active_only")}
    edges = {edge.id: edge for edge in engine.read.get_edges(ids=sorted(referenced_edge_ids), resolve_mode="active_only")}
    for entity_id, entity in {**nodes, **edges}.items():
        metadata = dict(entity.metadata or {})
        if metadata.get("scope_kind") != patch.scope.scope_kind or metadata.get(metadata_key) != expected_value:
            issues.append(
                MaintenancePatchValidationIssue(
                    code="scope_entity_mismatch",
                    message=(
                        f"{entity_id} does not belong to {patch.scope.scope_kind} scope "
                        f"{expected_value!r}"
                    ),
                )
            )
    return issues


def _stale_revision_issues(
    engine: _MaintenanceEngineLike,
    expected_revisions: Mapping[str, str | int | None],
) -> list[MaintenancePatchValidationIssue]:
    if not expected_revisions:
        return []
    read = getattr(engine, "read", engine)
    node_getter = getattr(read, "get_nodes", None)
    edge_getter = getattr(read, "get_edges", None)
    current: dict[str, str | int | None] = {}
    if callable(node_getter):
        current.update(_entity_revisions(node_getter, list(expected_revisions)))
    if callable(edge_getter):
        current.update(_entity_revisions(edge_getter, list(expected_revisions)))
    return [
        MaintenancePatchValidationIssue(
            code="missing_entity_revision" if expected is None else "stale_entity_revision",
            message=(
                f"target {entity_id!r} has no revision suitable for safe confirmation"
                if expected is None
                else f"target {entity_id!r} changed since the proposal was created"
            ),
        )
        for entity_id, expected in expected_revisions.items()
        if expected is None or entity_id not in current or current[entity_id] != expected
    ]


def _entity_revisions(getter: Callable[..., list[MaintenanceEntity]], ids: list[str]) -> dict[str, str | int | None]:
    try:
        items = getter(ids=ids, resolve_mode="active_only")
    except TypeError:
        items = getter(ids=ids)
    return {
        str(item.id): _revision_from_entity(item)
        for item in items
        if getattr(item, "id", None)
    }


def _revision_from_entity(entity: MaintenanceEntity) -> str | int | None:
    metadata = dict(getattr(entity, "metadata", None) or {})
    for key in ("entity_revision", "revision_id", "revision"):
        value = metadata.get(key)
        if isinstance(value, (str, int)) and value != "":
            return value
    return None


def _engine_uow(engine: _MaintenanceEngineLike) -> EngineUnitOfWork:
    uow = getattr(engine, "uow", None)
    if callable(uow):
        return cast(Callable[[], EngineUnitOfWork], uow)()
    return nullcontext()


def _atomic_mutation_capability(engine: _MaintenanceEngineLike) -> AtomicMutationCapability:
    """Read the backend contract without depending on a concrete backend type."""

    declared = getattr(engine, "atomic_mutation_capability", None)
    if isinstance(declared, AtomicMutationCapability):
        return declared
    return get_atomic_mutation_capability(getattr(engine, "backend", None))


def _read_active_ids(engine: _MaintenanceEngineLike, kind: str) -> set[str]:
    read = getattr(engine, "read", engine)
    if kind == "node":
        getter = getattr(read, "get_nodes", None)
    else:
        getter = getattr(read, "get_edges", None)
    if not callable(getter):
        return set()
    items: list[MaintenanceEntity]
    try:
        items = getter(resolve_mode="active_only")
    except TypeError:
        items = getter()
    return {str(item.id) for item in items if getattr(item, "id", None)}


def _exists(engine: _MaintenanceEngineLike, kind: str, entity_id: str, *, include_tombstones: bool = False) -> bool:
    read = getattr(engine, "read", engine)
    getter = getattr(read, "get_nodes" if kind == "node" else "get_edges", None)
    if not callable(getter):
        return False
    items: list[MaintenanceEntity]
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
    items: list[MaintenanceEntity]
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
    operation: MaintenancePatchOperation,
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


def _node_from_operation(patch: MaintenancePatch, operation: MaintenancePatchOperation) -> Node:
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


def _edge_from_operation(patch: MaintenancePatch, operation: MaintenancePatchOperation) -> Edge:
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


def _operation_metadata(patch: MaintenancePatch, operation: MaintenancePatchOperation) -> dict[str, JsonScalar]:
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
        "node_type": operation.node_type,
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


def _source_document_id(operation: MaintenancePatchOperation) -> str:
    provenance = operation.provenance
    if provenance and provenance.source_document_id:
        return provenance.source_document_id
    return "maintenance"


def _span_from_operation(operation: MaintenancePatchOperation) -> Span:
    provenance = operation.provenance
    pointer: dict[str, str | int | float | bool | None] = {}
    if provenance and provenance.source_pointers:
        pointer = dict(
            next(
                (
                    item
                    for item in provenance.source_pointers
                    if source_pointer_has_character_span(item)
                ),
                provenance.source_pointers[0],
            )
        )
    doc_id = str(pointer.get("doc_id") or _source_document_id(operation))
    verified_span = False
    if source_pointer_has_character_span(pointer):
        validated_pointer = validate_source_pointer(
            pointer,
            end_mode="exclusive",
            require_source_cluster=False,
            require_source_text=False,
            require_parent_containment=False,
            require_text_match=False,
        )
        start_char = validated_pointer.start_char
        end_char = validated_pointer.end_char
        excerpt = validated_pointer.text or ""
        verified_span = True
    else:
        start_char = 0
        end_char = 1
        excerpt = ""
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
        verification=MentionVerification(
            method="system",
            is_verified=verified_span,
            score=provenance.confidence if provenance and verified_span else 0.0,
            notes="maintenance patch character span" if verified_span else "maintenance patch document-level provenance",
        ),
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
