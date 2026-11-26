use kogwistar_llm_wiki_port::maintenance_profiles::{
    budget_fits, normalize_budget, normalize_profile_ladder, resolve_profile,
    resolve_profile_ladder,
};
use serde_json::json;

#[test]
fn profile_decision_matches_python_branches() {
    let decision = resolve_profile(true, "budgeted", Some(&json!({}))).unwrap();
    assert_eq!(decision.effective, "lite");
    assert_eq!(decision.reason, "budgeted_requires_configured_budget");

    let decision = resolve_profile(
        true,
        "budgeted",
        Some(&json!({"daily": {"output_tokens": 20}})),
    )
    .unwrap();
    assert_eq!(decision.effective, "budgeted");
    assert_eq!(decision.reason, "configured");
}

#[test]
fn budget_fits_are_independent_across_windows() {
    let budget = normalize_budget(Some(&json!({
        "daily": {"output_tokens": 20, "money": 1.0},
        "monthly": {"tokens": 100}
    })))
    .unwrap();
    let fits = budget_fits(
        &json!(budget),
        &json!({"daily": {"output_tokens": 19}, "monthly": {"tokens": 50}}),
        &json!({"output_tokens": 2, "tokens": 10, "money": 0.1}),
    )
    .unwrap();
    assert_eq!(fits, (false, "daily_output_tokens_cap".to_owned()));
}

#[test]
fn profile_ladder_cascades_and_recovers_primary() {
    let ladder = json!([
        {"name": "primary", "provider": "codex", "profile": "high", "budget": {"daily": {"tokens": 10}}},
        {"name": "fallback", "provider": "ollama", "profile": "budgeted", "budget": {"daily": {"tokens": 20}}}
    ]);
    let estimate = json!({"tokens": 5});
    let first = resolve_profile_ladder(
        true,
        "budgeted",
        None,
        Some(&ladder),
        Some(&json!({})),
        &estimate,
    )
    .unwrap();
    assert_eq!(first.level_index, Some(0));

    let spend = json!({"primary": {"daily": {"tokens": 10}}});
    let second = resolve_profile_ladder(
        true,
        "budgeted",
        None,
        Some(&ladder),
        Some(&spend),
        &estimate,
    )
    .unwrap();
    assert_eq!(second.level_index, Some(1));

    let spend = json!({
        "primary": {"daily": {"tokens": 10}},
        "fallback": {"daily": {"tokens": 20}}
    });
    let exhausted = resolve_profile_ladder(
        true,
        "budgeted",
        None,
        Some(&ladder),
        Some(&spend),
        &estimate,
    )
    .unwrap();
    assert_eq!(exhausted.level_index, None);
    assert_eq!(exhausted.profile.reason, "all_ladder_levels_exhausted");
}

#[test]
fn profile_ladder_rejects_bad_shape() {
    assert!(normalize_profile_ladder(Some(&json!([
        {"name": "primary", "provider": "codex", "profile": "high"},
        {"name": "primary", "provider": "codex", "profile": "high"}
    ])))
    .unwrap_err()
    .contains("unique"));
    assert!(
        normalize_profile_ladder(Some(&json!({"name": "not-a-list"})))
            .unwrap_err()
            .contains("JSON array")
    );
}
