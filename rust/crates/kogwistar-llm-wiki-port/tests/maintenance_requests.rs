use kogwistar_llm_wiki_port::ingest::maintenance_requests::{
    candidate_link_id, maintenance_request_lane_id, maintenance_request_node_id,
    maintenance_request_parameters_fingerprint, projection_request_id, projection_request_payload,
    promotion_candidate_id, promotion_evidence_pack_digest,
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

#[test]
fn candidate_and_promotion_ids_match_python_goldens() {
    assert_eq!(
        candidate_link_id("demo", "source:1").to_string(),
        "cb377a24-7793-54d0-a6b2-45dd590e0996"
    );
    assert_eq!(
        promotion_candidate_id("demo", "source:1").to_string(),
        "d5e25e4e-c5c7-5027-8da4-4c8cffde2e8c"
    );
}

#[test]
fn promotion_evidence_pack_digest_matches_python_golden() {
    let digest = promotion_evidence_pack_digest(&["b", "a"], &["e2", "e1"]);
    assert_eq!(
        serde_json::to_value(&digest).unwrap(),
        json!({
            "node_ids": ["a", "b"],
            "edge_ids": ["e1", "e2"],
            "depth": "parsed_graph_extraction",
            "max_chars_per_item": 0,
            "max_total_chars": 0,
            "evidence_pack_hash": "2ac176c9f2896a3d3ac0731a9f536fe1beaf629ad2fcfa6c1d2083bc1eaa72dd"
        })
    );
}

#[test]
fn projection_request_contract_matches_python() {
    assert_eq!(
        projection_request_id("demo", "kg:1").to_string(),
        "7faa7b4e-e660-5908-a78b-05f81cd23d6a"
    );
    assert_eq!(
        serde_json::to_value(projection_request_payload("demo", "kg:1", "pending")).unwrap(),
        json!({
            "workspace_id": "demo",
            "promoted_entity_id": "kg:1",
            "promotion_mode": "pending"
        })
    );
}
