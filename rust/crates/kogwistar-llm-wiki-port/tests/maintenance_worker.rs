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
