use kogwistar_llm_wiki_port::models::JsonObject;
use kogwistar_llm_wiki_port::policies::{is_projection_eligible, visibility_for, PromotionPolicy};
use serde_json::json;

fn metadata(value: serde_json::Value) -> JsonObject {
    value
        .as_object()
        .expect("JSON object")
        .iter()
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect()
}

#[test]
fn promotion_policy_matches_python_conservative_default() {
    let policy = PromotionPolicy::default();
    let approved = policy.decide("Sync", 0.95, None);
    assert!(approved.should_promote);
    assert_eq!(
        approved.reason,
        "explicit promotion approval accepted by default policy"
    );
    assert_eq!(
        serde_json::to_value(&approved.metadata).unwrap(),
        json!({
            "promotion_approved": true,
            "auto_accept_threshold": 0.95,
            "default_accept_threshold": 0.95
        })
    );

    let pending = policy.decide("pending", 0.95, None);
    assert!(!pending.should_promote);
    assert_eq!(pending.reason, "promotion was not explicitly approved");

    let above_default = policy.decide("sync", 0.96, None);
    assert!(!above_default.should_promote);
    assert_eq!(
        above_default.reason,
        "auto_accept_threshold is above the default accept threshold"
    );
}

#[test]
fn visibility_policy_matches_python_artifact_taxonomy() {
    let promoted_projection = metadata(json!({
        "artifact_kind": "promoted_knowledge",
        "projection_visible": true
    }));
    assert_eq!(visibility_for(&promoted_projection), "projection");

    let promoted_hidden = metadata(json!({
        "artifact_kind": "promoted_knowledge",
        "projection_visible": false
    }));
    assert_eq!(visibility_for(&promoted_hidden), "knowledge");

    let candidate = metadata(json!({"artifact_kind": "candidate_link"}));
    assert_eq!(visibility_for(&candidate), "review");

    let maintenance = metadata(json!({"artifact_kind": "maintenance_job_request"}));
    assert_eq!(visibility_for(&maintenance), "internal");

    let wisdom = metadata(json!({"artifact_kind": "execution_wisdom"}));
    assert_eq!(visibility_for(&wisdom), "wisdom");

    let explicit_internal = metadata(json!({
        "artifact_kind": "maintenance_job_request",
        "visibility": "Internal"
    }));
    assert_eq!(visibility_for(&explicit_internal), "internal");
}

#[test]
fn core_visibility_and_projection_eligibility_match_python() {
    let stage = metadata(json!({"knowledge_stage": "promoted"}));
    assert_eq!(visibility_for(&stage), "knowledge");

    let review = metadata(json!({"knowledge_stage": "review"}));
    assert_eq!(visibility_for(&review), "review");

    let unknown = metadata(json!({}));
    assert_eq!(visibility_for(&unknown), "internal");

    assert!(is_projection_eligible(&promoted_projection()));
    assert!(!is_projection_eligible(&metadata(json!({}))));
}

fn promoted_projection() -> JsonObject {
    metadata(json!({
        "artifact_kind": "promoted_knowledge",
        "projection_visible": true
    }))
}
