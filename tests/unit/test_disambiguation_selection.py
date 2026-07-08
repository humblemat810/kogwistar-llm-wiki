from __future__ import annotations

from kogwistar_llm_wiki.disambiguation_selection import (
    DefaultDisambiguationReviewPolicy,
    DisambiguationReviewService,
    select_disambiguation_review_requests,
)
from kogwistar_llm_wiki.entity_disambiguation import (
    DisambiguationArtifactStatus,
    DisambiguationCandidate,
    DisambiguationDecisionKind,
    DisambiguationScoreBundle,
)


def _candidate(
    *,
    artifact_id: str,
    candidate_key: str,
    review_priority: float,
    answer_risk: float,
    usage_frequency: int = 3,
    question_is_concrete: bool = True,
    status: DisambiguationArtifactStatus = DisambiguationArtifactStatus.PENDING,
) -> DisambiguationCandidate:
    return DisambiguationCandidate(
        artifact_id=artifact_id,
        workspace_id="demo",
        candidate_key=candidate_key,
        entity_ids=("ws:demo:entity:1", "ws:demo:entity:2"),
        evidence_snapshot_id="snapshot-1",
        evidence_cutoff_ms=1_700_000_000_000,
        question="Are these the same person?",
        question_is_concrete=question_is_concrete,
        artifact_status=status,
        semantic_decision=DisambiguationDecisionKind.AMBIGUOUS,
        evidence_summary="Ambiguous mention requires review.",
        source_document_ids=("doc-1",),
        source_span_ids=("span-1",),
        score_bundle=DisambiguationScoreBundle(
            merge_likelihood=0.52,
            distinction_pressure=0.48,
            review_priority=review_priority,
            usage_frequency=usage_frequency,
            answer_risk=answer_risk,
        ),
    )


def test_default_selector_orders_pending_reviews_by_priority() -> None:
    high = _candidate(
        artifact_id="ws:demo:disambiguation:high",
        candidate_key="demo:high",
        review_priority=0.91,
        answer_risk=0.92,
    )
    medium = _candidate(
        artifact_id="ws:demo:disambiguation:medium",
        candidate_key="demo:medium",
        review_priority=0.62,
        answer_risk=0.63,
    )
    skipped = _candidate(
        artifact_id="ws:demo:disambiguation:skipped",
        candidate_key="demo:skipped",
        review_priority=0.99,
        answer_risk=0.99,
        status=DisambiguationArtifactStatus.RESOLVED,
    )

    selection = select_disambiguation_review_requests([medium, skipped, high])

    assert [pick.candidate.candidate_key for pick in selection.selected] == ["demo:high", "demo:medium"]
    assert selection.skipped[0].candidate.candidate_key == "demo:skipped"
    assert "pending ambiguous question" in selection.selected[0].reason


def test_selector_can_be_overridden_for_non_concrete_questions() -> None:
    candidate = _candidate(
        artifact_id="ws:demo:disambiguation:non-concrete",
        candidate_key="demo:non-concrete",
        review_priority=0.42,
        answer_risk=0.88,
        question_is_concrete=False,
    )

    default_selection = select_disambiguation_review_requests([candidate])
    override_selection = DisambiguationReviewService().select(
        [candidate],
        policy=DefaultDisambiguationReviewPolicy(
            max_reviews_per_tick=5,
            min_review_priority=0.2,
            min_answer_risk=0.2,
            allow_non_concrete=True,
        ),
    )

    assert default_selection.selected == ()
    assert override_selection.selected[0].candidate.candidate_key == "demo:non-concrete"
    assert override_selection.selected[0].priority_score > 0.0

