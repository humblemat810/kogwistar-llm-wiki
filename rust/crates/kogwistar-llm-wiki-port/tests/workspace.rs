use std::collections::BTreeMap;

use kogwistar_llm_wiki_port::configuration::workspace::{
    namespace_matches_graph_space_metadata, GraphSpace, GraphSpaceNamespace, WorkspaceNamespaces,
};

#[test]
fn graph_space_round_trips_python_wire_names() {
    for value in [
        GraphSpace::Source,
        GraphSpace::BaseKg,
        GraphSpace::CuratedKg,
        GraphSpace::Conversation,
        GraphSpace::Workflow,
        GraphSpace::Review,
        GraphSpace::Wisdom,
        GraphSpace::Policy,
        GraphSpace::Projection,
    ] {
        assert_eq!(value.as_str().parse::<GraphSpace>().unwrap(), value);
        assert_eq!(serde_json::to_value(value).unwrap(), value.as_str());
    }
}

#[test]
fn workspace_namespaces_match_python_contract() {
    let namespaces = WorkspaceNamespaces {
        workspace_id: "ws-a".to_owned(),
    };

    assert_eq!(
        namespaces.space(GraphSpace::Source, None),
        "ws:ws-a:g:source"
    );
    assert_eq!(
        namespaces.space(GraphSpace::Conversation, Some("foreground")),
        "ws:ws-a:g:conversation:lane:foreground"
    );
    assert_eq!(namespaces.conv_fg(), "ws:ws-a:conv:fg");
    assert_eq!(namespaces.workflow_maintenance(), "ws:ws-a:wf:maintenance");
    assert_eq!(
        namespaces.projection_manifest(),
        "ws:ws-a:projection_manifest"
    );
}

#[test]
fn namespace_metadata_omits_absent_lane_and_legacy_value() {
    let descriptor = GraphSpaceNamespace {
        workspace_id: "ws-a".to_owned(),
        graph_space: GraphSpace::BaseKg,
        lane: None,
    };
    let metadata = descriptor.metadata(None);
    let expected = BTreeMap::from([
        ("workspace_id".to_owned(), "ws-a".to_owned()),
        ("graph_space".to_owned(), "base_kg".to_owned()),
    ]);

    assert_eq!(metadata, expected);
}

#[test]
fn metadata_matcher_rejects_wrong_workspace_graph_space_and_lane() {
    let metadata = BTreeMap::from([
        ("workspace_id".to_owned(), "ws-a".to_owned()),
        ("graph_space".to_owned(), "base_kg".to_owned()),
        ("graph_lane".to_owned(), "foreground".to_owned()),
    ]);

    assert!(namespace_matches_graph_space_metadata(
        "ws:ws-a:g:base_kg:lane:foreground",
        &metadata
    ));
    assert!(!namespace_matches_graph_space_metadata(
        "ws:ws-b:g:base_kg:lane:foreground",
        &metadata
    ));
    assert!(!namespace_matches_graph_space_metadata(
        "ws:ws-a:g:curated_kg:lane:foreground",
        &metadata
    ));
    assert!(!namespace_matches_graph_space_metadata(
        "ws:ws-a:g:base_kg:lane:background",
        &metadata
    ));
    assert!(!namespace_matches_graph_space_metadata(
        "app:ws-a:g:base_kg",
        &metadata
    ));
}
