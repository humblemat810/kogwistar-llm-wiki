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
