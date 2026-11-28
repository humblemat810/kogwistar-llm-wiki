use serde::{Deserialize, Serialize};
use serde_json::Value;

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct McpToolSpec {
    pub name: String,
    pub description: String,
    #[serde(rename = "inputSchema")]
    pub input_schema: Value,
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
