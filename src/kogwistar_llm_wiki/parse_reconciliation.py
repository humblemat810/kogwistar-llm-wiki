"""Deterministic decisions for activating new parse derivations."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ParseReconciliationOutcome(StrEnum):
    EQUIVALENT = "equivalent"
    ADDITIVE = "additive"
    CORRECTIVE_REPLACEMENT = "corrective_replacement"
    LOWER_CONFIDENCE = "lower_confidence"
    REVIEW_REQUIRED = "review_required"


class ParseReconciliationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    outcome: ParseReconciliationOutcome
    view_update_allowed: bool
    active_graph_patch_required: bool = False
    requires_review: bool = False
    overlapping_member_ids: tuple[str, ...] = ()
    rationale: str = Field(min_length=1)


def decide_parse_reconciliation(
    *,
    overlapping_member_ids: tuple[str, ...] = (),
    same_fingerprint: bool = False,
    new_confidence: float | None = None,
    previous_confidence: float | None = None,
    replacement_approved: bool = False,
) -> ParseReconciliationDecision:
    """Classify a derivation without mutating graph or source evidence."""

    overlap = tuple(sorted({str(value) for value in overlapping_member_ids if str(value).strip()}))
    if not overlap:
        return ParseReconciliationDecision(
            outcome=ParseReconciliationOutcome.ADDITIVE,
            view_update_allowed=True,
            overlapping_member_ids=overlap,
            rationale="new derivation covers a region without an active selection",
        )
    if same_fingerprint:
        return ParseReconciliationDecision(
            outcome=ParseReconciliationOutcome.EQUIVALENT,
            view_update_allowed=True,
            overlapping_member_ids=overlap,
            rationale="derivation fingerprint matches the active interpretation",
        )
    if (
        new_confidence is not None
        and previous_confidence is not None
        and new_confidence < previous_confidence
    ):
        return ParseReconciliationDecision(
            outcome=ParseReconciliationOutcome.LOWER_CONFIDENCE,
            view_update_allowed=False,
            requires_review=True,
            overlapping_member_ids=overlap,
            rationale="replacement derivation has lower confidence than the active interpretation",
        )
    if replacement_approved:
        return ParseReconciliationDecision(
            outcome=ParseReconciliationOutcome.CORRECTIVE_REPLACEMENT,
            view_update_allowed=True,
            active_graph_patch_required=True,
            overlapping_member_ids=overlap,
            rationale="replacement was explicitly approved for reconciliation",
        )
    return ParseReconciliationDecision(
        outcome=ParseReconciliationOutcome.REVIEW_REQUIRED,
        view_update_allowed=False,
        requires_review=True,
        overlapping_member_ids=overlap,
        rationale="overlapping derivation lacks an equivalence proof or replacement approval",
    )


__all__ = [
    "ParseReconciliationDecision",
    "ParseReconciliationOutcome",
    "decide_parse_reconciliation",
]
