"""Bounded, review-first cockpit planning for the interactive workbench.

The model never receives a graph writer.  It may request one small read action
at a time and return a grounded ``MaintenancePatch`` proposal.  The host keeps
the proposal non-authoritative until a user explicitly confirms it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .maintenance_patches import MaintenancePatch, MaintenancePatchOperation, validate_maintenance_patch
from .semantic_lens import SemanticLensRequest, SemanticLensSnapshot, validate_edit_proposal


CockpitActionKind = Literal[
    "answer",
    "no_change",
    "request_clarification",
    "resolve_lens",
    "inspect_evidence",
    "query_history",
    "propose_patch",
]

MAX_COCKPIT_PATCH_OPERATIONS = 12


class CockpitMaintenancePatch(MaintenancePatch):
    """Narrow the general maintenance contract to one reviewable cockpit turn."""

    operations: list[MaintenancePatchOperation] = Field(
        min_length=1,
        max_length=MAX_COCKPIT_PATCH_OPERATIONS,
    )


class CockpitAction(BaseModel):
    """One model-selected action; exactly one bounded action is processed per turn."""

    model_config = ConfigDict(extra="forbid")

    kind: CockpitActionKind
    answer: str | None = None
    query_text: str | None = Field(default=None, max_length=500)
    entity_ids: list[str] = Field(default_factory=list, max_length=12)
    patch: CockpitMaintenancePatch | None = None
    cited_entity_ids: list[str] = Field(default_factory=list, max_length=24)
    rationale: str = Field(default="", max_length=2_000)

    @field_validator("patch", mode="before")
    @classmethod
    def _coerce_general_patch(cls, value: object) -> object:
        if isinstance(value, MaintenancePatch):
            return value.model_dump(mode="python")
        return value

    @model_validator(mode="after")
    def _shape_matches_action(self) -> "CockpitAction":
        if self.kind == "propose_patch" and self.patch is None:
            raise ValueError("propose_patch requires patch")
        if self.kind != "propose_patch" and self.patch is not None:
            raise ValueError("patch is only allowed for propose_patch")
        if self.kind in {"answer", "request_clarification"} and not (self.answer or "").strip():
            raise ValueError(f"{self.kind} requires answer")
        if self.kind == "resolve_lens" and not (self.query_text or "").strip():
            raise ValueError("resolve_lens requires query_text")
        if self.kind == "inspect_evidence" and not self.entity_ids:
            raise ValueError("inspect_evidence requires entity_ids")
        return self


class CockpitObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    summary: str
    payload: dict[str, object] = Field(default_factory=dict)


class CockpitTurnResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str
    outcome: Literal["answer", "no_change", "request_clarification", "proposal"]
    cited_entity_ids: list[str] = Field(default_factory=list)
    proposal: dict[str, object] | None = None
    trace: list[dict[str, object]] = Field(default_factory=list)
    observations: list[dict[str, object]] = Field(default_factory=list)
    lens: dict[str, object]
    final_request: dict[str, object]


class CockpitResponder(Protocol):
    def __call__(
        self,
        request: SemanticLensRequest,
        snapshot: SemanticLensSnapshot,
        observations: tuple[CockpitObservation, ...],
        progress: Callable[[], None],
    ) -> CockpitAction: ...


@dataclass(frozen=True, slots=True)
class CockpitLimits:
    max_actions: int = 4
    max_history_records: int = 8

    def __post_init__(self) -> None:
        if not 1 <= self.max_actions <= 8:
            raise ValueError("max_actions must be between 1 and 8")


class WorkbenchCockpit:
    """Execute a small, deterministic tool loop around an untrusted planner."""

    def __init__(
        self,
        *,
        resolve_lens: Callable[[SemanticLensRequest], SemanticLensSnapshot],
        query_history: Callable[[str, str, int], list[dict[str, object]]],
        limits: CockpitLimits | None = None,
    ) -> None:
        self._resolve_lens = resolve_lens
        self._query_history = query_history
        self._limits = limits or CockpitLimits()
        self.last_snapshot: SemanticLensSnapshot | None = None

    def run(
        self,
        *,
        request: SemanticLensRequest,
        session_id: str,
        responder: CockpitResponder,
        progress: Callable[[], None],
    ) -> CockpitTurnResult:
        snapshot = self._resolve_lens(request)
        self.last_snapshot = snapshot
        current_request = request
        observations: list[CockpitObservation] = []
        trace: list[dict[str, object]] = []
        for action_index in range(self._limits.max_actions):
            progress()
            action = responder(current_request, snapshot, tuple(observations), progress)
            trace.append({"action_index": action_index + 1, "kind": action.kind, "rationale": action.rationale})
            if action.kind == "resolve_lens":
                visible_node_ids = {node.id for node in snapshot.nodes}
                invalid_anchor_ids = sorted(set(action.entity_ids) - visible_node_ids)
                if invalid_anchor_ids:
                    trace[-1]["rejected_anchor_ids"] = invalid_anchor_ids
                    observations.append(CockpitObservation(
                        kind="resolve_lens_rejected",
                        summary="rejected anchors outside the current lens",
                        payload={"invalid_anchor_ids": invalid_anchor_ids},
                    ))
                    continue
                current_request = _bounded_follow_up_request(current_request, action)
                snapshot = self._resolve_lens(current_request)
                self.last_snapshot = snapshot
                observations.append(CockpitObservation(
                    kind="resolve_lens",
                    summary="resolved a bounded follow-up lens",
                    payload={"lens_id": snapshot.lens_id, "node_count": len(snapshot.nodes), "edge_count": len(snapshot.edges)},
                ))
                continue
            if action.kind == "inspect_evidence":
                observation = _inspect_visible_entities(snapshot, action.entity_ids)
                observations.append(observation)
                trace[-1]["entity_ids"] = list(action.entity_ids)
                continue
            if action.kind == "query_history":
                records = self._query_history(request.workspace_id, session_id, self._limits.max_history_records)
                observations.append(CockpitObservation(
                    kind="query_history",
                    summary=f"returned {len(records)} prior interactions",
                    payload={"records": records},
                ))
                continue
            self.last_snapshot = snapshot
            return _terminal_result(action, snapshot, trace, observations, current_request)
        self.last_snapshot = snapshot
        return CockpitTurnResult(
            answer="I reached the bounded investigation-action limit without a safe conclusion.",
            outcome="no_change",
            cited_entity_ids=[],
            trace=trace,
            observations=[item.model_dump(mode="json") for item in observations],
            lens=snapshot.to_dict(),
            final_request=_request_payload(current_request),
        )


def validate_cockpit_proposal(
    snapshot: SemanticLensSnapshot,
    proposal: dict[str, object],
    *,
    expected_workspace_id: str | None = None,
) -> tuple[bool, str]:
    """Validate the lens envelope and the existing maintenance-patch contract."""
    raw_patch = proposal.get("maintenance_patch")
    if not isinstance(raw_patch, dict):
        return False, "maintenance_patch_required"
    try:
        patch = MaintenancePatch.model_validate(raw_patch)
    except ValueError as exc:
        return False, f"invalid_maintenance_patch: {exc}"
    workspace_id = expected_workspace_id or snapshot.workspace_id
    if patch.scope.workspace_id != workspace_id:
        return False, "maintenance_scope_workspace_mismatch"
    if patch.scope.scope_kind != "workspace":
        return False, "cockpit_scope_not_supported"
    envelope = validate_edit_proposal(snapshot, proposal)
    if not envelope.accepted:
        return False, envelope.reason
    visible_node_ids = {node.id for node in snapshot.nodes}
    visible_edges = (*snapshot.edges, *snapshot.hyperedges)
    visible_edge_ids = {edge.id for edge in visible_edges}
    visible_ids = visible_node_ids | visible_edge_ids
    evidence_ids = {str(value) for value in (proposal.get("evidence_ids") or ())}
    if not evidence_ids or not evidence_ids <= visible_ids:
        return False, "evidence_not_in_scoped_lens"
    evidence_documents = _evidence_document_ids(snapshot, evidence_ids)
    for operation in patch.operations:
        if operation.kind.value in {"NOOP", "REQUEST_REVIEW"}:
            continue
        provenance = operation.provenance
        if provenance is None:
            return False, f"operation_missing_provenance:{operation.operation_id}"
        operation_documents = set()
        if provenance.source_document_id:
            operation_documents.add(provenance.source_document_id)
        operation_documents.update(
            str(pointer.get("doc_id") or pointer.get("source_document_id"))
            for pointer in provenance.source_pointers
            if pointer.get("doc_id") or pointer.get("source_document_id")
        )
        if not operation_documents or not operation_documents & evidence_documents:
            return False, f"operation_grounding_not_in_evidence:{operation.operation_id}"
    report = validate_maintenance_patch(
        patch,
        active_node_ids=visible_node_ids,
        active_edge_ids=visible_edge_ids,
    )
    if not report.valid:
        return False, "maintenance_patch_invalid: " + ",".join(issue.code for issue in report.issues)
    return True, "ready_for_explicit_confirmation"


def _evidence_document_ids(snapshot: SemanticLensSnapshot, evidence_ids: set[str]) -> set[str]:
    documents: set[str] = set()
    for node in snapshot.nodes:
        if node.id in evidence_ids:
            documents.update(_grounding_document_ids(node.grounding))
    for edge in (*snapshot.edges, *snapshot.hyperedges):
        if edge.id in evidence_ids:
            documents.update(_grounding_document_ids(edge.grounding))
    return documents


def _grounding_document_ids(grounding: tuple[dict[str, object], ...]) -> set[str]:
    return {
        str(item.get("doc_id") or item.get("source_document_id"))
        for item in grounding
        if item.get("doc_id") or item.get("source_document_id")
    }


def _bounded_follow_up_request(request: SemanticLensRequest, action: CockpitAction) -> SemanticLensRequest:
    return SemanticLensRequest(
        workspace_id=request.workspace_id,
        graph_spaces=request.graph_spaces,
        query_text=str(action.query_text or ""),
        semantic_retrieval=request.semantic_retrieval,
        hop_limit=min(request.hop_limit, 2),
        max_nodes=min(request.max_nodes, 40),
        max_edges=min(request.max_edges, 80),
        max_hyperedges=min(request.max_hyperedges, 12),
        pinned_node_ids=request.pinned_node_ids,
        explicit_anchor_ids=tuple(action.entity_ids),
        source_watermark=request.source_watermark,
        include_tombstones=request.include_tombstones,
    )


def _inspect_visible_entities(snapshot: SemanticLensSnapshot, entity_ids: list[str]) -> CockpitObservation:
    nodes = {node.id: node for node in snapshot.nodes}
    edges = {edge.id: edge for edge in (*snapshot.edges, *snapshot.hyperedges)}
    visible: dict[str, object] = {}
    missing: list[str] = []
    for entity_id in entity_ids:
        entity = nodes.get(entity_id) or edges.get(entity_id)
        if entity is None:
            missing.append(entity_id)
        else:
            visible[entity_id] = entity.payload
    return CockpitObservation(
        kind="inspect_evidence",
        summary=f"inspected {len(visible)} visible entities" + (f"; rejected {len(missing)} out-of-lens ids" if missing else ""),
        payload={"entities": visible, "missing_ids": missing},
    )


def _terminal_result(
    action: CockpitAction,
    snapshot: SemanticLensSnapshot,
    trace: list[dict[str, object]],
    observations: list[CockpitObservation],
    request: SemanticLensRequest,
) -> CockpitTurnResult:
    visible_ids = {node.id for node in snapshot.nodes} | {edge.id for edge in snapshot.edges} | {edge.id for edge in snapshot.hyperedges}
    cited = [entity_id for entity_id in action.cited_entity_ids if entity_id in visible_ids]
    if action.kind == "propose_patch":
        assert action.patch is not None
        target_ids = _patch_target_ids(action.patch)
        proposal = {
            "operation": "maintenance_patch",
            "lens_id": snapshot.lens_id,
            "source_watermark": snapshot.source_watermark,
            "target_ids": target_ids,
            "evidence_ids": cited,
                "expected_revisions": _expected_revisions(snapshot, target_ids),
            "maintenance_patch": action.patch.model_dump(mode="json"),
        }
        valid, reason = validate_cockpit_proposal(snapshot, proposal)
        trace[-1]["proposal_validation"] = reason
        if not valid:
            return CockpitTurnResult(
                answer="I could not produce a safely grounded graph proposal: " + reason,
                outcome="no_change",
                cited_entity_ids=cited,
                trace=trace,
                observations=[item.model_dump(mode="json") for item in observations],
                lens=snapshot.to_dict(),
                final_request=_request_payload(request),
            )
        return CockpitTurnResult(
            answer=action.answer or "I prepared a grounded graph-change proposal for your review.",
            outcome="proposal",
            cited_entity_ids=cited,
            proposal=proposal,
            trace=trace,
            observations=[item.model_dump(mode="json") for item in observations],
            lens=snapshot.to_dict(),
            final_request=_request_payload(request),
        )
    outcome: Literal["answer", "no_change", "request_clarification"] = (
        "request_clarification" if action.kind == "request_clarification" else "no_change" if action.kind == "no_change" else "answer"
    )
    return CockpitTurnResult(
        answer=action.answer or "No graph change is warranted from the current grounded evidence.",
        outcome=outcome,
        cited_entity_ids=cited,
        trace=trace,
        observations=[item.model_dump(mode="json") for item in observations],
        lens=snapshot.to_dict(),
        final_request=_request_payload(request),
    )


def _patch_target_ids(patch: MaintenancePatch) -> list[str]:
    ids: set[str] = set()
    for operation in patch.operations:
        references = (operation.target_id, operation.from_node_id, operation.to_node_id, *operation.supersedes_ids)
        if operation.kind.value == "TOMBSTONE_NODE":
            references += (operation.node_id,)
        if operation.kind.value == "TOMBSTONE_EDGE":
            references += (operation.edge_id,)
        for entity_id in references:
            if entity_id:
                ids.add(entity_id)
    return sorted(ids)


def _expected_revisions(snapshot: SemanticLensSnapshot, target_ids: list[str]) -> dict[str, str | int | None]:
    revisions = {node.id: node.entity_revision for node in snapshot.nodes}
    revisions.update({edge.id: edge.entity_revision for edge in (*snapshot.edges, *snapshot.hyperedges)})
    return {entity_id: revisions[entity_id] for entity_id in target_ids if entity_id in revisions}


def _request_payload(request: SemanticLensRequest) -> dict[str, object]:
    return {
        "workspace_id": request.workspace_id,
        "graph_spaces": [str(space) for space in request.graph_spaces],
        "query_text": request.query_text,
        "semantic_retrieval": request.semantic_retrieval,
        "explicit_anchor_ids": list(request.explicit_anchor_ids),
        "hop_limit": request.hop_limit,
        "max_nodes": request.max_nodes,
        "max_edges": request.max_edges,
        "max_hyperedges": request.max_hyperedges,
        "pinned_node_ids": list(request.pinned_node_ids),
        "source_watermark": request.source_watermark,
        "include_tombstones": request.include_tombstones,
    }


__all__ = [
    "CockpitAction",
    "CockpitActionKind",
    "CockpitLimits",
    "CockpitObservation",
    "CockpitResponder",
    "CockpitTurnResult",
    "WorkbenchCockpit",
    "validate_cockpit_proposal",
]
