"""Queryable workbench interaction history using the existing conversation graph."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Mapping, Sequence

from kogwistar.engine_core.models import Grounding, Node, Span
from kogwistar.id_provider import stable_id

from .models import NamespaceEngines
from .namespaces import WorkspaceNamespaces
from .semantic_lens import InvestigationOutcome, SemanticLensSnapshot
from .utils import _temporary_namespace


@dataclass(frozen=True, slots=True)
class InvestigationHistoryRecord:
    id: str
    workspace_id: str
    session_id: str
    lens_id: str
    source_watermark: str | int | None
    question: str
    action_kind: str
    outcome: str
    cited_entity_ids: tuple[str, ...]
    proposal: dict[str, object] | None
    insufficiency_reason: str | None
    created_at_ms: int


class InvestigationHistoryService:
    """Persist and query meaningful turns; transient browser state stays local."""

    artifact_kind = "investigation_history"

    def __init__(self, engines: NamespaceEngines) -> None:
        self.engines = engines

    def record(
        self,
        *,
        workspace_id: str,
        session_id: str,
        question: str,
        action_kind: str,
        snapshot: SemanticLensSnapshot,
        outcome: InvestigationOutcome,
        created_at_ms: int,
    ) -> InvestigationHistoryRecord:
        record_id = str(
            stable_id(
                "kogwistar_llm_wiki.investigation_history",
                workspace_id,
                session_id,
                snapshot.lens_id,
                action_kind,
                str(created_at_ms),
            )
        )
        record = InvestigationHistoryRecord(
            id=record_id,
            workspace_id=workspace_id,
            session_id=session_id,
            lens_id=snapshot.lens_id,
            source_watermark=snapshot.source_watermark,
            question=question,
            action_kind=action_kind,
            outcome=outcome.outcome,
            cited_entity_ids=tuple(outcome.cited_entity_ids),
            proposal=outcome.proposal,
            insufficiency_reason=outcome.insufficiency_reason,
            created_at_ms=created_at_ms,
        )
        namespace = WorkspaceNamespaces(workspace_id).conv_fg
        with _temporary_namespace(self.engines.conversation, namespace):
            existing = self.engines.conversation.read.get_nodes(ids=[record_id], limit=1)
            if not existing:
                self.engines.conversation.write.add_node(_history_node(record))
        return record

    def query(
        self,
        *,
        workspace_id: str,
        session_id: str | None = None,
        limit: int = 100,
    ) -> list[InvestigationHistoryRecord]:
        if limit < 1:
            return []
        where: dict[str, object] = {
            "workspace_id": workspace_id,
            "artifact_kind": self.artifact_kind,
        }
        if session_id is not None:
            where["session_id"] = session_id
        namespace = WorkspaceNamespaces(workspace_id).conv_fg
        with _temporary_namespace(self.engines.conversation, namespace):
            nodes = self.engines.conversation.read.get_nodes(where=where, limit=limit)
        records = [_record_from_node(node) for node in nodes]
        return sorted(records, key=lambda item: (item.created_at_ms, item.id))


def _history_node(record: InvestigationHistoryRecord) -> Node:
    payload = {
        "workspace_id": record.workspace_id,
        "session_id": record.session_id,
        "lens_id": record.lens_id,
        "source_watermark": record.source_watermark,
        "question": record.question,
        "action_kind": record.action_kind,
        "outcome": record.outcome,
        "cited_entity_ids": list(record.cited_entity_ids),
        "proposal": record.proposal,
        "insufficiency_reason": record.insufficiency_reason,
        "created_at_ms": record.created_at_ms,
    }
    span = Span.from_dummy_for_conversation(f"investigation:{record.id}")
    return Node(
        id=record.id,
        label=f"Investigation: {record.action_kind}",
        type="entity",
        summary=record.question or record.action_kind,
        doc_id=f"_conv:{record.id}",
        mentions=[Grounding(spans=[span])],
        metadata={
            "workspace_id": record.workspace_id,
            "conversation_lane": "foreground",
            "artifact_kind": InvestigationHistoryService.artifact_kind,
            "history_payload_json": json.dumps(payload, sort_keys=True, separators=(",", ":")),
            **payload,
        },
    )


def _record_from_node(node: Node) -> InvestigationHistoryRecord:
    metadata = dict(getattr(node, "metadata", None) or {})
    raw = metadata.get("history_payload_json")
    payload: Mapping[str, object]
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            payload = parsed if isinstance(parsed, Mapping) else metadata
        except json.JSONDecodeError:
            payload = metadata
    else:
        payload = metadata
    proposal = payload.get("proposal")
    return InvestigationHistoryRecord(
        id=str(getattr(node, "id", "") or ""),
        workspace_id=str(payload.get("workspace_id") or ""),
        session_id=str(payload.get("session_id") or ""),
        lens_id=str(payload.get("lens_id") or ""),
        source_watermark=payload.get("source_watermark"),
        question=str(payload.get("question") or ""),
        action_kind=str(payload.get("action_kind") or ""),
        outcome=str(payload.get("outcome") or ""),
        cited_entity_ids=tuple(str(value) for value in (payload.get("cited_entity_ids") or ())),
        proposal=dict(proposal) if isinstance(proposal, Mapping) else None,
        insufficiency_reason=str(payload.get("insufficiency_reason") or "") or None,
        created_at_ms=int(payload.get("created_at_ms") or 0),
    )


__all__ = ["InvestigationHistoryRecord", "InvestigationHistoryService"]
