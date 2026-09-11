"""Application orchestration for grounded workbench investigations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, Mapping, Protocol

from .investigation_history import InvestigationHistoryRecord, InvestigationHistoryService
from .semantic_lens import (
    InvestigationOutcome,
    SemanticLensRequest,
    SemanticLensService,
    SemanticLensSnapshot,
    ProposalValidation,
    validate_edit_proposal,
)

WorkbenchMode = Literal["deterministic", "codex"]


class MutationExecutor(Protocol):
    def __call__(self, proposal: Mapping[str, object]) -> Mapping[str, object]: ...


@dataclass(frozen=True, slots=True)
class GroundedAnswer:
    answer: str
    lens_id: str
    source_watermark: str | int | None
    cited_entity_ids: tuple[str, ...]
    outcome: str
    insufficiency_reason: str | None = None


@dataclass(frozen=True, slots=True)
class WorkbenchTurn:
    answer: GroundedAnswer
    snapshot: SemanticLensSnapshot
    history: InvestigationHistoryRecord


class KnowledgeWorkbench:
    """Shared grounding/history path for deterministic and current Codex turns.

    The current Codex callback is answer-only. The intended cockpit agent will
    use this path for its individual grounded actions, but tool selection and
    graph-patch orchestration are deliberately not implied by this class.
    """

    def __init__(
        self,
        *,
        lens_service: SemanticLensService,
        history_service: InvestigationHistoryService,
        clock_ms: Callable[[], int],
    ) -> None:
        self.lens_service = lens_service
        self.history_service = history_service
        self.clock_ms = clock_ms

    def ask(
        self,
        *,
        request: SemanticLensRequest,
        session_id: str,
        mode: WorkbenchMode,
        agent_answer: Callable[[SemanticLensSnapshot], str] | None = None,
    ) -> WorkbenchTurn:
        snapshot = self.lens_service.resolve(request)
        cited = tuple(node.id for node in snapshot.nodes if node.grounding)
        if mode == "codex" and agent_answer is not None:
            answer_text = str(agent_answer(snapshot))
            insufficiency = "no_grounded_candidates" if not snapshot.nodes else None
            outcome = "no_change" if not snapshot.nodes else "answer"
        elif not snapshot.nodes:
            answer_text = "I found no grounded candidates in the requested scope."
            insufficiency = "no_grounded_candidates"
            outcome = "no_change"
        else:
            labels = ", ".join(node.label for node in snapshot.nodes[:5])
            answer_text = f"The bounded graph context contains: {labels}."
            insufficiency = None
            outcome = "answer"
        investigation = InvestigationOutcome(
            outcome=outcome,
            session_id=session_id,
            lens_id=snapshot.lens_id,
            source_watermark=snapshot.source_watermark,
            cited_entity_ids=cited,
            insufficiency_reason=insufficiency,
        )
        history = self.history_service.record(
            workspace_id=request.workspace_id,
            session_id=session_id,
            question=request.query_text,
            action_kind="ask",
            snapshot=snapshot,
            outcome=investigation,
            created_at_ms=self.clock_ms(),
        )
        return WorkbenchTurn(
            answer=GroundedAnswer(
                answer=answer_text,
                lens_id=snapshot.lens_id,
                source_watermark=snapshot.source_watermark,
                cited_entity_ids=cited,
                outcome=outcome,
                insufficiency_reason=insufficiency,
            ),
            snapshot=snapshot,
            history=history,
        )

    def validate_proposal(
        self,
        *,
        snapshot: SemanticLensSnapshot,
        proposal: Mapping[str, object],
    ) -> ProposalValidation:
        return validate_edit_proposal(snapshot, proposal)

    def confirm_and_execute(
        self,
        *,
        snapshot: SemanticLensSnapshot,
        proposal: Mapping[str, object],
        confirmed: bool,
        execute: MutationExecutor,
    ) -> Mapping[str, object]:
        validation = self.validate_proposal(snapshot=snapshot, proposal=proposal)
        if not validation.accepted:
            return {"status": "rejected", "reason": validation.reason}
        if not confirmed:
            return {"status": "confirmation_required", "reason": validation.reason}
        # The executor is supplied by llm-wiki's existing Kogwistar command
        # path. This service never writes graph state directly.
        return dict(execute(proposal))


__all__ = ["GroundedAnswer", "KnowledgeWorkbench", "MutationExecutor", "WorkbenchMode", "WorkbenchTurn"]
