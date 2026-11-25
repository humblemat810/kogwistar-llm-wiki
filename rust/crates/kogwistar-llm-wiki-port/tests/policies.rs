use kogwistar_llm_wiki_port::models::JsonObject;
use kogwistar_llm_wiki_port::policies::{
    derived_knowledge_match_where, derived_knowledge_metadata, derived_knowledge_source_where,
    is_projection_eligible, requires_provenance, visibility_for, wisdom_match_where,
    wisdom_metadata, wisdom_source_where, PromotionPolicy,
};
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

#[test]
fn derived_and_wisdom_queries_match_python_where_contract() {
    assert_eq!(
        derived_knowledge_source_where("demo"),
        json!({"$and": [
            {"artifact_kind": "promoted_knowledge"},
            {"workspace_id": "demo"}
        ]})
    );
    assert_eq!(
        derived_knowledge_match_where("demo", "Topic"),
        json!({"$and": [
            {"artifact_kind": "derived_knowledge"},
            {"workspace_id": "demo"},
            {"label": "Topic"}
        ]})
    );
    assert_eq!(
        wisdom_source_where("demo"),
        json!({"$and": [
            {"entity_type": "workflow_step_exec"},
            {"workspace_id": "demo"}
        ]})
    );
    assert_eq!(
        wisdom_match_where("demo", "summarize"),
        json!({"$and": [
            {"artifact_kind": "execution_wisdom"},
            {"workspace_id": "demo"},
            {"step_op": "summarize"}
        ]})
    );
}

#[test]
fn derived_wisdom_metadata_and_lifecycle_match_python() {
    assert_eq!(
        serde_json::to_value(derived_knowledge_metadata(
            "demo",
            "Topic",
            &["", "node:2"],
            &["node:1"],
            12,
            None
        ))
        .unwrap(),
        json!({
            "workspace_id": "demo",
            "artifact_kind": "derived_knowledge",
            "label": "Topic",
            "source_node_ids": ["node:2"],
            "replaces_ids": ["node:1"],
            "created_at_ms": 12
        })
    );
    assert_eq!(
        serde_json::to_value(wisdom_metadata(
            "demo",
            "summarize",
            2,
            &["", "run:2"],
            &["run:1"],
            13,
            None
        ))
        .unwrap(),
        json!({
            "workspace_id": "demo",
            "artifact_kind": "execution_wisdom",
            "step_op": "summarize",
            "failure_count": 2,
            "evidence_run_ids": ["run:2"],
            "replaces_ids": ["run:1"],
            "created_at_ms": 13
        })
    );
    assert!(requires_provenance("promoted_knowledge"));
    assert!(requires_provenance("derived_knowledge"));
    assert!(requires_provenance("execution_wisdom"));
    assert!(!requires_provenance("candidate_link"));
}
