use std::collections::BTreeMap;

use jsonwebtoken::{encode, EncodingKey, Header};
use kogwistar_llm_wiki_port::mcp_server::{
    auth_mode, authenticate_bearer, bearer_from_headers, build_mcp_auth, headers_from_scope,
};
use serde_json::Value;

fn environment(values: &[(&str, &str)]) -> BTreeMap<String, String> {
    values
        .iter()
        .map(|(key, value)| ((*key).to_string(), (*value).to_string()))
        .collect()
}

#[test]
fn defaults_to_disabled_without_auth_variables() {
    let environment = environment(&[]);
    assert_eq!(auth_mode(&environment), "disabled");
    assert_eq!(build_mcp_auth(&environment).unwrap(), None);
}

#[test]
fn builds_required_static_token_settings() {
    let environment = environment(&[
        ("LLM_WIKI_AUTH_MODE", "static_token"),
        ("LLM_WIKI_MCP_AUTH_REQUIRED", "true"),
        ("LLM_WIKI_MCP_TOKEN", "secret"),
        ("LLM_WIKI_MCP_TOKEN_SCOPES", "read,write"),
    ]);
    let settings = build_mcp_auth(&environment).unwrap().unwrap();
    assert_eq!(settings.mode, "static_token");
    assert!(settings.required);
    assert!(settings.scopes.contains("read"));
    assert!(settings.scopes.contains("write"));
}

#[test]
fn rejects_static_token_without_token_and_jwt_without_secret() {
    let static_environment = environment(&[
        ("LLM_WIKI_AUTH_MODE", "static_token"),
        ("LLM_WIKI_MCP_AUTH_REQUIRED", "true"),
    ]);
    assert!(build_mcp_auth(&static_environment).is_err());
    let jwt_environment = environment(&[
        ("LLM_WIKI_AUTH_MODE", "kogwistar_jwt"),
        ("LLM_WIKI_MCP_AUTH_REQUIRED", "true"),
    ]);
    assert!(build_mcp_auth(&jwt_environment).is_err());
}

#[test]
fn extracts_lowercase_scope_headers_and_bearer() {
    let scope = serde_json::json!({
        "headers": [
            [serde_json::json!("Authorization"), serde_json::json!("Bearer token")],
            [serde_json::json!("X-Trace-Id"), serde_json::json!("trace")],
        ]
    });
    let headers = headers_from_scope(&scope);
    assert_eq!(
        headers.get("authorization").map(String::as_str),
        Some("Bearer token")
    );
    assert_eq!(headers.get("x-trace-id").map(String::as_str), Some("trace"));
    assert_eq!(bearer_from_headers(&headers).as_deref(), Some("token"));
}

#[test]
fn authenticates_static_bearer_identity() {
    let environment = environment(&[
        ("LLM_WIKI_AUTH_MODE", "static_token"),
        ("LLM_WIKI_MCP_TOKEN", "secret"),
    ]);
    let identity = authenticate_bearer(Some("Bearer secret"), &environment)
        .unwrap()
        .unwrap();
    assert_eq!(identity.principal_id, "llm-wiki-static-client");
    assert_eq!(identity.role, "rw");
    assert_eq!(identity.workspaces, vec!["*".to_string()]);
}

fn hs256_token(secret: &str, claims: BTreeMap<&str, Value>) -> String {
    let claims: BTreeMap<String, Value> = claims
        .into_iter()
        .map(|(key, value)| (key.to_string(), value))
        .collect();
    encode(
        &Header::new(jsonwebtoken::Algorithm::HS256),
        &claims,
        &EncodingKey::from_secret(secret.as_bytes()),
    )
    .unwrap()
}

#[test]
fn authenticates_hs256_jwt_identity_and_claims() {
    let environment = environment(&[
        ("LLM_WIKI_AUTH_MODE", "kogwistar_jwt"),
        ("JWT_ALG", "HS256"),
        ("JWT_SECRET", "secret"),
        ("JWT_ISS", "issuer"),
        ("JWT_AUD", "audience"),
    ]);
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs();
    let claims: BTreeMap<&str, Value> = BTreeMap::from([
        ("sub", Value::String("agent-1".to_string())),
        ("scope", Value::String("read write".to_string())),
        ("role", Value::String("RW".to_string())),
        ("security_scope", Value::String("Tenant".to_string())),
        (
            "workspaces",
            Value::Array(vec![Value::String("ws-1".to_string())]),
        ),
        ("exp", Value::from(now + 60)),
        ("iss", Value::String("issuer".to_string())),
        ("aud", Value::String("audience".to_string())),
    ]);
    let token = hs256_token("secret", claims);
    let identity = authenticate_bearer(Some(&format!("Bearer {token}")), &environment)
        .unwrap()
        .unwrap();
    assert_eq!(identity.principal_id, "agent-1");
    assert_eq!(identity.role, "rw");
    assert_eq!(
        identity.scopes,
        vec!["read".to_string(), "write".to_string()]
    );
    assert_eq!(identity.security_scope, "tenant");
    assert_eq!(identity.workspaces, vec!["ws-1".to_string()]);
}

#[test]
fn rejects_expired_or_issuer_mismatched_jwt() {
    let environment = environment(&[
        ("LLM_WIKI_AUTH_MODE", "kogwistar_jwt"),
        ("JWT_SECRET", "secret"),
        ("JWT_ISS", "issuer"),
    ]);
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap()
        .as_secs();
    let expired = BTreeMap::from([
        ("sub", Value::String("agent-1".to_string())),
        ("exp", Value::from(now - 1)),
        ("iss", Value::String("issuer".to_string())),
    ]);
    let expired_token = hs256_token("secret", expired);
    assert!(authenticate_bearer(Some(&expired_token), &environment).is_err());

    let mismatched = BTreeMap::from([
        ("sub", Value::String("agent-1".to_string())),
        ("exp", Value::from(now + 60)),
        ("iss", Value::String("other".to_string())),
    ]);
    let mismatched_token = hs256_token("secret", mismatched);
    assert!(authenticate_bearer(Some(&mismatched_token), &environment).is_err());
}
