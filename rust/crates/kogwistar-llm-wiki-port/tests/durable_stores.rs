use std::collections::HashSet;

use kogwistar_llm_wiki_port::maintenance::{
    and_where, belongs_to_workspace, durable_maintenance_usage, edge_ids, maintenance_budget_state,
    metadata_mapping, persisted_budget_state, semantic_fingerprint, SemanticExtraction,
};
use serde_json::{json, Map, Value};

fn obj(pairs: &[(&str, Value)]) -> Map<String, Value> {
    pairs
        .iter()
        .map(|(key, value)| (key.to_string(), value.clone()))
        .collect()
}

fn node(id: &str, label: &str) -> Map<String, Value> {
    obj(&[
        ("id", json!(id)),
        ("label", json!(label)),
        (
            "metadata",
            json!({"parse_generation_event_id": "e1", "other": 1}),
        ),
    ])
}

#[test]
fn and_where_combines_clauses() {
    let filter = and_where(&[
        obj(&[("workspace", json!("ws:1"))]),
        obj(&[("kind", json!("parse"))]),
    ]);
    assert_eq!(
        filter,
        json!({"$and": [{"workspace": "ws:1"}, {"kind": "parse"}]})
    );
}

#[test]
fn belongs_to_workspace_matches_boundary() {
    let meta = obj(&[("workspace_id", json!("ws:1"))]);
    assert!(belongs_to_workspace(&meta, "ws:1"));
    assert!(!belongs_to_workspace(&meta, "ws:2"));
    assert!(belongs_to_workspace(&Map::new(), "ws:2"));
    let blank = obj(&[("workspace_id", json!("   "))]);
    assert!(belongs_to_workspace(&blank, "ws:2"));
}

#[test]
fn metadata_mapping_merges_nested_json() {
    let raw = obj(&[
        ("a", json!(1)),
        ("metadata", Value::from(r#"{"b": 2, "a": "overridden"}"#)),
    ]);
    let mapped = metadata_mapping(&raw);
    assert_eq!(mapped.get("a"), Some(&json!("overridden")));
    assert_eq!(mapped.get("b"), Some(&json!(2)));
    assert_eq!(mapped.len(), 3);
}

#[test]
fn semantic_fingerprint_excludes_derivation_ids() {
    let same = semantic_fingerprint(&SemanticExtraction {
        nodes: vec![node("n1", "x")],
        edges: vec![],
    });
    assert_eq!(
        same,
        semantic_fingerprint(&SemanticExtraction {
            nodes: vec![node("n2", "x")],
            edges: vec![],
        })
    );
    let different = semantic_fingerprint(&SemanticExtraction {
        nodes: vec![node("n1", "y")],
        edges: vec![],
    });
    assert_ne!(same, different);
    let with_edge = semantic_fingerprint(&SemanticExtraction {
        nodes: vec![],
        edges: vec![obj(&[
            ("id", json!("edge-1")),
            ("metadata", json!({"parse_generation_member_id": "m1"})),
        ])],
    });
    assert_ne!(same, with_edge);
}

#[test]
fn persisted_budget_state_keeps_allowed_keys() {
    let state = obj(&[("token_used", json!(5)), ("internal_secret", json!("nope"))]);
    let kept = persisted_budget_state(&state);
    assert_eq!(kept.len(), 1);
    assert_eq!(kept.get("token_used"), Some(&json!(5)));
}

#[test]
fn maintenance_budget_state_applies_request_limits_and_durable_usage() {
    let state = obj(&[("token_used", json!(100)), ("call_used", json!(3))]);
    let budgets = obj(&[("max_tokens", json!(1000)), ("max_cost_usd", json!(0.5))]);
    let durable = obj(&[("token_used", json!(250))]);
    let next = maintenance_budget_state(&state, &budgets, &durable, false, 0, 0, 0);
    assert_eq!(next.get("token_used"), Some(&json!(250)));
    assert_eq!(next.get("call_used"), Some(&json!(3)));
    assert_eq!(next.get("token_budget"), Some(&json!(1000)));
    assert_eq!(next.get("cost_budget"), Some(&json!(0.5)));
}

#[test]
fn maintenance_budget_state_fair_scheduling_caps_step() {
    let state = obj(&[
        ("step_used", json!(10)),
        ("request_step_budget", json!(100)),
    ]);
    let next = maintenance_budget_state(&state, &Map::new(), &Map::new(), true, 20, 0, 0);
    assert_eq!(next.get("step_budget"), Some(&json!(30)));
}

#[test]
fn maintenance_budget_state_fair_scheduling_uses_request_when_smaller() {
    let state = obj(&[("step_used", json!(10)), ("request_step_budget", json!(5))]);
    let next = maintenance_budget_state(&state, &Map::new(), &Map::new(), true, 20, 0, 0);
    assert_eq!(next.get("step_budget"), Some(&json!(5)));
}

#[test]
fn durable_maintenance_usage_filters_job_and_kind() {
    let payload = |job: &str| {
        json!({
            "attribution": {"maintenance_job_id": job},
            "artifact_kind": "usage_event",
            "kind": "debit",
            "unit": "token",
            "amount": 10,
        })
    };
    let rows: Vec<Vec<Value>> = vec![
        vec![json!(1), json!(payload("job:1").to_string())],
        vec![json!(2), json!(payload("job:2").to_string())],
        vec![
            json!(3),
            json!({
                "attribution": {"maintenance_job_id": "job:1"},
                "artifact_kind": "other",
                "kind": "debit",
                "unit": "token",
                "amount": 999,
            }),
        ],
    ];
    let usage = durable_maintenance_usage("ns", &rows, "job:1");
    assert_eq!(usage.get("token_used"), Some(&json!(10)));
    assert_eq!(usage.get("call_used"), Some(&json!(0)));
}

#[test]
fn durable_maintenance_usage_skips_malformed_rows() {
    let rows: Vec<Vec<Value>> = vec![
        vec![json!(1)],
        vec![json!(2), json!("not-json")],
        vec![
            json!(3),
            json!({"attribution": {"maintenance_job_id": "job:1"}}),
        ],
    ];
    let usage = durable_maintenance_usage("ns", &rows, "job:1");
    assert_eq!(usage.get("token_used"), Some(&json!(0)));
}

#[test]
fn edge_ids_collects_source_and_target() {
    let edge = obj(&[
        ("source_ids", json!(["a", " b "])),
        ("target_ids", json!("c")),
    ]);
    let ids = edge_ids(&edge);
    assert_eq!(
        ids,
        HashSet::from(["a".to_string(), "b".to_string(), "c".to_string()])
    );
}

#[test]
fn edge_ids_ignores_blank_and_non_string() {
    let edge = obj(&[
        ("source_ids", json!(["", "  ", null])),
        ("target_ids", json!(42)),
    ]);
    assert!(edge_ids(&edge).is_empty());
}
