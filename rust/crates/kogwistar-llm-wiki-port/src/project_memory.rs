// T5.14 project memory contracts - mirroring Python memory/service.py
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;

pub type JsonObject = BTreeMap<String, Value>;

/// Mirrors Python `MemoryKind`.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MemoryKind {
    Decision,
    Constraint,
    Convention,
    Finding,
    TaskOutcome,
}

/// Mirrors Python `MemoryConfidence`.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MemoryConfidence {
    Verified,
    Inferred,
}

/// Mirrors Python `EvidenceKind`.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EvidenceKind {
    SourceSpan,
    Repository,
}

/// Mirrors Python `MemoryDisabledError` / `MemoryValidationError`.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum MemoryError {
    Disabled,
    Validation(String),
}

impl std::fmt::Display for MemoryError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Disabled => write!(
                f,
                "autonomous Codex memory is disabled; set LLM_WIKI_CODEX_MEMORY_ENABLED=true"
            ),
            Self::Validation(msg) => write!(f, "memory validation error: {msg}"),
        }
    }
}

impl std::error::Error for MemoryError {}

/// Mirrors Python `MemoryEvidence` (bounded reference to known evidence).
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MemoryEvidence {
    pub kind: EvidenceKind,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub evidence_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_document_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub span: Option<JsonObject>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub repository_path: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub revision: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub content_sha256: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub start_line: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub end_line: Option<u64>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub excerpt: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub locator: Option<String>,
}

/// Mirrors Python `MemoryRecord`.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MemoryRecord {
    pub workspace_id: String,
    pub statement: String,
    pub rationale: String,
    pub kind: MemoryKind,
    pub confidence: MemoryConfidence,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub lifecycle_status: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub created_at_ms: Option<u64>,
    #[serde(default)]
    pub links: Vec<String>,
    #[serde(default)]
    pub metadata: JsonObject,
    #[serde(default)]
    pub evidence: Vec<MemoryEvidence>,
}

impl MemoryRecord {
    pub fn memory_id(&self) -> String {
        format!("mem:{}", self.workspace_id)
    }
}

/// Mirrors Python `MemoryService` configuration.
#[derive(Clone, Debug, Default)]
pub struct MemoryServiceConfig {
    pub enabled: bool,
    pub max_records_per_capture: usize,
    pub max_recall_records: usize,
}

impl MemoryServiceConfig {
    pub fn from_env_or_defaults(enabled: Option<bool>) -> Self {
        Self {
            enabled: enabled.unwrap_or(false),
            max_records_per_capture: 8,
            max_recall_records: 12,
        }
    }
}

/// Mirrors Python `MemoryService` (in-memory store mirror; graph persistence
/// boundary is the caller-owned `NamespaceEngines` equivalent).
#[derive(Clone, Debug, Default)]
pub struct MemoryService {
    pub config: MemoryServiceConfig,
    records: Vec<MemoryRecord>,
}

impl MemoryService {
    pub fn new(config: MemoryServiceConfig) -> Self {
        Self {
            config,
            records: Vec::new(),
        }
    }

    pub fn enabled(&self) -> bool {
        self.config.enabled
    }

    /// Mirrors Python `MemoryService.capture`.
    pub fn capture(&mut self, payload: Vec<JsonObject>) -> Result<JsonObject, MemoryError> {
        if !self.config.enabled {
            return Err(MemoryError::Disabled);
        }
        if payload.is_empty() || payload.len() > self.config.max_records_per_capture {
            return Err(MemoryError::Validation(format!(
                "memory capture accepts 1..{} records",
                self.config.max_records_per_capture
            )));
        }
        let mut records: Vec<MemoryRecord> = Vec::new();
        for raw in &payload {
            let value: Value = serde_json::to_value(raw.clone()).map_err(|e| {
                MemoryError::Validation(format!("memory record serialization failed: {e}"))
            })?;
            records.push(serde_json::from_value(value).map_err(|e| {
                MemoryError::Validation(format!("memory record validation failed: {e}"))
            })?);
        }
        let workspace_ids: std::collections::HashSet<&str> =
            records.iter().map(|r| r.workspace_id.as_str()).collect();
        if workspace_ids.len() != 1 {
            return Err(MemoryError::Validation(
                "a memory capture batch must contain one workspace only".to_owned(),
            ));
        }
        let workspace_id = records[0].workspace_id.clone();
        for record in records {
            self._persist(record);
        }
        let mut out = JsonObject::new();
        out.insert("status".to_owned(), Value::String("captured".to_owned()));
        out.insert("workspace_id".to_owned(), Value::String(workspace_id));
        let records_value: Vec<Value> = self
            .records
            .iter()
            .rev()
            .take(payload.len())
            .map(|r| serde_json::to_value(r).unwrap_or(Value::Null))
            .collect();
        out.insert("records".to_owned(), Value::Array(records_value));
        Ok(out)
    }

    /// Mirrors Python `MemoryService.recall`.
    pub fn recall(
        &self,
        workspace_id: &str,
        query_text: &str,
        include_inferred: bool,
        limit: Option<usize>,
    ) -> Result<JsonObject, MemoryError> {
        if workspace_id.trim().is_empty() {
            return Err(MemoryError::Validation(
                "memory recall requires workspace_id".to_owned(),
            ));
        }
        let mut records: Vec<&MemoryRecord> = self
            .records
            .iter()
            .filter(|r| r.workspace_id == workspace_id)
            .collect();
        if !include_inferred {
            records.retain(|r| r.confidence != MemoryConfidence::Inferred);
        }
        let query_words: std::collections::HashSet<String> = query_text
            .split_whitespace()
            .map(|w| w.to_lowercase())
            .collect();
        let mut ranked: Vec<(f64, &MemoryRecord)> = Vec::new();
        for record in records {
            let words: std::collections::HashSet<String> =
                format!("{} {}", record.statement, record.rationale)
                    .split_whitespace()
                    .map(|w| w.to_lowercase())
                    .collect();
            let overlap = if query_words.is_empty() {
                0.0
            } else {
                words.intersection(&query_words).count() as f64 / query_words.len() as f64
            };
            let confidence_bonus = if record.confidence == MemoryConfidence::Verified {
                0.1
            } else {
                0.0
            };
            ranked.push((overlap + confidence_bonus, record));
        }
        ranked.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap_or(std::cmp::Ordering::Equal));
        let maximum = limit.unwrap_or(self.config.max_recall_records);
        let selected: Vec<&MemoryRecord> =
            ranked.into_iter().take(maximum).map(|(_, r)| r).collect();
        let verified: Vec<Value> = selected
            .iter()
            .filter(|r| r.confidence == MemoryConfidence::Verified)
            .map(|r| self._record_payload(r, None))
            .collect();
        let inferred: Vec<Value> = selected
            .iter()
            .filter(|r| r.confidence == MemoryConfidence::Inferred)
            .map(|r| self._record_payload(r, None))
            .collect();
        let mut out = JsonObject::new();
        out.insert("status".to_owned(), Value::String("ok".to_owned()));
        out.insert(
            "workspace_id".to_owned(),
            Value::String(workspace_id.to_owned()),
        );
        out.insert("enabled".to_owned(), Value::Bool(self.config.enabled));
        out.insert(
            "query_text".to_owned(),
            Value::String(query_text.to_owned()),
        );
        out.insert("verified".to_owned(), Value::Array(verified));
        out.insert("inferred".to_owned(), Value::Array(inferred));
        out.insert("count".to_owned(), Value::Number(selected.len().into()));
        Ok(out)
    }

    /// Mirrors Python `MemoryService.review`.
    pub fn review(
        &self,
        workspace_id: &str,
        kind: Option<&str>,
        confidence: Option<&str>,
        lifecycle_status: Option<&str>,
        limit: Option<usize>,
    ) -> Result<JsonObject, MemoryError> {
        if workspace_id.trim().is_empty() {
            return Err(MemoryError::Validation(
                "memory review requires workspace_id".to_owned(),
            ));
        }
        if let Some(k) = kind {
            if !matches!(
                k,
                "decision" | "constraint" | "convention" | "finding" | "task_outcome"
            ) {
                return Err(MemoryError::Validation(
                    "memory review kind is invalid".to_owned(),
                ));
            }
        }
        if let Some(c) = confidence {
            if !matches!(c, "verified" | "inferred") {
                return Err(MemoryError::Validation(
                    "memory review confidence is invalid".to_owned(),
                ));
            }
        }
        if let Some(s) = lifecycle_status {
            if !matches!(s, "candidate" | "reviewed" | "superseded" | "retired") {
                return Err(MemoryError::Validation(
                    "memory review lifecycle_status is invalid".to_owned(),
                ));
            }
        }
        let mut records: Vec<&MemoryRecord> = self
            .records
            .iter()
            .filter(|r| r.workspace_id == workspace_id)
            .collect();
        records.retain(|r| {
            kind.is_none_or(|k| format!("{:?}", r.kind).to_lowercase() == k)
                && confidence.is_none_or(|c| format!("{:?}", r.confidence).to_lowercase() == c)
                && lifecycle_status.is_none_or(|s| r.lifecycle_status.as_deref() == Some(s))
        });
        records.sort_by(|a, b| b.created_at_ms.cmp(&a.created_at_ms));
        let maximum = limit.unwrap_or(50);
        let selected: Vec<&MemoryRecord> = records.into_iter().take(maximum).collect();
        let payload: Vec<Value> = selected
            .iter()
            .map(|r| self._record_payload(r, None))
            .collect();
        let mut out = JsonObject::new();
        out.insert("status".to_owned(), Value::String("ok".to_owned()));
        out.insert(
            "workspace_id".to_owned(),
            Value::String(workspace_id.to_owned()),
        );
        out.insert("enabled".to_owned(), Value::Bool(self.config.enabled));
        out.insert("records".to_owned(), Value::Array(payload));
        out.insert("count".to_owned(), Value::Number(selected.len().into()));
        Ok(out)
    }

    fn _persist(&mut self, record: MemoryRecord) {
        self.records.push(record);
    }

    fn _record_payload(&self, record: &MemoryRecord, _score: Option<f64>) -> Value {
        serde_json::to_value(record).unwrap_or(Value::Null)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[allow(dead_code)]
    fn record(workspace_id: &str, kind: MemoryKind, confidence: MemoryConfidence) -> MemoryRecord {
        MemoryRecord {
            workspace_id: workspace_id.to_owned(),
            statement: "decide to proceed".to_owned(),
            rationale: "risk is acceptable".to_owned(),
            kind,
            confidence,
            lifecycle_status: Some("candidate".to_owned()),
            created_at_ms: Some(1000),
            links: Vec::new(),
            metadata: JsonObject::new(),
            evidence: Vec::new(),
        }
    }

    #[test]
    fn capture_disabled_when_config_off() {
        let mut service = MemoryService::new(MemoryServiceConfig::from_env_or_defaults(None));
        assert!(!service.enabled());
        let err = service.capture(vec![JsonObject::new()]).unwrap_err();
        assert!(matches!(err, MemoryError::Disabled));
    }

    #[test]
    fn capture_rejects_empty_and_multi_workspace() {
        let mut service = MemoryService::new(MemoryServiceConfig {
            enabled: true,
            max_records_per_capture: 8,
            max_recall_records: 12,
        });
        assert!(service.capture(Vec::new()).is_err());
        let bad = vec![
            JsonObject::from([("workspace_id".to_owned(), Value::String("ws-1".to_owned()))]),
            JsonObject::from([("workspace_id".to_owned(), Value::String("ws-2".to_owned()))]),
        ];
        assert!(matches!(
            service.capture(bad).unwrap_err(),
            MemoryError::Validation { .. }
        ));
    }

    #[test]
    fn capture_then_recall_and_review() {
        let mut service = MemoryService::new(MemoryServiceConfig {
            enabled: true,
            max_records_per_capture: 8,
            max_recall_records: 12,
        });
        let mut payload =
            JsonObject::from([("workspace_id".to_owned(), Value::String("ws-1".to_owned()))]);
        payload.insert(
            "statement".to_owned(),
            Value::String("decide to proceed".to_owned()),
        );
        payload.insert(
            "rationale".to_owned(),
            Value::String("risk is acceptable".to_owned()),
        );
        payload.insert("kind".to_owned(), Value::String("decision".to_owned()));
        payload.insert(
            "confidence".to_owned(),
            Value::String("verified".to_owned()),
        );
        let result = service.capture(vec![payload]).unwrap();
        assert_eq!(
            result.get("status"),
            Some(&Value::String("captured".to_owned()))
        );
        assert_eq!(
            result.get("workspace_id"),
            Some(&Value::String("ws-1".to_owned()))
        );

        let recall = service.recall("ws-1", "decide", true, None).unwrap();
        assert_eq!(recall.get("count"), Some(&Value::Number(1.into())));
        let verified = recall.get("verified").unwrap();
        assert!(verified.is_array());
        let arr = verified.as_array().unwrap();
        assert_eq!(arr.len(), 1);
        let obj = arr.first().unwrap();
        assert_eq!(
            obj.get("workspace_id"),
            Some(&Value::String("ws-1".to_owned()))
        );
        assert_eq!(
            obj.get("statement"),
            Some(&Value::String("decide to proceed".to_owned()))
        );

        let review = service
            .review("ws-1", Some("decision"), None, None, None)
            .unwrap();
        assert_eq!(review.get("count"), Some(&Value::Number(1.into())));
    }

    #[test]
    fn review_rejects_invalid_kind() {
        let service = MemoryService::new(MemoryServiceConfig::default());
        let err = service
            .review("ws-1", Some("bogus"), None, None, None)
            .unwrap_err();
        assert!(matches!(err, MemoryError::Validation { .. }));
    }
}
