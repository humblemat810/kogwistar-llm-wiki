use kogwistar_llm_wiki_port::maintenance_dependency::{
    dependency_invalidation_payload, plan_dependency_invalidation, DependencyEntity,
};
use serde_json::{json, Value};

fn entity(id: &str, metadata: Value) -> DependencyEntity {
    serde_json::from_value(json!({"id": id, "metadata": metadata})).unwrap()
}

#[test]
fn invalidation_uses_edge_endpoints_and_same_workspace_only() {
    let nodes = vec![
        entity(
            "summary:a",
            json!({
                "workspace_id": "workspace-a",
                "artifact_kind": "derived_summary",
                "source_document_id": "source:a",
                "lineage_node_ids": ["node:changed"]
            }),
        ),
        entity(
            "summary:foreign",
            json!({
                "workspace_id": "workspace-b",
                "artifact_kind": "derived_summary",
                "source_document_id": "source:b",
                "lineage_node_ids": ["node:changed"]
            }),
        ),
    ];
    let edges = vec![DependencyEntity {
        id: "edge:changed".to_owned(),
        source_ids: Some(vec!["node:changed".to_owned()]),
        target_ids: Some(vec!["node:other".to_owned()]),
        metadata: json!({
            "workspace_id": "workspace-a",
            "artifact_kind": "crosslink",
            "source_document_id": "source:a"
        }),
    }];
    let plan = plan_dependency_invalidation(
        "workspace-a",
        &["node:changed".to_owned()],
        &nodes,
        &edges,
        256,
    )
    .unwrap();
    assert_eq!(plan.affected_entity_ids, vec!["edge:changed", "summary:a"]);
    assert_eq!(plan.affected_source_document_ids, vec!["source:a"]);
    assert_eq!(
        plan.follow_up_kinds,
        vec!["document_propose_crosslinks", "document_summarize_units"]
    );
    assert_eq!(plan.skipped_cross_workspace_ids, vec!["summary:foreign"]);
}

#[test]
fn invalidation_is_bounded_and_projects_changed_entities() {
    let nodes = (0..4)
        .map(|index| {
            entity(
                &format!("summary:{index}"),
                json!({
                    "workspace_id": "workspace-a",
                    "artifact_kind": "derived_summary",
                    "source_document_id": format!("source:{index}"),
                    "lineage_node_ids": ["node:changed"]
                }),
            )
        })
        .collect::<Vec<_>>();
    let plan =
        plan_dependency_invalidation("workspace-a", &["node:changed".to_owned()], &nodes, &[], 2)
            .unwrap();
    assert!(plan.truncated);
    assert_eq!(plan.affected_entity_ids.len(), 2);
    assert_eq!(plan.affected_source_document_ids.len(), 2);

    let promoted = vec![entity(
        "promoted:1",
        json!({
            "workspace_id": "workspace-a",
            "artifact_kind": "promoted_knowledge",
            "projection_visible": true
        }),
    )];
    let plan = plan_dependency_invalidation(
        "workspace-a",
        &["promoted:1".to_owned()],
        &promoted,
        &[],
        256,
    )
    .unwrap();
    assert_eq!(plan.projection_entity_ids, vec!["promoted:1"]);
}

#[test]
fn dependency_payload_uses_python_wire_fields() {
    let nodes = vec![entity(
        "summary:a",
        json!({
            "workspace_id": "w",
            "artifact_kind": "summary",
            "source_document_id": "source:a",
            "depends_on_ids": ["changed"]
        }),
    )];
    let plan =
        plan_dependency_invalidation("w", &["changed".to_owned()], &nodes, &[], 256).unwrap();
    let payload = dependency_invalidation_payload(&plan);
    assert_eq!(payload["workspace_id"], json!("w"));
    assert_eq!(payload["changed_entity_ids"], json!(["changed"]));
    assert_eq!(payload["truncated"], json!(false));
}

#[test]
fn invalidation_rejects_non_positive_bound() {
    assert!(
        plan_dependency_invalidation("w", &["changed".to_owned()], &[], &[], 0)
            .unwrap_err()
            .contains("max_dependents")
    );
}
