use kogwistar_llm_wiki_port::maintenance_selection::{
    select_embedding_exploration, select_request_candidates, SelectionEdge, SelectionNode,
};
use serde_json::{json, Value};

fn node(id: &str, vector: Option<Vec<f64>>, metadata: Value) -> SelectionNode {
    serde_json::from_value(json!({
        "id": id,
        "embedding": vector,
        "metadata": metadata
    }))
    .unwrap()
}

#[test]
fn request_selection_orders_neighbors_then_semantic_then_evidence() {
    let seeds = vec![node("seed", Some(vec![1.0, 0.0]), json!({}))];
    let candidates = vec![
        node("seed", Some(vec![1.0, 0.0]), json!({})),
        node("neighbor", Some(vec![0.0, 1.0]), json!({})),
        node("semantic", Some(vec![0.9, 0.1]), json!({})),
        node("evidence", None, json!({"evidence_ids": ["source-1"]})),
    ];
    let edges = vec![SelectionEdge {
        source_ids: vec!["seed".to_owned()],
        target_ids: vec!["neighbor".to_owned()],
    }];
    let selected = select_request_candidates(&seeds, &candidates, &edges, 24, 0.2);
    assert_eq!(
        selected
            .iter()
            .map(|item| item.candidate_id.clone())
            .collect::<Vec<_>>(),
        vec!["neighbor", "semantic", "evidence"]
    );
    assert_eq!(
        selected
            .iter()
            .map(|item| item.reason.clone())
            .collect::<Vec<_>>(),
        vec!["connected_neighbor", "semantic_similar", "shared_evidence"]
    );
}

#[test]
fn request_selection_does_not_call_opposite_vectors_semantically_similar() {
    let seeds = vec![node("seed", Some(vec![1.0, 0.0]), json!({}))];
    let candidates = vec![
        node("seed", Some(vec![1.0, 0.0]), json!({})),
        node("opposite", Some(vec![-1.0, 0.0]), json!({})),
        node("evidence", None, json!({"evidence_ids": ["source-1"]})),
    ];
    let selected = select_request_candidates(&seeds, &candidates, &[], 24, 0.2);
    assert_eq!(
        selected
            .iter()
            .map(|item| item.candidate_id.clone())
            .collect::<Vec<_>>(),
        vec!["evidence"]
    );
    assert_eq!(selected[0].reason, "shared_evidence");
}

#[test]
fn embedding_probe_is_seeded_and_not_row_order_random() {
    let nodes = vec![
        node("a", Some(vec![1.0, 0.0]), json!({})),
        node("b", Some(vec![0.0, 1.0]), json!({})),
        node("c", Some(vec![-1.0, 0.0]), json!({})),
    ];
    let first = select_embedding_exploration(&nodes, 2, 41, 12, None);
    let reversed = nodes.iter().rev().cloned().collect::<Vec<_>>();
    let repeat = select_embedding_exploration(&reversed, 2, 41, 12, None);
    let different = select_embedding_exploration(&nodes, 2, 42, 12, None);
    assert_eq!(first.1, "embedding_probe");
    assert_eq!(repeat.1, "embedding_probe");
    assert_eq!(
        first
            .0
            .iter()
            .map(|item| item.candidate_id.clone())
            .collect::<Vec<_>>(),
        repeat
            .0
            .iter()
            .map(|item| item.candidate_id.clone())
            .collect::<Vec<_>>()
    );
    assert_ne!(
        first
            .0
            .iter()
            .map(|item| item.candidate_id.clone())
            .collect::<Vec<_>>(),
        different
            .0
            .iter()
            .map(|item| item.candidate_id.clone())
            .collect::<Vec<_>>()
    );
}

#[test]
fn embedding_outage_only_degrades_exploration() {
    let nodes = vec![node("no-vector", None, json!({}))];
    let (candidates, strategy) = select_embedding_exploration(&nodes, 2, 1, 12, None);
    assert!(candidates.is_empty());
    assert_eq!(strategy, "embedding_unavailable");
}
