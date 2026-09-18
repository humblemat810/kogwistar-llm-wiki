from kogwistar_llm_wiki.parse_reconciliation import (
    ParseReconciliationOutcome,
    decide_parse_reconciliation,
)


def test_new_region_is_additive_and_can_enter_the_parse_view() -> None:
    decision = decide_parse_reconciliation()

    assert decision.outcome == ParseReconciliationOutcome.ADDITIVE
    assert decision.view_update_allowed is True
    assert decision.requires_review is False


def test_overlapping_region_without_equivalence_proof_is_review_only() -> None:
    decision = decide_parse_reconciliation(overlapping_member_ids=("member-old",))

    assert decision.outcome == ParseReconciliationOutcome.REVIEW_REQUIRED
    assert decision.view_update_allowed is False
    assert decision.active_graph_patch_required is False
    assert decision.requires_review is True


def test_explicit_approved_replacement_is_separate_from_equivalence() -> None:
    decision = decide_parse_reconciliation(
        overlapping_member_ids=("member-old",),
        replacement_approved=True,
    )

    assert decision.outcome == ParseReconciliationOutcome.CORRECTIVE_REPLACEMENT
    assert decision.view_update_allowed is True
    assert decision.active_graph_patch_required is True


def test_lower_confidence_derivation_cannot_replace_active_selection() -> None:
    decision = decide_parse_reconciliation(
        overlapping_member_ids=("member-old",),
        new_confidence=0.6,
        previous_confidence=0.9,
    )

    assert decision.outcome == ParseReconciliationOutcome.LOWER_CONFIDENCE
    assert decision.view_update_allowed is False
    assert decision.requires_review is True
