use kogwistar_llm_wiki_port::crosslink_context::{pack_crosslink_context, CrosslinkContextBudget};
use serde_json::{json, Value};

fn nodes() -> Vec<Value> {
    (0..3)
        .map(|index| {
            json!({
                "context_kind": "neighbor_node",
                "node_id": format!("ws:demo:node:{index}"),
                "summary": format!("neighbor summary {}", "x".repeat(40))
            })
        })
        .collect()
}

fn edges() -> Vec<Value> {
    (0..2)
        .map(|index| {
            json!({
                "context_kind": "neighbor_edge",
                "edge_id": format!("ws:demo:edge:{index}"),
                "source_ids": ["ws:demo:node:0"],
                "target_ids": [format!("ws:demo:node:{}", index + 1)],
                "relation": "related_to"
            })
        })
        .collect()
}

#[test]
fn crosslink_context_packs_deterministically() {
    let budget = CrosslinkContextBudget {
        max_nodes: 2,
        max_edges: 1,
        max_tokens: 200,
        max_characters: 500,
    };
    let first = pack_crosslink_context(&nodes(), &edges(), &budget);
    let reversed_nodes = nodes().into_iter().rev().collect::<Vec<_>>();
    let reversed_edges = edges().into_iter().rev().collect::<Vec<_>>();
    let second = pack_crosslink_context(&reversed_nodes, &reversed_edges, &budget);
    assert_eq!(first, second);
    assert!(first["nodes"].as_array().unwrap().len() <= 2);
    assert!(first["edges"].as_array().unwrap().len() <= 1);
    assert!(first["estimated_tokens"].as_i64().unwrap() <= 200);
    assert!(first["characters"].as_i64().unwrap() <= 500);
    assert!(first["omitted_nodes"].as_i64().unwrap() >= 1);
    assert!(first["omitted_edges"].as_i64().unwrap() >= 1);
}

#[test]
fn budget_defaults_use_compatibility_aliases() {
    let payload = serde_json::from_value(json!({
        "crosslink_context": {"max_nodes": 3, "max_chars": 700}
    }))
    .unwrap();
    let budget = CrosslinkContextBudget::from_payload(&payload).unwrap();
    assert_eq!(
        budget,
        CrosslinkContextBudget {
            max_nodes: 3,
            max_edges: 24,
            max_tokens: 0,
            max_characters: 700
        }
    );
}

#[test]
fn empty_neighbor_graph_packs_to_zero() {
    let budget = CrosslinkContextBudget {
        max_nodes: 4,
        max_edges: 4,
        max_tokens: 0,
        max_characters: 4000,
    };
    let packed = pack_crosslink_context(&[], &[], &budget);
    assert_eq!(
        packed,
        json!({
            "nodes": [],
            "edges": [],
            "omitted_nodes": 0,
            "omitted_edges": 0,
            "estimated_tokens": 0,
            "characters": 0,
            "budget": {
                "max_nodes": 4,
                "max_edges": 4,
                "max_tokens": 0,
                "max_characters": 4000
            },
            "dropped_by_builder": {}
        })
    );
}
