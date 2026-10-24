from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager, nullcontext
from typing import Protocol, cast

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
from kogwistar.json_types import JsonObject, JsonValue
from kogwistar.server.auth_middleware import can_access_security_scope
from kogwistar.typing_interfaces import ReadLike, WriteLike
from kogwistar.utils import source_pointer_has_character_span, validate_source_pointer
from pydantic import BaseModel, ConfigDict, Field

from ..configuration.workspace import WorkspaceNamespaces
from ..models import NamespaceEngines
from ..utils import _background_namespace, _temporary_namespace
from .maintenance_patches import (
    MaintenanceIntent,
    MaintenanceOperationKind,
    MaintenancePatch,
    MaintenancePatchOperation,
    MaintenancePatchStatus,
    MaintenancePatchValidationIssue,
    MaintenancePatchValidationReport,
    validate_maintenance_patch,
)
from .maintenance_status import graph_status_for_patch

JsonScalar = str | int | float | bool | None
EngineUnitOfWork = AbstractContextManager[object | None]
MaintenanceEntity = Node | Edge


class EntityGetter(Protocol):
    """Compatibility surface for core read APIs used by patch validation."""

    def __call__(
        self,
        *,
        ids: Sequence[str] | None = None,
        resolve_mode: str | None = None,
    ) -> Sequence[MaintenanceEntity]: ...


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


class _AuthorizationChanged(Exception):
    def __init__(self, issues: list[MaintenancePatchValidationIssue]) -> None:
        super().__init__("maintenance authorization changed before mutation")
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


class _MaintenanceEngineLike(Protocol):
    @property
    def read(self) -> ReadLike: ...

    @property
    def write(self) -> WriteLike: ...

    def uow(self) -> EngineUnitOfWork: ...

    def tombstone_node(self, node_id: str, **kw: JsonValue) -> bool: ...
    def tombstone_edge(self, edge_id: str, **kw: JsonValue) -> bool: ...


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
    acl_issues = _acl_issues_for_patch(engine, patch)
    if acl_issues:
        validation = MaintenancePatchValidationReport(
            valid=False,
            issues=[*validation.issues, *acl_issues],
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
    derived_link_issues = _derived_crosslink_retraction_issues(engine, patch)
    if derived_link_issues:
        validation = MaintenancePatchValidationReport(
            valid=False,
            issues=[*validation.issues, *derived_link_issues],
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
            _lock_maintenance_identities(engine, patch)
            authorization_issues = _acl_issues_for_patch(engine, patch)
            if authorization_issues:
                raise _AuthorizationChanged(authorization_issues)
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
    except _AuthorizationChanged as exc:
        validation = MaintenancePatchValidationReport(
            valid=False,
            issues=[*validation.issues, *exc.issues],
        )
        artifact_id = (
            _emit_patch_artifact(
                engine,
                patch,
                validation,
                operation_results,
                MaintenancePatchStatus.REJECTED,
            )
            if emit_artifact
            else None
        )
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

    with _background_namespace(engines.conversation, ns.conv_bg):
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


def _acl_issues_for_patch(
    engine: _MaintenanceEngineLike,
    patch: MaintenancePatch,
) -> list[MaintenancePatchValidationIssue]:
    """Re-check entity ACLs at the final mutation boundary.

    Selection and review happen earlier than application.  A scope can be
    revoked in between, so workspace membership alone is not sufficient for
    existing targets.  Crosslink code has a stricter domain-specific check;
    this shared check covers ordinary maintenance patches as well.
    """

    node_ids: set[str] = set()
    edge_ids: set[str] = set()
    created_node_ids = {
        str(operation.node_id)
        for operation in patch.operations
        if operation.kind == MaintenanceOperationKind.ADD_NODE and operation.node_id
    }
    for operation in patch.operations:
        if operation.kind == MaintenanceOperationKind.ADD_EDGE:
            node_ids.update(
                value
                for value in (operation.from_node_id, operation.to_node_id)
                if value
            )
        elif operation.kind == MaintenanceOperationKind.TOMBSTONE_NODE:
            if operation.tombstone_target_id:
                node_ids.add(operation.tombstone_target_id)
        elif (
            operation.kind == MaintenanceOperationKind.TOMBSTONE_EDGE
            and operation.tombstone_target_id
        ):
            edge_ids.add(operation.tombstone_target_id)

    issues: list[MaintenancePatchValidationIssue] = []
    # An edge may point at a node created by this same atomic patch. Before
    # exempting it from the ACL check, inspect the raw store: graph writes are
    # upserts, so a hidden existing ID must never masquerade as a new node.
    raw_existing = _raw_existing_entity_ids(engine, "node", created_node_ids)
    if raw_existing is None and created_node_ids:
        issues.append(
            MaintenancePatchValidationIssue(
                code="acl_new_target_unverifiable",
                message="cannot prove maintenance ADD_NODE targets are new",
            )
        )
        raw_existing_node_ids = set()
    elif raw_existing is None:
        raw_existing_node_ids = set()
    else:
        raw_existing_node_ids = raw_existing
    # Every pre-existing endpoint remains subject to the final visibility
    # check below. Only a raw-store-confirmed new ID may be used immediately.
    existing_node_ids = (node_ids - created_node_ids) | raw_existing_node_ids
    visible_nodes = {
        str(entity.id): entity
        for entity in _read_entities_by_ids(engine, "node", existing_node_ids)
    }
    visible_edges = {
        str(entity.id): entity
        for entity in _read_entities_by_ids(engine, "edge", edge_ids)
    }
    # Core lifecycle writes deliberately operate by identifier and do not run an
    # ACL decision themselves. A filtered read must therefore fail closed here:
    # a caller-supplied active-id set cannot turn an invisible target writable.
    for target_id in sorted(existing_node_ids - set(visible_nodes)):
        issues.append(
            MaintenancePatchValidationIssue(
                code="acl_target_unavailable",
                message="maintenance target is not available to the current principal",
            )
        )
    for target_id in sorted(edge_ids - set(visible_edges)):
        issues.append(
            MaintenancePatchValidationIssue(
                code="acl_target_unavailable",
                message="maintenance target is not available to the current principal",
            )
        )
    for entity in (*visible_nodes.values(), *visible_edges.values()):
        metadata = dict(getattr(entity, "metadata", None) or {})
        scope = str(
            metadata.get("acl_scope") or metadata.get("security_scope") or ""
        ).strip()
        if scope and not can_access_security_scope(scope):
            issues.append(
                MaintenancePatchValidationIssue(
                    code="acl_scope_denied",
                    message=f"maintenance target {entity.id!r} is outside the current security scope",
                )
            )
    if bool(getattr(engine, "acl_enabled", False)):
        for operation in patch.operations:
            if operation.kind != MaintenanceOperationKind.ADD_EDGE or patch.intent not in {
                MaintenanceIntent.DERIVE_CROSSLINK_CANDIDATE,
                MaintenanceIntent.ADD_CROSSLINK,
            }:
                continue
            if not operation.from_node_id or not operation.to_node_id:
                continue
            if not {operation.from_node_id, operation.to_node_id}.issubset(visible_nodes):
                continue
            _metadata, policy_issues = _crosslink_acl_metadata(engine, operation)
            issues.extend(policy_issues)
    return issues


def _crosslink_acl_metadata(
    engine: _MaintenanceEngineLike,
    operation: MaintenancePatchOperation,
) -> tuple[dict[str, JsonValue], list[MaintenancePatchValidationIssue]]:
    """Derive edge ACL only when both endpoint policies are identical.

    The current graph ACL model cannot represent arbitrary intersections of
    shared principals, groups, and security scopes. Requiring exact policy
    parity is conservative and avoids silently broadening or hiding a derived
    relation under the worker's default identity.
    """

    if not bool(getattr(engine, "acl_enabled", False)):
        return {}, []
    acl_graph = getattr(engine, "acl_graph", None)
    latest_record = getattr(acl_graph, "latest_record", None)
    if not callable(latest_record):
        return {}, [
            MaintenancePatchValidationIssue(
                operation_id=operation.operation_id,
                code="acl_policy_unavailable",
                message="cannot resolve endpoint ACL policies for derived cross-link",
            )
        ]
    endpoint_ids = (operation.from_node_id, operation.to_node_id)
    records = [
        latest_record(
            grain="node",
            truth_graph=str(getattr(engine, "kg_graph_type", "")),
            entity_id=str(endpoint_id),
        )
        for endpoint_id in endpoint_ids
        if endpoint_id
    ]
    if len(records) != 2 or any(record is None for record in records):
        return {}, [
            MaintenancePatchValidationIssue(
                operation_id=operation.operation_id,
                code="acl_policy_unavailable",
                message="both cross-link endpoints must have current ACL policies",
            )
        ]

    def signature(record: object) -> tuple[object, ...]:
        return (
            getattr(record, "mode", None),
            getattr(record, "owner_id", None),
            getattr(record, "security_scope", None),
            tuple(sorted(getattr(record, "shared_with_principals", ()) or ())),
            tuple(sorted(getattr(record, "shared_with_groups", ()) or ())),
        )

    first = records[0]
    if signature(first) != signature(records[1]):
        return {}, [
            MaintenancePatchValidationIssue(
                operation_id=operation.operation_id,
                code="acl_policy_incompatible",
                message=(
                    "cross-link endpoints have different ACL policies; an edge ACL "
                    "intersection is not representable by the current graph policy model"
                ),
            )
        ]
    assert first is not None
    metadata: dict[str, JsonValue] = {
        "acl_mode": str(first.mode),
        "shared_with_agents": list(getattr(first, "shared_with_principals", ()) or ()),
        "shared_with_groups": list(getattr(first, "shared_with_groups", ()) or ()),
    }
    owner_id = getattr(first, "owner_id", None)
    security_scope = getattr(first, "security_scope", None)
    if isinstance(owner_id, str) and owner_id:
        metadata["owner_id"] = owner_id
    if isinstance(security_scope, str) and security_scope:
        metadata["security_scope"] = security_scope
    return metadata, []


def _lock_maintenance_identities(
    engine: _MaintenanceEngineLike, patch: MaintenancePatch
) -> None:
    """Serialize ACL reads/mutations against the canonical identity lock."""

    metadata_store = getattr(engine, "meta_sqlite", None)
    lock_identity = getattr(metadata_store, "lock_entity_identity", None)
    if not callable(lock_identity):
        return
    namespace = str(getattr(engine, "namespace", "default"))
    identities: set[tuple[str, str]] = set()
    for operation in patch.operations:
        if operation.kind == MaintenanceOperationKind.ADD_NODE and operation.node_id:
            identities.add(("node", str(operation.node_id)))
        elif operation.kind == MaintenanceOperationKind.ADD_EDGE and operation.edge_id:
            identities.add(("edge", str(operation.edge_id)))
            for endpoint_id in (operation.from_node_id, operation.to_node_id):
                if endpoint_id:
                    identities.add(("node", str(endpoint_id)))
        elif operation.kind == MaintenanceOperationKind.TOMBSTONE_NODE and operation.tombstone_target_id:
            identities.add(("node", str(operation.tombstone_target_id)))
        elif operation.kind == MaintenanceOperationKind.TOMBSTONE_EDGE and operation.tombstone_target_id:
            identities.add(("edge", str(operation.tombstone_target_id)))
    for entity_kind, entity_id in sorted(identities):
        lock_identity(
            namespace=namespace,
            entity_kind=entity_kind,
            entity_id=entity_id,
        )


def _derived_crosslink_retraction_issues(
    engine: _MaintenanceEngineLike,
    patch: MaintenancePatch,
) -> list[MaintenancePatchValidationIssue]:
    """Allow retraction only for edges explicitly created as derived links."""

    if patch.intent.value != "retract_crosslink":
        return []
    target_ids = {
        str(operation.tombstone_target_id)
        for operation in patch.operations
        if operation.kind == MaintenanceOperationKind.TOMBSTONE_EDGE
        and operation.tombstone_target_id
    }
    if not target_ids:
        return []
    try:
        edges = _read_entities_by_ids(engine, "edge", target_ids)
    except Exception:
        edges = []
    by_id = {str(edge.id): edge for edge in edges}
    issues: list[MaintenancePatchValidationIssue] = []
    for target_id in sorted(target_ids):
        edge = by_id.get(target_id)
        metadata = dict(getattr(edge, "metadata", None) or {}) if edge is not None else {}
        status = str(metadata.get("crosslink_status") or "").strip().lower()
        source_native = bool(metadata.get("source_native")) or str(
            metadata.get("edge_kind") or ""
        ).strip().lower() in {"source_native", "has_child", "source_map"}
        if edge is None or source_native or status not in {
            "candidate",
            "accepted",
            "stale",
            "needs_revalidation",
        }:
            issues.append(
                MaintenancePatchValidationIssue(
                    code="derived_crosslink_required",
                    message=(
                        f"crosslink retraction may target only a derived crosslink edge; "
                        f"{target_id!r} is source-native, unknown, or lacks crosslink status"
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
    getter = cast(
        EntityGetter | None,
        getattr(read, "get_nodes" if kind == "node" else "get_edges", None),
    )
    if not callable(getter):
        return []
    try:
        return list(getter(ids=sorted(ids), resolve_mode="include_tombstones"))
    except TypeError:
        return list(getter(ids=sorted(ids)))


def _raw_existing_entity_ids(
    engine: _MaintenanceEngineLike,
    kind: str,
    ids: set[str],
) -> set[str] | None:
    """Return raw-store IDs without treating an ACL-filtered absence as new."""

    if not ids:
        return set()
    raw_read = getattr(engine, "raw_read", None)
    getter = cast(
        EntityGetter | None,
        getattr(raw_read, "get_nodes" if kind == "node" else "get_edges", None),
    )
    if not callable(getter):
        return None
    try:
        items = getter(ids=sorted(ids), resolve_mode="include_tombstones")
    except TypeError:
        items = getter(ids=sorted(ids))
    return {str(item.id) for item in items if getattr(item, "id", None)}


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
    node_getter = cast(EntityGetter | None, getattr(read, "get_nodes", None))
    edge_getter = cast(EntityGetter | None, getattr(read, "get_edges", None))
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


def _entity_revisions(
    getter: EntityGetter, ids: list[str]
) -> dict[str, str | int | None]:
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
        getter = cast(EntityGetter | None, getattr(read, "get_nodes", None))
    else:
        getter = cast(EntityGetter | None, getattr(read, "get_edges", None))
    if not callable(getter):
        return set()
    items: Sequence[MaintenanceEntity]
    try:
        items = getter(resolve_mode="active_only")
    except TypeError:
        items = getter()
    return {str(item.id) for item in items if getattr(item, "id", None)}


def _exists(engine: _MaintenanceEngineLike, kind: str, entity_id: str, *, include_tombstones: bool = False) -> bool:
    read = getattr(engine, "read", engine)
    getter = cast(
        EntityGetter | None,
        getattr(read, "get_nodes" if kind == "node" else "get_edges", None),
    )
    if not callable(getter):
        return False
    items: Sequence[MaintenanceEntity]
    try:
        items = getter(ids=[entity_id], resolve_mode="include_tombstones" if include_tombstones else "active_only")
    except TypeError:
        items = getter(ids=[entity_id])
    return any(str(getattr(item, "id", "")) == entity_id for item in items)


def _is_tombstoned(engine: _MaintenanceEngineLike, kind: str, entity_id: str) -> bool:
    read = getattr(engine, "read", engine)
    getter = cast(
        EntityGetter | None,
        getattr(read, "get_nodes" if kind == "node" else "get_edges", None),
    )
    if not callable(getter):
        return False
    items: Sequence[MaintenanceEntity]
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
        creator = getattr(_write(engine), "create_node_if_absent", None)
        if callable(creator):
            outcome = creator(_node_from_operation(patch, operation))
            outcome_status = str(getattr(outcome, "status", ""))
            if outcome_status == "created":
                return MaintenancePatchOperationApplyResult(
                    operation_id=operation.operation_id,
                    kind=operation.kind,
                    status="applied",
                    entity_id=node_id,
                )
            if outcome_status == "already_exists":
                return MaintenancePatchOperationApplyResult(
                    operation_id=operation.operation_id,
                    kind=operation.kind,
                    status="skipped",
                    entity_id=node_id,
                    message="canonical node already exists with the same payload",
                )
            return MaintenancePatchOperationApplyResult(
                operation_id=operation.operation_id,
                kind=operation.kind,
                status="failed",
                entity_id=node_id,
                message=str(getattr(outcome, "message", None) or "canonical node identity conflict"),
            )
        if _exists(engine, "node", node_id, include_tombstones=True):
            return MaintenancePatchOperationApplyResult(operation_id=operation.operation_id, kind=operation.kind, status="skipped", entity_id=node_id)
        _write(engine).add_node(_node_from_operation(patch, operation))
        return MaintenancePatchOperationApplyResult(operation_id=operation.operation_id, kind=operation.kind, status="applied", entity_id=node_id)
    if operation.kind == MaintenanceOperationKind.ADD_EDGE:
        edge_id = str(operation.edge_id)
        if _exists(engine, "edge", edge_id, include_tombstones=True):
            return MaintenancePatchOperationApplyResult(operation_id=operation.operation_id, kind=operation.kind, status="skipped", entity_id=edge_id)
        acl_metadata, acl_issues = _crosslink_acl_metadata(engine, operation)
        if acl_issues:
            return MaintenancePatchOperationApplyResult(
                operation_id=operation.operation_id,
                kind=operation.kind,
                status="failed",
                entity_id=edge_id,
                message=acl_issues[0].code,
            )
        _write(engine).add_edge(_edge_from_operation(patch, operation, acl_metadata))
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
    return cast(WriteLike, getattr(engine, "write", engine))


def _node_from_operation(patch: MaintenancePatch, operation: MaintenancePatchOperation) -> Node:
    metadata = _operation_metadata(patch, operation)
    return Node(
        id=operation.node_id,
        label=str(operation.label or operation.node_id),
        type="entity",
        summary=str(operation.reason or operation.label or operation.node_id),
        domain_id=None,
        canonical_entity_id=None,
        doc_id=_source_document_id(operation),
        embedding=None,
        level_from_root=None,
        properties=operation.properties or None,
        mentions=[Grounding(spans=[_span_from_operation(operation)])],
        metadata=cast(JsonObject, metadata),
    )


def _edge_from_operation(
    patch: MaintenancePatch,
    operation: MaintenancePatchOperation,
    acl_metadata: Mapping[str, JsonValue] | None = None,
) -> Edge:
    metadata = _operation_metadata(patch, operation)
    if acl_metadata:
        metadata.update(acl_metadata)
    spans = (
        _spans_from_operation(operation)
        if patch.intent
        in {
            MaintenanceIntent.DERIVE_CROSSLINK_CANDIDATE,
            MaintenanceIntent.ADD_CROSSLINK,
        }
        else [_span_from_operation(operation)]
    )
    return Edge(
        id=operation.edge_id,
        label=str(operation.label or operation.relation or operation.edge_id),
        type="relationship",
        summary=str(operation.reason or operation.label or operation.relation or operation.edge_id),
        domain_id=None,
        canonical_entity_id=None,
        doc_id=_source_document_id(operation),
        embedding=None,
        source_ids=[str(operation.from_node_id)],
        target_ids=[str(operation.to_node_id)],
        relation=str(operation.relation),
        source_edge_ids=[],
        target_edge_ids=[],
        properties=operation.properties or None,
        mentions=[Grounding(spans=spans)],
        metadata=cast(JsonObject, metadata),
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
    return _span_from_pointer(operation, pointer)


def _spans_from_operation(operation: MaintenancePatchOperation) -> list[Span]:
    provenance = operation.provenance
    pointers = (
        [
            dict(pointer)
            for pointer in provenance.source_pointers
            if source_pointer_has_character_span(pointer)
        ]
        if provenance is not None
        else []
    )
    if not pointers:
        return [_span_from_operation(operation)]
    return [_span_from_pointer(operation, pointer) for pointer in pointers]


def _span_from_pointer(
    operation: MaintenancePatchOperation,
    pointer: dict[str, str | int | float | bool | None],
) -> Span:
    provenance = operation.provenance
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
        chunk_id=None,
        source_cluster_id=None,
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
        domain_id=None,
        canonical_entity_id=None,
        properties={},
        embedding=None,
        level_from_root=None,
        mentions=[Grounding(spans=[Span.from_dummy_for_workflow(patch.patch_id)])],
        metadata={key: value for key, value in metadata.items() if value not in (None, "")},
    )
    _write(engine).add_node(node)
    return artifact_id
