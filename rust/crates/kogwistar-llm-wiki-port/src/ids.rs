use serde_json::Value;
use uuid::Uuid;

#[must_use]
pub fn stable_id_values(values: &[Value]) -> Uuid {
    let key = serde_json::to_string(&Value::Array(values.to_vec()))
        .expect("canonical JSON for string-array stable IDs");
    let project_namespace = Uuid::new_v5(&Uuid::NAMESPACE_URL, b"graph-knowledge-engine");
    Uuid::new_v5(&project_namespace, key.as_bytes())
}

#[must_use]
pub fn stable_id(kind: &str, parts: &[&str]) -> Uuid {
    let mut values = Vec::with_capacity(parts.len() + 1);
    values.push(Value::String(kind.to_owned()));
    values.extend(parts.iter().map(|part| Value::String((*part).to_owned())));
    stable_id_values(&values)
}
