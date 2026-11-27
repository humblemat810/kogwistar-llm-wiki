use kogwistar_llm_wiki_port::parse_reconciliation::{decide_parse_reconciliation, Outcome};

#[test]
fn new_region_is_additive_and_can_enter_parse_view() {
    let decision = decide_parse_reconciliation(&[], None, None, false);

    assert_eq!(decision.outcome, Outcome::Additive);
    assert!(decision.view_update_allowed);
    assert!(!decision.active_graph_patch_required);
    assert!(!decision.requires_review);
    assert!(decision.overlapping_member_ids.is_empty());
    assert_eq!(
        decision.rationale,
        "new derivation covers a region without an active selection"
    );
}

#[test]
fn overlapping_region_without_equivalence_proof_is_review_only() {
    let decision = decide_parse_reconciliation(&["member-old"], None, None, false);

    assert_eq!(decision.outcome, Outcome::ReviewRequired);
    assert!(!decision.view_update_allowed);
    assert!(!decision.active_graph_patch_required);
    assert!(decision.requires_review);
    assert_eq!(decision.overlapping_member_ids, ["member-old"]);
}

#[test]
fn equivalent_fingerprint_allows_view_update_without_graph_patch() {
    let decision = decide_parse_reconciliation(&["member-old"], Some(true), None, false);

    assert_eq!(decision.outcome, Outcome::Equivalent);
    assert!(decision.view_update_allowed);
    assert!(!decision.active_graph_patch_required);
    assert!(!decision.requires_review);
}

#[test]
fn approved_replacement_requires_active_graph_patch() {
    let decision = decide_parse_reconciliation(&["member-old"], None, None, true);

    assert_eq!(decision.outcome, Outcome::CorrectiveReplacement);
    assert!(decision.view_update_allowed);
    assert!(decision.active_graph_patch_required);
    assert!(!decision.requires_review);
}

#[test]
fn lower_confidence_cannot_replace_active_selection() {
    let decision = decide_parse_reconciliation(&["member-old"], None, Some((0.6, 0.9)), false);

    assert_eq!(decision.outcome, Outcome::LowerConfidence);
    assert!(!decision.view_update_allowed);
    assert!(!decision.active_graph_patch_required);
    assert!(decision.requires_review);
}
