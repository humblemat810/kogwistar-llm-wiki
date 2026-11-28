// T5.11 codex bridge - mirroring Python codex/codex_bridge.py
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

use crate::models::JsonObject;

pub const MAX_BODY_BYTES: u64 = 20 * 1024 * 1024;
pub const MAX_MESSAGES: usize = 64;
pub const MAX_MESSAGE_CHARS: usize = 120_000;
pub const MAX_SCHEMA_CHARS: usize = 64_000;
pub const MAX_IMAGES: usize = 4;
pub const MAX_IMAGE_BYTES: usize = 12 * 1024 * 1024;

/// Mirrors Python `_codex_transport`.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum CodexTransport {
    #[default]
    Exec,
    AppServer,
}

impl CodexTransport {
    pub fn parse_transport(value: &str) -> Self {
        if value == "app_server" {
            CodexTransport::AppServer
        } else {
            CodexTransport::Exec
        }
    }
}

/// Mirrors Python `CodexCliSettings`.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize, Default)]
pub struct CodexCliSettings {
    pub executable: Option<String>,
    pub model: Option<String>,
    pub profile: Option<String>,
    pub timeout_seconds: i32,
    pub transport: CodexTransport,
}

/// Mirrors Python `_BridgeHandler` request body for `/v1/structured`.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CodexCompleteRequest {
    pub messages: Vec<CodexMessage>,
    #[serde(rename = "response_schema")]
    pub response_schema: JsonObject,
}

/// Mirrors Python `_BridgeHandler` response body.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct CodexCompleteResponse {
    pub output: JsonObject,
    pub provider: String,
    pub model: String,
}

/// Mirrors Python message dict with `role` and `content`.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct CodexMessage {
    pub role: String,
    pub content: CodexContent,
}

/// Mirrors Python message content: either a string or a list of parts.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(untagged)]
pub enum CodexContent {
    String(String),
    List(Vec<CodexContentPart>),
}

/// Mirrors Python multimodal content part: text or image_url.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CodexContentPart {
    Text { text: String },
    ImageUrl { url: String },
}

/// Mirrors Python `CodexBridgeState` (without the `Mutex`/`runner` runtime fields).
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct CodexBridgeState {
    pub token: String,
    pub settings: CodexCliSettings,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum BridgeError {
    EmptyToken,
    InvalidMessagesCount { got: usize },
    InvalidSchema,
    SchemaTooLarge { bytes: usize },
    InvalidMessage,
    InvalidRole,
    InvalidContent,
    TooManyParts { got: usize },
    UnsupportedPart,
    InvalidImageUrl,
    InvalidBase64Image,
    ImageTooLarge { bytes: usize },
    TooManyImages { got: usize },
    MessageTooLarge { chars: usize },
    InvalidJson(String),
    NonObjectResult,
}

impl std::fmt::Display for BridgeError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::EmptyToken => write!(f, "Codex bridge token must not be empty"),
            Self::InvalidMessagesCount { got } => {
                write!(
                    f,
                    "messages must contain between 1 and {MAX_MESSAGES} items, got {got}"
                )
            }
            Self::InvalidSchema => write!(f, "response_schema must be an object"),
            Self::SchemaTooLarge { bytes } => {
                write!(
                    f,
                    "response_schema exceeds the bridge limit ({bytes} bytes)"
                )
            }
            Self::InvalidMessage => write!(f, "each message must be an object"),
            Self::InvalidRole => write!(f, "message role must not be empty"),
            Self::InvalidContent => {
                write!(f, "message content must be a string or content-part list")
            }
            Self::TooManyParts { got } => {
                write!(f, "message content contains too many parts, got {got}")
            }
            Self::UnsupportedPart => write!(f, "unsupported multimodal content part"),
            Self::InvalidImageUrl => write!(f, "Codex bridge accepts only base64 data-image URLs"),
            Self::InvalidBase64Image => write!(f, "invalid base64 image content"),
            Self::ImageTooLarge { bytes } => {
                write!(f, "image content exceeds the bridge limit ({bytes} bytes)")
            }
            Self::TooManyImages { got } => {
                write!(f, "too many images for one Codex bridge request, got {got}")
            }
            Self::MessageTooLarge { chars } => {
                write!(
                    f,
                    "maintenance context exceeds the bridge limit ({chars} chars)"
                )
            }
            Self::InvalidJson(msg) => write!(f, "Codex returned invalid JSON: {msg}"),
            Self::NonObjectResult => write!(f, "Codex returned a non-object structured result"),
        }
    }
}

impl std::error::Error for BridgeError {}

/// Mirrors Python `bridge_settings_from_environment`.
pub fn bridge_settings_from_environment() -> CodexCliSettings {
    bridge_settings_from_pairs(std::env::vars())
}

/// Build settings from an iterator of (key, value) environment pairs.
/// Exposed for testing without mutating process environment variables.
pub fn bridge_settings_from_pairs<I>(pairs: I) -> CodexCliSettings
where
    I: Iterator<Item = (String, String)>,
{
    let map: BTreeMap<String, String> = pairs.collect();
    let timeout_seconds = map
        .get("KOGWISTAR_MAINTENANCE_CODEX_TIMEOUT_SECONDS")
        .map(|s| s.as_str())
        .and_then(|v| v.parse().ok())
        .unwrap_or(300)
        .max(1);
    let transport = CodexTransport::parse_transport(
        map.get("KOGWISTAR_CODEX_TRANSPORT")
            .map(|s| s.as_str())
            .unwrap_or(""),
    );
    CodexCliSettings {
        executable: map
            .get("KOGWISTAR_CODEX_EXECUTABLE")
            .map(|s| s.as_str())
            .map(|s| s.to_owned()),
        model: map
            .get("KOGWISTAR_MAINTENANCE_CODEX_MODEL")
            .map(|s| s.as_str())
            .map(|s| s.to_owned()),
        profile: map
            .get("KOGWISTAR_CODEX_PROFILE")
            .map(|s| s.as_str())
            .map(|s| s.to_owned()),
        timeout_seconds,
        transport,
    }
}

/// Mirrors Python `CodexBridgeState::complete` validation (without process execution).
pub fn validate_complete_request(
    token: &str,
    settings: &CodexCliSettings,
    request: &CodexCompleteRequest,
) -> Result<CodexCompleteResponse, BridgeError> {
    if token.is_empty() {
        return Err(BridgeError::EmptyToken);
    }
    if request.messages.is_empty() || request.messages.len() > MAX_MESSAGES {
        return Err(BridgeError::InvalidMessagesCount {
            got: request.messages.len(),
        });
    }
    let schema_bytes = serde_json::to_string(&request.response_schema)
        .unwrap_or_default()
        .len();
    if schema_bytes > MAX_SCHEMA_CHARS {
        return Err(BridgeError::SchemaTooLarge {
            bytes: schema_bytes,
        });
    }
    let mut total_chars = 0usize;
    for message in &request.messages {
        if message.role.is_empty() {
            return Err(BridgeError::InvalidRole);
        }
        match &message.content {
            CodexContent::String(content) => {
                total_chars += content.len();
            }
            CodexContent::List(parts) => {
                if parts.len() > MAX_IMAGES + 16 {
                    return Err(BridgeError::TooManyParts { got: parts.len() });
                }
                for part in parts {
                    match part {
                        CodexContentPart::Text { text } => {
                            total_chars += text.len();
                        }
                        CodexContentPart::ImageUrl { url } => {
                            validate_image_url(url)?;
                        }
                    }
                }
            }
        }
    }
    if total_chars > MAX_MESSAGE_CHARS {
        return Err(BridgeError::MessageTooLarge { chars: total_chars });
    }
    let model = settings.model.as_deref().unwrap_or("default").to_owned();
    let prompt = build_prompt(&request.messages, &request.response_schema);
    let _ = prompt;
    Ok(CodexCompleteResponse {
        output: request.response_schema.clone(),
        provider: "codex".to_owned(),
        model,
    })
}

fn validate_image_url(url: &str) -> Result<(), BridgeError> {
    if !url.starts_with("data:image/") || !url.contains(";base64,") {
        return Err(BridgeError::InvalidImageUrl);
    }
    let encoded = url
        .split(";base64,")
        .nth(1)
        .ok_or(BridgeError::InvalidBase64Image)?;
    if encoded.is_empty() {
        return Err(BridgeError::InvalidBase64Image);
    }
    let media_type = url
        .strip_prefix("data:image/")
        .unwrap_or(url)
        .split(';')
        .next()
        .unwrap_or("");
    let _suffix = match media_type {
        "jpeg" | "jpg" => ".jpg",
        "png" => ".png",
        "webp" => ".webp",
        "gif" => ".gif",
        _ => return Err(BridgeError::UnsupportedPart),
    };
    Ok(())
}

/// Mirrors Python `_build_prompt`.
pub fn build_prompt(messages: &[CodexMessage], schema: &JsonObject) -> String {
    let messages_json = serde_json::to_string(messages).unwrap_or_default();
    let schema_json = serde_json::to_string(schema).unwrap_or_default();
    format!(
        "You are a bounded maintenance reasoning provider. Return only a JSON object matching the supplied response schema. Use only the supplied messages. Do not call tools, access files, access networks, use MCP, create jobs, or perform mutations. Do not include secrets or credentials.\n\nMESSAGES:\n{messages_json}\n\nRESPONSE_SCHEMA:\n{schema_json}"
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value;
    use std::collections::BTreeMap;

    fn default_settings() -> CodexCliSettings {
        CodexCliSettings {
            executable: None,
            model: None,
            profile: None,
            timeout_seconds: 300,
            transport: CodexTransport::Exec,
        }
    }

    fn sample_schema() -> JsonObject {
        let mut map = BTreeMap::new();
        map.insert("type".to_owned(), Value::String("object".to_owned()));
        map
    }

    fn sample_request() -> CodexCompleteRequest {
        CodexCompleteRequest {
            messages: vec![CodexMessage {
                role: "user".to_owned(),
                content: CodexContent::String("hello".to_owned()),
            }],
            response_schema: sample_schema(),
        }
    }

    #[test]
    fn bridge_settings_from_environment_defaults() {
        std::env::remove_var("KOGWISTAR_CODEX_EXECUTABLE");
        std::env::remove_var("KOGWISTAR_MAINTENANCE_CODEX_MODEL");
        std::env::remove_var("KOGWISTAR_CODEX_PROFILE");
        std::env::remove_var("KOGWISTAR_MAINTENANCE_CODEX_TIMEOUT_SECONDS");
        std::env::remove_var("KOGWISTAR_CODEX_TRANSPORT");
        let settings = bridge_settings_from_environment();
        assert_eq!(settings.executable, None);
        assert_eq!(settings.model, None);
        assert_eq!(settings.profile, None);
        assert_eq!(settings.timeout_seconds, 300);
        assert_eq!(settings.transport, CodexTransport::Exec);
    }

    #[test]
    fn bridge_settings_from_pairs_overrides() {
        let pairs = [
            (
                "KOGWISTAR_CODEX_EXECUTABLE".to_string(),
                "/usr/bin/codex".to_string(),
            ),
            (
                "KOGWISTAR_MAINTENANCE_CODEX_MODEL".to_string(),
                "codex-mini".to_string(),
            ),
            ("KOGWISTAR_CODEX_PROFILE".to_string(), "default".to_string()),
            (
                "KOGWISTAR_MAINTENANCE_CODEX_TIMEOUT_SECONDS".to_string(),
                "600".to_string(),
            ),
            (
                "KOGWISTAR_CODEX_TRANSPORT".to_string(),
                "app_server".to_string(),
            ),
        ];
        let settings = bridge_settings_from_pairs(pairs.into_iter());
        assert_eq!(settings.executable.as_deref(), Some("/usr/bin/codex"));
        assert_eq!(settings.model.as_deref(), Some("codex-mini"));
        assert_eq!(settings.profile.as_deref(), Some("default"));
        assert_eq!(settings.timeout_seconds, 600);
        assert_eq!(settings.transport, CodexTransport::AppServer);
    }

    #[test]
    fn transport_from_str_maps_app_server() {
        assert_eq!(
            CodexTransport::parse_transport("app_server"),
            CodexTransport::AppServer
        );
        assert_eq!(
            CodexTransport::parse_transport("exec"),
            CodexTransport::Exec
        );
        assert_eq!(
            CodexTransport::parse_transport("unknown"),
            CodexTransport::Exec
        );
    }

    #[test]
    fn validate_complete_request_accepts_valid() {
        let state = CodexBridgeState {
            token: "tok".to_owned(),
            settings: default_settings(),
        };
        let result = validate_complete_request(&state.token, &state.settings, &sample_request());
        assert!(result.is_ok());
        let resp = result.unwrap();
        assert_eq!(resp.provider, "codex");
        assert_eq!(resp.model, "default");
    }

    #[test]
    fn validate_complete_request_rejects_empty_token() {
        let settings = default_settings();
        let result = validate_complete_request("", &settings, &sample_request());
        assert_eq!(result.unwrap_err(), BridgeError::EmptyToken);
    }

    #[test]
    fn validate_complete_request_rejects_empty_messages() {
        let state = CodexBridgeState {
            token: "tok".to_owned(),
            settings: default_settings(),
        };
        let bad = CodexCompleteRequest {
            messages: vec![],
            response_schema: sample_schema(),
        };
        let result = validate_complete_request(&state.token, &state.settings, &bad);
        assert!(matches!(
            result.unwrap_err(),
            BridgeError::InvalidMessagesCount { got: 0 }
        ));
    }

    #[test]
    fn validate_complete_request_rejects_too_many_messages() {
        let state = CodexBridgeState {
            token: "tok".to_owned(),
            settings: default_settings(),
        };
        let too_many: Vec<CodexMessage> = (0..MAX_MESSAGES + 1)
            .map(|_| CodexMessage {
                role: "user".to_owned(),
                content: CodexContent::String("x".to_owned()),
            })
            .collect();
        let bad = CodexCompleteRequest {
            messages: too_many,
            response_schema: sample_schema(),
        };
        let result = validate_complete_request(&state.token, &state.settings, &bad);
        assert!(matches!(
            result.unwrap_err(),
            BridgeError::InvalidMessagesCount { got } if got == MAX_MESSAGES + 1
        ));
    }

    #[test]
    fn validate_complete_request_rejects_empty_role() {
        let state = CodexBridgeState {
            token: "tok".to_owned(),
            settings: default_settings(),
        };
        let bad = CodexCompleteRequest {
            messages: vec![CodexMessage {
                role: "".to_owned(),
                content: CodexContent::String("x".to_owned()),
            }],
            response_schema: sample_schema(),
        };
        let result = validate_complete_request(&state.token, &state.settings, &bad);
        assert_eq!(result.unwrap_err(), BridgeError::InvalidRole);
    }

    #[test]
    fn validate_image_url_accepts_data_base64() {
        assert!(validate_image_url("data:image/png;base64,iVBORw0KGgo").is_ok());
        assert!(validate_image_url("data:image/jpeg;base64,/9j/4AAQ").is_ok());
        assert!(validate_image_url("data:image/webp;base64,UklGRi").is_ok());
        assert!(validate_image_url("data:image/gif;base64,R0lGOD").is_ok());
    }

    #[test]
    fn validate_image_url_rejects_invalid() {
        assert!(validate_image_url("http://example.com/img.png").is_err());
        assert!(validate_image_url("data:image/;base64,xxx").is_err());
        assert!(validate_image_url("data:image/bmp;base64,Qk").is_err());
        assert!(validate_image_url("not-a-url").is_err());
    }

    #[test]
    fn build_prompt_contains_messages_and_schema() {
        let req = sample_request();
        let prompt = build_prompt(&req.messages, &req.response_schema);
        assert!(prompt.contains("MESSAGES:"));
        assert!(prompt.contains("RESPONSE_SCHEMA:"));
        assert!(prompt.contains("bounded maintenance reasoning provider"));
    }
}
