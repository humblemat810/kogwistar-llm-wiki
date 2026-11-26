use kogwistar_llm_wiki_port::maintenance_designs::{
    build_crosslink_group_design, build_derived_knowledge_design, build_execution_wisdom_design,
    build_graph_patch_apply_design, build_graph_patch_proposal_design,
    build_multimodal_retrieval_design,
};
use serde_json::json;

#[test]
fn graph_patch_designs_are_materializable_noop_workflows() {
    let proposal = build_graph_patch_proposal_design(None);
    let apply = build_graph_patch_apply_design(None);
    assert_eq!(proposal.workflow_id, "maintenance.graph_patch_proposal.v1");
    assert_eq!(apply.workflow_id, "maintenance.graph_patch_apply.v1");
    assert!(proposal
        .nodes
        .iter()
        .any(|node| node.metadata["wf_start"] == json!(true)));
    assert!(proposal
        .nodes
        .iter()
        .any(|node| node.metadata["wf_op"] == json!("noop")));
    assert!(apply
        .nodes
        .iter()
        .any(|node| node.metadata["wf_start"] == json!(true)));
    assert!(apply
        .nodes
        .iter()
        .any(|node| node.metadata["wf_op"] == json!("noop")));
}

#[test]
fn maintenance_workflow_designs_use_stable_shapes() {
    let derived = build_derived_knowledge_design(None);
    let wisdom = build_execution_wisdom_design(None);
    let crosslink = build_crosslink_group_design(None);
    let multimodal = build_multimodal_retrieval_design(None);
    assert_eq!(derived.workflow_id, "maintenance.derived_knowledge.v1");
    assert_eq!(wisdom.workflow_id, "maintenance.execution_wisdom.v1");
    assert_eq!(crosslink.workflow_id, "maintenance.crosslink_group.v1");
    assert_eq!(multimodal.workflow_id, "retrieval.multimodal_sidecar.v1");
    assert_eq!(derived.nodes.len(), 3);
    assert_eq!(wisdom.nodes.len(), 2);
    assert_eq!(crosslink.nodes.len(), 10);
    assert_eq!(multimodal.nodes.len(), 3);
    assert!(crosslink.nodes.iter().all(|node| node.metadata["wf_op"]
        .as_str()
        .unwrap()
        .starts_with("crosslink_")));
}
