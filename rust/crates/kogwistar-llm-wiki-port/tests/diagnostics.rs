use kogwistar_llm_wiki_port::diagnostics::{
    aggregate_stage_timings, append_jsonl, summarize_semantic_tree, summarize_stage_timings,
};
use serde_json::{json, Value};

#[test]
fn stage_timings_identify_dominant_operation_stage() {
    let events = vec![
        json!({"stage": "parse_start", "timestamp_ms": 100}),
        json!({"stage": "boundary_proposal_start", "timestamp_ms": 110}),
        json!({"stage": "boundary_proposal_completed", "timestamp_ms": 410}),
        json!({"stage": "parse_returned", "timestamp_ms": 500}),
    ];
    let summary = summarize_stage_timings(&events);

    assert_eq!(summary.stage_count, 2);
    assert_eq!(summary.dominant_stage.as_deref(), Some("parse"));
    assert_eq!(summary.dominant_stage_total_ms, 400);
    assert_eq!(
        summary.dominant_operation_stage.as_deref(),
        Some("boundary_proposal")
    );
    assert_eq!(summary.dominant_operation_stage_total_ms, 300);
    assert_eq!(summary.stages["parse"].total_ms, 400);
    assert_eq!(summary.stages["parse"].open_count, 0);
    assert_eq!(summary.stages["boundary_proposal"].total_ms, 300);
}

#[test]
fn stage_timings_keep_open_stage_visible() {
    let summary = summarize_stage_timings(&[json!({
        "stage": "boundary_review_start",
        "timestamp_ms": 100
    })]);

    assert_eq!(summary.stages["boundary_review"].open_count, 1);
    assert_eq!(summary.stages["boundary_review"].count, 0);
    assert_eq!(summary.stages["boundary_review"].total_ms, 0);
    assert_eq!(summary.dominant_stage, None);
}

#[test]
fn aggregate_stage_timings_matches_python() {
    let summaries = vec![
        json!({
            "stages": {
                "boundary_proposal": {
                    "count": 2,
                    "total_ms": 300,
                    "open_count": 0
                }
            }
        }),
        json!({
            "stages": {
                "child_proposal": {
                    "count": 1,
                    "total_ms": 500,
                    "open_count": 0
                }
            }
        }),
    ];
    let summary = aggregate_stage_timings(&summaries);

    assert_eq!(summary.dominant_stage.as_deref(), Some("child_proposal"));
    assert_eq!(summary.dominant_stage_total_ms, 500);
    assert_eq!(
        summary.dominant_operation_stage.as_deref(),
        Some("child_proposal")
    );
    assert_eq!(summary.stages["boundary_proposal"].total_ms, 300);
    assert_eq!(summary.stages["boundary_proposal"].count, 2);
    assert_eq!(summary.stages["boundary_proposal"].average_ms, 150.0);
}

#[test]
fn semantic_tree_summary_matches_python() {
    let tree = json!({
        "child_nodes": [
            {"child_nodes": [{"child_nodes": []}]},
            {"child_nodes": [{"child_nodes": []}]}
        ]
    });
    assert_eq!(summarize_semantic_tree(&tree).tree_depth, 3);
    assert_eq!(summarize_semantic_tree(&tree).tree_node_count, 5);
    assert_eq!(summarize_semantic_tree(&tree).tree_leaf_count, 2);
}

#[test]
fn append_jsonl_writes_machine_readable_lines() -> std::io::Result<()> {
    let directory = tempfile::tempdir()?;
    let path = directory.path().join("trace").join("trace.jsonl");
    append_jsonl(
        &path,
        &json!({"stage": "demo", "timestamp_ms": 123, "workspace_id": "ws-1"}),
    )?;
    append_jsonl(
        &path,
        &json!({"stage": "next", "timestamp_ms": 456, "workspace_id": "ws-1"}),
    )?;
    let text = std::fs::read_to_string(&path)?;
    let lines = text
        .lines()
        .map(|line| serde_json::from_str::<Value>(line).unwrap())
        .collect::<Vec<_>>();
    assert_eq!(lines[0]["stage"], json!("demo"));
    assert_eq!(lines[1]["stage"], json!("next"));
    assert_eq!(lines[0]["workspace_id"], json!("ws-1"));
    Ok(())
}
