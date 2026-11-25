use serde_json::Value;
use sha2::{Digest, Sha256};

use crate::models::JsonObject;

#[must_use]
pub fn canonicalize_evidence_pack_digest(digest: &JsonObject) -> JsonObject {
    let mut payload = digest.clone();
    payload.insert(
        "node_ids".to_owned(),
        Value::Array(string_ids(payload.get("node_ids"))),
    );
    payload.insert(
        "edge_ids".to_owned(),
        Value::Array(string_ids(payload.get("edge_ids"))),
    );
    payload.remove("evidence_pack_hash");
    payload
}

#[must_use]
pub fn evidence_pack_digest_hash(digest: &JsonObject) -> String {
    let payload = canonicalize_evidence_pack_digest(digest);
    let blob = python_ascii_json(&Value::Object(payload.into_iter().collect()));
    let mut hasher = Sha256::new();
    hasher.update(blob.as_bytes());
    hex_lower(&hasher.finalize())
}

fn string_ids(value: Option<&Value>) -> Vec<Value> {
    let Some(Value::Array(values)) = value else {
        return Vec::new();
    };
    let mut ids: Vec<String> = values
        .iter()
        .map(|value| match value {
            Value::String(value) => value.clone(),
            other => other.to_string(),
        })
        .filter(|id| !id.is_empty())
        .collect();
    ids.sort();
    ids.into_iter().map(Value::String).collect()
}

pub fn python_ascii_json(value: &Value) -> String {
    match value {
        Value::Null => "null".to_owned(),
        Value::Bool(value) => value.to_string(),
        Value::Number(value) => value.to_string(),
        Value::String(value) => json_string(value),
        Value::Array(values) => {
            let items: Vec<String> = values.iter().map(python_ascii_json).collect();
            format!("[{}]", items.join(","))
        }
        Value::Object(values) => {
            let items: Vec<String> = values
                .iter()
                .map(|(key, value)| format!("{}:{}", json_string(key), python_ascii_json(value)))
                .collect();
            format!("{{{}}}", items.join(","))
        }
    }
}

#[must_use]
pub fn python_spaced_json(value: &Value) -> String {
    match value {
        Value::Null => "null".to_owned(),
        Value::Bool(value) => value.to_string(),
        Value::Number(value) => value.to_string(),
        Value::String(value) => json_string(value),
        Value::Array(values) => {
            let items: Vec<String> = values.iter().map(python_spaced_json).collect();
            format!("[{}]", items.join(", "))
        }
        Value::Object(values) => {
            let items: Vec<String> = values
                .iter()
                .map(|(key, value)| format!("{}: {}", json_string(key), python_spaced_json(value)))
                .collect();
            format!("{{{}}}", items.join(", "))
        }
    }
}

fn json_string(value: &str) -> String {
    let mut output = String::from("\"");
    for character in value.chars() {
        match character {
            '"' => output.push_str("\\\""),
            '\\' => output.push_str("\\\\"),
            '\n' => output.push_str("\\n"),
            '\r' => output.push_str("\\r"),
            '\t' => output.push_str("\\t"),
            '\u{08}' => output.push_str("\\b"),
            '\u{0c}' => output.push_str("\\f"),
            character if (character as u32) < 0x20 => {
                output.push_str(&format!("\\u{:04x}", character as u32));
            }
            character if (character as u32) < 0x80 => output.push(character),
            character if (character as u32) <= 0xffff => {
                output.push_str(&format!("\\u{:04x}", character as u32));
            }
            character => {
                let code_point = character as u32 - 0x1_0000;
                let high = 0xd800 + (code_point >> 10);
                let low = 0xdc00 + (code_point & 0x3ff);
                output.push_str(&format!("\\u{high:04x}\\u{low:04x}"));
            }
        }
    }
    output.push('"');
    output
}

fn hex_lower(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}
