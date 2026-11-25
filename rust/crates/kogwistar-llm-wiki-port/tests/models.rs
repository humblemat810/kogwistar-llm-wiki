use std::collections::BTreeMap;

use kogwistar_llm_wiki_port::models::{
    IngestPipelineArtifacts, IngestPipelineRequest, MaintenanceJobRequest, MaintenanceJobResult,
    MentionSpan, ObsidianBuildResult, ProjectionEntity, ProjectionSnapshot, ProvenancePolicy,
    SemanticRelationship,
};
use serde_json::{json, Value};

#[test]
fn ingest_artifacts_match_python_defaults() {
    let artifacts = IngestPipelineArtifacts {
        source_document_id: "source:1".to_owned(),
        maintenance_job_id: "job:1".to_owned(),
        candidate_link_id: "candidate:1".to_owned(),
        promotion_candidate_id: "promotion:1".to_owned(),
        promoted_entity_id: Some("kg:1".to_owned()),
        operation_mode: "parse_first".to_owned(),
        graph_status: "expanding".to_owned(),
    };

    assert_eq!(
        serde_json::to_value(&artifacts).unwrap(),
        json!({
            "source_document_id": "source:1",
            "maintenance_job_id": "job:1",
            "candidate_link_id": "candidate:1",
            "promotion_candidate_id": "promotion:1",
            "promoted_entity_id": "kg:1",
            "operation_mode": "parse_first",
            "graph_status": "expanding"
        })
    );
}

#[test]
fn ingest_request_round_trips_python_wire_contract() {
    let request = IngestPipelineRequest {
        workspace_id: "demo".to_owned(),
        source_uri: "file:///x.txt".to_owned(),
        title: "X".to_owned(),
        raw_text: "hello".to_owned(),
        source_format: "text".to_owned(),
        operation_mode: "parse_first".to_owned(),
        parser_mode: "heuristic".to_owned(),
        parser_lane: "page_index".to_owned(),
        promotion_mode: "pending".to_owned(),
        auto_accept_threshold: 0.95,
        llm_provider: None,
        llm_model: None,
        provenance_policy: ProvenancePolicy::Optional,
        provenance: None,
        parse_limits: BTreeMap::new(),
    };

    let value = serde_json::to_value(&request).unwrap();
    assert_eq!(value["source_format"], json!("text"));
    assert_eq!(value["parser_mode"], json!("heuristic"));
    assert_eq!(value["auto_accept_threshold"], json!(0.95));
    assert_eq!(value["provenance_policy"], json!("optional"));
    assert_eq!(
        serde_json::from_value::<IngestPipelineRequest>(value).unwrap(),
        request
    );
    assert!(serde_json::from_value::<IngestPipelineRequest>(json!({
        "workspace_id": "demo",
        "source_uri": "file:///x.txt",
        "title": "X",
        "raw_text": "hello",
        "provenance_policy": "invalid"
    }))
    .is_err());
}

#[test]
fn maintenance_contract_uses_python_field_names() {
    let request = MaintenanceJobRequest {
        job_type: "crosslink".to_owned(),
        workspace_id: "demo".to_owned(),
        trigger_type: "manual".to_owned(),
        candidate_ids: vec!["candidate:1".to_owned()],
        requested_by: "system".to_owned(),
        priority: 10,
        policy_version: "1.0".to_owned(),
    };
    let result = MaintenanceJobResult {
        job_id: "job:1".to_owned(),
        job_type: "crosslink".to_owned(),
        outputs: BTreeMap::from([("count".to_owned(), Value::from(1))]),
        review_required: false,
        emitted_event_ids: vec!["event:1".to_owned()],
        status: "completed".to_owned(),
    };

    assert_eq!(
        serde_json::to_value(&request).unwrap()["requested_by"],
        json!("system")
    );
    assert_eq!(
        serde_json::to_value(&result).unwrap()["status"],
        json!("completed")
    );
}

#[test]
fn obsidian_result_serializes_vault_root_as_string() {
    let result = ObsidianBuildResult {
        vault_root: "vault".into(),
        notes: 7,
        canvases: 7,
        dangling_links: 0,
    };

    assert_eq!(
        serde_json::to_value(&result).unwrap(),
        json!({
            "vault_root": "vault",
            "notes": 7,
            "canvases": 7,
            "dangling_links": 0
        })
    );
}

#[test]
fn projection_snapshot_round_trips_entities() {
    let snapshot = ProjectionSnapshot {
        entities: vec![ProjectionEntity {
            kg_id: "kg:1".to_owned(),
            title: "Entity".to_owned(),
            entity_type: "note".to_owned(),
            summary: String::new(),
            metadata: BTreeMap::new(),
            source_ids: Vec::new(),
            target_ids: Vec::new(),
            relation: None,
            relationships: vec![SemanticRelationship::default()],
            mentions: vec![MentionSpan::default()],
            body: String::new(),
            version: Some(1),
            event_seq: Some(2),
        }],
    };

    let value = serde_json::to_value(&snapshot).unwrap();
    assert_eq!(value["entities"][0]["entity_type"], json!("note"));
    assert_eq!(value["entities"][0]["mentions"][0]["page_number"], json!(1));
    assert_eq!(
        serde_json::from_value::<ProjectionSnapshot>(value).unwrap(),
        snapshot
    );
}
