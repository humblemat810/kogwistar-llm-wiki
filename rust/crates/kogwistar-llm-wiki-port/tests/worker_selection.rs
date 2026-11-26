use kogwistar_llm_wiki_port::worker_selection::{
    filter_edges_to_visible_nodes, selection_audit, selection_seed_ids, selection_topic_seeds,
};
use serde_json::{json, Value};

#[test]
fn selection_seed_ids_add_context_and_request() {
    let payload: kogwistar_llm_wiki_port::models::JsonObject = serde_json::from_value(json!({
        "seed_node_ids": ["seed-1"],
        "maintenance_context": {
            "turns": [{"next_seed_node_ids": ["seed-2"]}],
            "compressed_node_ids": ["seed-3"]
        }
    }))
    .unwrap();
    let seeds = selection_seed_ids(&payload, Some("request-1"));
    let actual = seeds.iter().cloned().collect::<Vec<_>>();
    assert_eq!(actual, vec!["request-1", "seed-1", "seed-2", "seed-3"]);
}

#[test]
fn edges_keep_only_accessible_endpoints() {
    let nodes = vec![json!({"id": "node-1"}), json!({"id": "node-2"})];
    let edges = vec![
        json!({
            "id": "edge-ok",
            "source_ids": ["node-1"],
            "target_ids": ["node-2"],
            "metadata": {"workspace_id": "ws"}
        }),
        json!({
            "id": "edge-missing",
            "source_ids": ["node-3"],
            "target_ids": ["node-2"],
            "metadata": {"workspace_id": "ws"}
        }),
    ];
    let kept = filter_edges_to_visible_nodes(&edges, &nodes, "ws");
    assert_eq!(kept.len(), 1);
    assert_eq!(kept[0]["id"], json!("edge-ok"));
}

#[test]
fn topic_seeds_only_match_unseen_nodes() {
    let payload: kogwistar_llm_wiki_port::models::JsonObject = serde_json::from_value(json!({
        "topic": "long-context reasoning"
    }))
    .unwrap();
    let nodes = vec![
        json!({
            "id": "seen",
            "label": "Long Context",
            "summary": "",
            "metadata": {}
        }),
        json!({
            "id": "new",
            "label": "",
            "summary": "Reasoning across documents",
            "metadata": {}
        }),
    ];
    let seed_ids = ["seen".to_owned()].into_iter().collect();
    let seeds = selection_topic_seeds(&payload, &nodes, &seed_ids);
    assert_eq!(seeds.len(), 1);
    assert_eq!(seeds[0]["id"], json!("new"));
}

#[test]
fn selection_audit_uses_worker_lane_and_stable_key() {
    let payload: kogwistar_llm_wiki_port::models::JsonObject = serde_json::from_value(json!({
        "mode": "request",
        "topic": "reasoning",
        "selection_strategy": "connected_semantic_evidence_history"
    }))
    .unwrap();
    let candidates: Vec<Value> = vec![json!({"candidate_id": "node-1"})];
    let audit = selection_audit("ws", "job-1", "request-1", &payload, &candidates);
    assert_eq!(audit.conversation_id, "maintenance:request-1");
    assert_eq!(audit.msg_type, "maintenance.selection");
    assert_eq!(
        audit.payload["selection_strategy"],
        json!("connected_semantic_evidence_history")
    );
    assert_eq!(audit.audit_key.len(), 36);
}
