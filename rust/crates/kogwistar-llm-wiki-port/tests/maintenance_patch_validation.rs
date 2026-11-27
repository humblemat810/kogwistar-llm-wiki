use kogwistar_llm_wiki_port::maintenance_patch_validation::validate_maintenance_patch;
use serde_json::json;

#[test]
fn validation_reports_missing_provenance_and_endpoints() {
    let patch = json!({
        "patch_id": "patch-1",
        "intent": "derive_entity",
        "operations": [{
            "operation_id": "op-1",
            "kind": "ADD_EDGE",
            "edge_id": "ws:demo:edge:crosslink",
            "from_node_id": "ws:demo:node:left",
            "to_node_id": "ws:demo:node:right",
            "relation": "related_to"
        }]
    });
    let report = validate_maintenance_patch(
        &patch,
        &["ws:demo:node:left".to_owned()],
        &[],
        Some("ws:demo:"),
    );
    assert!(!report.valid);
    let codes = report
        .issues
        .iter()
        .map(|issue| issue.code.as_str())
        .collect::<Vec<_>>();
    assert_eq!(codes, vec!["missing_provenance", "missing_edge_endpoint"]);
}

#[test]
fn validation_reports_idempotency_and_tombstone_target_errors() {
    let patch = json!({
        "patch_id": "patch-1",
        "intent": "correct_fact",
        "operations": [
            {
                "operation_id": "op-1",
                "kind": "TOMBSTONE_EDGE",
                "target_id": "ws:demo:edge:missing",
                "provenance": {}
            },
            {
                "operation_id": "op-1",
                "kind": "ADD_NODE",
                "node_id": "outside:node:1",
                "provenance": {}
            }
        ]
    });
    let report = validate_maintenance_patch(
        &patch,
        &[],
        &["ws:demo:edge:active".to_owned()],
        Some("ws:demo:"),
    );
    assert!(!report.valid);
    let mut codes = report
        .issues
        .iter()
        .map(|issue| issue.code.as_str())
        .collect::<Vec<_>>();
    codes.sort();
    assert_eq!(
        codes,
        vec![
            "duplicate_operation_id",
            "missing_tombstone_target",
            "namespace_scope_violation",
        ]
    );
}

#[test]
fn validation_requires_supersession_lineage() {
    let patch = json!({
        "patch_id": "patch-1",
        "intent": "correct_fact",
        "operations": [{
            "operation_id": "op-1",
            "kind": "ADD_NODE",
            "node_id": "ws:demo:node:new",
            "supersedes_ids": ["ws:demo:node:old"],
            "provenance": {}
        }]
    });
    let report = validate_maintenance_patch(
        &patch,
        &["ws:demo:node:old".to_owned()],
        &[],
        Some("ws:demo:"),
    );
    assert!(!report.valid);
    let codes = report
        .issues
        .iter()
        .map(|issue| issue.code.as_str())
        .collect::<Vec<_>>();
    assert_eq!(codes, vec!["unpaired_supersession"]);
}
