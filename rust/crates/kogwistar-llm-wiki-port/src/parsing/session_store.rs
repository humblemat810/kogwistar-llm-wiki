//! Durable CAS storage for application-owned layered parse sessions.
//!
//! Mirrors Python contract src/kogwistar_llm_wiki/parsing/parse_session_store.py.
//! Persists session/frontier state independently from parser temp files.

use serde::{Deserialize, Serialize};
use serde_json::{Map, Value};
use std::collections::HashSet;

use crate::parsing::SourceRegion;

/// Scope id used to group full-source retries together and targeted retries by exact region.
#[must_use]
pub fn parse_session_scope_id(region: Option<&SourceRegion>) -> String {
    match region {
        None => "full".to_string(),
        Some(region) => format!("region:{}:{}", region.start_char, region.end_char),
    }
}

/// Another worker committed this session first (Python ParseSessionStoreConflict parity).
#[derive(Debug, PartialEq, Eq)]
pub struct ParseSessionStoreConflict;

/// Parse session store operation failure.
#[derive(Debug, PartialEq, Eq)]
pub enum ParseSessionStoreError {
    /// CAS lost a race, or expected version did not match current persisted version.
    Conflict,
    /// Session, frontier, or transition rejected by validation.
    Invalid(String),
}

impl std::fmt::Display for ParseSessionStoreError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Conflict => formatter.write_str("parse session changed before commit"),
            Self::Invalid(message) => write!(formatter, "invalid parse session: {message}"),
        }
    }
}

impl std::error::Error for ParseSessionStoreError {}

impl std::fmt::Display for ParseSessionStoreConflict {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("parse session changed before commit")
    }
}

impl std::error::Error for ParseSessionStoreConflict {}

/// Parse session lifecycle phase.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub enum ParseSessionPhase {
    #[default]
    Seeded,
    Expanding,
    Stable,
    ReviewRequired,
    Failed,
}

/// Parse frontier item status.
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub enum ParseFrontierStatus {
    #[default]
    Pending,
    Claimed,
    Complete,
    Failed,
}

/// Durable bounded parse frontier work item.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct ParseFrontierItem {
    pub frontier_id: String,
    pub session_id: String,
    pub generation_id: String,
    pub workspace_id: String,
    pub source_document_id: String,
    pub source_revision_id: String,
    pub revision_document_id: String,
    pub parent_member_id: Option<String>,
    pub region: SourceRegion,
    pub depth: u64,
    pub ordinal: u64,
    pub status: ParseFrontierStatus,
    pub attempt: u64,
    pub last_error: Option<String>,
}

/// Restartable parser session state owned by LLM-Wiki.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ParseSessionState {
    pub session_id: String,
    pub workspace_id: String,
    pub source_document_id: String,
    pub source_revision_id: String,
    pub source_digest: String,
    pub revision_document_id: String,
    pub generation_id: String,
    pub phase: ParseSessionPhase,
    pub frontier_ids: Vec<String>,
    pub consumed_frontier_ids: Vec<String>,
    pub max_depth: u64,
    pub max_frontier_items: u64,
    pub max_parser_calls: u64,
    pub max_region_chars: u64,
    pub parser_calls: u64,
    pub token_budget: Option<u64>,
    pub wall_time_seconds: Option<f64>,
    pub last_progress_at: Option<String>,
    pub failure_reason: Option<String>,
    pub parser_state: Value,
    pub pending_view: Option<Value>,
    pub dependents_enqueued_view_version: Option<u64>,
}

/// Row returned by a named projection store.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ProjectionRow {
    pub payload: Map<String, Value>,
    pub last_authoritative_seq: Option<i64>,
    pub last_materialized_seq: Option<i64>,
}

/// Metadata projection store abstraction mirroring Python NamedProjectionStore.
pub trait NamedProjectionStore: Send + Sync {
    fn get_named_projection(&self, namespace: &str, key: &str) -> Option<ProjectionRow>;
    #[allow(clippy::too_many_arguments)]
    fn compare_and_swap_named_projection(
        &self,
        namespace: &str,
        key: &str,
        payload: Value,
        expected_last_authoritative_seq: Option<i64>,
        expected_last_materialized_seq: Option<i64>,
        last_authoritative_seq: i64,
        last_materialized_seq: i64,
        projection_schema_version: u32,
        materialization_status: &str,
    ) -> bool;
}

/// Durable CAS storage for application-owned layered parse sessions.
pub struct ParseSessionStore<M: NamedProjectionStore> {
    metadata: M,
    namespace: String,
    workspace_id: String,
    schema_version: u32,
}

impl<M: NamedProjectionStore> ParseSessionStore<M> {
    pub fn new(metadata: M, workspace_id: impl Into<String>) -> Self {
        let workspace_id = workspace_id.into();
        let namespace = format!("ws:{workspace_id}:projection_state");
        Self {
            metadata,
            namespace,
            workspace_id,
            schema_version: 1,
        }
    }

    pub fn workspace_id(&self) -> &str {
        &self.workspace_id
    }

    pub fn key(&self, session_id: &str) -> String {
        format!("parse_session:{session_id}")
    }

    pub fn active_key(&self, source_document_id: &str, scope_id: &str) -> String {
        format!("active_parse_session:{source_document_id}:{scope_id}")
    }

    pub fn active_session_id(&self, source_document_id: &str, scope_id: &str) -> Option<String> {
        let row = self.metadata.get_named_projection(
            &self.namespace,
            &self.active_key(source_document_id, scope_id),
        )?;
        let payload = row.payload;
        let workspace_id = payload.get("workspace_id")?.as_str()?;
        if workspace_id != self.workspace_id {
            return None;
        }
        let document_id = payload.get("source_document_id")?.as_str()?;
        if document_id != source_document_id {
            return None;
        }
        let session_id = payload.get("active_session_id")?.as_str()?;
        if session_id.is_empty() {
            return None;
        }
        Some(session_id.to_string())
    }

    fn get(&self, session_id: &str) -> Option<(ParseSessionState, Vec<ParseFrontierItem>, u64)> {
        let row = self
            .metadata
            .get_named_projection(&self.namespace, &self.key(session_id))?;
        let payload = row.payload.get("session")?;
        let session = serde_json::from_value(payload.clone()).ok()?;
        let frontier = row
            .payload
            .get("frontier")
            .and_then(|value| value.as_array())
            .and_then(|array| {
                array
                    .iter()
                    .cloned()
                    .map(serde_json::from_value)
                    .collect::<Result<Vec<ParseFrontierItem>, _>>()
                    .ok()
            })?;
        let version = row.last_authoritative_seq? as u64;
        Some((session, frontier, version))
    }

    #[allow(dead_code)]
    fn sequence(value: Option<&Value>, default: i64) -> i64 {
        match value {
            Some(Value::Bool(flag)) => *flag as i64,
            Some(Value::Number(number)) => number.as_i64().unwrap_or(default),
            Some(Value::String(text)) => text.parse::<i64>().unwrap_or(default),
            _ => default,
        }
    }

    fn validate_frontier(
        session: &ParseSessionState,
        frontier: &[ParseFrontierItem],
    ) -> Result<(), String> {
        let frontier_ids: Vec<&str> = frontier
            .iter()
            .map(|item| item.frontier_id.as_str())
            .collect();
        let unique: HashSet<&str> = frontier_ids.iter().copied().collect();
        if frontier_ids.len() != unique.len() {
            return Err("parse frontier contains duplicate item IDs".into());
        }
        let persisted: Vec<&str> = session.frontier_ids.iter().map(|id| id.as_str()).collect();
        if frontier_ids != persisted {
            return Err("parse session frontier pointer does not match persisted frontier".into());
        }
        for item in frontier {
            if item.session_id != session.session_id
                || item.workspace_id != session.workspace_id
                || item.source_document_id != session.source_document_id
                || item.source_revision_id != session.source_revision_id
                || item.revision_document_id != session.revision_document_id
                || item.generation_id != session.generation_id
                || item.region.source_document_id != session.revision_document_id
                || item.depth > session.max_depth
            {
                return Err("parse frontier item does not belong to its session".into());
            }
        }
        Ok(())
    }

    fn validate_session_identity(session: &ParseSessionState) -> Result<(), String> {
        if session.session_id.is_empty()
            || session.source_document_id.is_empty()
            || session.source_revision_id.is_empty()
            || session.source_digest.is_empty()
            || session.revision_document_id.is_empty()
            || session.generation_id.is_empty()
        {
            return Err("parse session is missing a stable source identity".into());
        }
        Ok(())
    }

    fn validate_transition(
        current: &ParseSessionState,
        next: &ParseSessionState,
    ) -> Result<(), String> {
        let mut changed = Vec::new();
        if current.session_id != next.session_id {
            changed.push("session_id".to_string());
        }
        if current.workspace_id != next.workspace_id {
            changed.push("workspace_id".to_string());
        }
        if current.source_document_id != next.source_document_id {
            changed.push("source_document_id".to_string());
        }
        if current.source_revision_id != next.source_revision_id {
            changed.push("source_revision_id".to_string());
        }
        if current.source_digest != next.source_digest {
            changed.push("source_digest".to_string());
        }
        if current.revision_document_id != next.revision_document_id {
            changed.push("revision_document_id".to_string());
        }
        if current.generation_id != next.generation_id {
            changed.push("generation_id".to_string());
        }
        if current.parser_state != next.parser_state {
            changed.push("parser_state".to_string());
        }
        if current.max_depth != next.max_depth {
            changed.push("max_depth".to_string());
        }
        if current.max_frontier_items != next.max_frontier_items {
            changed.push("max_frontier_items".to_string());
        }
        if current.max_parser_calls != next.max_parser_calls {
            changed.push("max_parser_calls".to_string());
        }
        if current.max_region_chars != next.max_region_chars {
            changed.push("max_region_chars".to_string());
        }
        if current.token_budget != next.token_budget {
            changed.push("token_budget".to_string());
        }
        if current.wall_time_seconds != next.wall_time_seconds {
            changed.push("wall_time_seconds".to_string());
        }
        if !changed.is_empty() {
            return Err(format!(
                "parse session transition changed immutable identity fields: {}",
                changed.join(", ")
            ));
        }
        if next.parser_calls < current.parser_calls {
            return Err("parse session parser call count cannot decrease".into());
        }
        let current_consumed: HashSet<&str> = current
            .consumed_frontier_ids
            .iter()
            .map(|id| id.as_str())
            .collect();
        for id in &next.consumed_frontier_ids {
            if !current_consumed.contains(id.as_str()) {
                return Err("parse session consumed frontier history cannot be removed".into());
            }
        }
        Ok(())
    }

    pub fn save(
        &self,
        session: &ParseSessionState,
        frontier: &[ParseFrontierItem],
        expected_version: Option<u64>,
    ) -> Result<u64, ParseSessionStoreError> {
        if session.workspace_id != self.workspace_id {
            return Err(ParseSessionStoreError::Invalid(
                "parse session workspace does not match store workspace".into(),
            ));
        }
        Self::validate_session_identity(session).map_err(ParseSessionStoreError::Invalid)?;
        Self::validate_frontier(session, frontier).map_err(ParseSessionStoreError::Invalid)?;
        let key = self.key(&session.session_id);
        let (next_version, expected_authoritative, expected_materialized) = match expected_version {
            None => (1u64, None, None),
            Some(expected) => {
                let (current_session, _, current_version) = self
                    .get(&session.session_id)
                    .ok_or(ParseSessionStoreError::Conflict)?;
                if current_version != expected {
                    return Err(ParseSessionStoreError::Conflict);
                }
                Self::validate_transition(&current_session, session)
                    .map_err(ParseSessionStoreError::Invalid)?;
                let row = self.metadata.get_named_projection(&self.namespace, &key);
                let next_version = expected + 1;
                (
                    next_version,
                    row.as_ref().and_then(|r| r.last_authoritative_seq),
                    row.as_ref().and_then(|r| r.last_materialized_seq),
                )
            }
        };
        let payload = serde_json::json!({
            "session": session,
            "frontier": frontier,
        });
        let inserted = self.metadata.compare_and_swap_named_projection(
            &self.namespace,
            &key,
            payload,
            expected_authoritative,
            expected_materialized,
            next_version as i64,
            next_version as i64,
            self.schema_version,
            "ready",
        );
        if !inserted {
            return Err(ParseSessionStoreError::Conflict);
        }
        Ok(next_version)
    }

    pub fn activate(
        &self,
        session: &ParseSessionState,
        scope_id: &str,
    ) -> Result<(), ParseSessionStoreError> {
        if session.workspace_id != self.workspace_id {
            return Err(ParseSessionStoreError::Invalid(
                "parse session workspace does not match store workspace".into(),
            ));
        }
        if session.session_id.is_empty() || session.source_document_id.is_empty() {
            return Err(ParseSessionStoreError::Invalid(
                "active parse session requires stable source and session IDs".into(),
            ));
        }
        let stored = self.get(&session.session_id);
        if stored.is_none() {
            return Err(ParseSessionStoreError::Invalid(
                "cannot activate a parse session that is not persisted".into(),
            ));
        }
        let (stored_session, _, _) = stored.unwrap();
        if stored_session.source_document_id != session.source_document_id {
            return Err(ParseSessionStoreError::Invalid(
                "active parse session source does not match persisted session".into(),
            ));
        }
        if scope_id.is_empty() || scope_id.len() > 256 {
            return Err(ParseSessionStoreError::Invalid(
                "parse session scope must be a non-empty string of at most 256 characters".into(),
            ));
        }
        let key = self.active_key(&session.source_document_id, scope_id);
        for _ in 0..8 {
            if let Some(active) = self.active_session_id(&session.source_document_id, scope_id) {
                if active == session.session_id {
                    return Ok(());
                }
            }
            let row = self.metadata.get_named_projection(&self.namespace, &key);
            let (expected_authoritative, expected_materialized) = match &row {
                Some(row) => (row.last_authoritative_seq, row.last_materialized_seq),
                None => (None, None),
            };
            let next_version = expected_authoritative.unwrap_or(0) + 1;
            let payload = serde_json::json!({
                "active_session_id": session.session_id,
                "workspace_id": &self.workspace_id,
                "source_document_id": &session.source_document_id,
            });
            let inserted = self.metadata.compare_and_swap_named_projection(
                &self.namespace,
                &key,
                payload,
                expected_authoritative,
                expected_materialized,
                next_version,
                next_version,
                self.schema_version,
                "ready",
            );
            if inserted {
                return Ok(());
            }
        }
        Err(ParseSessionStoreError::Conflict)
    }
}
