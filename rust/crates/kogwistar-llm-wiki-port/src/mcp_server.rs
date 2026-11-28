use serde::{Deserialize, Serialize};
use serde_json::Value;

pub type McpSseResponse = (u16, Vec<(String, String)>, Value);

pub use crate::mcp_auth::{
    auth_mode, authenticate_bearer, bearer_from_headers, build_mcp_auth, headers_from_scope,
    McpAuthSettings,
};

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct McpToolSpec {
    pub name: String,
    pub description: String,
    #[serde(rename = "inputSchema")]
    pub input_schema: Value,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum McpServerError {
    UnknownTool(String),
    Gateway(String),
}

impl std::fmt::Display for McpServerError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::UnknownTool(name) => write!(formatter, "unknown tool: {name}"),
            Self::Gateway(message) => write!(formatter, "gateway error: {message}"),
        }
    }
}

impl std::error::Error for McpServerError {}

pub trait McpGateway {
    fn call_mcp_tool(&self, name: &str, arguments: &Value) -> Result<Value, McpServerError>;
}

pub fn dispatch(
    name: &str,
    arguments: &Value,
    gateway: &dyn McpGateway,
) -> Result<Value, McpServerError> {
    let specs = tool_specs().map_err(|error| McpServerError::Gateway(error.to_string()))?;
    if !specs.iter().any(|spec| spec.name == name) {
        return Err(McpServerError::UnknownTool(name.to_owned()));
    }
    gateway.call_mcp_tool(name, arguments)
}

pub fn call_tool_result(result: Result<&Value, &McpServerError>, pretty: bool) -> Value {
    match result {
        Ok(value) => {
            let text = if pretty {
                serde_json::to_string_pretty(value).unwrap_or_else(|_| value.to_string())
            } else {
                value.to_string()
            };
            serde_json::json!({
                "content": [{"type": "text", "text": text}],
                "isError": false,
                "structuredContent": value,
            })
        }
        Err(error) => serde_json::json!({
            "content": [{"type": "text", "text": error.to_string()}],
            "isError": true,
        }),
    }
}

pub fn handle_stdio_message(
    message: &Value,
    gateway: &dyn McpGateway,
) -> Result<Option<Value>, McpServerError> {
    let method = message
        .get("method")
        .and_then(Value::as_str)
        .unwrap_or_default();
    let id = message.get("id").cloned();
    if method.starts_with("notifications/") {
        return Ok(None);
    }
    let response = match method {
        "initialize" => crate::gateway_protocol::jsonrpc_result(
            &id.unwrap_or(Value::Null),
            &serde_json::json!({
                "protocolVersion": "2025-03-26",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "llm-wiki", "version": "1.0.0"},
            }),
        ),
        "tools/list" => crate::gateway_protocol::jsonrpc_result(
            &id.unwrap_or(Value::Null),
            &serde_json::json!({"tools": tool_specs().map_err(|error| McpServerError::Gateway(error.to_string()))?}),
        ),
        "tools/call" => {
            let params = message.get("params").cloned().unwrap_or(Value::Null);
            let name = params
                .get("name")
                .and_then(Value::as_str)
                .unwrap_or_default();
            let arguments = params
                .get("arguments")
                .cloned()
                .unwrap_or(Value::Object(Default::default()));
            match dispatch(name, &arguments, gateway) {
                Ok(result) => crate::gateway_protocol::jsonrpc_result(
                    &id.unwrap_or(Value::Null),
                    &call_tool_result(Ok(&result), true),
                ),
                Err(error) => crate::gateway_protocol::jsonrpc_result(
                    &id.unwrap_or(Value::Null),
                    &call_tool_result(Err(&error), true),
                ),
            }
        }
        _ => crate::gateway_protocol::jsonrpc_error(
            &id.unwrap_or(Value::Null),
            -32601,
            &format!("unknown method: {method}"),
            None,
        ),
    };
    Ok(Some(response))
}

pub fn handle_http_request(
    method: &str,
    path: &str,
    message: &Value,
    gateway: &dyn McpGateway,
) -> Result<(u16, Value), McpServerError> {
    if path != "/mcp" {
        return Ok((404, serde_json::json!({"error": "not found"})));
    }
    match method {
        "POST" => Ok(match handle_stdio_message(message, gateway)? {
            Some(response) => (200, response),
            None => (202, serde_json::json!({})),
        }),
        "GET" => Ok((405, serde_json::json!({"error": "method not allowed"}))),
        _ => Ok((405, serde_json::json!({"error": "method not allowed"}))),
    }
}

pub fn handle_sse_request(
    method: &str,
    path: &str,
    session_id: &str,
    message: &Value,
    gateway: &dyn McpGateway,
) -> Result<McpSseResponse, McpServerError> {
    if path == "/mcp" {
        if method == "GET" {
            return Ok((
                200,
                vec![
                    ("content-type".to_owned(), "text/event-stream".to_owned()),
                    ("mcp-session-id".to_owned(), session_id.to_owned()),
                ],
                Value::String("event: connected\n\n".to_owned()),
            ));
        }
        return Ok((
            405,
            Vec::new(),
            serde_json::json!({"error": "method not allowed"}),
        ));
    }
    if path == "/mcp/messages" {
        if method != "POST" {
            return Ok((
                405,
                Vec::new(),
                serde_json::json!({"error": "method not allowed"}),
            ));
        }
        let (_status, response) = match handle_stdio_message(message, gateway)? {
            Some(response) => (200, response),
            None => (202, serde_json::json!({})),
        };
        return Ok((
            202,
            vec![("mcp-session-id".to_owned(), session_id.to_owned())],
            response,
        ));
    }
    Ok((404, Vec::new(), Value::String("Not Found".to_owned())))
}

pub fn tool_specs() -> Result<Vec<McpToolSpec>, serde_json::Error> {
    let raw_specs = [
        include_str!("mcp_tool_specs_1.json"),
        include_str!("mcp_tool_specs_2.json"),
        include_str!("mcp_tool_specs_3.json"),
        include_str!("mcp_tool_specs_4.json"),
    ];
    let mut specs = Vec::new();
    for raw_spec in raw_specs {
        let tuples = serde_json::from_str::<Vec<(String, String, Value)>>(raw_spec)?;
        specs.extend(tuples.into_iter());
    }
    Ok(specs
        .into_iter()
        .map(|(name, description, input_schema)| McpToolSpec {
            name,
            description,
            input_schema,
        })
        .collect())
}
