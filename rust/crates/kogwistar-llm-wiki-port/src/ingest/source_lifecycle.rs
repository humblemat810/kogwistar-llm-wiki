use std::collections::BTreeMap;
use std::fmt;

use serde_json::{Number, Value};
use sha2::{Digest, Sha256};

use crate::ids::stable_id;
use crate::models::{IngestPipelineRequest, JsonObject};

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SourceRevision {
    pub source_document_id: String,
    pub revision_id: String,
    pub source_digest: String,
    pub revision_document_id: String,
}

#[derive(Debug)]
pub enum SourceLifecycleError {
    InvalidOperationMode(String),
    UnsupportedParseLimits(String),
    InvalidPositiveInteger(&'static str),
    InvalidWallTimeSeconds,
    InvalidParserProfile,
}

impl fmt::Display for SourceLifecycleError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidOperationMode(_) => write!(
                formatter,
                "operation_mode must be one of: parse_first, maintenance_first, hybrid"
            ),
            Self::UnsupportedParseLimits(values) => {
                write!(formatter, "unsupported parse_limits: {values}")
            }
            Self::InvalidPositiveInteger(name) => {
                write!(formatter, "parse_limits.{name} must be a positive integer")
            }
            Self::InvalidWallTimeSeconds => write!(
                formatter,
                "parse_limits.wall_time_seconds must be positive and at most 3600"
            ),
            Self::InvalidParserProfile => write!(
                formatter,
                "parse_limits.parser_profile must be a non-empty string of at most 256 characters"
            ),
        }
    }
}

impl std::error::Error for SourceLifecycleError {}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct DurableParseLimits {
    pub max_depth: u64,
    pub max_frontier_items: u64,
    pub max_parser_calls: u64,
    pub max_region_chars: u64,
    pub token_budget: Option<u64>,
    pub wall_time_seconds: Option<f64>,
    pub parser_profile: Option<String>,
}

impl DurableParseLimits {
    pub(crate) fn to_json(&self, include_profile: bool) -> Value {
        let mut limits = BTreeMap::from([
            (
                "max_depth".to_owned(),
                Value::Number(Number::from(self.max_depth)),
            ),
            (
                "max_frontier_items".to_owned(),
                Value::Number(Number::from(self.max_frontier_items)),
            ),
            (
                "max_parser_calls".to_owned(),
                Value::Number(Number::from(self.max_parser_calls)),
            ),
            (
                "max_region_chars".to_owned(),
                Value::Number(Number::from(self.max_region_chars)),
            ),
        ]);
        if let Some(token_budget) = self.token_budget {
            limits.insert(
                "token_budget".to_owned(),
                Value::Number(Number::from(token_budget)),
            );
        }
        if let Some(wall_time_seconds) = self.wall_time_seconds {
            limits.insert(
                "wall_time_seconds".to_owned(),
                Value::Number(Number::from_f64(wall_time_seconds).expect("finite wall time")),
            );
        }
        if include_profile {
            if let Some(parser_profile) = &self.parser_profile {
                limits.insert(
                    "parser_profile".to_owned(),
                    Value::String(parser_profile.clone()),
                );
            }
        }
        Value::Object(limits.into_iter().collect())
    }
}

#[must_use]
pub fn source_digest(raw_text: &str) -> String {
    let mut digest = Sha256::new();
    digest.update(raw_text.as_bytes());
    hex_upper(&digest.finalize())
}

pub fn build_source_revision(
    workspace_id: &str,
    source_document_id: &str,
    raw_text: &str,
) -> SourceRevision {
    let digest = source_digest(raw_text);
    let revision_id = stable_id(
        "kogwistar_llm_wiki.source_revision",
        &[workspace_id, source_document_id, digest.as_str(), "content"],
    );
    let revision_document_id = source_revision_document_id(
        workspace_id,
        source_document_id,
        revision_id.to_string().as_str(),
    );
    SourceRevision {
        source_document_id: source_document_id.to_owned(),
        revision_id: revision_id.to_string(),
        source_digest: digest,
        revision_document_id: revision_document_id.to_string(),
    }
}

#[must_use]
pub fn source_revision_document_id(
    workspace_id: &str,
    source_document_id: &str,
    revision_id: &str,
) -> uuid::Uuid {
    stable_id(
        "kogwistar_llm_wiki.source_revision_document",
        &[workspace_id, source_document_id, revision_id],
    )
}

#[must_use]
pub fn readiness_id(source_document_id: &str, revision_id: &str, stage: &str) -> uuid::Uuid {
    stable_id(
        "kogwistar_llm_wiki.source_readiness",
        &[source_document_id, revision_id, stage],
    )
}

#[must_use]
pub fn source_document_id(request: &IngestPipelineRequest) -> uuid::Uuid {
    stable_id(
        "kogwistar_llm_wiki.source_document",
        &[request.workspace_id.as_str(), request.source_uri.as_str()],
    )
}

pub fn operation_mode(
    request: &IngestPipelineRequest,
) -> Result<&'static str, SourceLifecycleError> {
    match request.operation_mode.trim().to_ascii_lowercase().as_str() {
        "parse_first" => Ok("parse_first"),
        "maintenance_first" => Ok("maintenance_first"),
        "hybrid" => Ok("hybrid"),
        _ => Err(SourceLifecycleError::InvalidOperationMode(
            request.operation_mode.clone(),
        )),
    }
}

#[must_use]
pub fn maintenance_kind_for_operation_mode(operation_mode: &str) -> &'static str {
    match operation_mode {
        "maintenance_first" => "document_seed_graph",
        "hybrid" => "document_expand_parse_children",
        _ => "distill",
    }
}

#[must_use]
pub fn required_stage_for_maintenance(maintenance_kind: &str) -> &'static str {
    match maintenance_kind {
        "document_seed_graph"
        | "document_parse_graph"
        | "document_expand_parse_children"
        | "document_reparse_region" => "source_map_seeded",
        _ => "parsed_graph_persisted",
    }
}

pub fn durable_parse_limits(
    request: &IngestPipelineRequest,
) -> Result<DurableParseLimits, SourceLifecycleError> {
    let mut limits = DurableParseLimits {
        max_depth: 10,
        max_frontier_items: 1,
        max_parser_calls: 1000,
        max_region_chars: 16_384,
        token_budget: None,
        wall_time_seconds: None,
        parser_profile: None,
    };
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
        return Err(SourceLifecycleError::UnsupportedParseLimits(
            sorted.join(", "),
        ));
    }

    for (name, default) in [
        ("max_depth", limits.max_depth),
        ("max_frontier_items", limits.max_frontier_items),
        ("max_parser_calls", limits.max_parser_calls),
        ("max_region_chars", limits.max_region_chars),
    ] {
        let parsed = match request.parse_limits.get(name) {
            None => default,
            Some(value) => {
                let Some(number) = value.as_u64() else {
                    return Err(SourceLifecycleError::InvalidPositiveInteger(name));
                };
                number
            }
        };
        if parsed < 1 {
            return Err(SourceLifecycleError::InvalidPositiveInteger(name));
        }
        match name {
            "max_depth" => limits.max_depth = parsed,
            "max_frontier_items" => limits.max_frontier_items = parsed,
            "max_parser_calls" => limits.max_parser_calls = parsed,
            "max_region_chars" => limits.max_region_chars = parsed,
            _ => unreachable!("known positive integer key"),
        }
    }

    if let Some(token_budget) = request.parse_limits.get("token_budget") {
        if !token_budget.is_null() {
            let Some(value) = token_budget.as_u64() else {
                return Err(SourceLifecycleError::InvalidPositiveInteger("token_budget"));
            };
            if value < 1 {
                return Err(SourceLifecycleError::InvalidPositiveInteger("token_budget"));
            }
            limits.token_budget = Some(value);
        }
    }

    if let Some(wall_time) = request.parse_limits.get("wall_time_seconds") {
        if !wall_time.is_null() {
            let Some(value) = wall_time.as_f64() else {
                return Err(SourceLifecycleError::InvalidWallTimeSeconds);
            };
            if value <= 0.0 || value > 3_600.0 {
                return Err(SourceLifecycleError::InvalidWallTimeSeconds);
            }
            limits.wall_time_seconds = Some(value);
        }
    }

    if let Some(profile) = request.parse_limits.get("parser_profile") {
        if !profile.is_null() {
            let Some(value) = profile.as_str() else {
                return Err(SourceLifecycleError::InvalidParserProfile);
            };
            let trimmed = value.trim();
            if trimmed.is_empty() || trimmed.chars().count() > 256 {
                return Err(SourceLifecycleError::InvalidParserProfile);
            }
            limits.parser_profile = Some(trimmed.to_owned());
        }
    }

    Ok(limits)
}

pub fn durable_parse_profile(
    request: &IngestPipelineRequest,
) -> Result<String, SourceLifecycleError> {
    let limits = durable_parse_limits(request)?;
    let Some(requested_profile) = limits.parser_profile.clone() else {
        return Ok(request.parser_lane.clone());
    };

    let fingerprint_payload = serde_json::json!({
        "limits": limits.to_json(false),
        "parser_lane": request.parser_lane,
        "parser_mode": request.parser_mode,
        "llm_provider": request.llm_provider,
        "llm_model": request.llm_model,
    });
    let fingerprint = stable_id(
        "kogwistar_llm_wiki.parse_profile_limits",
        &[serde_json::to_string(&fingerprint_payload)
            .expect("finite fingerprint JSON")
            .as_str()],
    );
    Ok(format!("{requested_profile}@{fingerprint}"))
}

fn hex_upper(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

#[allow(dead_code)]
fn parse_limit_keys(metadata: &JsonObject) -> Vec<String> {
    metadata.keys().cloned().collect()
}
