use kogwistar_llm_wiki_port::configuration::identity::{
    authorize, identity_can, runtime_authority_context, workspace_permissions, LlmWikiIdentity,
};
use serde_json::{json, Value};

fn identity(workspaces: &[&str]) -> LlmWikiIdentity {
    LlmWikiIdentity::new(
        "alice",
        ["read", "write"],
        "ro",
        "tenant-a",
        workspaces,
        json!({"sub": "alice", "scope": "read write"}),
        "kogwistar_jwt",
    )
}

#[test]
fn scope_check_allows_admin_and_rw_write() {
    let mut ro = identity(&["team-a"]);
    assert!(identity_can(&ro, "read"));
    assert!(identity_can(&ro, "write"));

    ro.role = "ro".to_string();
    ro.scopes = ["read"].into_iter().map(str::to_owned).collect();
    assert!(!identity_can(&ro, "write"));

    ro.scopes = ["admin"].into_iter().map(str::to_owned).collect();
    assert!(identity_can(&ro, "write"));
}

#[test]
fn workspace_membership_is_case_sensitive() {
    let acl = Value::Null;
    let identity = identity(&["Team-A"]);

    assert_eq!(
        workspace_permissions(&identity, "Team-A", &acl).unwrap(),
        ["read", "write"].into_iter().map(str::to_owned).collect()
    );
    let error = authorize(Some(&identity), Some("team-a"), "read", &acl).unwrap_err();
    assert_eq!(error.status, 403);
}

#[test]
fn explicit_acl_grants_without_workspace_claim() {
    let acl = json!({
        "alice": {"workspaces": {"team-b": ["read"]}}
    });
    let identity = identity(&[]);

    assert_eq!(
        workspace_permissions(&identity, "team-b", &acl).unwrap(),
        ["read"].into_iter().map(str::to_owned).collect()
    );
    let error = authorize(Some(&identity), Some("team-b"), "write", &acl).unwrap_err();
    assert_eq!(error.status, 403);
}

#[test]
fn disabled_identity_bypasses_authorization() {
    let acl = Value::Null;

    assert_eq!(authorize(None, Some("team-a"), "write", &acl), Ok(()));
}

#[test]
fn runtime_authority_context_never_invents_capabilities() {
    let context = runtime_authority_context(
        &json!({"sub": "alice", "security_scope": "tenant-a", "scope": "read write"}),
        "team-a",
    );

    assert_eq!(
        context,
        json!({
            "principal_id": "alice",
            "tenant_id": "tenant-a",
            "project_id": "team-a",
            "security_scope": "tenant-a",
            "effective_capabilities": []
        })
    );
}
