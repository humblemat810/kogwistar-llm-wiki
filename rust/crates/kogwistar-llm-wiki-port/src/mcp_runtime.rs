use serde_json::{json, Value};

use crate::cli_runtime::InMemoryCliRuntime;
use crate::mcp_server::{McpGateway, McpServerError};

#[derive(Debug, Default)]
pub struct InMemoryMcpGateway {
    runtime: InMemoryCliRuntime,
}

impl InMemoryMcpGateway {
    pub fn new(runtime: InMemoryCliRuntime) -> Self {
        Self { runtime }
    }
}

impl McpGateway for InMemoryMcpGateway {
    fn call_mcp_tool(&self, name: &str, arguments: &Value) -> Result<Value, McpServerError> {
        let workspace_id = arguments
            .get("workspace_id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .trim();
        match name {
            "status" => self
                .runtime
                .report(workspace_id, "all", "summary", 20)
                .map_err(|error| McpServerError::Gateway(error.to_string())),
            "query" => Ok(json!({
                "answer": {
                    "text": "No matching knowledge yet.",
                    "workspace_id": workspace_id,
                },
                "session_id": arguments.get("session_id").unwrap_or(&Value::Null),
            })),
            _ => Err(McpServerError::Gateway(format!("unsupported tool: {name}"))),
        }
    }
}
