use kogwistar_llm_wiki_port::ingest::source_lifecycle::{
    build_source_revision, durable_parse_limits, durable_parse_profile,
    maintenance_kind_for_operation_mode, operation_mode, readiness_id,
    required_stage_for_maintenance, source_digest, source_document_id, SourceLifecycleError,
};
use kogwistar_llm_wiki_port::models::IngestPipelineRequest;
use serde_json::{json, Value};

fn request() -> IngestPipelineRequest {
    IngestPipelineRequest {
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
        provenance_policy: kogwistar_llm_wiki_port::models::ProvenancePolicy::Optional,
        provenance: None,
        parse_limits: Default::default(),
    }
}

#[test]
fn stable_ids_match_python_goldens() {
    let request = request();
    assert_eq!(
        source_document_id(&request).to_string(),
        "ee71427f-2f63-5750-8623-329f0d9e7854"
    );
    let revision = build_source_revision("demo", "source:1", "hello");
    assert_eq!(revision.revision_id, "7ea94385-d115-5d94-b37b-00ca351ff3f1");
    assert_eq!(
        revision.source_digest,
        "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    );
    assert_eq!(
        revision.revision_document_id,
        "268a3fea-1aae-56c7-87f9-2a95c51109a3"
    );
    assert_eq!(
        readiness_id(
            "source:1",
            revision.revision_id.as_str(),
            "source_map_seeded"
        )
        .to_string(),
        "da4d71b0-4a8f-5b78-a2a8-30e4af5a3b6d"
    );
}

#[test]
fn source_digest_and_operation_mode_match_python() {
    assert_eq!(
        source_digest("hello"),
        "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    );
    assert_eq!(operation_mode(&request()).unwrap(), "parse_first");
    assert_eq!(
        maintenance_kind_for_operation_mode("maintenance_first"),
        "document_seed_graph"
    );
    assert_eq!(
        maintenance_kind_for_operation_mode("hybrid"),
        "document_expand_parse_children"
    );
    assert_eq!(maintenance_kind_for_operation_mode("other"), "distill");
    assert_eq!(
        required_stage_for_maintenance("document_seed_graph"),
        "source_map_seeded"
    );
    assert_eq!(
        required_stage_for_maintenance("other"),
        "parsed_graph_persisted"
    );
}

#[test]
fn durable_limits_and_profile_match_python_goldens() {
    let default_request = request();
    assert_eq!(
        durable_parse_limits(&default_request).unwrap().max_depth,
        10
    );
    assert_eq!(
        durable_parse_profile(&default_request).unwrap(),
        "page_index"
    );

    let custom = IngestPipelineRequest {
        parse_limits: json_to_map(json!({
            "max_depth": 3,
            "wall_time_seconds": 1.5,
            "parser_profile": "profile x"
        })),
        ..request()
    };
    let limits = durable_parse_limits(&custom).unwrap();
    assert_eq!(limits.max_depth, 3);
    assert_eq!(limits.max_frontier_items, 1);
    assert_eq!(limits.max_parser_calls, 1000);
    assert_eq!(limits.max_region_chars, 16_384);
    assert_eq!(limits.wall_time_seconds, Some(1.5));
    assert_eq!(limits.parser_profile.as_deref(), Some("profile x"));
    assert_eq!(
        durable_parse_profile(&custom).unwrap(),
        "profile x@eed53631-7cd8-53a4-b69e-23bbc7518651"
    );
}

#[test]
fn invalid_modes_and_limits_match_python_rejections() {
    let invalid_mode = IngestPipelineRequest {
        operation_mode: "wrong".to_owned(),
        ..request()
    };
    assert!(matches!(
        operation_mode(&invalid_mode).unwrap_err(),
        SourceLifecycleError::InvalidOperationMode(_)
    ));

    let unknown_limit = IngestPipelineRequest {
        parse_limits: json_to_map(json!({"unknown": 1})),
        ..request()
    };
    assert!(matches!(
        durable_parse_limits(&unknown_limit).unwrap_err(),
        SourceLifecycleError::UnsupportedParseLimits(_)
    ));

    let zero_depth = IngestPipelineRequest {
        parse_limits: json_to_map(json!({"max_depth": 0})),
        ..request()
    };
    assert!(matches!(
        durable_parse_limits(&zero_depth).unwrap_err(),
        SourceLifecycleError::InvalidPositiveInteger("max_depth")
    ));

    let long_wall_time = IngestPipelineRequest {
        parse_limits: json_to_map(json!({"wall_time_seconds": 3601})),
        ..request()
    };
    assert!(matches!(
        durable_parse_limits(&long_wall_time).unwrap_err(),
        SourceLifecycleError::InvalidWallTimeSeconds
    ));
}

fn json_to_map(value: Value) -> kogwistar_llm_wiki_port::models::JsonObject {
    value
        .as_object()
        .expect("JSON object")
        .iter()
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect()
}
