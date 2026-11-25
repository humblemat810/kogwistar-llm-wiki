use std::collections::{BTreeMap, BTreeSet};

use kogwistar_llm_wiki_port::configuration::workspace::{GraphSpace, WorkspaceNamespaces};
use kogwistar_llm_wiki_port::models::JsonObject;
use kogwistar_llm_wiki_port::projections::{
    build_adjacency, namespace_for_graph_space, node_graph_space, normalize_graph_spaces,
    select_visible_nodes, GraphSpaceInput, ProjectionEdge, ProjectionError, ProjectionNode,
};
use serde_json::{json, Value};

#[test]
fn graph_spaces_default_and_normalize_like_python() {
    assert_eq!(
        normalize_graph_spaces(None).unwrap(),
        vec![GraphSpace::CuratedKg]
    );
    assert_eq!(
        normalize_graph_spaces(Some(&[
            GraphSpaceInput::Space(GraphSpace::Source),
            GraphSpaceInput::Text("  Curated_KG "),
        ]))
        .unwrap(),
        vec![GraphSpace::Source, GraphSpace::CuratedKg]
    );
    assert!(matches!(
        normalize_graph_spaces(Some(&[])).unwrap_err(),
        ProjectionError::EmptyGraphSpaces
    ));
    assert!(matches!(
        normalize_graph_spaces(Some(&[GraphSpaceInput::Text("invalid")])).unwrap_err(),
        ProjectionError::InvalidGraphSpace(_)
    ));
}

#[test]
fn projection_namespaces_support_only_python_supported_spaces() {
    let namespaces = WorkspaceNamespaces {
        workspace_id: "demo".to_owned(),
    };

    assert_eq!(
        namespace_for_graph_space(&namespaces, GraphSpace::Source).unwrap(),
        "ws:demo:g:source"
    );
    assert_eq!(
        namespace_for_graph_space(&namespaces, GraphSpace::BaseKg).unwrap(),
        "ws:demo:g:base_kg"
    );
    assert_eq!(
        namespace_for_graph_space(&namespaces, GraphSpace::CuratedKg).unwrap(),
        "ws:demo:g:curated_kg"
    );
    assert_eq!(
        namespace_for_graph_space(&namespaces, GraphSpace::Conversation)
            .unwrap_err()
            .to_string(),
        "Projection does not support graph space 'conversation'"
    );
}

#[test]
fn visible_nodes_follow_space_manifest_and_first_id() {
    let node = |id: &str, space: &str| ProjectionNode {
        id: id.to_owned(),
        label: format!("label:{id}"),
        summary: String::new(),
        metadata: BTreeMap::from([("graph_space".to_owned(), Value::from(space))]),
    };
    let all_nodes = vec![
        node("kg:first", "curated_kg"),
        node("kg:first", "curated_kg"),
        node("kg:duplicate", "curated_kg"),
        node("kg:first-duplicate", "curated_kg"),
        node("kg:source", "source"),
        node("kg:manifest-missing", "curated_kg"),
    ];
    let manifest_ids = BTreeSet::from([
        "kg:first".to_owned(),
        "kg:duplicate".to_owned(),
        "kg:first-duplicate".to_owned(),
    ]);

    assert_eq!(
        select_visible_nodes(&all_nodes, &[GraphSpace::CuratedKg], Some(&manifest_ids))
            .iter()
            .map(|node| node.id.as_str())
            .collect::<Vec<_>>(),
        vec!["kg:first", "kg:duplicate", "kg:first-duplicate"]
    );
    assert_eq!(
        select_visible_nodes(&all_nodes, &[GraphSpace::Source], None)
            .iter()
            .map(|node| node.id.as_str())
            .collect::<Vec<_>>(),
        vec!["kg:source"]
    );
}

#[test]
fn node_graph_space_normalizes_missing_metadata() {
    assert_eq!(node_graph_space(&JsonObject::new()), "");
    assert_eq!(
        node_graph_space(&BTreeMap::from([(
            "graph_space".to_owned(),
            json!(" Curated_KG ")
        )])),
        "curated_kg"
    );
}

#[test]
fn adjacency_is_bidirectional_and_skips_empty_endpoints() {
    let edges = vec![
        ProjectionEdge {
            source_ids: vec!["a".to_owned(), "".to_owned()],
            target_ids: vec!["b".to_owned(), "c".to_owned()],
        },
        ProjectionEdge {
            source_ids: vec!["d".to_owned()],
            target_ids: vec![],
        },
    ];
    let expected = BTreeMap::from([
        (
            "a".to_owned(),
            BTreeSet::from(["b".to_owned(), "c".to_owned()]),
        ),
        ("b".to_owned(), BTreeSet::from(["a".to_owned()])),
        ("c".to_owned(), BTreeSet::from(["a".to_owned()])),
        ("d".to_owned(), BTreeSet::new()),
    ]);

    assert_eq!(build_adjacency(&edges), expected);
}
