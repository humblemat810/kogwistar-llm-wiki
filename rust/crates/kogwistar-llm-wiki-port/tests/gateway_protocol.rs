use kogwistar_llm_wiki_port::gateway_protocol::{
    a2a_task, answer_text, count_job_statuses, jsonrpc_error, jsonrpc_result, request_id_from,
};
use serde_json::json;

#[test]
fn job_statuses_are_counted() {
    let jobs = vec![
        json!({"status": "running"}),
        json!({"status": "running"}),
        json!({"status": "completed"}),
        json!({}),
    ];

    assert_eq!(
        count_job_statuses(&jobs),
        [
            ("running".to_string(), 2),
            ("completed".to_string(), 1),
            ("unknown".to_string(), 1)
        ]
        .into_iter()
        .collect::<std::collections::HashMap<String, usize>>()
    );
}

#[test]
fn answer_text_prefers_nested_text() {
    assert_eq!(answer_text(&json!({"answer": {"text": "ready"}})), "ready");
    assert_eq!(answer_text(&json!({"answer": "plain"})), "plain");
    assert_eq!(answer_text(&json!({})), "");
}

#[test]
fn request_id_uses_existing_or_explicit_generator() {
    assert_eq!(
        request_id_from(&json!({"id": "req-1"}), "job", || "generated".to_string()),
        "req-1"
    );
    assert_eq!(
        request_id_from(&json!({}), "job", || "generated".to_string()),
        "job_generated"
    );
}

#[test]
fn a2a_task_shapes_pending_interaction() {
    let interaction = json!({
        "interaction_id": "i-1",
        "context_id": "ctx-1",
        "workspace_id": "ws-1",
        "response": {"answer": {"text": "hello"}}
    });

    assert_eq!(
        a2a_task(&interaction, false),
        json!({
            "id": "i-1",
            "contextId": "ctx-1",
            "status": {"state": "working"},
            "metadata": {"workspace_id": "ws-1"},
            "artifacts": [{"parts": [{"kind": "text", "text": "hello"}]}]
        })
    );
}

#[test]
fn a2a_task_preserves_status_objects() {
    let interaction = json!({
        "interaction_id": "i-1",
        "workspace_id": "ws-1",
        "status": {"state": "failed"}
    });
    let result = a2a_task(&interaction, true);

    assert_eq!(result["status"], json!({"state": "failed"}));
    assert_eq!(result["contextId"], json!("default"));
    assert_eq!(result["interaction_id"], json!("i-1"));
}

#[test]
fn jsonrpc_envelopes_match_the_wire_contract() {
    assert_eq!(
        jsonrpc_result(&json!("id-1"), &json!({"ok": true})),
        json!({"jsonrpc": "2.0", "id": "id-1", "result": {"ok": true}})
    );
    assert_eq!(
        jsonrpc_error(
            &json!(1),
            -32601,
            "not found",
            Some(&json!({"kind": "method"}))
        ),
        json!({
            "jsonrpc": "2.0",
            "id": 1,
            "error": {"code": -32601, "message": "not found", "data": {"kind": "method"}}
        })
    );
    assert_eq!(
        jsonrpc_error(&json!(2), -32600, "bad request", None),
        json!({
            "jsonrpc": "2.0",
            "id": 2,
            "error": {"code": -32600, "message": "bad request"}
        })
    );
}
