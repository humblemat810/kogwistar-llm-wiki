use kogwistar_llm_wiki_port::ingest::maintenance_requests::{
    maintenance_request_lane_id, maintenance_request_node_id,
    maintenance_request_parameters_fingerprint,
};
use kogwistar_llm_wiki_port::ingest::source_lifecycle::DurableParseLimits;
use kogwistar_llm_wiki_port::models::JsonObject;
use serde_json::json;

fn map(value: serde_json::Value) -> JsonObject {
    value
        .as_object()
        .expect("JSON object")
        .iter()
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect()
}

#[test]
fn maintenance_request_ids_match_python_goldens() {
    let budgets = map(json!({"a": 1}));
    let context = map(json!({"ctx": 1}));
    let limits = DurableParseLimits {
        max_depth: 10,
        max_frontier_items: 1,
        max_parser_calls: 1000,
        max_region_chars: 16_384,
        token_budget: None,
        wall_time_seconds: None,
        parser_profile: None,
    };
    let fingerprint = maintenance_request_parameters_fingerprint(
        Some("objective"),
        Some(&budgets),
        Some("topic"),
        &["b", "a"],
        Some(&context),
        &limits,
    );

    assert_eq!(fingerprint, "398166d7-5d79-5880-925a-a2e78804514c");
    assert_eq!(
        maintenance_request_node_id(
            "demo",
            "source:1",
            "document_seed_graph",
            "revision:1",
            &fingerprint
        )
        .to_string(),
        "33b3f463-4c20-5d91-a7da-c820a84a722b"
    );
    assert_eq!(
        maintenance_request_lane_id(
            "demo",
            "source:1",
            "document_seed_graph",
            "revision:1",
            &fingerprint
        )
        .to_string(),
        "3d31db7f-f0c3-5337-b5e0-ea1b2bd1fc98"
    );
}
