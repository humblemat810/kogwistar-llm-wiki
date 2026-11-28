use kogwistar_llm_wiki_port::app_contracts::validate_source_request;
use serde_json::json;

#[test]
fn source_request_requires_bounded_identity() {
    let payload = json!({"workspace_id": " w ", "vault_id": " v "});
    let (workspace_id, vault_id) = validate_source_request(&payload).unwrap();
    assert_eq!((workspace_id, vault_id), ("w".to_owned(), "v".to_owned()));
}

#[test]
fn source_request_rejects_non_string_identity() {
    let payload = json!({"workspace_id": 1, "vault_id": "v"});
    assert_eq!(
        validate_source_request(&payload).unwrap_err().as_str(),
        "Obsidian source identity values must be strings"
    );
}

#[test]
fn source_request_rejects_blank_identity() {
    let payload = json!({"workspace_id": " ", "vault_id": "v"});
    assert_eq!(
        validate_source_request(&payload).unwrap_err().as_str(),
        "Obsidian source requests require workspace_id and vault_id"
    );
}

#[test]
fn source_request_rejects_overlong_identity() {
    let payload = json!({
        "workspace_id": "w".repeat(257),
        "vault_id": "v"
    });
    assert_eq!(
        validate_source_request(&payload).unwrap_err().as_str(),
        "Obsidian source identity values are too long"
    );
}
