from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MaintenanceIntent(StrEnum):
    SEED_DOCUMENT = "seed_document"
    SPLIT_NODE = "split_node"
    MERGE_NODES = "merge_nodes"
    CORRECT_FACT = "correct_fact"
    DERIVE_SUMMARY = "derive_summary"
    DERIVE_ENTITY = "derive_entity"
    DERIVE_CROSSLINK_CANDIDATE = "derive_crosslink_candidate"
    ADD_CROSSLINK = "add_crosslink"
    RETRACT_CROSSLINK = "retract_crosslink"
    REFRESH_SUMMARY = "refresh_summary"
    PROMOTE_CANDIDATE = "promote_candidate"
    RETRACT_PROMOTION = "retract_promotion"
    DISTILL_TO_WISDOM = "distill_to_wisdom"
    REQUEST_REVIEW = "request_review"


class MaintenanceOperationKind(StrEnum):
    ADD_NODE = "ADD_NODE"
    ADD_EDGE = "ADD_EDGE"
    TOMBSTONE_NODE = "TOMBSTONE_NODE"
    TOMBSTONE_EDGE = "TOMBSTONE_EDGE"
    REQUEST_REVIEW = "REQUEST_REVIEW"
    NOOP = "NOOP"


class MaintenancePatchStatus(StrEnum):
    PROPOSED = "proposed"
    VALIDATED = "validated"
    PARTIALLY_ACCEPTED = "partially_accepted"
    APPLIED = "applied"
    REJECTED = "rejected"
    NEEDS_REVIEW = "needs_review"
    RETRACTED = "retracted"


class MaintenanceScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: str
    scope_kind: Literal["workspace", "conversation", "thread"] = "workspace"
    conversation_id: str | None = None
    thread_id: str | None = None

    @model_validator(mode="after")
    def _scope_ids_match_kind(self) -> "MaintenanceScope":
        if self.scope_kind == "conversation" and not self.conversation_id:
            raise ValueError("conversation scope requires conversation_id")
        if self.scope_kind == "thread" and not self.thread_id:
            raise ValueError("thread scope requires thread_id")
        return self


class MaintenanceProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_document_id: str | None = None
    source_span_ids: list[str] = Field(default_factory=list)
    source_pointers: list[dict[str, str | int | float | bool | None]] = Field(default_factory=list)
    maintenance_run_id: str
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _requires_grounding(self) -> "MaintenanceProvenance":
        if not self.source_document_id and not self.source_span_ids and not self.source_pointers:
            raise ValueError("provenance requires source_document_id, source_span_ids, or source_pointers")
        return self


class MaintenancePatchOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str
    kind: MaintenanceOperationKind
    target_id: str | None = None
    node_id: str | None = None
    edge_id: str | None = None
    from_node_id: str | None = None
    to_node_id: str | None = None
    relation: str | None = None
    label: str | None = None
    node_type: str | None = None
    properties: dict[str, str | int | float | bool | None] = Field(default_factory=dict)
    supersedes_ids: list[str] = Field(default_factory=list)
    provenance: MaintenanceProvenance | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def _shape_matches_kind(self) -> "MaintenancePatchOperation":
        if self.kind == MaintenanceOperationKind.ADD_NODE:
            if not self.node_id:
                raise ValueError("ADD_NODE requires node_id")
        elif self.kind == MaintenanceOperationKind.ADD_EDGE:
            if not self.edge_id or not self.from_node_id or not self.to_node_id or not self.relation:
                raise ValueError("ADD_EDGE requires edge_id, from_node_id, to_node_id, and relation")
        elif self.kind == MaintenanceOperationKind.TOMBSTONE_NODE:
            if not (self.target_id or self.node_id):
                raise ValueError("TOMBSTONE_NODE requires target_id or node_id")
        elif self.kind == MaintenanceOperationKind.TOMBSTONE_EDGE:
            if not (self.target_id or self.edge_id):
                raise ValueError("TOMBSTONE_EDGE requires target_id or edge_id")
        elif self.kind == MaintenanceOperationKind.REQUEST_REVIEW:
            if not self.reason:
                raise ValueError("REQUEST_REVIEW requires reason")
        return self

    @property
    def created_node_id(self) -> str | None:
        return self.node_id if self.kind == MaintenanceOperationKind.ADD_NODE else None

    @property
    def created_edge_id(self) -> str | None:
        return self.edge_id if self.kind == MaintenanceOperationKind.ADD_EDGE else None

    @property
    def tombstone_target_id(self) -> str | None:
        if self.kind == MaintenanceOperationKind.TOMBSTONE_NODE:
            return self.target_id or self.node_id
        if self.kind == MaintenanceOperationKind.TOMBSTONE_EDGE:
            return self.target_id or self.edge_id
        return None


class MaintenancePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patch_id: str
    intent: MaintenanceIntent
    scope: MaintenanceScope
    operations: list[MaintenancePatchOperation]
    status: MaintenancePatchStatus = MaintenancePatchStatus.PROPOSED
    rationale: str | None = None

    @model_validator(mode="after")
    def _requires_operations(self) -> "MaintenancePatch":
        if not self.operations:
            raise ValueError("MaintenancePatch requires at least one operation")
        return self


class MaintenancePatchValidationIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_id: str | None = None
    code: str
    message: str


class MaintenancePatchValidationReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    issues: list[MaintenancePatchValidationIssue] = Field(default_factory=list)


def _evidence_document_ids(operation: MaintenancePatchOperation) -> set[str]:
    provenance = operation.provenance
    if provenance is None:
        return set()
    doc_ids: set[str] = set()
    if provenance.source_document_id:
        doc_ids.add(provenance.source_document_id)
    for pointer in provenance.source_pointers:
        doc_id = pointer.get("doc_id") or pointer.get("source_document_id")
        if isinstance(doc_id, str) and doc_id.strip():
            doc_ids.add(doc_id.strip())
    for key in ("left_source_document_id", "right_source_document_id"):
        value = operation.properties.get(key)
        if isinstance(value, str) and value.strip():
            doc_ids.add(value.strip())
    return doc_ids


def _is_crosslink_intent(intent: MaintenanceIntent) -> bool:
    return intent in {
        MaintenanceIntent.DERIVE_CROSSLINK_CANDIDATE,
        MaintenanceIntent.ADD_CROSSLINK,
        MaintenanceIntent.RETRACT_CROSSLINK,
    }


def validate_maintenance_patch(
    patch: MaintenancePatch,
    *,
    active_node_ids: set[str] | None = None,
    active_edge_ids: set[str] | None = None,
    namespace_prefix: str | None = None,
) -> MaintenancePatchValidationReport:
    active_node_ids = set(active_node_ids or set())
    active_edge_ids = set(active_edge_ids or set())
    created_nodes = {op.created_node_id for op in patch.operations if op.created_node_id}
    created_edges = {op.created_edge_id for op in patch.operations if op.created_edge_id}
    active_or_created_nodes = active_node_ids | created_nodes
    active_or_created_ids = active_node_ids | active_edge_ids | created_nodes | created_edges
    tombstoned_ids = {
        op.tombstone_target_id
        for op in patch.operations
        if op.kind
        in {
            MaintenanceOperationKind.TOMBSTONE_NODE,
            MaintenanceOperationKind.TOMBSTONE_EDGE,
        }
        and op.tombstone_target_id
    }
    issues: list[MaintenancePatchValidationIssue] = []

    seen_operation_ids: set[str] = set()
    seen_created_ids: set[str] = set()
    for operation in patch.operations:
        if operation.operation_id in seen_operation_ids:
            issues.append(
                MaintenancePatchValidationIssue(
                    operation_id=operation.operation_id,
                    code="duplicate_operation_id",
                    message="operation_id must be unique within a patch",
                )
            )
        seen_operation_ids.add(operation.operation_id)

        created_id = operation.created_node_id or operation.created_edge_id
        if created_id:
            if created_id in seen_created_ids:
                issues.append(
                    MaintenancePatchValidationIssue(
                        operation_id=operation.operation_id,
                        code="duplicate_created_id",
                        message="created node or edge ids must be unique within a patch",
                    )
                )
            seen_created_ids.add(created_id)
            if namespace_prefix and not created_id.startswith(namespace_prefix):
                issues.append(
                    MaintenancePatchValidationIssue(
                        operation_id=operation.operation_id,
                        code="namespace_scope_violation",
                        message=f"created id must start with namespace prefix {namespace_prefix!r}",
                    )
                )

        if operation.kind in {
            MaintenanceOperationKind.ADD_NODE,
            MaintenanceOperationKind.ADD_EDGE,
            MaintenanceOperationKind.TOMBSTONE_NODE,
            MaintenanceOperationKind.TOMBSTONE_EDGE,
        } and operation.provenance is None:
            issues.append(
                MaintenancePatchValidationIssue(
                    operation_id=operation.operation_id,
                    code="missing_provenance",
                    message="graph-changing operations require provenance",
                )
            )

        if operation.kind == MaintenanceOperationKind.ADD_EDGE:
            if str(operation.from_node_id) not in active_or_created_nodes:
                issues.append(
                    MaintenancePatchValidationIssue(
                        operation_id=operation.operation_id,
                        code="missing_edge_endpoint",
                        message="ADD_EDGE from_node_id must reference an active or same-patch node",
                    )
                )
            if str(operation.to_node_id) not in active_or_created_nodes:
                issues.append(
                    MaintenancePatchValidationIssue(
                        operation_id=operation.operation_id,
                        code="missing_edge_endpoint",
                        message="ADD_EDGE to_node_id must reference an active or same-patch node",
                    )
                )

            if _is_crosslink_intent(patch.intent):
                crosslink_status = operation.properties.get("crosslink_status")
                expected_status = (
                    "candidate"
                    if patch.intent == MaintenanceIntent.DERIVE_CROSSLINK_CANDIDATE
                    else "accepted"
                )
                if crosslink_status != expected_status:
                    issues.append(
                        MaintenancePatchValidationIssue(
                            operation_id=operation.operation_id,
                            code="invalid_crosslink_status",
                            message=f"cross-link ADD_EDGE must declare crosslink_status={expected_status!r}",
                        )
                    )
                if len(_evidence_document_ids(operation)) < 2:
                    issues.append(
                        MaintenancePatchValidationIssue(
                            operation_id=operation.operation_id,
                            code="missing_crosslink_two_sided_evidence",
                            message="cross-link ADD_EDGE requires source evidence from both linked documents",
                        )
                    )
                confidence = operation.provenance.confidence if operation.provenance else 0.0
                min_confidence = 0.5 if expected_status == "candidate" else 0.8
                if confidence < min_confidence:
                    issues.append(
                        MaintenancePatchValidationIssue(
                            operation_id=operation.operation_id,
                            code="crosslink_confidence_below_threshold",
                            message=f"cross-link confidence must be at least {min_confidence}",
                        )
                    )

        if patch.intent == MaintenanceIntent.RETRACT_CROSSLINK and operation.kind == MaintenanceOperationKind.TOMBSTONE_EDGE:
            if operation.reason is None or not operation.reason.strip():
                issues.append(
                    MaintenancePatchValidationIssue(
                        operation_id=operation.operation_id,
                        code="missing_retraction_reason",
                        message="cross-link retractions must record why the edge is stale or incorrect",
                    )
                )

        if operation.kind == MaintenanceOperationKind.TOMBSTONE_NODE:
            target_id = operation.tombstone_target_id
            if target_id not in active_node_ids:
                issues.append(
                    MaintenancePatchValidationIssue(
                        operation_id=operation.operation_id,
                        code="missing_tombstone_target",
                        message="TOMBSTONE_NODE target must reference an active node",
                    )
                )

        if operation.kind == MaintenanceOperationKind.TOMBSTONE_EDGE:
            target_id = operation.tombstone_target_id
            if target_id not in active_edge_ids:
                issues.append(
                    MaintenancePatchValidationIssue(
                        operation_id=operation.operation_id,
                        code="missing_tombstone_target",
                        message="TOMBSTONE_EDGE target must reference an active edge",
                    )
                )

        if operation.supersedes_ids and operation.kind not in {
            MaintenanceOperationKind.ADD_NODE,
            MaintenanceOperationKind.ADD_EDGE,
        }:
            issues.append(
                MaintenancePatchValidationIssue(
                    operation_id=operation.operation_id,
                    code="invalid_supersession",
                    message="supersedes_ids must be declared on replacement ADD_NODE or ADD_EDGE operations",
                )
            )
        for superseded_id in operation.supersedes_ids:
            if superseded_id not in active_or_created_ids:
                issues.append(
                    MaintenancePatchValidationIssue(
                        operation_id=operation.operation_id,
                        code="missing_supersession_target",
                        message="supersedes_ids must reference an active or same-patch graph object",
                    )
                )
            if superseded_id not in tombstoned_ids:
                issues.append(
                    MaintenancePatchValidationIssue(
                        operation_id=operation.operation_id,
                        code="unpaired_supersession",
                        message="replacement operations must be paired with a tombstone for each superseded id",
                    )
                )

    return MaintenancePatchValidationReport(valid=not issues, issues=issues)


def validate_maintenance_patch_payload(payload: dict[str, object], **kwargs: object) -> MaintenancePatchValidationReport:
    patch = MaintenancePatch.model_validate(payload)
    return validate_maintenance_patch(patch, **kwargs)
