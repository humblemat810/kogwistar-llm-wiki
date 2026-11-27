use kogwistar_llm_wiki_port::app_contracts::{MessageChannel, MessageIntent};
use serde_json::{json, Value};

#[test]
fn message_channel_returns_only_public_dto_slice() {
    let payload = json!({"job_id": "job-1"});
    let message = MessageChannel::wrap_message(
        &payload,
        "background",
        MessageIntent::Request,
        Some("source-1"),
    );

    assert_eq!(
        message,
        json!({
            "target": "background",
            "payload": {"job_id": "job-1"},
            "intent": "request",
            "provenance_id": "source-1"
        })
    );
    assert!(message.get("internal_trace_id").is_none());
}

#[test]
fn message_channel_defaults_provenance_to_null() {
    let message =
        MessageChannel::wrap_message(&Value::Null, "foreground", MessageIntent::Alert, None);

    assert_eq!(message["intent"], json!("alert"));
    assert_eq!(message["provenance_id"], Value::Null);
}
