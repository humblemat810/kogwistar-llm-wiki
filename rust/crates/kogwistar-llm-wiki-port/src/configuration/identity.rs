//! Authentication and workspace authorization contract primitives.

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::BTreeSet;

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct LlmWikiIdentity {
    pub principal_id: String,
    pub scopes: BTreeSet<String>,
    pub role: String,
    pub security_scope: String,
    pub workspaces: BTreeSet<String>,
    pub claims: Value,
    pub auth_mode: String,
}

impl LlmWikiIdentity {
    pub fn new<I, S>(
        principal_id: &str,
        scopes: I,
        role: &str,
        security_scope: &str,
        workspaces: &[&str],
        claims: Value,
        auth_mode: &str,
    ) -> Self
    where
        I: IntoIterator<Item = S>,
        S: AsRef<str>,
    {
        Self {
            principal_id: principal_id.to_string(),
            scopes: scopes
                .into_iter()
                .map(|scope| scope.as_ref().to_owned())
                .collect(),
            role: role.to_string(),
            security_scope: security_scope.to_string(),
            workspaces: workspaces.iter().map(|name| name.to_string()).collect(),
            claims,
            auth_mode: auth_mode.to_string(),
        }
    }
}

#[derive(Debug, Eq, PartialEq)]
pub struct IdentityError {
    pub message: String,
    pub status: u16,
}

impl std::fmt::Display for IdentityError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.message)
    }
}

impl std::error::Error for IdentityError {}

fn string_items(value: &Value) -> BTreeSet<String> {
    match value {
        Value::String(text) => text
            .split(',')
            .map(str::trim)
            .filter(|item| !item.is_empty())
            .map(str::to_lowercase)
            .collect(),
        Value::Array(values) => values
            .iter()
            .filter_map(Value::as_str)
            .map(str::trim)
            .filter(|item| !item.is_empty())
            .map(str::to_lowercase)
            .collect(),
        _ => BTreeSet::new(),
    }
}

#[must_use]
pub fn identity_can(identity: &LlmWikiIdentity, scope: &str) -> bool {
    identity.scopes.contains(scope)
        || identity.scopes.contains("admin")
        || (identity.role == "rw" && scope == "write")
}

pub fn workspace_permissions(
    identity: &LlmWikiIdentity,
    workspace_id: &str,
    acl: &Value,
) -> Result<BTreeSet<String>, IdentityError> {
    let workspace = workspace_id.trim();
    if workspace.is_empty() {
        return Err(IdentityError {
            message: "workspace_id is required for this operation".to_string(),
            status: 400,
        });
    }

    let entry = acl.get(&identity.principal_id).or_else(|| acl.get("*"));
    let entry = entry.map(|value| {
        value
            .get("workspaces")
            .filter(|item| item.is_object())
            .unwrap_or(value)
    });
    if let Some(entry) = entry.filter(|item| item.is_object()) {
        return Ok(string_items(
            entry
                .get(workspace)
                .unwrap_or(entry.get("*").unwrap_or(&Value::Null)),
        ));
    }

    if identity.workspaces.contains(workspace) || identity.workspaces.contains("*") {
        return Ok(identity.scopes.clone());
    }
    Ok(BTreeSet::new())
}

pub fn authorize(
    identity: Option<&LlmWikiIdentity>,
    workspace_id: Option<&str>,
    scope: &str,
    acl: &Value,
) -> Result<(), IdentityError> {
    let Some(identity) = identity else {
        return Ok(());
    };
    if !identity_can(identity, scope) {
        return Err(IdentityError {
            message: format!("forbidden: scope '{scope}' is required"),
            status: 403,
        });
    }
    let Some(workspace_id) = workspace_id else {
        return Ok(());
    };
    let permissions = workspace_permissions(identity, workspace_id, acl)?;
    if !permissions.contains(scope) && !permissions.contains("admin") {
        return Err(IdentityError {
            message: "forbidden: identity is not a member of this workspace".to_string(),
            status: 403,
        });
    }
    Ok(())
}

#[must_use]
pub fn runtime_authority_context(claims: &Value, workspace_id: &str) -> Value {
    let source = if claims.is_object() {
        claims
    } else {
        &Value::Null
    };
    let principal = source
        .get("agent_id")
        .or_else(|| source.get("sub"))
        .or_else(|| source.get("client_id"))
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|principal| !principal.is_empty())
        .unwrap_or("system");
    let security_scope = source
        .get("security_scope")
        .or_else(|| source.get("tenant"))
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|scope| !scope.is_empty())
        .map(str::to_lowercase);
    let capabilities = source
        .get("capabilities")
        .map_or_else(Vec::new, |value| match value {
            Value::String(text) => text.split_whitespace().map(str::to_owned).collect(),
            Value::Array(values) => values
                .iter()
                .filter_map(Value::as_str)
                .map(str::trim)
                .filter(|capability| !capability.is_empty())
                .map(str::to_owned)
                .collect(),
            _ => Vec::new(),
        });
    json!({
        "principal_id": principal,
        "tenant_id": security_scope,
        "project_id": workspace_id,
        "security_scope": security_scope,
        "effective_capabilities": capabilities,
    })
}
