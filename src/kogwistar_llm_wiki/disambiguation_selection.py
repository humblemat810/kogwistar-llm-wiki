"""Backward-compatible imports for disambiguation review selection."""

from .disambiguation.selection import (
    DefaultDisambiguationReviewPolicy,
    DisambiguationReviewPick,
    DisambiguationReviewSelection,
    DisambiguationReviewService,
    select_disambiguation_review_requests,
)

__all__ = [
    "DefaultDisambiguationReviewPolicy",
    "DisambiguationReviewPick",
    "DisambiguationReviewSelection",
    "DisambiguationReviewService",
    "select_disambiguation_review_requests",
]
