use kogwistar_llm_wiki_port::models::JsonObject;
use kogwistar_llm_wiki_port::provenance::{
    canonicalize_evidence_pack_digest, evidence_pack_digest_hash, python_spaced_json,
};
use serde_json::{json, Value};

fn digest() -> JsonObject {
    json!({
        "node_ids": ["b", "a"],
        "edge_ids": ["e2", "e1"],
        "depth": "deep",
        "max_chars_per_item": 10,
        "max_total_chars": 20,
        "evidence_pack_hash": "ignored",
        "extra": "x"
    })
    .as_object()
    .expect("JSON object")
    .iter()
    .map(|(key, value)| (key.clone(), value.clone()))
    .collect()
}

#[test]
fn canonical_digest_matches_python_golden() {
    let expected = json!({
        "node_ids": ["a", "b"],
        "edge_ids": ["e1", "e2"],
        "depth": "deep",
        "max_chars_per_item": 10,
        "max_total_chars": 20,
        "extra": "x"
    })
    .as_object()
    .expect("JSON object")
    .iter()
    .map(|(key, value)| (key.clone(), value.clone()))
    .collect::<JsonObject>();

    assert_eq!(canonicalize_evidence_pack_digest(&digest()), expected);
}

#[test]
fn evidence_pack_hash_matches_python_golden() {
    assert_eq!(
        evidence_pack_digest_hash(&digest()),
        "08e8f5a5272cffdf4b88ce7c12bf778380f335b3d7b8ae2ecf8c18e296f86d0d"
    );
}

#[test]
fn ascii_json_escapes_python_non_ascii() {
    assert_eq!(
        kogwistar_llm_wiki_port::provenance::python_ascii_json(&Value::from("世界")),
        "\"\\u4e16\\u754c\""
    );
}

#[test]
fn spaced_json_matches_python_default_separators() {
    assert_eq!(
        python_spaced_json(&serde_json::json!({"z": 2, "a": 1})),
        "{\"a\": 1, \"z\": 2}"
    );
}
