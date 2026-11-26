use kogwistar_llm_wiki_port::maintenance_lane::{
    durable_claims_snapshot, identity_from_durable_claims, lane_reply_decision,
    runtime_authority_context, LaneReplyAction,
};
use serde_json::{json, Value};

fn map(value: serde_json::Value) -> kogwistar_llm_wiki_port::models::JsonObject {
    value
        .as_object()
        .expect("JSON object")
        .iter()
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect()
}

#[test]
fn lane_reply_normalizes_and_wraps_payload() {
    let decision = lane_reply_decision(
        "workspace-1",
        "doc-1",
        "request-1",
        Some("reply-message-1"),
        "wrong",
        &map(json!({"maintenance_kind": "distill"})),
        false,
    );

    assert_eq!(decision.action, LaneReplyAction::Send);
    assert_eq!(
        decision.msg_type.as_deref(),
        Some("reply.maintenance.failed")
    );
    assert_eq!(decision.lane_status, Some("failed"));
    assert!(decision.lane_error.is_some());
    assert!(decision.lane_completed);
    assert_eq!(
        decision.idempotency_key.as_deref(),
        Some("6213487e-e11a-542b-8a69-963d17442c1e")
    );
    assert_eq!(decision.conversation_id, "maintenance:doc-1");
    assert_eq!(decision.payload["workspace_id"], json!("workspace-1"));
    assert_eq!(decision.payload["request_node_id"], json!("request-1"));
    assert_eq!(decision.payload["maintenance_kind"], json!("distill"));
}

#[test]
fn lane_reply_suppresses_after_completed_reply() {
    let decision = lane_reply_decision(
        "workspace-1",
        "doc-1",
        "request-1",
        Some("reply-message-1"),
        "failed",
        &Default::default(),
        true,
    );

    assert_eq!(decision.action, LaneReplyAction::Suppress);
    assert_eq!(
        decision.msg_type.as_deref(),
        Some("reply.maintenance.failed")
    );
    assert!(decision.lane_error.is_none());
}

#[test]
fn lane_reply_without_reply_target_skips() {
    let decision = lane_reply_decision(
        "workspace-1",
        "doc-1",
        "request-1",
        None,
        "completed",
        &Default::default(),
        false,
    );
    assert_eq!(decision.action, LaneReplyAction::Skip);
}

#[test]
fn durable_claims_snapshot_filters_python_allowed_keys() {
    let claims = json!({
        "sub": "user-1",
        "role": "rw",
        "scope": "read write",
        "token": "never",
        "ignored": null
    });
    let snapshot = durable_claims_snapshot(Some(&claims)).unwrap();
    assert_eq!(
        serde_json::to_value(&snapshot).unwrap(),
        json!({"sub": "user-1", "role": "rw", "scope": "read write"})
    );
    assert_eq!(durable_claims_snapshot(Some(&json!({}))), None);
}

#[test]
fn durable_identity_matches_python_contract() {
    let claims = json!({
        "sub": "user-1",
        "role": "ADMIN",
        "scope": "read write",
        "workspaces": "ws-1,ws-2",
        "tenant": "Tenant-1"
    });
    let identity = identity_from_durable_claims(Some(&claims)).unwrap();
    assert_eq!(identity.principal_id, "user-1");
    assert_eq!(identity.role, "ro");
    assert!(identity.scopes.contains("read"));
    assert!(identity.scopes.contains("write"));
    assert_eq!(
        identity.workspaces.iter().cloned().collect::<Vec<_>>(),
        vec!["ws-1", "ws-2"]
    );
    assert_eq!(identity.security_scope, "tenant-1");
    assert_eq!(identity.auth_mode, "durable");
}

#[test]
fn runtime_authority_matches_python_context() {
    let claims = json!({
        "agent_id": "agent-1",
        "tenant": "Tenant-1",
        "capabilities": "read write"
    });
    assert_eq!(
        runtime_authority_context(Some(&claims), "workspace-1"),
        json!({
            "principal_id": "agent-1",
            "tenant_id": "tenant-1",
            "project_id": "workspace-1",
            "security_scope": "tenant-1",
            "effective_capabilities": ["read", "write"]
        })
    );
    assert_eq!(
        runtime_authority_context(Some(&Value::Null), "workspace-1"),
        json!({
            "principal_id": "system",
            "tenant_id": null,
            "project_id": "workspace-1",
            "security_scope": null,
            "effective_capabilities": []
        })
    );
}
