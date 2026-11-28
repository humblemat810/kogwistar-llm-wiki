use serde_json::{json, Value};

use kogwistar_llm_wiki_port::gateway_protocol::jsonrpc_error;
use kogwistar_llm_wiki_port::mcp_server::{call_tool_result, McpGateway, McpServerError};

#[test]
fn successful_call_result_has_text_and_structured_content() {
    let gateway = kogwistar_llm_wiki_port::mcp_server::dispatch(
        "query",
        &json!({"workspace_id": "ws", "query_text": "q"}),
        &SuccessGateway(json!({"answer": {"text": "grounded"}})),
    )
    .unwrap();
    let result = call_tool_result(Ok(&gateway), true);
    assert_eq!(result["isError"], false);
    assert_eq!(result["structuredContent"]["answer"]["text"], "grounded");
    let text = result["content"][0]["text"].as_str().unwrap();
    let parsed: Value = serde_json::from_str(text).unwrap();
    assert_eq!(parsed["answer"]["text"], "grounded");
}

#[test]
fn failed_dispatch_becomes_error_call_result() {
    let error = McpServerError::UnknownTool("nope".to_string());
    let result = call_tool_result(Err(&error), true);
    assert_eq!(result["isError"], true);
    assert_eq!(result["content"][0]["text"], "unknown tool: nope");
}

#[test]
fn gateway_failure_maps_to_jsonrpc_error_shape() {
    let error = McpServerError::Gateway("boom".to_string());
    let payload = jsonrpc_error(&json!(1), -32000, &error.to_string(), None);
    assert_eq!(payload["jsonrpc"], "2.0");
    assert_eq!(payload["id"], 1);
    assert_eq!(payload["error"]["code"], -32000);
}

struct SuccessGateway(Value);

impl McpGateway for SuccessGateway {
    fn call_mcp_tool(&self, _name: &str, _arguments: &Value) -> Result<Value, McpServerError> {
        Ok(self.0.clone())
    }
}
