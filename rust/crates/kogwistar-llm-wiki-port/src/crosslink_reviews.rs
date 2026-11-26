use serde::Serialize;
use serde_json::{json, Value};

use crate::crosslink_proposals::CrosslinkReviewDecision;
use crate::models::JsonObject;

#[derive(Clone, Debug, PartialEq)]
pub enum CrosslinkReviewError {
    Conflict,
    Rejected(String),
}

impl std::fmt::Display for CrosslinkReviewError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Conflict => write!(formatter, "cross-link review conflict"),
            Self::Rejected(message) => write!(formatter, "{message}"),
        }
    }
}

impl std::error::Error for CrosslinkReviewError {}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct CrosslinkReviewOutcome {
    pub artifact_id: String,
    pub status: &'static str,
    pub decision: Option<String>,
    pub error: Option<String>,
}

fn normalized_decision(decision: &CrosslinkReviewDecision) -> Result<(), CrosslinkReviewError> {
    if decision.artifact_id.is_empty() {
        return Err(CrosslinkReviewError::Rejected(
            "artifact_id is required".to_owned(),
        ));
    }
    if !matches!(decision.decision.as_str(), "approve" | "reject") {
        return Err(CrosslinkReviewError::Rejected(
            "decision must be approve or reject".to_owned(),
        ));
    }
    if decision.expected_version < 1 {
        return Err(CrosslinkReviewError::Rejected(
            "expected_version must be at least 1".to_owned(),
        ));
    }
    Ok(())
}

pub fn normalize_review_batch(
    decisions: &[CrosslinkReviewDecision],
) -> Result<Vec<CrosslinkReviewDecision>, CrosslinkReviewError> {
    if decisions.is_empty() || decisions.len() > 100 {
        return Err(CrosslinkReviewError::Rejected(
            "batch must contain between 1 and 100 independent decisions".to_owned(),
        ));
    }
    let mut normalized = Vec::new();
    for decision in decisions {
        normalized_decision(decision)?;
        normalized.push(decision.clone());
    }
    let unique = normalized
        .iter()
        .map(|decision| decision.artifact_id.clone())
        .collect::<std::collections::BTreeSet<_>>();
    if unique.len() != normalized.len() {
        return Err(CrosslinkReviewError::Rejected(
            "a batch may decide each group only once".to_owned(),
        ));
    }
    Ok(normalized)
}

pub fn review_event(
    workspace_id: &str,
    actor_id: &str,
    decision: &CrosslinkReviewDecision,
) -> Result<Value, CrosslinkReviewError> {
    normalized_decision(decision)?;
    Ok(json!({
        "workspace_id": workspace_id,
        "artifact_id": decision.artifact_id,
        "decision": decision.decision,
        "expected_version": decision.expected_version,
        "actor_id": actor_id
    }))
}

#[must_use]
pub fn review_decision_node_metadata(event: &JsonObject) -> Value {
    json!({
        "artifact_kind": "crosslink_group_decision",
        "workspace_id": event["workspace_id"],
        "artifact_id": event["artifact_id"],
        "decision": event["decision"],
        "expected_version": event["expected_version"],
        "actor_id": event["actor_id"]
    })
}

pub fn review_outcome(
    decision: &CrosslinkReviewDecision,
    existing: Option<&Value>,
) -> Result<CrosslinkReviewOutcome, CrosslinkReviewError> {
    normalized_decision(decision)?;
    if let Some(existing) = existing.and_then(Value::as_object) {
        if existing
            .get("decision")
            .and_then(Value::as_str)
            .is_some_and(|value| value != decision.decision)
        {
            return Err(CrosslinkReviewError::Conflict);
        }
        return Ok(CrosslinkReviewOutcome {
            artifact_id: decision.artifact_id.clone(),
            status: "already_decided",
            decision: Some(decision.decision.clone()),
            error: None,
        });
    }
    Ok(CrosslinkReviewOutcome {
        artifact_id: decision.artifact_id.clone(),
        status: "decided",
        decision: Some(decision.decision.clone()),
        error: None,
    })
}

#[must_use]
pub fn revalidation_payload(
    workspace_id: &str,
    metadata: &Value,
    group_id: &str,
    job_id: &str,
) -> Value {
    let mut payload = json!({
        "workspace_id": workspace_id,
        "request_node_id": job_id,
        "maintenance_kind": "document_validate_crosslinks",
        "mode": "background",
        "crosslink_candidate_group_id": group_id,
        "patch": metadata.get("patch_json").cloned().unwrap_or(Value::Null),
        "source_document_id": metadata.get("source_document_id").cloned().unwrap_or(Value::Null),
        "source_revision_id": metadata.get("source_revision_id").cloned().unwrap_or(Value::Null),
        "source_digest": metadata.get("source_digest").cloned().unwrap_or(Value::Null),
        "revision_document_id": metadata.get("revision_document_id").cloned().unwrap_or(Value::Null),
        "source_revision_fences": metadata.get("source_revision_fences").cloned().unwrap_or(Value::Null),
        "required_stage": "parsed_graph_persisted",
        "accepted_confidence": 1.0,
        "seed_node_ids": [],
        "maintenance_plan": ["document_validate_crosslinks"],
        "maintenance_phase_index": 0
    });
    for key in [
        "crosslink_parse_quality",
        "parse_quality_status",
        "source_region_status",
        "source_view_status",
    ] {
        if let Some(value) = metadata.get(key) {
            if !value.is_null() {
                payload[key] = value.clone();
            }
        }
    }
    payload
}
