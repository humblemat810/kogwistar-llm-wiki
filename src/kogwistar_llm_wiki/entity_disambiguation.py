"""Compatibility facade for the disambiguation reconciliation domain."""

from .disambiguation.disambiguation_contracts import (
    DisambiguationArtifactStatus,
    DisambiguationCandidate,
    DisambiguationDecisionKind,
    DisambiguationEvidenceUpdate,
    DisambiguationReconciliationResult,
    DisambiguationResolutionSource,
    DisambiguationScoreBundle,
)
from .disambiguation.reconciliation import (
    build_disambiguation_patch,
    is_review_request_fresh,
    reconcile_disambiguation_candidate,
)

__all__ = [
    "DisambiguationArtifactStatus",
    "DisambiguationCandidate",
    "DisambiguationDecisionKind",
    "DisambiguationEvidenceUpdate",
    "DisambiguationReconciliationResult",
    "DisambiguationResolutionSource",
    "DisambiguationScoreBundle",
    "build_disambiguation_patch",
    "is_review_request_fresh",
    "reconcile_disambiguation_candidate",
]
