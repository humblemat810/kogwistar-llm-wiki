use serde_json::{json, Value};

use kogwistar_llm_wiki_port::mcp_server::{dispatch, tool_specs, McpGateway, McpServerError};

struct RecordingGateway(Option<Value>);

impl McpGateway for RecordingGateway {
    fn call_mcp_tool(&self, _name: &str, _arguments: &Value) -> Result<Value, McpServerError> {
        match self.0.clone() {
            Some(value) => Ok(value),
            None => Err(McpServerError::Gateway("gateway unavailable".to_string())),
        }
    }
}

#[test]
fn dispatches_known_tool_to_gateway() {
    let gateway = RecordingGateway(Some(json!({"answer": {"text": "grounded"}})));
    let result = dispatch(
        "query",
        &json!({"workspace_id": "ws", "query_text": "q"}),
        &gateway,
    )
    .unwrap();
    assert_eq!(result["answer"]["text"], "grounded");
}

#[test]
fn rejects_unknown_tool() {
    let gateway = RecordingGateway(Some(json!({})));
    let error = dispatch("unknown", &json!({}), &gateway).unwrap_err();
    assert_eq!(error.to_string(), "unknown tool: unknown");
}

#[test]
fn propagates_gateway_error() {
    let gateway = RecordingGateway(None);
    let error = dispatch("query", &json!({"workspace_id": "ws"}), &gateway).unwrap_err();
    assert_eq!(error.to_string(), "gateway error: gateway unavailable");
}

#[test]
fn exposes_spec_registry() {
    let specs = tool_specs().unwrap();
    assert_eq!(specs.len(), 18);
    assert_eq!(specs[0].name, "query");
    assert_eq!(specs[0].input_schema["type"], "object");
}
