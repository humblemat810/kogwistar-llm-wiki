use kogwistar_llm_wiki_port::parse_quality::basic_sense_eval_value;
use serde_json::{json, Value};

#[test]
fn basic_sense_eval_matches_python_golden() {
    let payload = json!({
        "nodes": [{
            "type": "entity",
            "metadata": {
                "semantic_node_type": "ENTITY",
                "level_from_root": 0
            },
            "mentions": [{
                "spans": [{
                    "excerpt": "hello world",
                    "source_cluster_id": "c",
                    "start_char": 0,
                    "end_char": 10
                }]
            }]
        }],
        "edges": []
    });
    let diagnostics = json!({
        "page_index": {"assignment_mode": "heuristic_deterministic"}
    });
    assert_eq!(
        basic_sense_eval_value(&payload, &diagnostics),
        json!({
            "basic_sense_score": 50.5,
            "basic_sense_verdict": "mixed",
            "coverage_ratio": 1.0,
            "duplicate_excerpt_hits": 0,
            "fallback_used": false,
            "max_depth": 1,
            "node_count": 1,
            "node_type_diversity": 1
        })
    );
}

#[test]
fn basic_sense_eval_counts_overlap_duplicates() {
    let payload = json!({
        "nodes": [
            {
                "type": "entity",
                "metadata": {
                    "semantic_node_type": "ENTITY",
                    "level_from_root": 0
                },
                "mentions": [{
                    "spans": [{
                        "excerpt": "x",
                        "source_cluster_id": "c",
                        "start_char": 0,
                        "end_char": 5
                    }]
                }]
            },
            {
                "type": "entity",
                "metadata": {
                    "semantic_node_type": "ENTITY",
                    "level_from_root": 1
                },
                "mentions": [{
                    "spans": [{
                        "excerpt": "x",
                        "source_cluster_id": "c",
                        "start_char": 3,
                        "end_char": 8
                    }]
                }]
            }
        ],
        "edges": []
    });
    let evaluation = basic_sense_eval_value(&payload, &Value::Null);
    assert_eq!(evaluation["duplicate_excerpt_hits"], json!(1));
    assert_eq!(evaluation["basic_sense_score"], json!(46.5));
    assert_eq!(evaluation["basic_sense_verdict"], json!("mixed"));
}

#[test]
fn basic_sense_eval_handles_empty_graph_and_fallback() {
    let evaluation = basic_sense_eval_value(
        &json!({"nodes": [], "edges": []}),
        &json!({"fallback_reason": "missing"}),
    );
    assert_eq!(
        evaluation,
        json!({
            "basic_sense_score": 0.0,
            "basic_sense_verdict": "weak",
            "coverage_ratio": 0.0,
            "duplicate_excerpt_hits": 0,
            "fallback_used": true,
            "max_depth": 0,
            "node_count": 0,
            "node_type_diversity": 0
        })
    );
}
