use kogwistar_llm_wiki_port::seeding::{canonicalized, validate_bundle, GraphSeedBundle};
use serde_json::json;

fn bundle_payload() -> serde_json::Value {
    json!({
        "schema_version": 1,
        "bundle_id": "demo",
        "title": "Demo",
        "description": "A small grounded graph",
        "created_at": "2026-08-12",
        "sources": [
            {
                "id": "source:a",
                "title": "A",
                "url": "https://example.test/a",
                "revision": "1",
                "text": "unique text"
            }
        ],
        "nodes": [
            {
                "id": "node:a",
                "label": "A",
                "kind": "concept",
                "summary": "A",
                "mentions": [
                    {"source_id": "source:a", "excerpt": "unique"}
                ]
            }
        ],
        "edges": [
            {
                "id": "edge:a",
                "label": "A",
                "relation": "related_to",
                "summary": "A",
                "sources": ["node:a"],
                "targets": ["source:a"],
                "mentions": [
                    {"source_id": "source:a", "excerpt": "text", "start_char": 7, "end_char": 11}
                ]
            }
        ]
    })
}

#[test]
fn seed_bundle_validates_and_canonicalizes_grounding() {
    let bundle: GraphSeedBundle = serde_json::from_value(bundle_payload()).unwrap();
    validate_bundle(&bundle).unwrap();
    let canonical = canonicalized(&bundle);
    assert_eq!(canonical.bundle_id, "demo");
    assert_eq!(canonical.nodes[0].mentions[0].start_char, Some(0));
    assert_eq!(canonical.nodes[0].mentions[0].end_char, Some(6));
    assert_eq!(canonical.edges[0].mentions[0].start_char, Some(7));
    assert_eq!(canonical.edges[0].mentions[0].end_char, Some(11));
}

#[test]
fn seed_bundle_rejects_ambiguous_implicit_excerpt() {
    let payload = bundle_payload();
    let mut payload = payload;
    payload["sources"][0]["text"] = json!("same same");
    payload["nodes"][0]["mentions"][0]["excerpt"] = json!("same");
    payload["edges"] = json!([]);
    let bundle: GraphSeedBundle = serde_json::from_value(payload).unwrap();
    let error = validate_bundle(&bundle).unwrap_err();
    assert!(error.contains("ambiguous within source:a"));
}

#[test]
fn seed_bundle_rejects_mismatched_explicit_excerpt() {
    let payload = bundle_payload();
    let mut payload = payload;
    payload["edges"][0]["mentions"][0]["excerpt"] = json!("wrong");
    let bundle: GraphSeedBundle = serde_json::from_value(payload).unwrap();
    let error = validate_bundle(&bundle).unwrap_err();
    assert!(error.contains("does not match half-open offsets"));
}
