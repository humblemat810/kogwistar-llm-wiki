use std::collections::{BTreeMap, BTreeSet};

use jsonwebtoken::{Algorithm, DecodingKey, Validation};
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

fn jwt_scopes(claims: &BTreeMap<String, Value>) -> Vec<String> {
    let mut scopes = Vec::new();
    for key in ["scope", "scp"] {
        let value = claims.get(key);
        if let Some(Value::String(raw)) = value {
            scopes.extend(raw.split_whitespace().map(str::to_owned));
        } else if let Some(Value::Array(items)) = value {
            for item in items {
                if let Some(scope) = item.as_str() {
                    if !scope.trim().is_empty() {
                        scopes.push(scope.trim().to_owned());
                    }
                }
            }
        }
    }
    scopes
        .into_iter()
        .map(|scope| scope.trim().to_lowercase())
        .filter(|scope| !scope.is_empty())
        .collect::<BTreeSet<_>>()
        .into_iter()
        .collect()
}

fn jwt_workspaces(claims: &BTreeMap<String, Value>) -> Vec<String> {
    let raw = claims
        .get("workspaces")
        .or_else(|| claims.get("workspace_ids"))
        .or_else(|| claims.get("allowed_workspaces"));
    let mut values = Vec::new();
    if let Some(Value::String(value)) = raw {
        values.extend(value.split(',').map(str::trim).map(str::to_owned));
    } else if let Some(Value::Array(items)) = raw {
        for item in items {
            if let Some(value) = item.as_str() {
                if !value.trim().is_empty() {
                    values.push(value.trim().to_owned());
                }
            } else if let Some(value) = item.as_i64() {
                values.push(value.to_string());
            }
        }
    }
    values.retain(|value| !value.is_empty());
    values
}

fn decode_jwt(
    token: &str,
    environment: &BTreeMap<String, String>,
) -> Result<BTreeMap<String, Value>, McpAuthError> {
    let secret = environment
        .get("JWT_SECRET")
        .map(String::as_str)
        .unwrap_or_default();
    if secret.trim().is_empty() {
        return Err(McpAuthError::MissingJwtSecret);
    }
    let configured = environment
        .get("JWT_ALG")
        .map(String::as_str)
        .unwrap_or("HS256")
        .trim()
        .to_uppercase();
    let algorithm = match configured.as_str() {
        "HS256" => Algorithm::HS256,
        "RS256" => Algorithm::RS256,
        _ => return Err(McpAuthError::InvalidBearer),
    };
    let decoding_key = if algorithm == Algorithm::RS256 {
        DecodingKey::from_rsa_pem(secret.as_bytes()).map_err(|_| McpAuthError::InvalidBearer)?
    } else {
        DecodingKey::from_secret(secret.as_bytes())
    };
    let mut validation = Validation::new(algorithm);
    validation.leeway = 0;
    if let Some(audience) = environment
        .get("JWT_AUD")
        .map(String::as_str)
        .filter(|value| !value.trim().is_empty())
    {
        validation.set_audience(&[audience]);
    }
    if let Some(issuer) = environment
        .get("JWT_ISS")
        .map(String::as_str)
        .filter(|value| !value.trim().is_empty())
    {
        validation.set_issuer(&[issuer]);
    }
    let decoded =
        jsonwebtoken::decode::<BTreeMap<String, Value>>(token, &decoding_key, &validation)
            .map_err(|_| McpAuthError::InvalidBearer)?;
    Ok(decoded.claims)
}

fn authenticate_jwt(
    token: &str,
    environment: &BTreeMap<String, String>,
) -> Result<McpIdentity, McpAuthError> {
    let claims = decode_jwt(token, environment)?;
    let principal = ["sub", "client_id"]
        .iter()
        .find_map(|key| claims.get(*key))
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .ok_or(McpAuthError::InvalidBearer)?
        .to_owned();
    let scopes = jwt_scopes(&claims);
    let role = claims
        .get("role")
        .and_then(Value::as_str)
        .unwrap_or("ro")
        .trim()
        .to_lowercase();
    let role = if role == "rw" { "rw" } else { "ro" };
    let security_scope = ["security_scope", "tenant"]
        .iter()
        .find_map(|key| claims.get(*key))
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .unwrap_or(&principal)
        .to_lowercase();
    let mut workspaces = jwt_workspaces(&claims);
    if workspaces.is_empty() {
        workspaces.push(principal.clone());
    }
    Ok(McpIdentity {
        principal_id: principal,
        scopes,
        role: role.to_owned(),
        security_scope,
        workspaces,
    })
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
        if auth_mode(environment) == "kogwistar_jwt" {
            return authenticate_jwt(supplied, environment).map(Some);
        }
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
