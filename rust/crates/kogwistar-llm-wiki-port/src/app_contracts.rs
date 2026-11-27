use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum MessageIntent {
    Request,
    Notification,
    Alert,
}

impl MessageIntent {
    #[must_use]
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Request => "request",
            Self::Notification => "notification",
            Self::Alert => "alert",
        }
    }
}

#[derive(Debug, Eq, PartialEq)]
pub struct MessageContractError(String);

impl MessageContractError {
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl std::fmt::Display for MessageContractError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for MessageContractError {}

#[derive(Debug, Eq, PartialEq)]
pub struct SourceRequestError(String);

impl SourceRequestError {
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl std::fmt::Display for SourceRequestError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for SourceRequestError {}

pub fn validate_source_request(payload: &Value) -> Result<(String, String), SourceRequestError> {
    let Some(workspace_id) = payload.get("workspace_id").and_then(Value::as_str) else {
        return Err(SourceRequestError(
            "Obsidian source identity values must be strings".to_owned(),
        ));
    };
    let Some(vault_id) = payload.get("vault_id").and_then(Value::as_str) else {
        return Err(SourceRequestError(
            "Obsidian source identity values must be strings".to_owned(),
        ));
    };
    let workspace_id = workspace_id.trim();
    let vault_id = vault_id.trim();
    if workspace_id.is_empty() || vault_id.is_empty() {
        return Err(SourceRequestError(
            "Obsidian source requests require workspace_id and vault_id".to_owned(),
        ));
    }
    if workspace_id.len() > 256 || vault_id.len() > 256 {
        return Err(SourceRequestError(
            "Obsidian source identity values are too long".to_owned(),
        ));
    }
    Ok((workspace_id.to_owned(), vault_id.to_owned()))
}

pub struct MessageChannel;

impl MessageChannel {
    #[must_use]
    pub fn wrap_message(
        payload: &Value,
        target: &str,
        intent: MessageIntent,
        provenance_id: Option<&str>,
    ) -> Value {
        json!({
            "target": target,
            "payload": payload,
            "intent": intent.as_str(),
            "provenance_id": provenance_id,
        })
    }
}
