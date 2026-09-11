"""Selection policy for surfacing disambiguation review work.

This stays app-level because it decides what llm-wiki should ask a user or a
reviewer to inspect, while the reusable reconciliation semantics live in the
shared disambiguation model helpers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .entity_disambiguation import (
    DisambiguationArtifactStatus,
    DisambiguationCandidate,
    DisambiguationDecisionKind,
)


@dataclass(frozen=True, slots=True)
class DefaultDisambiguationReviewPolicy:
    """Conservative default for choosing which pending questions to surface."""

    max_reviews_per_tick: int = 5
    min_review_priority: float = 0.5
    min_answer_risk: float = 0.35
    min_usage_frequency: int = 0
    allow_non_pending: bool = False
    allow_non_concrete: bool = False


@dataclass(frozen=True, slots=True)
class DisambiguationReviewPick:
    candidate: DisambiguationCandidate
    priority_score: float
    reason: str


@dataclass(frozen=True, slots=True)
class DisambiguationReviewSelection:
    selected: tuple[DisambiguationReviewPick, ...]
    deferred: tuple[DisambiguationReviewPick, ...]
    skipped: tuple[DisambiguationReviewPick, ...]


@dataclass(frozen=True, slots=True)
class DisambiguationReviewService:
    """Stable selection facade with a default policy and overridable knobs."""

    policy: DefaultDisambiguationReviewPolicy = field(default_factory=DefaultDisambiguationReviewPolicy)

    def select(
        self,
        candidates: Iterable[DisambiguationCandidate],
        *,
        policy: DefaultDisambiguationReviewPolicy | None = None,
    ) -> DisambiguationReviewSelection:
        return select_disambiguation_review_requests(
            candidates,
            policy=policy or self.policy,
        )


def select_disambiguation_review_requests(
    candidates: Iterable[DisambiguationCandidate],
    *,
    policy: DefaultDisambiguationReviewPolicy | None = None,
) -> DisambiguationReviewSelection:
    """Pick the most urgent disambiguation questions to surface next."""

    policy = policy or DefaultDisambiguationReviewPolicy()
    selected: list[DisambiguationReviewPick] = []
    deferred: list[DisambiguationReviewPick] = []
    skipped: list[DisambiguationReviewPick] = []

    scored: list[DisambiguationReviewPick] = []
    for candidate in candidates:
        pick = _score_candidate(candidate)
        if not _is_eligible(candidate, policy):
            skipped.append(
                DisambiguationReviewPick(
                    candidate=candidate,
                    priority_score=pick.priority_score,
                    reason=pick.reason,
                )
            )
            continue
        if pick.priority_score < float(policy.min_review_priority):
            deferred.append(
                DisambiguationReviewPick(
                    candidate=candidate,
                    priority_score=pick.priority_score,
                    reason=(
                        "review priority is below the configured threshold "
                        f"({pick.priority_score:.3f} < {float(policy.min_review_priority):.3f})"
                    ),
                )
            )
            continue
        if candidate.score_bundle.answer_risk < float(policy.min_answer_risk):
            deferred.append(
                DisambiguationReviewPick(
                    candidate=candidate,
                    priority_score=pick.priority_score,
                    reason=(
                        "answer risk is below the configured threshold "
                        f"({candidate.score_bundle.answer_risk:.3f} < {float(policy.min_answer_risk):.3f})"
                    ),
                )
            )
            continue
        scored.append(pick)

    scored.sort(
        key=lambda item: (
            item.priority_score,
            float(item.candidate.score_bundle.answer_risk),
            int(item.candidate.score_bundle.usage_frequency),
            str(item.candidate.candidate_key),
        ),
        reverse=True,
    )

    limit = max(0, int(policy.max_reviews_per_tick))
    for index, pick in enumerate(scored):
        bucket = selected if index < limit else deferred
        bucket.append(
            DisambiguationReviewPick(
                candidate=pick.candidate,
                priority_score=pick.priority_score,
                reason=pick.reason,
            )
        )

    return DisambiguationReviewSelection(
        selected=tuple(selected),
        deferred=tuple(deferred),
        skipped=tuple(skipped),
    )


def _is_eligible(
    candidate: DisambiguationCandidate,
    policy: DefaultDisambiguationReviewPolicy,
) -> bool:
    if not policy.allow_non_pending and candidate.artifact_status != DisambiguationArtifactStatus.PENDING:
        return False
    if not policy.allow_non_concrete and not candidate.question_is_concrete:
        return False
    if candidate.semantic_decision != DisambiguationDecisionKind.AMBIGUOUS:
        return False
    if int(candidate.score_bundle.usage_frequency) < int(policy.min_usage_frequency):
        return False
    if not str(candidate.question or "").strip():
        return False
    return True


def _score_candidate(candidate: DisambiguationCandidate) -> DisambiguationReviewPick:
    bundle = candidate.score_bundle
    priority_score = (
        float(bundle.review_priority) * 0.60
        + float(bundle.answer_risk) * 0.25
        + max(float(bundle.merge_likelihood), float(bundle.distinction_pressure)) * 0.15
    )
    reason = (
        "pending ambiguous question with "
        f"review_priority={bundle.review_priority:.3f}, "
        f"answer_risk={bundle.answer_risk:.3f}, "
        f"usage_frequency={bundle.usage_frequency}"
    )
    return DisambiguationReviewPick(
        candidate=candidate,
        priority_score=priority_score,
        reason=reason,
    )


__all__ = [
    "DefaultDisambiguationReviewPolicy",
    "DisambiguationReviewPick",
    "DisambiguationReviewSelection",
    "DisambiguationReviewService",
    "select_disambiguation_review_requests",
]
