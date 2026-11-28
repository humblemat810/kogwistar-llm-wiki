use serde_json::{json, Value};

use kogwistar_llm_wiki_port::mcp_server::{handle_sse_request, McpGateway, McpServerError};

struct RecordingGateway(Value);

impl McpGateway for RecordingGateway {
    fn call_mcp_tool(&self, _name: &str, _arguments: &Value) -> Result<Value, McpServerError> {
        Ok(self.0.clone())
    }
}

#[test]
fn opens_sse_session_with_required_headers_and_body() {
    let gateway = RecordingGateway(json!({}));
    let (status, headers, body) =
        handle_sse_request("GET", "/mcp", "session-1", &json!({}), &gateway).unwrap();
    assert_eq!(status, 200);
    assert!(headers
        .iter()
        .any(|(key, value)| key == "content-type" && value == "text/event-stream"));
    assert!(headers
        .iter()
        .any(|(key, value)| key == "mcp-session-id" && value == "session-1"));
    assert_eq!(body, "event: connected\n\n");
}

#[test]
fn routes_sse_message_and_unknown_paths() {
    let gateway = RecordingGateway(json!({"answer": {"text": "grounded"}}));
    let (status, _, body) = handle_sse_request(
        "POST",
        "/mcp/messages",
        "session-1",
        &json!({
            "jsonrpc":"2.0","id":1,"method":"tools/call",
            "params":{"name":"query","arguments":{"workspace_id":"ws","query_text":"q"}}
        }),
        &gateway,
    )
    .unwrap();
    assert_eq!(status, 202);
    assert_eq!(
        body["result"]["structuredContent"]["answer"]["text"],
        "grounded"
    );

    let (status, _, body) =
        handle_sse_request("GET", "/unknown", "session-1", &json!({}), &gateway).unwrap();
    assert_eq!(status, 404);
    assert_eq!(body, "Not Found");
}
