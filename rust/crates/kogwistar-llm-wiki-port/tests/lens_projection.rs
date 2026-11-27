use kogwistar_llm_wiki_port::lens_projection::{
    default_retrieval_status, is_hyperedge, lens_id, project_bounded, validate_edit_proposal,
    LensEdge, LensNode, LensRequestError, RetrievalMode, SemanticLensRequest,
};
use serde_json::json;

#[must_use]
fn node(id: &str) -> LensNode {
    LensNode {
        id: id.to_owned(),
        graph_space: "curated_kg".to_owned(),
        namespace: "ns".to_owned(),
        label: id.to_owned(),
        node_type: "entity".to_owned(),
        entity_revision: None,
        metadata: json!({}),
        grounding: Vec::new(),
        payload: json!({}),
    }
}

#[must_use]
fn edge(id: &str) -> LensEdge {
    LensEdge {
        id: id.to_owned(),
        graph_space: "curated_kg".to_owned(),
        namespace: "ns".to_owned(),
        source_ids: vec!["a".to_owned()],
        target_ids: vec!["b".to_owned()],
        relation: "rel".to_owned(),
        entity_revision: None,
        metadata: json!({}),
        grounding: Vec::new(),
        payload: json!({}),
    }
}

#[must_use]
fn base_request() -> SemanticLensRequest {
    SemanticLensRequest::new(
        "ws-1",
        ["curated_kg"],
        RetrievalMode::Auto,
        10,
        None,
        false,
        1,
        Vec::new(),
        json!(null),
    )
    .unwrap()
}

#[test]
fn semantic_lens_request_rejects_empty_workspace_id() {
    let err = SemanticLensRequest::new(
        "   ",
        ["curated_kg"],
        RetrievalMode::Auto,
        10,
        None,
        false,
        1,
        Vec::new(),
        json!(null),
    )
    .unwrap_err();
    assert_eq!(err, LensRequestError::EmptyWorkspaceId);
}

#[test]
fn semantic_lens_request_rejects_empty_graph_spaces() {
    let err = SemanticLensRequest::new(
        "ws-1",
        [] as [&str; 0],
        RetrievalMode::Auto,
        10,
        None,
        false,
        1,
        Vec::new(),
        json!(null),
    )
    .unwrap_err();
    assert_eq!(err, LensRequestError::EmptyGraphSpaces);
}

#[test]
fn semantic_lens_request_rejects_hop_limit_over_eight() {
    let mut req = base_request();
    req.hop_limit = 9;
    let err = SemanticLensRequest::new(
        &req.workspace_id,
        req.graph_spaces.clone(),
        req.retrieval_mode,
        req.max_nodes,
        req.similarity_threshold,
        req.source_evidence_required,
        req.hop_limit,
        req.pinned_node_ids.clone(),
        req.source_watermark.clone(),
    )
    .unwrap_err();
    assert_eq!(err, LensRequestError::HopLimitOutOfRange);
}

#[test]
fn semantic_lens_request_rejects_max_nodes_below_one() {
    let err = SemanticLensRequest::new(
        "ws-1",
        ["curated_kg"],
        RetrievalMode::Auto,
        0,
        None,
        false,
        1,
        Vec::new(),
        json!(null),
    )
    .unwrap_err();
    assert!(matches!(err, LensRequestError::PinnedNodesExceedMaxNodes));
}

#[test]
fn semantic_lens_request_rejects_pinned_nodes_exceed_max_nodes() {
    let err = SemanticLensRequest::new(
        "ws-1",
        ["curated_kg"],
        RetrievalMode::Auto,
        1,
        None,
        false,
        1,
        vec!["p1".to_owned(), "p2".to_owned()],
        json!(null),
    )
    .unwrap_err();
    assert_eq!(err, LensRequestError::PinnedNodesExceedMaxNodes);
}

#[test]
fn lens_id_is_deterministic_and_prefixed() {
    let req = base_request();
    let id1 = lens_id(&req);
    let id2 = lens_id(&req);
    assert_eq!(id1, id2);
    assert!(id1.starts_with("lens:"));
    assert_eq!(id1.len(), 5 + 24);
}

#[test]
fn project_bounded_truncates_nodes_to_max_nodes() {
    let mut req = base_request();
    req.max_nodes = 2;
    let nodes = vec![node("n1"), node("n2"), node("n3"), node("n4"), node("n5")];
    let snap = project_bounded(&req, nodes, vec![], 1000);
    assert_eq!(snap.nodes.len(), 2);
    assert_eq!(snap.completeness, "bounded");
    assert_eq!(
        snap.omitted_summary
            .get("nodes_omitted")
            .and_then(|v| v.as_u64()),
        Some(3)
    );
    assert_eq!(snap.projected_at_ms, 1000);
}

#[test]
fn project_bounded_truncates_edges_to_max_edges() {
    let mut req = base_request();
    req.max_edges = 1;
    let edges = vec![edge("e1"), edge("e2")];
    let snap = project_bounded(&req, vec![], edges, 0);
    assert_eq!(snap.edges.len(), 1);
    assert_eq!(snap.completeness, "bounded");
}

#[test]
fn project_bounded_complete_when_within_budget() {
    let mut req = base_request();
    req.max_nodes = 10;
    let snap = project_bounded(&req, vec![node("n1"), node("n2")], vec![], 0);
    assert_eq!(snap.nodes.len(), 2);
    assert_eq!(snap.completeness, "complete");
}

#[test]
fn project_bounded_splits_hyperedges() {
    let mut req = base_request();
    req.max_edges = 10;
    let mut he = edge("h1");
    he.source_ids = vec!["a".to_owned(), "b".to_owned()];
    he.target_ids = vec!["c".to_owned()];
    assert!(is_hyperedge(&he));
    let snap = project_bounded(&req, vec![], vec![he], 0);
    assert_eq!(snap.hyperedges.len(), 1);
    assert_eq!(snap.edges.len(), 0);
}

#[test]
fn validate_edit_proposal_stale_lens_id_rejected() {
    let req = base_request();
    let snap = project_bounded(&req, vec![node("n1")], vec![], 0);
    let proposal = json!({
        "lens_id": "lens:stale",
        "source_watermark": snap.source_watermark,
        "target_ids": ["n1"],
        "evidence_ids": ["n1"],
        "operation": "no_change",
    });
    let val = validate_edit_proposal(&snap, &proposal);
    assert!(!val.accepted);
    assert_eq!(val.reason, "stale_lens_id");
}

#[test]
fn validate_edit_proposal_stale_watermark_rejected() {
    let req = base_request();
    let snap = project_bounded(&req, vec![node("n1")], vec![], 0);
    let proposal = json!({
        "lens_id": snap.lens_id,
        "source_watermark": "other",
        "target_ids": ["n1"],
        "evidence_ids": ["n1"],
        "operation": "no_change",
    });
    let val = validate_edit_proposal(&snap, &proposal);
    assert!(!val.accepted);
    assert_eq!(val.reason, "stale_source_watermark");
}

#[test]
fn validate_edit_proposal_missing_target_rejected() {
    let req = base_request();
    let snap = project_bounded(&req, vec![node("n1")], vec![], 0);
    let proposal = json!({
        "lens_id": snap.lens_id,
        "source_watermark": snap.source_watermark,
        "target_ids": ["n9"],
        "evidence_ids": ["n1"],
        "operation": "no_change",
    });
    let val = validate_edit_proposal(&snap, &proposal);
    assert!(!val.accepted);
    assert_eq!(val.reason, "target_not_in_scoped_lens");
}

#[test]
fn validate_edit_proposal_valid_accepted() {
    let req = base_request();
    let snap = project_bounded(&req, vec![node("n1")], vec![], 0);
    let proposal = json!({
        "lens_id": snap.lens_id,
        "source_watermark": snap.source_watermark,
        "target_ids": ["n1"],
        "evidence_ids": ["n1"],
        "operation": "no_change",
    });
    let val = validate_edit_proposal(&snap, &proposal);
    assert!(val.accepted);
    assert_eq!(val.reason, "ready_for_explicit_confirmation");
}

#[test]
fn default_retrieval_status_is_graph() {
    let status = default_retrieval_status();
    assert_eq!(status.mode, "graph");
    assert!(!status.semantic_available);
}
