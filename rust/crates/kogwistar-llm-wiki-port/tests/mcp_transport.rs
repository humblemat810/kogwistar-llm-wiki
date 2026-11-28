use serde_json::{json, Value};

use kogwistar_llm_wiki_port::mcp_server::{
    handle_http_request, handle_stdio_message, McpGateway, McpServerError,
};

struct RecordingGateway(Value);

impl McpGateway for RecordingGateway {
    fn call_mcp_tool(&self, _name: &str, _arguments: &Value) -> Result<Value, McpServerError> {
        Ok(self.0.clone())
    }
}

#[test]
fn handles_stdio_initialize_list_call_and_notification() {
    let gateway = RecordingGateway(json!({"answer": {"text": "grounded"}}));
    let initialize = handle_stdio_message(
        &json!({"jsonrpc":"2.0","id":1,"method":"initialize","params":{}}),
        &gateway,
    )
    .unwrap();
    assert_eq!(initialize.as_ref().unwrap()["id"], 1);
    assert!(initialize.as_ref().unwrap()["result"]["protocolVersion"].is_string());

    let initialized = handle_stdio_message(
        &json!({"jsonrpc":"2.0","method":"notifications/initialized"}),
        &gateway,
    )
    .unwrap();
    assert_eq!(initialized, None);

    let list = handle_stdio_message(
        &json!({"jsonrpc":"2.0","id":2,"method":"tools/list"}),
        &gateway,
    )
    .unwrap()
    .unwrap();
    assert_eq!(list["result"]["tools"].as_array().map(Vec::len), Some(18));

    let call = handle_stdio_message(
        &json!({
            "jsonrpc":"2.0","id":3,"method":"tools/call",
            "params":{"name":"query","arguments":{"workspace_id":"ws","query_text":"q"}}
        }),
        &gateway,
    )
    .unwrap()
    .unwrap();
    assert_eq!(
        call["result"]["structuredContent"]["answer"]["text"],
        "grounded"
    );
}

#[test]
fn rejects_stdio_unknown_method() {
    let gateway = RecordingGateway(json!({}));
    let response = handle_stdio_message(
        &json!({"jsonrpc":"2.0","id":4,"method":"unknown"}),
        &gateway,
    )
    .unwrap()
    .unwrap();
    assert_eq!(response["error"]["code"], -32601);
}

#[test]
fn routes_streamable_http_post_get_and_unknown_paths() {
    let gateway = RecordingGateway(json!({"answer": {"text": "grounded"}}));
    let response = handle_http_request(
        "POST",
        "/mcp",
        &json!({
            "jsonrpc":"2.0","id":1,"method":"tools/call",
            "params":{"name":"query","arguments":{"workspace_id":"ws","query_text":"q"}}
        }),
        &gateway,
    )
    .unwrap();
    assert_eq!(response.0, 200);
    assert_eq!(
        response.1["result"]["structuredContent"]["answer"]["text"],
        "grounded"
    );

    let notification = handle_http_request(
        "POST",
        "/mcp",
        &json!({"jsonrpc":"2.0","method":"notifications/initialized"}),
        &gateway,
    )
    .unwrap();
    assert_eq!(notification.0, 202);

    let method = handle_http_request("GET", "/mcp", &json!({}), &gateway).unwrap();
    assert_eq!(method.0, 405);
    let path = handle_http_request("POST", "/other", &json!({}), &gateway).unwrap();
    assert_eq!(path.0, 404);
}
