use serde::{Deserialize, Serialize};
use serde_json::Value;

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
