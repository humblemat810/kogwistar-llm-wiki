use kogwistar_llm_wiki_port::ingest::artifacts::{
    artifact_node, leading_span, parse_document_id_for_request,
};
use kogwistar_llm_wiki_port::ingest::source_lifecycle::{build_source_revision, SourceRevision};
use kogwistar_llm_wiki_port::models::IngestPipelineRequest;
use serde_json::json;

#[test]
fn leading_span_matches_python_document_artifact() {
    let span = leading_span("source:1", "hello world", "artifact_kind");

    assert_eq!(span.collection_page_url, "document_collection/source:1");
    assert_eq!(span.document_page_url, "document/source:1");
    assert_eq!(span.doc_id, "source:1");
    assert_eq!(span.insertion_method, "artifact_kind");
    assert_eq!(span.page_number, 1);
    assert_eq!(span.start_char, 0);
    assert_eq!(span.end_char, 1);
    assert_eq!(span.excerpt, "h");
    assert_eq!(span.context_before, "");
    assert_eq!(span.context_after, "ello world");
    assert_eq!(span.chunk_id, None);
    assert_eq!(span.source_cluster_id, None);
    assert!(!span.verification.is_verified);
}

#[test]
fn leading_span_slices_python_characters_not_bytes() {
    let span = leading_span("source:1", "世界", "artifact_kind");

    assert_eq!(span.excerpt, "世");
    assert_eq!(span.context_after, "界");
}

#[test]
fn parse_document_id_prefers_immutable_revision() {
    let revision = build_source_revision("demo", "source:1", "hello");
    assert_eq!(
        parse_document_id_for_request(&revision, "source:1"),
        revision.revision_document_id
    );

    let fallback = SourceRevision {
        source_document_id: "source:1".to_owned(),
        revision_id: "revision:1".to_owned(),
        source_digest: "digest".to_owned(),
        revision_document_id: String::new(),
    };
    assert_eq!(
        parse_document_id_for_request(&fallback, "source:1"),
        "source:1"
    );
}

#[test]
fn artifact_node_matches_python_wire_contract() {
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
        provenance_policy: kogwistar_llm_wiki_port::models::ProvenancePolicy::Optional,
        provenance: None,
        parse_limits: Default::default(),
    };
    let node = artifact_node(
        &request,
        "source:1",
        "ws:demo:g:curated_kg",
        Some("node:1"),
        "promoted_knowledge",
        "knowledge",
        "projection",
        "X",
        "Promoted knowledge derived from X",
        Some(&Default::default()),
    );

    assert_eq!(node.id.as_deref(), Some("node:1"));
    assert_eq!(node.kind, "entity");
    assert_eq!(node.doc_id, "source:1");
    assert_eq!(node.mentions.len(), 1);
    assert_eq!(node.mentions[0].spans[0].excerpt, "h");
    assert_eq!(node.mentions[0].spans[0].context_after, "ello");
    let metadata = serde_json::to_value(&node.metadata).unwrap();
    assert_eq!(metadata["workspace_id"], json!("demo"));
    assert_eq!(metadata["artifact_kind"], json!("promoted_knowledge"));
    assert_eq!(metadata["visibility"], json!("knowledge"));
    assert_eq!(metadata["requires_provenance"], json!(true));
    assert_eq!(metadata["lifecycle_status"], json!("active"));
    assert_eq!(metadata["redirect_to_id"], serde_json::Value::Null);
    assert_eq!(metadata["level_from_root"], json!(0));
}
