use kogwistar_llm_wiki_port::ingest::queue_payloads::{
    maintenance_job_payload, MaintenanceJobEnqueue,
};
use kogwistar_llm_wiki_port::maintenance::bound_maintenance_context;
use kogwistar_llm_wiki_port::models::{IngestPipelineRequest, JsonObject};
use serde_json::{json, Value};

fn request() -> IngestPipelineRequest {
    IngestPipelineRequest {
        workspace_id: "demo".to_owned(),
        source_uri: "file:///x.txt".to_owned(),
        title: "X".to_owned(),
        raw_text: "hello".to_owned(),
        source_format: "text".to_owned(),
        operation_mode: "maintenance_first".to_owned(),
        parser_mode: "heuristic".to_owned(),
        parser_lane: "page_index".to_owned(),
        promotion_mode: "pending".to_owned(),
        auto_accept_threshold: 0.95,
        llm_provider: None,
        llm_model: None,
        provenance_policy: kogwistar_llm_wiki_port::models::ProvenancePolicy::Optional,
        provenance: None,
        parse_limits: Default::default(),
    }
}

fn enqueue() -> MaintenanceJobEnqueue {
    MaintenanceJobEnqueue {
        request: request(),
        request_node_id: "request:1".to_owned(),
        source_document_id: "source:1".to_owned(),
        maintenance_kind: "document_seed_graph".to_owned(),
        topic: Some(" topic ".to_owned()),
        seed_node_ids: None,
        maintenance_context: None,
        max_rounds: Some(3),
        lane_message_id: Some("lane:1".to_owned()),
        source_revision_id: "rev:1".to_owned(),
        source_digest: "digest".to_owned(),
        revision_document_id: "rev-doc:1".to_owned(),
        required_stage: "source_map_seeded".to_owned(),
        objective: Some("objective".to_owned()),
        budgets: Some(Default::default()),
        parse_target: None,
        parse_session_id_override: None,
        authority_claims: Some(Default::default()),
    }
}

#[test]
fn maintenance_job_payload_matches_python_contract() {
    let payload = maintenance_job_payload(&enqueue()).unwrap();
    let value = serde_json::to_value(&payload).unwrap();
    assert_eq!(value["workspace_id"], json!("demo"));
    assert_eq!(value["request_node_id"], json!("request:1"));
    assert_eq!(value["maintenance_kind"], json!("document_seed_graph"));
    assert_eq!(value["topic"], json!("topic"));
    assert_eq!(value["seed_node_ids"], json!(["source:1"]));
    assert_eq!(value["maintenance_round"], json!(0));
    assert_eq!(value["maintenance_max_rounds"], json!(3));
    assert_eq!(value["lane_message_id"], json!("lane:1"));
    assert_eq!(value["revision_document_id"], json!("rev-doc:1"));
    assert_eq!(
        value["parse_session_id"],
        json!("3c35f092-280c-5733-bfbe-53115ba1c647")
    );
    assert_eq!(value["parse_session_scope"], json!("full"));
    assert_eq!(value["required_stage"], json!("source_map_seeded"));
    assert_eq!(value["durable_layered_parse"], json!(false));
    assert_eq!(value["authority_required"], json!(true));
    assert_eq!(
        value["maintenance_plan"],
        json!([
            "document_seed_graph",
            "document_parse_graph",
            "document_expand_parse_children",
            "review_maintenance_subject",
            "document_propose_crosslinks",
            "document_validate_crosslinks"
        ])
    );
    assert_eq!(value["maintenance_phase_index"], json!(0));
}

#[test]
fn bounded_maintenance_context_matches_python_goldens() {
    assert_eq!(
        serde_json::to_value(bound_maintenance_context(None)).unwrap(),
        json!({
            "version": 1,
            "turns": [],
            "compressed_summary": "",
            "compressed_node_ids": [],
            "compressed_edge_ids": [],
            "truncated": false
        })
    );

    let context = map(json!({
        "compressed_summary": "x".repeat(5000),
        "truncated": 1,
        "turns": [{
            "round": -1,
            "summary": "s",
            "touched_node_ids": ["n1", "", "n1", "n2"],
            "hop_limit": 99,
            "selection_reasons": [{
                "candidate_id": "c1",
                "reason": "r",
                "score": "bad"
            }]
        }],
        "compressed_node_ids": ["n1", "n2"],
        "compressed_edge_ids": ["e1"]
    }));
    let bounded = bound_maintenance_context(Some(&context));
    let value = serde_json::to_value(&bounded).unwrap();
    assert_eq!(value["compressed_node_ids"], json!(["n1", "n2"]));
    assert_eq!(value["compressed_edge_ids"], json!(["e1"]));
    assert_eq!(value["truncated"], json!(true));
    assert_eq!(value["turns"][0]["round"], json!(0));
    assert_eq!(value["turns"][0]["hop_limit"], json!(8));
    assert_eq!(value["turns"][0]["touched_node_ids"], json!(["n1", "n2"]));
    assert_eq!(
        value["turns"][0]["selection_reasons"][0]["score"],
        Value::Null
    );
}

fn map(value: serde_json::Value) -> JsonObject {
    value
        .as_object()
        .expect("JSON object")
        .iter()
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect()
}
