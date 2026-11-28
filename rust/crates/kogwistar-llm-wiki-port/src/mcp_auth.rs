use std::collections::{BTreeMap, BTreeSet};

use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct McpAuthSettings {
    pub mode: String,
    pub required: bool,
    pub scopes: BTreeSet<String>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct McpIdentity {
    pub principal_id: String,
    pub scopes: Vec<String>,
    pub role: String,
    pub security_scope: String,
    pub workspaces: Vec<String>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum McpAuthError {
    InvalidMode(String),
    MissingToken,
    MissingJwtSecret,
    MissingBearer,
    InvalidBearer,
}

impl std::fmt::Display for McpAuthError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::InvalidMode(value) => write!(formatter, "invalid auth mode: {value}"),
            Self::MissingToken => write!(formatter, "bearer token is required"),
            Self::MissingJwtSecret => write!(formatter, "JWT_SECRET is missing"),
            Self::MissingBearer => write!(formatter, "bearer token is required"),
            Self::InvalidBearer => write!(formatter, "invalid bearer token"),
        }
    }
}

impl std::error::Error for McpAuthError {}

pub fn truthy(value: &str) -> bool {
    matches!(
        value.trim().to_lowercase().as_str(),
        "1" | "true" | "yes" | "on"
    )
}

pub fn env_value(
    environment: &BTreeMap<String, String>,
    name: &str,
    fallback_name: &str,
    default: &str,
) -> String {
    let value = environment
        .get(name)
        .map(String::as_str)
        .unwrap_or_default()
        .trim();
    if value.is_empty() {
        environment
            .get(fallback_name)
            .map(String::as_str)
            .unwrap_or(default)
            .trim()
            .to_owned()
    } else {
        value.to_owned()
    }
}

pub fn auth_mode(environment: &BTreeMap<String, String>) -> String {
    let configured = environment
        .get("LLM_WIKI_AUTH_MODE")
        .map(String::as_str)
        .unwrap_or_default()
        .trim()
        .to_lowercase();
    if matches!(
        configured.as_str(),
        "disabled" | "static_token" | "kogwistar_jwt"
    ) {
        return configured;
    }
    if configured.is_empty() {
        let has_token = !environment
            .get("LLM_WIKI_API_TOKEN")
            .map(String::as_str)
            .unwrap_or_default()
            .trim()
            .is_empty()
            || !environment
                .get("LLM_WIKI_MCP_TOKEN")
                .map(String::as_str)
                .unwrap_or_default()
                .trim()
                .is_empty();
        let required = truthy(
            environment
                .get("LLM_WIKI_AUTH_REQUIRED")
                .map(String::as_str)
                .unwrap_or_default(),
        ) || truthy(
            environment
                .get("LLM_WIKI_MCP_AUTH_REQUIRED")
                .map(String::as_str)
                .unwrap_or_default(),
        );
        if has_token || required {
            return "static_token".to_owned();
        }
        return "disabled".to_owned();
    }
    "disabled".to_owned()
}

pub fn build_mcp_auth(
    environment: &BTreeMap<String, String>,
) -> Result<Option<McpAuthSettings>, McpAuthError> {
    let mode = auth_mode(environment);
    if mode == "disabled" {
        return Ok(None);
    }
    let required = truthy(&env_value(
        environment,
        "LLM_WIKI_MCP_AUTH_REQUIRED",
        "LLM_WIKI_AUTH_REQUIRED",
        "",
    ));
    if mode == "static_token"
        && required
        && env_value(environment, "LLM_WIKI_MCP_TOKEN", "LLM_WIKI_API_TOKEN", "").is_empty()
    {
        return Err(McpAuthError::MissingToken);
    }
    if mode == "kogwistar_jwt"
        && environment
            .get("JWT_SECRET")
            .map(String::as_str)
            .unwrap_or_default()
            .trim()
            .is_empty()
    {
        return Err(McpAuthError::MissingJwtSecret);
    }
    let scopes = env_value(
        environment,
        "LLM_WIKI_MCP_TOKEN_SCOPES",
        "LLM_WIKI_API_TOKEN_SCOPES",
        "read,write",
    )
    .split(',')
    .filter_map(|scope| {
        let value = scope.trim();
        if value.is_empty() {
            None
        } else {
            Some(value.to_owned())
        }
    })
    .collect();
    if mode != "disabled" && required {
        Ok(Some(McpAuthSettings {
            mode,
            required,
            scopes,
        }))
    } else {
        Ok(None)
    }
}

pub fn headers_from_scope(scope: &Value) -> BTreeMap<String, String> {
    let mut headers = BTreeMap::new();
    if let Some(items) = scope.get("headers").and_then(Value::as_array) {
        for item in items {
            if let Some(pair) = item.as_array().filter(|pair| pair.len() == 2) {
                let name = pair[0].as_str().unwrap_or_default().to_lowercase();
                let value = pair[1].as_str().unwrap_or_default().to_owned();
                headers.insert(name, value);
            }
        }
    }
    headers
}

pub fn bearer_from_headers(headers: &BTreeMap<String, String>) -> Option<String> {
    let authorization = headers.get("authorization")?;
    let value = authorization.trim();
    if value.len() > 7 && value[..7].eq_ignore_ascii_case("bearer ") {
        Some(value[7..].trim().to_owned())
    } else {
        Some(value.to_owned())
    }
}

pub fn authenticate_bearer(
    value: Option<&str>,
    environment: &BTreeMap<String, String>,
) -> Result<Option<McpIdentity>, McpAuthError> {
    if auth_mode(environment) == "disabled" {
        return Ok(None);
    }
    let supplied = value.unwrap_or_default().trim();
    let supplied = supplied.strip_prefix("Bearer ").unwrap_or(supplied).trim();
    if supplied.is_empty() {
        return Err(McpAuthError::MissingBearer);
    }
    let api_expected = environment
        .get("LLM_WIKI_API_TOKEN")
        .map(String::as_str)
        .unwrap_or_default()
        .trim();
    let mcp_expected = environment
        .get("LLM_WIKI_MCP_TOKEN")
        .map(String::as_str)
        .unwrap_or_default()
        .trim();
    let matched_api = !api_expected.is_empty() && api_expected == supplied;
    let matched_mcp = !mcp_expected.is_empty() && mcp_expected == supplied;
    if !matched_api && !matched_mcp {
        return Err(McpAuthError::InvalidBearer);
    }
    let scopes = if matched_mcp {
        env_value(
            environment,
            "LLM_WIKI_MCP_TOKEN_SCOPES",
            "LLM_WIKI_API_TOKEN_SCOPES",
            "read,write",
        )
    } else {
        env_value(
            environment,
            "LLM_WIKI_API_TOKEN_SCOPES",
            "LLM_WIKI_API_TOKEN_SCOPES",
            "read,write",
        )
    }
    .split(',')
    .filter_map(|scope| {
        let value = scope.trim();
        if value.is_empty() {
            None
        } else {
            Some(value.to_owned())
        }
    })
    .collect::<Vec<_>>();
    let role = if scopes.iter().any(|scope| scope == "write") {
        "rw"
    } else {
        "ro"
    };
    Ok(Some(McpIdentity {
        principal_id: "llm-wiki-static-client".to_owned(),
        scopes,
        role: role.to_owned(),
        security_scope: environment
            .get("LLM_WIKI_SECURITY_SCOPE")
            .map(String::as_str)
            .unwrap_or("llm-wiki")
            .to_owned(),
        workspaces: vec!["*".to_owned()],
    }))
}
