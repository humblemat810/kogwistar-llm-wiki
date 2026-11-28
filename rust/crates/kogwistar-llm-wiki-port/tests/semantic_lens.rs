//! Round-trip and wire-shape tests for `semantic_lens` snapshot serialization.
//! Mirrors `kogwistar_llm_wiki.workbench.semantic_lens` snapshot `to_dict`.

use kogwistar_llm_wiki_port::lens_projection::LensNode;
use kogwistar_llm_wiki_port::lens_projection::{
    lens_id, project_bounded, RetrievalMode, SemanticLensRequest,
};
use serde_json::json;

fn lens_node(id: &str) -> LensNode {
    LensNode {
        id: id.to_owned(),
        graph_space: "curated_kg".to_owned(),
        namespace: "ns".to_owned(),
        label: id.to_owned(),
        node_type: "entity".to_owned(),
        entity_revision: None,
        metadata: json!({}),
        grounding: vec![],
        payload: json!({}),
    }
}

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
fn snapshot_to_dict_preserves_core_fields() {
    let req = base_request();
    let snap = project_bounded(&req, vec![lens_node("n1")], vec![], 1700000000000);
    let dict = snap.to_dict();
    assert_eq!(
        dict.get("lens_id").and_then(|v| v.as_str()),
        Some(snap.lens_id.as_str())
    );
    assert_eq!(
        dict.get("workspace_id").and_then(|v| v.as_str()),
        Some("ws-1")
    );
    assert_eq!(
        dict.get("projected_at_ms").and_then(|v| v.as_u64()),
        Some(1700000000000)
    );
    assert_eq!(
        dict.get("completeness").and_then(|v| v.as_str()),
        Some("complete")
    );
    assert_eq!(
        dict.get("nodes")
            .and_then(|v| v.as_array())
            .map(|a| a.len()),
        Some(1)
    );
    assert_eq!(
        dict.get("nodes")
            .and_then(|v| v.get(0))
            .and_then(|v| v.get("id"))
            .and_then(|v| v.as_str()),
        Some("n1")
    );
    assert_eq!(
        dict.get("retrieval")
            .and_then(|v| v.get("mode"))
            .and_then(|v| v.as_str()),
        Some("graph")
    );
    assert_eq!(
        dict.get("flat_hits").and_then(|v| v.as_array()),
        Some(&vec![])
    );
}

#[test]
fn snapshot_to_dict_is_deterministic() {
    let req = base_request();
    let snap = project_bounded(&req, vec![lens_node("n1"), lens_node("n2")], vec![], 5);
    assert_eq!(snap.to_dict(), snap.to_dict());
}

#[test]
fn lens_id_matches_python_shape() {
    let req = base_request();
    let id = lens_id(&req);
    assert!(id.starts_with("lens:"));
    assert_eq!(id.len(), 5 + 24);
}

#[test]
fn request_to_dict_matches_python_envelope() {
    let mut req = base_request();
    req.query_text = "rebate accrual".to_owned();
    req.max_nodes = 7;
    let dict = req.to_dict();
    assert_eq!(
        dict.get("workspace_id").and_then(|v| v.as_str()),
        Some("ws-1")
    );
    assert_eq!(dict.get("max_nodes").and_then(|v| v.as_u64()), Some(7));
    assert_eq!(dict.get("hop_limit").and_then(|v| v.as_u64()), Some(1));
    assert_eq!(
        dict.get("graph_spaces")
            .and_then(|v| v.as_array())
            .map(|a| a.len()),
        Some(1)
    );
}
