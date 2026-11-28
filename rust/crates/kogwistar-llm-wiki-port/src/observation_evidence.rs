use std::collections::BTreeMap;

use serde_json::{json, Value};

const SPAN_FIELDS: [&str; 4] = ["doc_id", "start_char", "end_char", "excerpt"];

fn mapping(value: Option<&Value>) -> BTreeMap<String, Value> {
    match value {
        Some(Value::Object(values)) => values
            .iter()
            .map(|(key, value)| (key.clone(), value.clone()))
            .collect(),
        Some(Value::String(value)) => serde_json::from_str(value).unwrap_or_default(),
        _ => BTreeMap::new(),
    }
}

#[must_use]
pub fn entity_metadata(entity: &Value) -> Value {
    let metadata = mapping(entity.get("metadata"));
    let nested = mapping(metadata.get("metadata"));
    let mut merged = metadata;
    merged.extend(nested);
    let mut properties = mapping(merged.get("properties"));
    properties.extend(mapping(entity.get("properties")));
    merged.extend(properties);
    Value::Object(merged.into_iter().collect())
}

#[must_use]
pub fn compact_entity_record(entity: &Value, workspace_id: &str, namespace: &str) -> Value {
    let metadata = entity_metadata(entity);
    let scalar_fields = [
        "relation",
        "quality_status",
        "parse_status",
        "source_document_id",
        "source_revision_id",
        "revision_id",
        "revision_document_id",
        "source_uri",
        "source_digest",
        "source_namespace",
        "target_namespace",
        "target_kind",
        "target_id",
        "artifact_kind",
        "knowledge_layer",
        "extraction_status",
        "verification_status",
        "parent_member_id",
        "member_id",
        "parse_generation_member_id",
        "is_pointer",
    ];
    let mut compact_metadata = BTreeMap::new();
    for key in scalar_fields {
        if let Some(value) = metadata.get(key) {
            match value {
                Value::String(value) => {
                    compact_metadata.insert(
                        key.to_owned(),
                        Value::from(value.chars().take(300).collect::<String>()),
                    );
                }
                Value::Bool(value) => {
                    compact_metadata.insert(key.to_owned(), Value::from(*value));
                }
                Value::Number(value) => {
                    compact_metadata.insert(key.to_owned(), Value::Number(value.clone()));
                }
                _ => {}
            }
        }
    }
    let id = entity
        .get("id")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .chars()
        .take(256)
        .collect::<String>();
    let label = entity
        .get("label")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .chars()
        .take(300)
        .collect::<String>();
    let summary = entity
        .get("summary")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .chars()
        .take(500)
        .collect::<String>();
    let mut record = json!({
        "id": id,
        "kind": "node",
        "workspace_id": workspace_id,
        "namespace": namespace,
        "authorized": true,
        "acl_authorized": true,
        "label": label,
        "summary": summary,
        "metadata": compact_metadata
    });
    for key in [
        "relation",
        "quality_status",
        "parse_status",
        "source_document_id",
        "source_revision_id",
        "revision_id",
        "revision_document_id",
        "source_uri",
        "source_digest",
        "evidence_ids",
        "provenance",
    ] {
        let Some(value) = metadata.get(key) else {
            continue;
        };
        match (key, value) {
            ("evidence_ids", Value::Array(values)) => {
                let bounded = values
                    .iter()
                    .take(16)
                    .map(|item| {
                        item.as_str()
                            .unwrap_or_default()
                            .chars()
                            .take(256)
                            .collect::<String>()
                    })
                    .collect::<Vec<_>>();
                record[key] = Value::from(bounded);
            }
            ("provenance", _) => {
                let provenance = mapping(Some(value));
                let mut bounded = BTreeMap::new();
                for key in ["source_document_id", "source_revision_id", "source_digest"] {
                    if let Some(value) = provenance.get(key).and_then(Value::as_str) {
                        bounded.insert(
                            key.to_owned(),
                            Value::from(value.chars().take(300).collect::<String>()),
                        );
                    }
                }
                record[key] = Value::Object(bounded.into_iter().collect());
            }
            (_, Value::String(value)) => {
                record[key] = Value::from(value.chars().take(500).collect::<String>());
            }
            ("quality_status" | "parse_status", Value::Null) => {}
            ("quality_status" | "parse_status", value) => {
                record[key] = Value::from(value.to_string().chars().take(128).collect::<String>());
            }
            _ => {}
        }
    }
    let properties: serde_json::Map<String, Value> = compact_metadata
        .iter()
        .filter(|(key, _)| {
            [
                "target_namespace",
                "target_kind",
                "target_id",
                "source_namespace",
            ]
            .contains(&key.as_str())
        })
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect();
    if !properties.is_empty() {
        record["properties"] = Value::Object(properties);
    }
    record
}

#[must_use]
pub fn entity_spans(entity: &Value) -> Vec<Value> {
    let mut spans = Vec::new();
    if let Some(Value::Array(values)) = entity.get("iter_evidence") {
        for value in values {
            let data = mapping(Some(value));
            if !data.is_empty() {
                let span: serde_json::Map<String, Value> = SPAN_FIELDS
                    .iter()
                    .map(|key| {
                        (
                            (*key).to_owned(),
                            data.get(*key).cloned().unwrap_or(Value::Null),
                        )
                    })
                    .collect();
                spans.push(Value::Object(span));
            }
        }
    }
    if !spans.is_empty() {
        return spans;
    }
    let Some(Value::Array(mentions)) = entity.get("mentions") else {
        return spans;
    };
    for mention in mentions {
        let grounding = mapping(Some(mention));
        let Some(Value::Array(grounding_spans)) = grounding.get("spans") else {
            continue;
        };
        for span in grounding_spans {
            let data = mapping(Some(span));
            if data.is_empty() {
                continue;
            }
            let span: serde_json::Map<String, Value> = SPAN_FIELDS
                .iter()
                .map(|key| {
                    (
                        (*key).to_owned(),
                        data.get(*key).cloned().unwrap_or(Value::Null),
                    )
                })
                .collect();
            spans.push(Value::Object(span));
        }
    }
    spans
}
