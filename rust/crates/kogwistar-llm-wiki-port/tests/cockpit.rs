//! Cockpit gate tests: action shape validation and proposal validation mirroring
//! `kogwistar_llm_wiki.workbench.workbench_cockpit`.

use kogwistar_llm_wiki_port::cockpit::{
    validate_cockpit_proposal, CockpitAction, CockpitActionKind, CockpitLimits,
};
use kogwistar_llm_wiki_port::lens_projection::{
    project_bounded, validate_edit_proposal, LensNode, RetrievalMode, SemanticLensRequest,
};
use kogwistar_llm_wiki_port::maintenance_patches::{
    MaintenanceOperationKind, MaintenancePatch, MaintenancePatchOperation, MaintenanceProvenance,
    MaintenanceScope,
};
use kogwistar_llm_wiki_port::maintenance_status::MaintenanceIntent;
use serde_json::{json, Value};

fn lens_node_with_grounding(id: &str, doc_id: &str) -> LensNode {
    LensNode {
        id: id.to_owned(),
        graph_space: "curated_kg".to_owned(),
        namespace: "ns".to_owned(),
        label: id.to_owned(),
        node_type: "entity".to_owned(),
        entity_revision: None,
        metadata: json!({}),
        grounding: vec![json!({ "doc_id": doc_id })],
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

fn base_snapshot() -> kogwistar_llm_wiki_port::lens_projection::SemanticLensSnapshot {
    project_bounded(
        &base_request(),
        vec![lens_node_with_grounding("n1", "doc-a")],
        vec![],
        0,
    )
}

fn test_provenance() -> MaintenanceProvenance {
    MaintenanceProvenance {
        source_document_id: Some("doc-a".to_owned()),
        source_span_ids: Vec::new(),
        source_pointers: Vec::new(),
        maintenance_run_id: "run-1".to_owned(),
        confidence: 1.0,
    }
}

fn test_patch() -> MaintenancePatch {
    MaintenancePatch {
        patch_id: "patch-1".to_owned(),
        intent: MaintenanceIntent::AddCrosslink,
        scope: MaintenanceScope {
            workspace_id: "ws-1".to_owned(),
            scope_kind: "workspace".to_owned(),
            conversation_id: None,
            thread_id: None,
        },
        operations: vec![MaintenancePatchOperation {
            operation_id: "op-1".to_owned(),
            kind: MaintenanceOperationKind::AddNode,
            target_id: None,
            node_id: Some("n2".to_owned()),
            edge_id: None,
            from_node_id: None,
            to_node_id: None,
            relation: None,
            label: None,
            node_type: None,
            properties: json!({}),
            supersedes_ids: Vec::new(),
            provenance: Some(test_provenance()),
            reason: None,
        }],
        status: "proposed".to_owned(),
        rationale: None,
        requires_atomic_group: false,
    }
}

fn proposal(
    patch_value: &Value,
    lens_id: &str,
    watermark: &Value,
    targets: &[&str],
    evidence: &[&str],
    operation: &str,
) -> Value {
    json!({
        "maintenance_patch": patch_value,
        "lens_id": lens_id,
        "source_watermark": watermark,
        "target_ids": targets,
        "evidence_ids": evidence,
        "operation": operation,
    })
}

#[test]
fn cockpit_action_rejects_propose_patch_without_patch() {
    let action = CockpitAction {
        kind: CockpitActionKind::ProposePatch,
        patch: None,
        ..default_action()
    };
    assert!(action.validate().is_err());
}

#[test]
fn cockpit_action_rejects_patch_on_non_propose() {
    let mut action = default_action();
    action.patch = Some(test_patch());
    assert!(action.validate().is_err());
}

#[test]
fn cockpit_action_answer_requires_answer() {
    let action = CockpitAction {
        kind: CockpitActionKind::Answer,
        answer: None,
        ..default_action()
    };
    assert!(action.validate().is_err());
}

#[test]
fn cockpit_action_resolve_lens_requires_query_text() {
    let action = CockpitAction {
        kind: CockpitActionKind::ResolveLens,
        query_text: None,
        ..default_action()
    };
    assert!(action.validate().is_err());
}

#[test]
fn cockpit_action_inspect_evidence_requires_entity_ids() {
    let action = CockpitAction {
        kind: CockpitActionKind::InspectEvidence,
        ..default_action()
    };
    assert!(action.validate().is_err());
}

#[test]
fn cockpit_action_rejects_operation_count_out_of_range() {
    let mut patch = test_patch();
    patch.operations = vec![test_patch().operations[0].clone(); 13];
    let mut action = default_action();
    action.kind = CockpitActionKind::ProposePatch;
    action.patch = Some(patch);
    assert!(action.validate().is_err());
    let mut patch = test_patch();
    patch.operations = Vec::new();
    let mut action = default_action();
    action.kind = CockpitActionKind::ProposePatch;
    action.patch = Some(patch);
    assert!(action.validate().is_err());
}

#[test]
fn cockpit_action_valid_propose_patch_passes() {
    let mut action = default_action();
    action.kind = CockpitActionKind::ProposePatch;
    action.patch = Some(test_patch());
    assert!(action.validate().is_ok());
}

#[test]
fn cockpit_limits_validates_max_actions() {
    let mut limits = CockpitLimits::default();
    assert!(limits.validate().is_ok());
    limits.max_actions = 0;
    assert!(limits.validate().is_err());
    limits.max_actions = 9;
    assert!(limits.validate().is_err());
}

#[test]
fn validate_edit_proposal_envelope_still_rejects_stale_lens() {
    let snap = base_snapshot();
    let val = validate_edit_proposal(&snap, &json!({ "lens_id": "lens:stale" }));
    assert!(!val.accepted);
    assert_eq!(val.reason, "stale_lens_id");
}

fn cockpit_proposal(patch: &MaintenancePatch) -> Value {
    proposal(
        &serde_json::to_value(patch).unwrap(),
        &base_snapshot().lens_id,
        &json!(null),
        &["n1"],
        &["n1"],
        "maintenance_patch",
    )
}

#[test]
fn validate_cockpit_proposal_missing_maintenance_patch_rejected() {
    let snap = base_snapshot();
    let proposal = json!({ "lens_id": snap.lens_id, "source_watermark": null, "target_ids": ["n1"], "evidence_ids": ["n1"], "operation": "maintenance_patch" });
    let (ok, reason) = validate_cockpit_proposal(&snap, &proposal, None);
    assert!(!ok);
    assert_eq!(reason, "maintenance_patch_required");
}

#[test]
fn validate_cockpit_proposal_invalid_maintenance_patch_rejected() {
    let snap = base_snapshot();
    let mut proposal = cockpit_proposal(&test_patch());
    proposal
        .as_object_mut()
        .unwrap()
        .insert("maintenance_patch".to_owned(), json!({}));
    let (ok, reason) = validate_cockpit_proposal(&snap, &proposal, None);
    assert!(!ok);
    assert!(reason.starts_with("invalid_maintenance_patch"));
}

#[test]
fn validate_cockpit_proposal_workspace_mismatch_rejected() {
    let snap = base_snapshot();
    let (ok, reason) =
        validate_cockpit_proposal(&snap, &cockpit_proposal(&test_patch()), Some("ws-2"));
    assert!(!ok);
    assert_eq!(reason, "maintenance_scope_workspace_mismatch");
}

#[test]
fn validate_cockpit_proposal_scope_not_workspace_rejected() {
    let snap = base_snapshot();
    let mut patch = test_patch();
    patch.scope.scope_kind = "conversation".to_owned();
    let (ok, reason) = validate_cockpit_proposal(&snap, &cockpit_proposal(&patch), None);
    assert!(!ok);
    assert_eq!(reason, "cockpit_scope_not_supported");
}

#[test]
fn validate_cockpit_proposal_stale_watermark_rejected() {
    let snap = base_snapshot();
    let mut proposal = cockpit_proposal(&test_patch());
    proposal
        .as_object_mut()
        .unwrap()
        .insert("source_watermark".to_owned(), json!("other"));
    let (ok, reason) = validate_cockpit_proposal(&snap, &proposal, None);
    assert!(!ok);
    assert_eq!(reason, "stale_source_watermark");
}

#[test]
fn validate_cockpit_proposal_target_not_in_lens_rejected() {
    let snap = base_snapshot();
    let mut proposal = cockpit_proposal(&test_patch());
    proposal
        .as_object_mut()
        .unwrap()
        .insert("target_ids".to_owned(), json!(["n9"]));
    let (ok, reason) = validate_cockpit_proposal(&snap, &proposal, None);
    assert!(!ok);
    assert_eq!(reason, "target_not_in_scoped_lens");
}

#[test]
fn validate_cockpit_proposal_evidence_not_in_lens_rejected() {
    let snap = base_snapshot();
    let mut proposal = cockpit_proposal(&test_patch());
    proposal
        .as_object_mut()
        .unwrap()
        .insert("evidence_ids".to_owned(), json!(["n9"]));
    let (ok, reason) = validate_cockpit_proposal(&snap, &proposal, None);
    assert!(!ok);
    assert_eq!(reason, "evidence_not_in_scoped_lens");
}

#[test]
fn validate_cockpit_proposal_missing_provenance_rejected() {
    let snap = base_snapshot();
    let mut patch = test_patch();
    patch.operations[0].provenance = None;
    let (ok, reason) = validate_cockpit_proposal(&snap, &cockpit_proposal(&patch), None);
    assert!(!ok);
    assert_eq!(reason, "operation_missing_provenance:op-1");
}

#[test]
fn validate_cockpit_proposal_grounding_not_in_evidence_rejected() {
    let snap = base_snapshot();
    let mut patch = test_patch();
    patch.operations[0].provenance = Some(MaintenanceProvenance {
        source_document_id: Some("doc-b".to_owned()),
        ..test_provenance()
    });
    let (ok, reason) = validate_cockpit_proposal(&snap, &cockpit_proposal(&patch), None);
    assert!(!ok);
    assert_eq!(reason, "operation_grounding_not_in_evidence:op-1");
}

#[test]
fn validate_cockpit_proposal_patch_invalid_rejected() {
    let snap = base_snapshot();
    let mut patch = test_patch();
    patch.operations[0] = MaintenancePatchOperation {
        operation_id: "op-1".to_owned(),
        kind: MaintenanceOperationKind::AddEdge,
        target_id: None,
        node_id: None,
        edge_id: Some("e1".to_owned()),
        from_node_id: Some("n9".to_owned()),
        to_node_id: Some("n1".to_owned()),
        relation: Some("rel".to_owned()),
        label: None,
        node_type: None,
        properties: json!({}),
        supersedes_ids: Vec::new(),
        provenance: Some(test_provenance()),
        reason: None,
    };
    let (ok, reason) = validate_cockpit_proposal(&snap, &cockpit_proposal(&patch), None);
    assert!(!ok);
    assert!(reason.starts_with("maintenance_patch_invalid"));
}

#[test]
fn validate_cockpit_proposal_valid_accepted() {
    let snap = base_snapshot();
    let (ok, reason) = validate_cockpit_proposal(&snap, &cockpit_proposal(&test_patch()), None);
    assert!(ok);
    assert_eq!(reason, "ready_for_explicit_confirmation");
}

fn default_action() -> CockpitAction {
    CockpitAction {
        kind: CockpitActionKind::Answer,
        answer: Some("ok".to_owned()),
        query_text: None,
        entity_ids: Vec::new(),
        patch: None,
        cited_entity_ids: Vec::new(),
        rationale: "".to_owned(),
    }
}
