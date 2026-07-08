from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from kogwistar.engine_core.engine import scoped_namespace
from kogwistar.engine_core.models import Grounding, Node, Span
from kogwistar.id_provider import stable_id

from .entity_disambiguation import (
    DisambiguationCandidate,
    DisambiguationDecisionKind,
    DisambiguationEvidenceUpdate,
    DisambiguationReconciliationResult,
    build_disambiguation_patch,
    reconcile_disambiguation_candidate,
)
from .models import NamespaceEngines
from .namespaces import WorkspaceNamespaces
from .utils import _temporary_namespace


DisambiguationAnswerSource = Literal["user", "reviewer", "policy"]


@dataclass(frozen=True, slots=True)
class DisambiguationAnswerRecord:
    candidate: DisambiguationCandidate
    update: DisambiguationEvidenceUpdate
    reconciliation: DisambiguationReconciliationResult
    decision_node_id: str
    patch_id: str
    patch_intent: str
    decision_kind: str


class DisambiguationService:
    """App-level API for recording disambiguation answers and reviewer decisions."""

    def __init__(self, engines: NamespaceEngines) -> None:
        self.engines = engines

    def record_user_answer(
        self,
        candidate: DisambiguationCandidate,
        *,
        evidence_version: int,
        decision: DisambiguationDecisionKind,
        evidence_summary: str | None = None,
        replacement_question: str | None = None,
        missing_evidence: list[str] | None = None,
        question_is_concrete: bool = True,
    ) -> DisambiguationAnswerRecord:
        update = DisambiguationEvidenceUpdate(
            evidence_version=int(evidence_version),
            question_is_concrete=bool(question_is_concrete),
            user_decision=decision,
            evidence_summary=evidence_summary,
            replacement_question=replacement_question,
            missing_evidence=list(missing_evidence or []),
        )
        return self.record_answer(candidate, update=update, answer_source="user")

    def record_reviewer_answer(
        self,
        candidate: DisambiguationCandidate,
        *,
        evidence_version: int,
        decision: DisambiguationDecisionKind | None = None,
        evidence_summary: str | None = None,
        replacement_question: str | None = None,
        missing_evidence: list[str] | None = None,
        question_is_concrete: bool = True,
    ) -> DisambiguationAnswerRecord:
        update = DisambiguationEvidenceUpdate(
            evidence_version=int(evidence_version),
            question_is_concrete=bool(question_is_concrete),
            policy_decision=decision,
            evidence_summary=evidence_summary,
            replacement_question=replacement_question,
            missing_evidence=list(missing_evidence or []),
        )
        return self.record_answer(candidate, update=update, answer_source="reviewer")

    def challenge_decision(
        self,
        candidate: DisambiguationCandidate,
        *,
        evidence_version: int,
        challenge_reason: str,
        evidence_summary: str | None = None,
        replacement_question: str | None = None,
    ) -> DisambiguationAnswerRecord:
        update = DisambiguationEvidenceUpdate(
            evidence_version=int(evidence_version),
            decision_challenged=True,
            challenge_reason=challenge_reason,
            evidence_summary=evidence_summary,
            replacement_question=replacement_question,
            question_is_concrete=False,
        )
        return self.record_answer(candidate, update=update, answer_source="reviewer")

    def record_answer(
        self,
        candidate: DisambiguationCandidate,
        *,
        update: DisambiguationEvidenceUpdate,
        answer_source: DisambiguationAnswerSource,
    ) -> DisambiguationAnswerRecord:
        reconciliation = reconcile_disambiguation_candidate(candidate, update)
        patch = build_disambiguation_patch(
            candidate,
            reconciliation,
            maintenance_run_id=str(
                stable_id(
                    "disambiguation_answer_run",
                    candidate.artifact_id,
                    int(update.evidence_version),
                    answer_source,
                )
            ),
            source_document_id=candidate.source_document_ids[0] if candidate.source_document_ids else None,
            source_span_ids=tuple(candidate.source_span_ids),
        )
        decision_node_id = self._persist_decision_node(
            candidate,
            update=update,
            answer_source=answer_source,
            reconciliation=reconciliation,
            patch_id=patch.patch_id,
            patch_intent=patch.intent.value,
        )
        return DisambiguationAnswerRecord(
            candidate=reconciliation.candidate,
            update=update,
            reconciliation=reconciliation,
            decision_node_id=decision_node_id,
            patch_id=patch.patch_id,
            patch_intent=patch.intent.value,
            decision_kind=reconciliation.semantic_decision.value,
        )

    def _persist_decision_node(
        self,
        candidate: DisambiguationCandidate,
        *,
        update: DisambiguationEvidenceUpdate,
        answer_source: DisambiguationAnswerSource,
        reconciliation: DisambiguationReconciliationResult,
        patch_id: str,
        patch_intent: str,
    ) -> str:
        ns = WorkspaceNamespaces(candidate.workspace_id)
        node_id = str(
            stable_id(
                "disambiguation_decision_node",
                candidate.artifact_id,
                int(update.evidence_version),
                answer_source,
                reconciliation.status.value,
                reconciliation.semantic_decision.value,
            )
        )
        with _temporary_namespace(self.engines.conversation, ns.conv_bg):
            existing = self.engines.conversation.read.get_nodes(ids=[node_id], limit=1)
            if existing:
                return node_id
            node = self._build_decision_node(
                candidate,
                node_id=node_id,
                update=update,
                answer_source=answer_source,
                reconciliation=reconciliation,
                patch_id=patch_id,
                patch_intent=patch_intent,
            )
            self.engines.conversation.write.add_node(node)
        return node_id

    def _build_decision_node(
        self,
        candidate: DisambiguationCandidate,
        *,
        node_id: str,
        update: DisambiguationEvidenceUpdate,
        answer_source: DisambiguationAnswerSource,
        reconciliation: DisambiguationReconciliationResult,
        patch_id: str,
        patch_intent: str,
    ) -> Node:
        span = _decision_span(candidate, update)
        summary = _decision_summary(candidate, reconciliation, answer_source)
        metadata = {
            "workspace_id": candidate.workspace_id,
            "artifact_kind": (
                "disambiguation_decision_challenge"
                if update.decision_challenged
                else "disambiguation_decision"
            ),
            "candidate_key": candidate.candidate_key,
            "candidate_artifact_id": candidate.artifact_id,
            "candidate_entity_ids": list(candidate.entity_ids),
            "evidence_snapshot_id": candidate.evidence_snapshot_id,
            "evidence_cutoff_ms": int(candidate.evidence_cutoff_ms),
            "last_reconciled_evidence_version": int(reconciliation.new_evidence_version),
            "artifact_status": reconciliation.status.value,
            "resolution_source": reconciliation.resolution_source.value,
            "semantic_decision": reconciliation.semantic_decision.value,
            "answer_source": answer_source,
            "patch_id": patch_id,
            "patch_intent": patch_intent,
            "question": candidate.question,
            "replacement_question": reconciliation.replacement_question,
            "reason": reconciliation.reason,
            "should_surface_to_user": bool(reconciliation.should_surface_to_user),
            "challenge_required": bool(reconciliation.challenge_required),
            "evidence_summary": candidate.evidence_summary,
            "answer_evidence_version": int(update.evidence_version),
        }
        if candidate.metadata:
            metadata["candidate_metadata"] = dict(candidate.metadata)
        if update.evidence_summary is not None:
            metadata["updated_evidence_summary"] = update.evidence_summary
        if update.challenge_reason is not None:
            metadata["challenge_reason"] = update.challenge_reason
        if update.missing_evidence:
            metadata["missing_evidence"] = list(update.missing_evidence)
        metadata["score_bundle"] = candidate.score_bundle.model_dump()
        if candidate.source_document_ids:
            metadata["source_document_ids"] = list(candidate.source_document_ids)
        if candidate.source_span_ids:
            metadata["source_span_ids"] = list(candidate.source_span_ids)

        return Node(
            id=node_id,
            label=f"disambiguation:{candidate.candidate_key}",
            type="entity",
            doc_id=node_id,
            summary=summary,
            mentions=[Grounding(spans=[span])],
            metadata=metadata,
        )


def _decision_summary(
    candidate: DisambiguationCandidate,
    reconciliation: DisambiguationReconciliationResult,
    answer_source: DisambiguationAnswerSource,
) -> str:
    return (
        f"{answer_source} {reconciliation.semantic_decision.value} for "
        f"{candidate.candidate_key}: {reconciliation.reason}"
    )


def _decision_span(candidate: DisambiguationCandidate, update: DisambiguationEvidenceUpdate) -> Span:
    source_document_id = candidate.source_document_ids[0] if candidate.source_document_ids else candidate.artifact_id
    excerpt = candidate.question or candidate.evidence_summary or candidate.candidate_key
    if not excerpt:
        excerpt = "disambiguation decision"
    return Span(
        collection_page_url=f"document_collection/{source_document_id}",
        document_page_url=f"document/{source_document_id}",
        doc_id=source_document_id,
        insertion_method="system",
        page_number=1,
        start_char=0,
        end_char=max(1, len(excerpt)),
        excerpt=excerpt[:200],
        context_before="",
        context_after="",
        chunk_id=None,
        source_cluster_id=None,
    )


__all__ = [
    "DisambiguationAnswerRecord",
    "DisambiguationAnswerSource",
    "DisambiguationService",
]
