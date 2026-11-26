use kogwistar_llm_wiki_port::maintenance_job_lowering::{
    build_review_patch_for_maintenance_job, maintenance_intent_for_job_kind,
};
use kogwistar_llm_wiki_port::maintenance_policy::{
    GRAPH_PATCH_APPLY_KINDS, GRAPH_PATCH_PROPOSAL_KINDS,
};
use kogwistar_llm_wiki_port::maintenance_status::MaintenanceIntent;

#[test]
fn graph_patch_job_kinds_lower_to_typed_review_patches() {
    for maintenance_kind in GRAPH_PATCH_PROPOSAL_KINDS
        .iter()
        .chain(GRAPH_PATCH_APPLY_KINDS.iter())
        .cloned()
        .collect::<Vec<_>>()
    {
        let patch = build_review_patch_for_maintenance_job(
            "demo",
            Some(maintenance_kind),
            &format!("request:{maintenance_kind}"),
            "awaiting graph patch proposal",
            None,
            None,
        );
        assert_eq!(
            patch.intent,
            maintenance_intent_for_job_kind(Some(maintenance_kind))
        );
        assert_eq!(patch.scope.workspace_id, "demo");
        assert_eq!(patch.scope.scope_kind, "workspace");
        assert_eq!(patch.operations.len(), 1);
        assert_eq!(patch.operations[0]["kind"], "REQUEST_REVIEW");
    }
}

#[test]
fn job_lowering_preserves_conversation_scope() {
    let patch = build_review_patch_for_maintenance_job(
        "demo",
        Some("conversation_promote_to_kg"),
        "request-1",
        "needs promotion review",
        Some("conversation:demo:1"),
        None,
    );
    assert_eq!(patch.scope.scope_kind, "conversation");
    assert_eq!(
        patch.scope.conversation_id,
        Some("conversation:demo:1".to_owned())
    );
}

#[test]
fn job_lowering_uses_stable_ids() {
    let patch = build_review_patch_for_maintenance_job(
        "demo",
        Some("graph_patch_apply"),
        "request-1",
        "needs review",
        None,
        None,
    );
    assert_eq!(patch.intent, MaintenanceIntent::RequestReview);
    assert!(patch.patch_id.starts_with("maintenance_job_review:"));
    assert!(patch.operations[0]["operation_id"]
        .as_str()
        .unwrap()
        .starts_with("review:"));
}
