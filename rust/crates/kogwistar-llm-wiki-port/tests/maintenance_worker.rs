use kogwistar_llm_wiki_port::maintenance_worker::{
    expected_revisions_from_mapping, expected_revisions_from_payload, maintenance_run_identity,
    MaintenanceRunContext,
};
use serde_json::{json, Value};

fn object(value: Value) -> kogwistar_llm_wiki_port::models::JsonObject {
    serde_json::from_value(value).unwrap()
}

fn payload(expected_revisions: Value) -> kogwistar_llm_wiki_port::models::JsonObject {
    serde_json::from_value(json!({"expected_revisions": expected_revisions})).unwrap()
}

#[test]
fn expected_revisions_preserve_string_integer_and_null_fences() {
    assert_eq!(
        expected_revisions_from_payload(&payload(
            json!({"node:1": 3, "edge:2": "rev-a", "node:none": null})
        )),
        Some(object(
            json!({"edge:2": "rev-a", "node:1": 3, "node:none": null}),
        ))
    );
}

#[test]
fn expected_revisions_reject_bool_and_complex_fences() {
    assert_eq!(
        expected_revisions_from_payload(&payload(json!({"node:1": true}))),
        Some(object(json!({"node:1": null})))
    );
    assert_eq!(
        expected_revisions_from_payload(&payload(json!({"node:1": []}))),
        Some(object(json!({"node:1": null})))
    );
    assert_eq!(expected_revisions_from_payload(&payload(json!([]))), None);
    assert_eq!(expected_revisions_from_mapping(None), None);
}

#[test]
fn maintenance_run_identity_prefers_explicit_run_id_and_attempt() {
    let payload = serde_json::from_value(json!({
        "maintenance_run_id": "run-a",
        "maintenance_attempt": 5
    }))
    .unwrap();
    let context = MaintenanceRunContext {
        workspace_id: "ws-1",
        request_node_id: "request-1",
        job_id: "job-1",
        maintenance_kind: "document_propose_crosslinks",
        worker_id: "maintenance-worker",
        claim_attempts: 9,
    };
    assert_eq!(
        maintenance_run_identity(&payload, &context),
        object(json!({
            "maintenance_run_id": "run-a",
            "workspace_id": "ws-1",
            "request_node_id": "request-1",
            "job_id": "job-1",
            "worker_id": "maintenance-worker",
            "attempt": 5,
            "maintenance_kind": "document_propose_crosslinks",
            "workflow_version": "crosslink-groups.v1"
        }))
    );
}

#[test]
fn maintenance_run_identity_uses_claim_attempts_then_stable_id() {
    let payload = kogwistar_llm_wiki_port::models::JsonObject::new();
    let context = MaintenanceRunContext {
        workspace_id: "ws-1",
        request_node_id: "request-1",
        job_id: "job-1",
        maintenance_kind: "document_propose_crosslinks",
        worker_id: "maintenance-worker",
        claim_attempts: 3,
    };
    let identity = maintenance_run_identity(&payload, &context);
    assert_eq!(identity["attempt"], json!(3));
    assert_eq!(
        identity["maintenance_run_id"],
        json!("f7e3d848-82a3-5869-9dcc-46ce218e207f")
    );
}

use kogwistar_llm_wiki_port::maintenance_worker::{
    accepted_candidate_from_payload, graph_patch_strategy_input, validate_graph_patch_replacement,
    ReplacementContract,
};

#[test]
fn accepted_candidate_extracts_graph_patch_kind_and_patch() {
    let payload = serde_json::from_value(json!({
        "accepted_candidate": {"kind": "graph_patch", "patch": {"op": "add_edge"}}
    }))
    .unwrap();
    let candidate = accepted_candidate_from_payload(&payload).unwrap();
    assert_eq!(candidate.kind, "graph_patch");
    assert_eq!(candidate.patch, Some(json!({"op": "add_edge"})));
}

#[test]
fn accepted_candidate_returns_none_for_non_object() {
    let payload = serde_json::from_value(json!({"accepted_candidate": "not-an-object"})).unwrap();
    assert_eq!(accepted_candidate_from_payload(&payload), None);
    let empty = kogwistar_llm_wiki_port::models::JsonObject::new();
    assert_eq!(accepted_candidate_from_payload(&empty), None);
}

#[test]
fn graph_patch_strategy_input_extracts_patch_only_for_graph_patch_kind() {
    let context = MaintenanceRunContext {
        workspace_id: "ws-1",
        request_node_id: "request-1",
        job_id: "job-1",
        maintenance_kind: "graph_patch_apply",
        worker_id: "worker-1",
        claim_attempts: 2,
    };
    let payload = serde_json::from_value(json!({
        "accepted_candidate": {"kind": "graph_patch", "patch": {"edges": {}}}
    }))
    .unwrap();
    let input = graph_patch_strategy_input(&payload, &context);
    assert_eq!(input.patch_payload, Some(json!({"edges": {}})));
    assert_eq!(input.maintenance_kind, "graph_patch_apply");
    assert_eq!(input.accepted_candidate.unwrap().kind, "graph_patch");
}

#[test]
fn graph_patch_strategy_input_ignores_patch_for_other_kind() {
    let context = MaintenanceRunContext {
        workspace_id: "ws-1",
        request_node_id: "request-1",
        job_id: "job-1",
        maintenance_kind: "document_propose_crosslinks",
        worker_id: "worker-1",
        claim_attempts: 0,
    };
    let payload = serde_json::from_value(json!({
        "accepted_candidate": {"kind": "entity_disambiguation_review", "patch": {"x": 1}}
    }))
    .unwrap();
    let input = graph_patch_strategy_input(&payload, &context);
    assert_eq!(input.patch_payload, None);
    assert_eq!(
        input.accepted_candidate.unwrap().kind,
        "entity_disambiguation_review"
    );
}

#[test]
fn validate_graph_patch_rejection_refuses_source_native_tombstone() {
    let patch = json!({"edges": {"e1": {"tombstone": true}}});
    let contract = ReplacementContract {
        tombstone_source_native: true,
        ..Default::default()
    };
    assert!(validate_graph_patch_replacement(&patch, &contract).is_err());
}

#[test]
fn validate_graph_patch_replacement_accepts_valid_contract() {
    let patch = json!({"edges": {"e1": {"source": "n1", "target": "n2"}}});
    let contract = ReplacementContract::default_supersede();
    let result = validate_graph_patch_replacement(&patch, &contract).unwrap();
    assert_eq!(
        result.replacement_reason,
        "accepted crosslink replacement supersedes stale evidence"
    );
    assert!(result.preserve_endpoints);
    assert!(!result.tombstone_source_native);
}
