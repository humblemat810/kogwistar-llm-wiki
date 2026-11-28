use serde_json::json;

#[test]
fn stable_id_json_matches_python_facade_contract() {
    let result = kogwistar_llm_wiki_python::stable_id_json(
        r#"["kogwistar_llm_wiki.source_document","demo","file:///x.txt"]"#,
    )
    .unwrap();
    assert_eq!(result, "ee71427f-2f63-5750-8623-329f0d9e7854");
}

#[test]
fn evidence_pack_hash_matches_python_facade_contract() {
    let result = kogwistar_llm_wiki_python::evidence_pack_digest_hash(
        r#"{"node_ids":["b","a"],"edge_ids":["e2","e1"],"depth":"deep","max_chars_per_item":10,"max_total_chars":20,"evidence_pack_hash":"ignored","extra":"x"}"#,
    )
    .unwrap();
    assert_eq!(
        result,
        "08e8f5a5272cffdf4b88ce7c12bf778380f335b3d7b8ae2ecf8c18e296f86d0d"
    );
}

#[test]
fn basic_sense_eval_matches_python_facade_contract() {
    let result = kogwistar_llm_wiki_python::basic_sense_eval(
        r#"{"graph_payload":{"nodes":[],"edges":[]},"diagnostics":{"fallback_reason":"missing"}}"#,
    )
    .unwrap();
    let value = serde_json::from_str::<serde_json::Value>(&result).unwrap();
    assert_eq!(value, json!({
        "basic_sense_score": 0.0,
        "basic_sense_verdict": "weak",
        "coverage_ratio": 0.0,
        "duplicate_excerpt_hits": 0,
        "fallback_used": true,
        "max_depth": 0,
        "node_count": 0,
        "node_type_diversity": 0
    }));
}
