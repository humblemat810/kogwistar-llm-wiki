use kogwistar_llm_wiki_port::maintenance_statistics::{
    build_maintenance_statistics, operation_category,
};
use serde_json::{json, Value};

fn rows() -> Vec<Value> {
    vec![
        json!({"event": "maintenance_poll_start", "worker_id": "maintenance-1"}),
        json!({
            "event": "maintenance_runtime_attempt_start",
            "worker_id": "maintenance-1",
            "source_document_id": "doc-1",
            "maintenance_kind": "distill"
        }),
        json!({
            "event": "maintenance_graph_effect",
            "worker_id": "maintenance-1",
            "source_document_id": "doc-1",
            "maintenance_kind": "distill",
            "source_node_count": 3,
            "derived_node_count": 1
        }),
        json!({
            "event": "maintenance_runtime_attempt_complete",
            "worker_id": "maintenance-1",
            "source_document_id": "doc-1",
            "maintenance_kind": "distill",
            "runtime_status": "suspended",
            "duration_ms": 1200
        }),
        json!({
            "event": "maintenance_runtime_attempt_failed",
            "worker_id": "maintenance-2",
            "source_document_id": "doc-2",
            "maintenance_kind": "crosslink",
            "error_type": "ValidationError"
        }),
    ]
}

#[test]
fn operation_categories_match_python_contract() {
    assert_eq!(operation_category("document_seed_graph"), "source_seeding");
    assert_eq!(operation_category("document_parse_graph"), "breakdown");
    assert_eq!(operation_category("document_propose_crosslinks"), "linking");
    assert_eq!(operation_category("document_merge_nodes"), "combining");
    assert_eq!(operation_category("document_correct_fact"), "correction");
    assert_eq!(operation_category("unknown_kind"), "unknown_kind");
}

#[test]
fn statistics_group_by_document_operation_and_failure() {
    let stats = build_maintenance_statistics(&rows());
    assert_eq!(stats["document_count"], json!(2));
    assert_eq!(
        stats["documents"]["doc-1"]["average_duration_ms"],
        json!(1200.0)
    );
    assert_eq!(stats["documents"]["doc-1"]["suspended_count"], json!(1));
    assert_eq!(stats["documents"]["doc-1"]["derived_node_count"], json!(1));
    assert_eq!(
        stats["documents"]["doc-1"]["operation_categories"]["distillation"],
        json!(2)
    );
    assert_eq!(
        stats["documents"]["doc-2"]["operation_categories"]["linking"],
        json!(1)
    );
    assert_eq!(
        stats["failure_hotspots"]["failed:ValidationError"],
        json!(1)
    );
}

#[test]
fn statistics_include_planner_parse_effects() {
    let stats = build_maintenance_statistics(&[json!({
        "event": "maintenance_parse_complete",
        "source_document_id": "doc-parse",
        "maintenance_kind": "document_parse_graph",
        "duration_ms": 1234,
        "node_count": 4,
        "edge_count": 2,
        "llm_call_count": 3
    })]);
    assert_eq!(stats["documents"]["doc-parse"]["parse_count"], json!(1));
    assert_eq!(
        stats["documents"]["doc-parse"]["parsed_node_count"],
        json!(4)
    );
    assert_eq!(
        stats["documents"]["doc-parse"]["parsed_edge_count"],
        json!(2)
    );
    assert_eq!(stats["documents"]["doc-parse"]["llm_call_count"], json!(3));
    assert_eq!(
        stats["documents"]["doc-parse"]["operation_categories"]["breakdown"],
        json!(1)
    );
}
