// T5.5 source unit - mirroring Python agent/source_tools.py source-unit boundary handling
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;

use crate::ids::stable_id;
use crate::models::{IngestPipelineRequest, JsonObject};

/// A single source document unit: identity + content digest + revision linkage.
#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct SourceUnit {
    pub source_document_id: String,
    pub workspace_id: String,
    pub source_digest: String,
    pub revision_id: String,
    pub revision_document_id: String,
}

impl SourceUnit {
    pub fn new(workspace_id: &str, source_document_id: &str, raw_text: &str) -> Self {
        let digest = source_digest(raw_text);
        let revision_id = stable_id(
            "kogwistar_llm_wiki.source_revision",
            &[workspace_id, source_document_id, digest.as_str(), "content"],
        );
        let revision_document_id = format!("{}:{}", revision_id, digest);
        Self {
            source_document_id: source_document_id.to_owned(),
            workspace_id: workspace_id.to_owned(),
            source_digest: digest,
            revision_id: revision_id.to_string(),
            revision_document_id,
        }
    }

    pub fn as_json(&self) -> Value {
        serde_json::to_value(self).unwrap_or(Value::Null)
    }
}

/// Mirrors Python `_provenance_policy` validation.
#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SourceProvenancePolicy {
    Required,
    Optional,
    Disabled,
}

impl SourceProvenancePolicy {
    pub fn from_value(value: &Option<Value>) -> Result<Self, SourceUnitError> {
        let policy = value
            .as_ref()
            .and_then(|v| v.as_str())
            .unwrap_or("optional")
            .trim()
            .to_lowercase();
        match policy.as_str() {
            "required" => Ok(SourceProvenancePolicy::Required),
            "optional" => Ok(SourceProvenancePolicy::Optional),
            "disabled" => Ok(SourceProvenancePolicy::Disabled),
            _ => Err(SourceUnitError::InvalidProvenancePolicy(policy)),
        }
    }
}

/// Mirrors Python `_parse_limits` validation surface.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct SourceParseLimits {
    pub max_depth: Option<u64>,
    pub max_frontier_items: Option<u64>,
    pub max_parser_calls: Option<u64>,
    pub max_region_chars: Option<u64>,
    pub token_budget: Option<u64>,
    pub wall_time_seconds: Option<f64>,
    pub parser_profile: Option<String>,
}

impl SourceParseLimits {
    pub fn from_request(request: &IngestPipelineRequest) -> Result<Self, SourceUnitError> {
        let allowed = [
            "max_depth",
            "max_frontier_items",
            "max_parser_calls",
            "max_region_chars",
            "token_budget",
            "wall_time_seconds",
            "parser_profile",
        ];
        let unknown: Vec<String> = request
            .parse_limits
            .keys()
            .filter(|name| !allowed.contains(&name.as_str()))
            .cloned()
            .collect();
        if !unknown.is_empty() {
            let mut sorted = unknown;
            sorted.sort();
            return Err(SourceUnitError::UnsupportedParseLimits(sorted.join(", ")));
        }
        Ok(Self {
            max_depth: request
                .parse_limits
                .get("max_depth")
                .and_then(Value::as_u64),
            max_frontier_items: request
                .parse_limits
                .get("max_frontier_items")
                .and_then(Value::as_u64),
            max_parser_calls: request
                .parse_limits
                .get("max_parser_calls")
                .and_then(Value::as_u64),
            max_region_chars: request
                .parse_limits
                .get("max_region_chars")
                .and_then(Value::as_u64),
            token_budget: request
                .parse_limits
                .get("token_budget")
                .and_then(Value::as_u64),
            wall_time_seconds: request
                .parse_limits
                .get("wall_time_seconds")
                .and_then(Value::as_f64),
            parser_profile: request
                .parse_limits
                .get("parser_profile")
                .and_then(Value::as_str)
                .map(|s| s.trim().to_owned()),
        })
    }

    pub fn to_json(&self) -> JsonObject {
        let mut map = BTreeMap::new();
        if let Some(v) = self.max_depth {
            map.insert("max_depth".to_owned(), Value::Number(v.into()));
        }
        if let Some(v) = self.max_frontier_items {
            map.insert("max_frontier_items".to_owned(), Value::Number(v.into()));
        }
        if let Some(v) = self.max_parser_calls {
            map.insert("max_parser_calls".to_owned(), Value::Number(v.into()));
        }
        if let Some(v) = self.max_region_chars {
            map.insert("max_region_chars".to_owned(), Value::Number(v.into()));
        }
        if let Some(v) = self.token_budget {
            map.insert("token_budget".to_owned(), Value::Number(v.into()));
        }
        if let Some(v) = self.wall_time_seconds {
            map.insert(
                "wall_time_seconds".to_owned(),
                Value::Number(serde_json::Number::from_f64(v).unwrap_or(0.into())),
            );
        }
        if let Some(v) = &self.parser_profile {
            map.insert("parser_profile".to_owned(), Value::String(v.clone()));
        }
        map
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum SourceUnitError {
    InvalidProvenancePolicy(String),
    UnsupportedParseLimits(String),
    InvalidPositiveInteger(&'static str),
    InvalidWallTimeSeconds,
    InvalidParserProfile,
}

impl std::fmt::Display for SourceUnitError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::InvalidProvenancePolicy(policy) => {
                write!(
                    f,
                    "provenance_policy must be required, optional, or disabled, got {policy}"
                )
            }
            Self::UnsupportedParseLimits(values) => {
                write!(f, "unsupported parse_limits: {values}")
            }
            Self::InvalidPositiveInteger(name) => {
                write!(f, "parse_limits.{name} must be a positive integer")
            }
            Self::InvalidWallTimeSeconds => write!(
                f,
                "parse_limits.wall_time_seconds must be positive and at most 3600"
            ),
            Self::InvalidParserProfile => write!(
                f,
                "parse_limits.parser_profile must be a non-empty string of at most 256 characters"
            ),
        }
    }
}

impl std::error::Error for SourceUnitError {}

#[must_use]
pub fn source_digest(raw_text: &str) -> String {
    use sha2::{Digest, Sha256};
    let mut digest = Sha256::new();
    digest.update(raw_text.as_bytes());
    hex_upper(&digest.finalize())
}

fn hex_upper(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_request() -> IngestPipelineRequest {
        IngestPipelineRequest {
            workspace_id: "ws-1".to_owned(),
            source_uri: "doc-1".to_owned(),
            title: "Hello".to_owned(),
            raw_text: "hello world".to_owned(),
            source_format: "text/plain".to_owned(),
            operation_mode: "parse_first".to_owned(),
            promotion_mode: "manual".to_owned(),
            auto_accept_threshold: 0.5,
            parser_mode: "heuristic".to_owned(),
            parser_lane: "default".to_owned(),
            llm_provider: None,
            llm_model: None,
            provenance_policy: crate::models::ProvenancePolicy::Optional,
            provenance: None,
            parse_limits: BTreeMap::from([
                ("max_depth".to_owned(), Value::Number(5.into())),
                ("max_region_chars".to_owned(), Value::Number(8192.into())),
                (
                    "parser_profile".to_owned(),
                    Value::String("fast".to_owned()),
                ),
            ]),
        }
    }

    #[test]
    fn source_unit_new_produces_digest_and_revision() {
        let unit = SourceUnit::new("ws-1", "doc-1", "hello world");
        assert_eq!(unit.source_document_id, "doc-1");
        assert_eq!(unit.workspace_id, "ws-1");
        assert!(!unit.source_digest.is_empty());
        assert!(!unit.revision_id.is_empty());
        assert!(!unit.revision_document_id.is_empty());
        // Same inputs => same digest/revision (unit boundary stable)
        let again = SourceUnit::new("ws-1", "doc-1", "hello world");
        assert_eq!(unit.source_digest, again.source_digest);
        assert_eq!(unit.revision_id, again.revision_id);
        // Different text => different digest
        let other = SourceUnit::new("ws-1", "doc-1", "hello world!");
        assert_ne!(unit.source_digest, other.source_digest);
    }

    #[test]
    fn provenance_policy_from_value_defaults_optional() {
        assert_eq!(
            SourceProvenancePolicy::from_value(&None).unwrap(),
            SourceProvenancePolicy::Optional
        );
        assert_eq!(
            SourceProvenancePolicy::from_value(&Some(Value::String("required".to_owned())))
                .unwrap(),
            SourceProvenancePolicy::Required
        );
        assert_eq!(
            SourceProvenancePolicy::from_value(&Some(Value::String("disabled".to_owned())))
                .unwrap(),
            SourceProvenancePolicy::Disabled
        );
        assert!(
            SourceProvenancePolicy::from_value(&Some(Value::String("bogus".to_owned()))).is_err()
        );
    }

    #[test]
    fn parse_limits_from_request_filters_unknown_keys() {
        let mut req = test_request();
        req.parse_limits
            .insert("bogus_key".to_owned(), Value::Number(1.into()));
        assert!(SourceParseLimits::from_request(&req).is_err());
    }

    #[test]
    fn parse_limits_round_trip_json() {
        let req = test_request();
        let limits = SourceParseLimits::from_request(&req).unwrap();
        let json = limits.to_json();
        assert_eq!(json.get("max_depth"), Some(&Value::Number(5.into())));
        assert_eq!(
            json.get("parser_profile"),
            Some(&Value::String("fast".to_owned()))
        );
    }
}
