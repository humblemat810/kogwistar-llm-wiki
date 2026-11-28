use std::collections::BTreeSet;

use serde::Serialize;
use serde_json::{json, Value};

use crate::maintenance_lease::maintenance_reply_idempotency_key;
use crate::models::JsonObject;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
pub enum LaneReplyAction {
    Skip,
    Suppress,
    Send,
    ReuseExisting,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct LaneReplyDecision {
    pub action: LaneReplyAction,
    pub msg_type: Option<String>,
    pub lane_status: Option<&'static str>,
    pub lane_error: Option<Value>,
    pub lane_completed: bool,
    pub idempotency_key: Option<String>,
    pub conversation_id: String,
    pub inbox_id: String,
    pub sender_id: String,
    pub recipient_id: String,
    pub reply_to: Option<String>,
    pub correlation_id: Option<String>,
    pub payload: Value,
}

#[allow(clippy::too_many_arguments)]
#[must_use]
pub fn lane_reply_decision(
    workspace_id: &str,
    source_document_id: &str,
    request_node_id: &str,
    reply_to_message_id: Option<&str>,
    status: &str,
    payload: &JsonObject,
    completed_reply_exists: bool,
) -> LaneReplyDecision {
    let empty = LaneReplyDecision {
        action: LaneReplyAction::Skip,
        msg_type: None,
        lane_status: None,
        lane_error: None,
        lane_completed: false,
        idempotency_key: None,
        conversation_id: String::new(),
        inbox_id: String::new(),
        sender_id: String::new(),
        recipient_id: String::new(),
        reply_to: None,
        correlation_id: None,
        payload: Value::Null,
    };
    let Some(reply_to_message_id) = reply_to_message_id.filter(|value| !value.trim().is_empty())
    else {
        return empty;
    };
    let lane_status = normalize_lane_status(status);
    if completed_reply_exists && lane_status != "completed" {
        return LaneReplyDecision {
            action: LaneReplyAction::Suppress,
            msg_type: Some(format!("reply.maintenance.{lane_status}")),
            lane_status: Some(lane_status),
            lane_error: None,
            lane_completed: lane_completed(lane_status),
            idempotency_key: Some(maintenance_reply_idempotency_key(
                workspace_id,
                reply_to_message_id,
                status,
            )),
            conversation_id: conversation_id(source_document_id, request_node_id),
            inbox_id: "inbox:foreground".to_owned(),
            sender_id: "lane:worker:maintenance".to_owned(),
            recipient_id: "lane:foreground".to_owned(),
            reply_to: Some(reply_to_message_id.to_owned()),
            correlation_id: Some(reply_to_message_id.to_owned()),
            payload: Value::Object(payload.clone().into_iter().collect()),
        };
    }
    let lane_error = match lane_status {
        "failed" | "cancelled" => Some(Value::Object(payload.clone().into_iter().collect())),
        _ => None,
    };
    let envelope = JsonObject::from([
        ("workspace_id".to_owned(), Value::from(workspace_id)),
        ("request_node_id".to_owned(), Value::from(request_node_id)),
    ]);
    LaneReplyDecision {
        action: LaneReplyAction::Send,
        msg_type: Some(format!("reply.maintenance.{lane_status}")),
        lane_status: Some(lane_status),
        lane_error,
        lane_completed: lane_completed(lane_status),
        idempotency_key: Some(maintenance_reply_idempotency_key(
            workspace_id,
            reply_to_message_id,
            status,
        )),
        conversation_id: conversation_id(source_document_id, request_node_id),
        inbox_id: "inbox:foreground".to_owned(),
        sender_id: "lane:worker:maintenance".to_owned(),
        recipient_id: "lane:foreground".to_owned(),
        reply_to: Some(reply_to_message_id.to_owned()),
        correlation_id: Some(reply_to_message_id.to_owned()),
        payload: {
            let mut value = serde_json::Map::new();
            for (key, item) in envelope {
                value.insert(key, item);
            }
            for (key, item) in payload {
                value.insert(key.clone(), item.clone());
            }
            Value::Object(value)
        },
    }
}

#[must_use]
pub fn normalize_lane_status(status: &str) -> &'static str {
    match status {
        "completed" => "completed",
        "failed" => "failed",
        "cancelled" => "cancelled",
        "suspended" => "suspended",
        _ => "failed",
    }
}

#[must_use]
pub fn lane_completed(status: &str) -> bool {
    matches!(status, "completed" | "failed" | "cancelled")
}

#[must_use]
fn conversation_id(source_document_id: &str, request_node_id: &str) -> String {
    format!(
        "maintenance:{}",
        if source_document_id.is_empty() {
            request_node_id
        } else {
            source_document_id
        }
    )
}

const DURABLE_CLAIM_KEYS: [&str; 16] = [
    "sub",
    "client_id",
    "agent_id",
    "role",
    "scope",
    "scp",
    "security_scope",
    "tenant",
    "groups",
    "group",
    "roles",
    "principal_groups",
    "workspaces",
    "workspace_ids",
    "allowed_workspaces",
    "storage_ns",
];

#[must_use]
pub fn durable_claims_snapshot(claims: Option<&Value>) -> Option<JsonObject> {
    let Some(Value::Object(values)) = claims else {
        return None;
    };
    let snapshot: JsonObject = values
        .iter()
        .filter(|(key, value)| DURABLE_CLAIM_KEYS.contains(&key.as_str()) && !value.is_null())
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect();
    if snapshot.is_empty() {
        None
    } else {
        Some(snapshot)
    }
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct DurableIdentity {
    pub principal_id: String,
    pub scopes: BTreeSet<String>,
    pub role: String,
    pub security_scope: String,
    pub workspaces: BTreeSet<String>,
    pub claims: JsonObject,
    pub auth_mode: &'static str,
}

#[derive(Debug)]
pub enum DurableIdentityError {
    NoPrincipal,
}

impl std::fmt::Display for DurableIdentityError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::NoPrincipal => {
                write!(formatter, "durable authority claims have no principal")
            }
        }
    }
}

impl std::error::Error for DurableIdentityError {}

fn string_items(value: Option<&Value>, split_words: bool) -> BTreeSet<String> {
    let Some(value) = value else {
        return BTreeSet::new();
    };
    match value {
        Value::String(value) => {
            if split_words {
                value
                    .split_whitespace()
                    .map(|item| item.trim().to_ascii_lowercase())
                    .filter(|item| !item.is_empty())
                    .collect()
            } else {
                value
                    .split(',')
                    .map(|item| item.trim().to_ascii_lowercase())
                    .filter(|item| !item.is_empty())
                    .collect()
            }
        }
        Value::Array(values) => values
            .iter()
            .filter_map(Value::as_str)
            .map(|item| item.trim().to_ascii_lowercase())
            .filter(|item| !item.is_empty())
            .collect(),
        _ => BTreeSet::new(),
    }
}

fn workspace_items(value: Option<&Value>) -> BTreeSet<String> {
    let Some(value) = value else {
        return BTreeSet::new();
    };
    match value {
        Value::String(value) => value
            .split(',')
            .map(|item| item.trim().to_owned())
            .filter(|item| !item.is_empty())
            .collect(),
        Value::Array(values) => values
            .iter()
            .filter_map(Value::as_str)
            .map(|item| item.trim().to_owned())
            .filter(|item| !item.is_empty())
            .collect(),
        _ => BTreeSet::new(),
    }
}

pub fn identity_from_durable_claims(
    claims: Option<&Value>,
) -> Result<DurableIdentity, DurableIdentityError> {
    let Some(Value::Object(values)) = claims else {
        return Err(DurableIdentityError::NoPrincipal);
    };
    let principal = values
        .get("sub")
        .and_then(Value::as_str)
        .or_else(|| values.get("client_id").and_then(Value::as_str))
        .or_else(|| values.get("agent_id").and_then(Value::as_str))
        .unwrap_or_default()
        .trim();
    if principal.is_empty() {
        return Err(DurableIdentityError::NoPrincipal);
    }
    let scopes = string_items(values.get("scope"), true)
        .union(&string_items(values.get("scp"), true))
        .cloned()
        .collect::<BTreeSet<_>>();
    let role = values
        .get("role")
        .and_then(Value::as_str)
        .unwrap_or("ro")
        .trim()
        .to_ascii_lowercase();
    let role = if role == "rw" { "rw" } else { "ro" }.to_owned();
    let workspaces = {
        let mut selected = workspace_items(values.get("workspaces"));
        if selected.is_empty() {
            selected = workspace_items(values.get("workspace_ids"));
        }
        if selected.is_empty() {
            selected = workspace_items(values.get("allowed_workspaces"));
        }
        selected
    };
    let security_scope = values
        .get("security_scope")
        .and_then(Value::as_str)
        .or_else(|| values.get("tenant").and_then(Value::as_str))
        .unwrap_or(principal)
        .trim()
        .to_ascii_lowercase();
    Ok(DurableIdentity {
        principal_id: principal.to_owned(),
        scopes,
        role,
        security_scope,
        workspaces,
        claims: values
            .iter()
            .map(|(key, value)| (key.clone(), value.clone()))
            .collect(),
        auth_mode: "durable",
    })
}

#[must_use]
pub fn runtime_authority_context(claims: Option<&Value>, workspace_id: &str) -> Value {
    let Some(Value::Object(values)) = claims else {
        return json!({
            "principal_id": "system",
            "tenant_id": null,
            "project_id": workspace_id,
            "security_scope": null,
            "effective_capabilities": []
        });
    };
    let principal = values
        .get("agent_id")
        .and_then(Value::as_str)
        .or_else(|| values.get("sub").and_then(Value::as_str))
        .or_else(|| values.get("client_id").and_then(Value::as_str))
        .unwrap_or("system")
        .trim();
    let security_scope = values
        .get("security_scope")
        .and_then(Value::as_str)
        .or_else(|| values.get("tenant").and_then(Value::as_str))
        .unwrap_or_default()
        .trim()
        .to_ascii_lowercase();
    let capabilities = match values.get("capabilities") {
        Some(Value::String(value)) => value
            .split_whitespace()
            .map(Value::from)
            .collect::<Vec<_>>(),
        Some(Value::Array(values)) => values
            .iter()
            .filter_map(Value::as_str)
            .filter(|item| !item.trim().is_empty())
            .map(Value::from)
            .collect(),
        _ => Vec::new(),
    };
    json!({
        "principal_id": principal,
        "tenant_id": if security_scope.is_empty() {
            Value::Null
        } else {
            Value::from(security_scope.clone())
        },
        "project_id": workspace_id,
        "security_scope": if security_scope.is_empty() {
            Value::Null
        } else {
            Value::from(security_scope)
        },
        "effective_capabilities": capabilities
    })
}
