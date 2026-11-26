use kogwistar_llm_wiki_port::maintenance_policy::{
    is_execution_wisdom_kind, is_graph_patch_kind, normalize_maintenance_kind,
    workflow_id_for_maintenance_kind, MaintenanceWorkflowId,
};

#[test]
fn normalize_maintenance_kind_defaults_to_distill() {
    assert_eq!(normalize_maintenance_kind(None), "distill");
    assert_eq!(normalize_maintenance_kind(Some("  Distill  ")), "distill");
}

#[test]
fn workflow_id_routes_execution_wisdom() {
    for kind in [
        "execution_wisdom",
        "derive_problem_solving_wisdom_from_history",
        "distill_to_wisdom",
    ] {
        assert_eq!(
            workflow_id_for_maintenance_kind(Some(kind)).unwrap(),
            MaintenanceWorkflowId::ExecutionWisdom
        );
    }
    assert!(is_execution_wisdom_kind(Some("history_wisdom")));
}

#[test]
fn workflow_id_defaults_to_derived_knowledge() {
    assert_eq!(
        workflow_id_for_maintenance_kind(Some("distill")).unwrap(),
        MaintenanceWorkflowId::DerivedKnowledge
    );
    assert!(!is_execution_wisdom_kind(Some("distill")));
}

#[test]
fn workflow_id_routes_graph_patch_jobs() {
    assert_eq!(
        workflow_id_for_maintenance_kind(Some("document_seed_graph")).unwrap(),
        MaintenanceWorkflowId::GraphPatchProposal
    );
    assert_eq!(
        workflow_id_for_maintenance_kind(Some("document_propose_crosslinks")).unwrap(),
        MaintenanceWorkflowId::CrosslinkGroup
    );
    assert_eq!(
        workflow_id_for_maintenance_kind(Some("conversation_promote_to_kg")).unwrap(),
        MaintenanceWorkflowId::GraphPatchProposal
    );
    assert_eq!(
        workflow_id_for_maintenance_kind(Some("entity_merge_candidate")).unwrap(),
        MaintenanceWorkflowId::GraphPatchProposal
    );
    assert!(
        workflow_id_for_maintenance_kind(Some("entity_disambiguation_scan"))
            .unwrap_err()
            .message
            .contains("direct maintenance operation")
    );
    assert_eq!(
        workflow_id_for_maintenance_kind(Some("entity_disambiguation_reconcile")).unwrap(),
        MaintenanceWorkflowId::GraphPatchProposal
    );
    assert_eq!(
        workflow_id_for_maintenance_kind(Some("entity_disambiguation_review")).unwrap(),
        MaintenanceWorkflowId::GraphPatchProposal
    );
    assert_eq!(
        workflow_id_for_maintenance_kind(Some("entity_disambiguation_patch_proposal")).unwrap(),
        MaintenanceWorkflowId::GraphPatchProposal
    );
    assert_eq!(
        workflow_id_for_maintenance_kind(Some("graph_patch_apply")).unwrap(),
        MaintenanceWorkflowId::GraphPatchApply
    );
    assert!(is_graph_patch_kind(Some("document_correct_parse_children")));
    assert!(!is_graph_patch_kind(Some("distill")));
}
