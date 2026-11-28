use std::time::Duration;

use kogwistar_llm_wiki_port::ingest::run::{
    ingest_run_plan, maintenance_first_artifacts, parse_complete_event, parse_runtime_ms,
    run_start_event,
};
use kogwistar_llm_wiki_port::models::{IngestPipelineArtifacts, IngestPipelineRequest};
use serde_json::{json, Value};

fn request(operation_mode: &str, parser_lane: &str) -> IngestPipelineRequest {
    IngestPipelineRequest {
        workspace_id: "demo".to_owned(),
        source_uri: "file:///x.txt".to_owned(),
        title: "X".to_owned(),
        raw_text: "hello".to_owned(),
        source_format: "text".to_owned(),
        operation_mode: operation_mode.to_owned(),
        parser_mode: "heuristic".to_owned(),
        parser_lane: parser_lane.to_owned(),
        promotion_mode: "pending".to_owned(),
        auto_accept_threshold: 0.95,
        llm_provider: None,
        llm_model: None,
        provenance_policy: kogwistar_llm_wiki_port::models::ProvenancePolicy::Optional,
        provenance: None,
        parse_limits: Default::default(),
    }
}

#[test]
fn ingest_run_plan_matches_python_branches() {
    let maintenance =
        ingest_run_plan(&request("maintenance_first", "page_index"), "source:1").unwrap();
    assert_eq!(maintenance.operation_mode, "maintenance_first");
    assert_eq!(maintenance.maintenance_kind, "document_seed_graph");
    assert_eq!(maintenance.graph_status, "seeded");
    assert!(maintenance.initialize_durable_parse_session);

    let hybrid = ingest_run_plan(&request("hybrid", "page_index"), "source:1").unwrap();
    assert_eq!(hybrid.maintenance_kind, "document_expand_parse_children");
    assert_eq!(hybrid.graph_status, "expanding");
    assert!(hybrid.initialize_durable_parse_session);

    let parse = ingest_run_plan(&request("parse_first", "page_index"), "source:1").unwrap();
    assert_eq!(parse.maintenance_kind, "distill");
    assert_eq!(parse.graph_status, "stable");
    assert!(!parse.initialize_durable_parse_session);

    let layered = ingest_run_plan(&request("parse_first", "workflow_layered"), "source:1").unwrap();
    assert!(layered.initialize_durable_parse_session);

    assert!(ingest_run_plan(&request("wrong", "page_index"), "source:1").is_err());
}

#[test]
fn maintenance_first_artifacts_match_python() {
    let plan = ingest_run_plan(&request("maintenance_first", "page_index"), "source:1").unwrap();
    let artifacts = maintenance_first_artifacts(&plan, "job:1");
    assert_eq!(
        artifacts,
        IngestPipelineArtifacts {
            source_document_id: "source:1".to_owned(),
            maintenance_job_id: "job:1".to_owned(),
            candidate_link_id: String::new(),
            promotion_candidate_id: String::new(),
            promoted_entity_id: None,
            operation_mode: "maintenance_first".to_owned(),
            graph_status: "seeded".to_owned(),
        }
    );
}

#[test]
fn parsed_artifacts_match_python() {
    let plan = ingest_run_plan(&request("hybrid", "page_index"), "source:1").unwrap();
    let artifacts = kogwistar_llm_wiki_port::ingest::run::parsed_artifacts(
        &plan,
        "job:1",
        "candidate:1",
        "promotion:1",
        Some("kg:1"),
    );
    assert_eq!(artifacts.graph_status, "expanding");
    assert_eq!(artifacts.promoted_entity_id.as_deref(), Some("kg:1"));
}

#[test]
fn run_events_match_python_shape() {
    let request = request("parse_first", "page_index");
    let plan = ingest_run_plan(&request, "source:1").unwrap();
    let start = run_start_event(&request, &plan, 123);
    assert_eq!(
        serde_json::to_value(&start).unwrap(),
        json!({
            "timestamp_ms": 123,
            "stage": "ingest_run_start",
            "workspace_id": "demo",
            "source_document_id": "source:1",
            "source_uri": "file:///x.txt",
            "parser_lane": "page_index",
            "parser_mode": "heuristic",
            "operation_mode": "parse_first"
        })
    );

    let complete = parse_complete_event(&request, &plan, 456, 100, Some("kg:1"));
    let value = serde_json::to_value(&complete).unwrap();
    assert_eq!(value["stage"], Value::from("ingest_run_complete"));
    assert_eq!(value["graph_status"], json!("stable"));
    assert_eq!(value["parse_runtime_ms"], json!(100));
    assert_eq!(value["promoted_entity_id"], json!("kg:1"));
}

#[test]
fn parse_runtime_ms_measures_elapsed_positive_milliseconds() {
    let started = std::time::Instant::now();
    std::thread::sleep(Duration::from_millis(2));
    assert!(parse_runtime_ms(started) >= 1);
}
