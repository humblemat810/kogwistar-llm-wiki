use kogwistar_llm_wiki_port::parse_statistics::build_parse_statistics_record;
use serde_json::{json, Value};

#[test]
fn parse_statistics_record_matches_python_golden() {
    let semantic_tree = json!({
        "child_nodes": [
            {"child_nodes": [{"child_nodes": []}]},
            {"child_nodes": [{"child_nodes": []}]}
        ]
    });
    let record = build_parse_statistics_record(
        123,
        "demo",
        "doc-1",
        "file:///demo.md",
        "Demo",
        "alpha beta gamma",
        "workflow_layered",
        "azure_openai",
        Some("boundaries"),
        Some("azure"),
        Some("gpt-5-nano"),
        123,
        &semantic_tree,
        Some(&json!({
            "nodes": [{"id": "n1"}, {"id": "n2"}],
            "edges": [{"id": "e1"}]
        })),
        Some(&json!({"retry_used": true})),
        Some(&json!({
            "basic_sense_score": 91.5,
            "basic_sense_verdict": "good",
            "coverage_ratio": 0.75,
            "fallback_used": false
        })),
        "ok",
    );

    assert_eq!(record.document_length_words, 3);
    assert_eq!(record.tree_depth, 3);
    assert_eq!(record.tree_node_count, 5);
    assert_eq!(record.tree_leaf_count, 2);
    assert_eq!(record.node_count, 2);
    assert_eq!(record.edge_count, 1);
    assert_eq!(record.basic_sense_score, Some(91.5));
    assert_eq!(record.basic_sense_verdict.as_deref(), Some("good"));
    assert_eq!(record.coverage_ratio, Some(0.75));
    assert_eq!(record.fallback_used, Some(false));
    assert_eq!(record.retry_used, Some(true));
    let details: Value = serde_json::from_str(&record.details_json).unwrap();
    assert_eq!(details["evaluation"]["basic_sense_verdict"], json!("good"));
    assert_eq!(details["graph_node_ids"], json!(["n1", "n2"]));
    assert_eq!(details["graph_edge_ids"], json!(["e1"]));
    assert_eq!(
        record.details_json,
        "{\"diagnostics\":{\"retry_used\":true},\"evaluation\":{\"basic_sense_score\":91.5,\"basic_sense_verdict\":\"good\",\"coverage_ratio\":0.75,\"fallback_used\":false},\"graph_edge_ids\":[\"e1\"],\"graph_node_ids\":[\"n1\",\"n2\"],\"model\":\"gpt-5-nano\",\"parser_lane\":\"workflow_layered\",\"proposal_mode\":\"boundaries\",\"provider\":\"azure\"}"
    );
}
