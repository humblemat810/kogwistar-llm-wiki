"""Application disambiguation service implementation.

The root module remains a compatibility façade; service behavior belongs to
the disambiguation domain package.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Literal

from kogwistar.engine_core.models import Grounding, Node, Span
from kogwistar.id_provider import stable_id

from ..configuration.workspace import WorkspaceNamespaces
from ..maintenance.maintenance_patches import MaintenancePatch
from ..models import NamespaceEngines
from ..utils import _temporary_namespace
from .disambiguation_contracts import (
    DisambiguationCandidate,
    DisambiguationDecisionKind,
    DisambiguationEvidenceUpdate,
    DisambiguationReconciliationResult,
)
from .reconciliation import (
    build_disambiguation_patch,
    reconcile_disambiguation_candidate,
)

_MAX_CONTACT_ARTIFACT_SCAN = 5000

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
    patch: MaintenancePatch | None = None


class DisambiguationService:
    """App-level API for recording disambiguation answers and reviewer decisions."""

    __slots__ = ("engines",)

    def __init__(self, engines: NamespaceEngines) -> None:
        self.engines = engines

    def persist_contact_candidates(
        self,
        candidates: Iterable[DisambiguationCandidate],
        *,
        authorize_stream: Callable[[str, str], bool],
    ) -> tuple[str, ...]:
        """Persist immutable contact-match snapshots in conversation graph.

        Candidate nodes are review evidence, never merge decisions. Every
        referenced source stream is authorized before any write occurs.
        """

        prepared: list[tuple[DisambiguationCandidate, tuple[str, ...], str, str]] = []
        for candidate in candidates:
            if not candidate.candidate_key.startswith("contact-match:"):
                raise ValueError("contact persistence accepts contact-match candidates only")
            raw_streams = candidate.metadata.get("source_stream_ids")
            if not isinstance(raw_streams, str):
                raise TypeError("contact candidate lacks source stream references")
            try:
                decoded_streams = json.loads(raw_streams)
            except (TypeError, ValueError) as exc:
                raise ValueError("contact candidate has invalid source stream references") from exc
            if (
                not isinstance(decoded_streams, list)
                or not decoded_streams
                or any(not isinstance(item, str) or not item.strip() for item in decoded_streams)
            ):
                raise ValueError("contact candidate requires valid source stream references")
            streams = tuple(sorted(set(decoded_streams)))
            for stream_id in streams:
                if not authorize_stream(candidate.workspace_id, stream_id):
                    raise PermissionError("contact candidate source stream is not authorized")
            candidate_json = json.dumps(
                candidate.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            )
            node_id = str(
                stable_id(
                    "contact_match_candidate_snapshot",
                    candidate.workspace_id,
                    candidate.candidate_key,
                    candidate.evidence_snapshot_id,
                )
            )
            prepared.append((candidate, streams, candidate_json, node_id))

        persisted_ids: list[str] = []
        for candidate, streams, candidate_json, node_id in prepared:
            ns = WorkspaceNamespaces(candidate.workspace_id)
            with _temporary_namespace(self.engines.conversation, ns.conv_bg):
                existing = self.engines.conversation.read.get_nodes(ids=[node_id], limit=1)
                if existing:
                    if existing[0].metadata.get("candidate_json") != candidate_json:
                        raise ValueError("contact candidate snapshot ID conflicts with stored payload")
                else:
                    node = Node(
                        id=node_id,
                        label="contact_match_candidate",
                        type="entity",
                        doc_id=node_id,
                        summary=candidate.question,
                        mentions=[Grounding(spans=[_decision_span(candidate)])],
                        metadata={
                            "workspace_id": candidate.workspace_id,
                            "artifact_kind": "contact_match_candidate",
                            "candidate_key": candidate.candidate_key,
                            "candidate_artifact_id": candidate.artifact_id,
                            "evidence_snapshot_id": candidate.evidence_snapshot_id,
                            "artifact_status": candidate.artifact_status.value,
                            "source_stream_ids": json.dumps(streams, separators=(",", ":")),
                            "candidate_json": candidate_json,
                        },
                    )
                    self.engines.conversation.write.add_node(node)
            persisted_ids.append(node_id)
        return tuple(persisted_ids)

    def list_contact_candidate_snapshots(
        self,
        *,
        workspace_id: str,
        authorize_stream: Callable[[str, str], bool],
        limit: int = 200,
    ) -> tuple[DisambiguationCandidate, ...]:
        """Read bounded immutable snapshots, hiding any with denied sources.

        Multiple evidence versions of one candidate are intentionally returned
        as history, not collapsed into a potentially stale "current" view.
        """

        if not isinstance(workspace_id, str) or not workspace_id.strip():
            raise ValueError("workspace_id must not be empty")
        if not callable(authorize_stream):
            raise TypeError("authorize_stream callback is required")
        if type(limit) is not int or not 1 <= limit <= _MAX_CONTACT_ARTIFACT_SCAN:
            raise ValueError(f"limit must be between 1 and {_MAX_CONTACT_ARTIFACT_SCAN}")
        ns = WorkspaceNamespaces(workspace_id)
        with _temporary_namespace(self.engines.conversation, ns.conv_bg):
            nodes = self.engines.conversation.read.get_nodes(
                where={
                    "workspace_id": workspace_id,
                    "artifact_kind": "contact_match_candidate",
                },
                limit=limit,
            )
        candidates: list[DisambiguationCandidate] = []
        for node in nodes:
            try:
                streams = json.loads(str(node.metadata["source_stream_ids"]))
                candidate = DisambiguationCandidate.model_validate_json(
                    str(node.metadata["candidate_json"])
                )
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("stored contact candidate artifact is malformed") from exc
            if candidate.workspace_id != workspace_id:
                raise ValueError("stored contact candidate escaped workspace namespace")
            if (
                not isinstance(streams, list)
                or not streams
                or any(not isinstance(item, str) or not item.strip() for item in streams)
            ):
                raise ValueError("stored contact candidate has invalid source references")
            if (
                node.metadata.get("candidate_key") != candidate.candidate_key
                or node.metadata.get("candidate_artifact_id") != candidate.artifact_id
                or node.metadata.get("evidence_snapshot_id") != candidate.evidence_snapshot_id
            ):
                raise ValueError("stored contact candidate metadata does not match its payload")
            if all(
                isinstance(stream_id, str)
                and authorize_stream(workspace_id, stream_id)
                for stream_id in streams
            ):
                candidates.append(candidate)
        return tuple(
            sorted(
                candidates,
                key=lambda candidate: (
                    -candidate.evidence_cutoff_ms,
                    candidate.candidate_key,
                    candidate.evidence_snapshot_id,
                ),
            )
        )

    def list_current_contact_candidates(
        self,
        *,
        workspace_id: str,
        authorize_stream: Callable[[str, str], bool],
        limit: int = 200,
    ) -> tuple[DisambiguationCandidate, ...]:
        """Return latest evidence snapshots with their latest persisted decision.

        This is a bounded review projection over immutable candidate and
        decision nodes. It does not mutate evidence or apply returned patches.
        """

        if type(limit) is not int or not 1 <= limit <= _MAX_CONTACT_ARTIFACT_SCAN:
            raise ValueError(f"limit must be between 1 and {_MAX_CONTACT_ARTIFACT_SCAN}")
        snapshots = self.list_contact_candidate_snapshots(
            workspace_id=workspace_id,
            authorize_stream=authorize_stream,
            limit=_MAX_CONTACT_ARTIFACT_SCAN,
        )
        latest_by_key: dict[str, DisambiguationCandidate] = {}
        for candidate in snapshots:
            prior = latest_by_key.get(candidate.candidate_key)
            if prior is None or (
                candidate.evidence_cutoff_ms,
                candidate.evidence_snapshot_id,
            ) > (prior.evidence_cutoff_ms, prior.evidence_snapshot_id):
                latest_by_key[candidate.candidate_key] = candidate

        if not latest_by_key:
            return ()
        ns = WorkspaceNamespaces(workspace_id)
        decisions_by_key: dict[str, tuple[int, str, DisambiguationCandidate]] = {}
        with _temporary_namespace(self.engines.conversation, ns.conv_bg):
            for artifact_kind in (
                "disambiguation_decision",
                "disambiguation_decision_challenge",
            ):
                decision_nodes = self.engines.conversation.read.get_nodes(
                    where={
                        "workspace_id": workspace_id,
                        "artifact_kind": artifact_kind,
                    },
                    limit=_MAX_CONTACT_ARTIFACT_SCAN,
                )
                for node in decision_nodes:
                    key = str(node.metadata.get("candidate_key") or "")
                    current = latest_by_key.get(key)
                    if (
                        current is None
                        or node.metadata.get("evidence_snapshot_id")
                        != current.evidence_snapshot_id
                    ):
                        continue
                    candidate_json = node.metadata.get("reconciled_candidate_json")
                    if not isinstance(candidate_json, str):
                        continue
                    try:
                        reconciled = DisambiguationCandidate.model_validate_json(candidate_json)
                        version = int(node.metadata["last_reconciled_evidence_version"])
                    except (KeyError, TypeError, ValueError) as exc:
                        raise ValueError("stored contact decision artifact is malformed") from exc
                    if (
                        reconciled.workspace_id != workspace_id
                        or reconciled.candidate_key != key
                        or reconciled.evidence_snapshot_id != current.evidence_snapshot_id
                    ):
                        raise ValueError("stored contact decision does not match current evidence")
                    prior = decisions_by_key.get(key)
                    if (
                        prior is not None
                        and prior[0] == version
                        and prior[2].model_dump_json() != reconciled.model_dump_json()
                    ):
                        raise ValueError(
                            "conflicting contact decisions share one evidence version"
                        )
                    if prior is None or (version, node.id) > (prior[0], prior[1]):
                        decisions_by_key[key] = (version, node.id, reconciled)

        current_candidates = [
            decisions_by_key[key][2] if key in decisions_by_key else candidate
            for key, candidate in latest_by_key.items()
        ]
        return tuple(
            sorted(
                current_candidates,
                key=lambda candidate: (
                    -candidate.score_bundle.review_priority,
                    -candidate.evidence_cutoff_ms,
                    candidate.candidate_key,
                ),
            )[:limit]
        )

    def record_user_answer(
        self,
        candidate: DisambiguationCandidate,
        *,
        evidence_version: int,
        decision: DisambiguationDecisionKind,
        actor_id: str | None = None,
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
        return self.record_answer(
            candidate,
            update=update,
            answer_source="user",
            actor_id=actor_id,
        )

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
        actor_id: str | None = None,
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
            patch_json=patch.model_dump_json(),
            actor_id=actor_id,
        )
        return DisambiguationAnswerRecord(
            candidate=reconciliation.candidate,
            update=update,
            reconciliation=reconciliation,
            patch=patch,
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
        patch_json: str,
        actor_id: str | None,
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
                patch_json=patch_json,
                actor_id=actor_id,
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
        patch_json: str,
        actor_id: str | None,
    ) -> Node:
        span = _decision_span(candidate)
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
            "reconciled_candidate_json": reconciliation.candidate.model_dump_json(),
            "artifact_status": reconciliation.status.value,
            "resolution_source": reconciliation.resolution_source.value,
            "semantic_decision": reconciliation.semantic_decision.value,
            "answer_source": answer_source,
            "patch_id": patch_id,
            "patch_intent": patch_intent,
            "maintenance_patch_json": patch_json,
            "question": candidate.question,
            "replacement_question": reconciliation.replacement_question,
            "reason": reconciliation.reason,
            "should_surface_to_user": bool(reconciliation.should_surface_to_user),
            "challenge_required": bool(reconciliation.challenge_required),
            "evidence_summary": candidate.evidence_summary,
            "answer_evidence_version": int(update.evidence_version),
        }
        if actor_id is not None:
            metadata["actor_id"] = actor_id
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


def _decision_span(candidate: DisambiguationCandidate) -> Span:
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
