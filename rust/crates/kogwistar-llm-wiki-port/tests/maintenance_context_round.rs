use kogwistar_llm_wiki_port::maintenance::append_maintenance_round;
use serde_json::{json, Value};

fn strings(values: &[&str]) -> Vec<String> {
    values.iter().map(|value| (*value).to_owned()).collect()
}

#[test]
fn append_round_preserves_prior_state_and_bounds_new_turn() {
    let prior: kogwistar_llm_wiki_port::models::JsonObject = serde_json::from_value(json!({
        "compressed_node_ids": ["seed-1"]
    }))
    .unwrap();
    let reasons: Vec<Value> = vec![json!({
        "candidate_id": "node-2",
        "reason": "connected_neighbor",
        "score": 0.8
    })];
    let context = append_maintenance_round(
        Some(&prior),
        3,
        "Expanded to the two-hop neighborhood.",
        &strings(&["node-1", "node-2"]),
        &strings(&[]),
        &strings(&["node-2"]),
        2,
        &reasons,
    );
    assert_eq!(context["compressed_node_ids"], json!(["seed-1"]));
    assert_eq!(context["turns"][0]["round"], json!(3));
    assert_eq!(context["turns"][0]["hop_limit"], json!(2));
    assert_eq!(
        context["turns"][0]["selection_reasons"][0]["score"],
        json!(0.8)
    );
}
