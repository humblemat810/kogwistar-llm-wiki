use kogwistar_llm_wiki_port::worker_derived::{build_derived_node_for_group, distillation_plan};
use serde_json::json;

#[test]
fn background_distillation_without_candidates_completes() {
    let payload: kogwistar_llm_wiki_port::models::JsonObject = serde_json::from_value(json!({
        "maintenance_mode": "background",
        "maintenance_candidates": []
    }))
    .unwrap();
    let plan = distillation_plan(&payload, "ws");
    assert!(plan.complete);
    assert_eq!(plan.candidate_count, 0);
}

#[test]
fn background_distillation_filters_to_selected_ids() {
    let payload: kogwistar_llm_wiki_port::models::JsonObject = serde_json::from_value(json!({
        "maintenance_mode": "background",
        "maintenance_candidates": [
            {"candidate_id": "node-b"},
            {"candidate_id": "node-a"}
        ]
    }))
    .unwrap();
    let plan = distillation_plan(&payload, "ws");
    assert!(!plan.complete);
    assert_eq!(plan.selected_ids, vec!["node-a", "node-b"]);
    assert_eq!(
        plan.source_where,
        json!({"$and": [
            {"artifact_kind": "promoted_knowledge", "workspace_id": "ws"},
            {"id": {"$in": ["node-a", "node-b"]}}
        ]})
    );
}

#[test]
fn derived_node_plan_is_deterministic_and_traces_sources() {
    let first = build_derived_node_for_group(
        "ws",
        "Shared Entity",
        &[json!({"id": "node-a"}), json!({"id": "node-b"})],
        1000,
    );
    let repeat = build_derived_node_for_group(
        "ws",
        "Shared Entity",
        &[json!({"id": "node-a"}), json!({"id": "node-b"})],
        1000,
    );
    assert_eq!(first, repeat);
    assert_eq!(first.metadata["artifact_kind"], json!("derived_knowledge"));
    assert_eq!(
        first.metadata["source_node_ids"],
        json!(["node-a", "node-b"])
    );
}
