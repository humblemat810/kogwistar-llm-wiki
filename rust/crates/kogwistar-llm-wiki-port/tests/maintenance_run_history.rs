use kogwistar_llm_wiki_port::maintenance_run_history::{make_run_record, RunHistory};
use serde_json::json;

#[test]
fn run_history_lists_for_workspace_most_recent_first() {
    let mut history = RunHistory::new();
    history.push(make_run_record(
        "r1",
        "ws-a",
        "graph_patch",
        "ok",
        json!({}),
    ));
    history.push(make_run_record(
        "r2",
        "ws-b",
        "graph_patch",
        "ok",
        json!({}),
    ));
    history.push(make_run_record("r3", "ws-a", "repair", "retry", json!({})));

    let ws_a = history.list_for_workspace("ws-a", 10);
    assert_eq!(ws_a.len(), 2);
    assert_eq!(ws_a[0].run_id, "r3");
    assert_eq!(ws_a[1].run_id, "r1");
}

#[test]
fn run_history_excludes_other_workspaces() {
    let mut history = RunHistory::new();
    history.push(make_run_record(
        "r1",
        "ws-a",
        "graph_patch",
        "ok",
        json!({}),
    ));
    history.push(make_run_record(
        "r2",
        "ws-b",
        "graph_patch",
        "ok",
        json!({}),
    ));

    assert_eq!(history.list_for_workspace("ws-c", 10).len(), 0);
    assert_eq!(history.count_for_workspace("ws-a"), 1);
    assert_eq!(history.count_for_workspace("ws-b"), 1);
    assert_eq!(history.len(), 2);
}

#[test]
fn run_history_bounds_by_limit() {
    let mut history = RunHistory::new();
    for i in 1..=5 {
        history.push(make_run_record(
            &format!("r{i}"),
            "ws-a",
            "graph_patch",
            "ok",
            json!({}),
        ));
    }
    let page = history.list_for_workspace("ws-a", 2);
    assert_eq!(page.len(), 2);
    assert_eq!(page[0].run_id, "r5");
    assert_eq!(page[1].run_id, "r4");
}
