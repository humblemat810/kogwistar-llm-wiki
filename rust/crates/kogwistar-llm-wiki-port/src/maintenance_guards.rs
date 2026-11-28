use serde::Serialize;
use sha2::{Digest, Sha256};

use crate::ids::stable_id;

pub const SEED_GRAPH_STAGES: [&str; 4] = [
    "document_seed_graph",
    "document_parse_graph",
    "document_expand_parse_children",
    "document_reparse_region",
];

#[derive(Clone, Debug, Default, Eq, PartialEq, Serialize)]
pub struct SourceRevision {
    pub source_document_id: String,
    pub revision_id: String,
    pub source_digest: String,
    pub revision_document_id: Option<String>,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize)]
pub struct MaintenanceGuardDecision {
    pub status: &'static str,
    pub reason: String,
    pub source_document_id: String,
    pub source_revision_id: String,
    pub source_digest: String,
    pub required_stage: String,
}

#[must_use]
pub fn source_digest(raw_text: &str) -> String {
    let digest = Sha256::digest((raw_text.to_owned()).as_bytes());
    digest.iter().map(|byte| format!("{byte:02x}")).collect()
}

#[must_use]
pub fn source_revision_document_id(
    workspace_id: &str,
    source_document_id: &str,
    revision_id: &str,
) -> String {
    stable_id(
        "kogwistar_llm_wiki.source_revision_document",
        &[workspace_id, source_document_id, revision_id],
    )
    .to_string()
}

#[must_use]
pub fn build_source_revision(
    workspace_id: &str,
    source_document_id: &str,
    raw_text: &str,
    attempt_id: Option<&str>,
) -> SourceRevision {
    let digest = source_digest(raw_text);
    let revision_id = stable_id(
        "kogwistar_llm_wiki.source_revision",
        &[
            workspace_id,
            source_document_id,
            digest.as_str(),
            attempt_id.unwrap_or("content"),
        ],
    )
    .to_string();
    SourceRevision {
        source_document_id: source_document_id.to_owned(),
        revision_document_id: Some(source_revision_document_id(
            workspace_id,
            source_document_id,
            revision_id.as_str(),
        )),
        revision_id,
        source_digest: digest,
    }
}

#[must_use]
pub fn required_stage_for_maintenance(maintenance_kind: &str) -> &'static str {
    if SEED_GRAPH_STAGES.contains(&maintenance_kind) {
        "source_map_seeded"
    } else {
        "parsed_graph_persisted"
    }
}

#[must_use]
pub fn readiness_id(source_document_id: &str, revision_id: &str, stage: &str) -> String {
    stable_id(
        "kogwistar_llm_wiki.source_readiness",
        &[source_document_id, revision_id, stage],
    )
    .to_string()
}

#[allow(clippy::too_many_arguments)]
#[must_use]
pub fn evaluate_maintenance_guard(
    source_revision: Option<&SourceRevision>,
    requested_revision_id: &str,
    requested_digest: &str,
    requested_revision_document_id: Option<&str>,
    required_stage: &str,
    ready_revision_ids: &[String],
) -> MaintenanceGuardDecision {
    let Some(revision) = source_revision else {
        return MaintenanceGuardDecision {
            status: "blocked",
            reason: "source_revision_missing".to_owned(),
            source_document_id: String::new(),
            source_revision_id: requested_revision_id.to_owned(),
            source_digest: requested_digest.to_owned(),
            required_stage: required_stage.to_owned(),
        };
    };
    let document_mismatch = requested_revision_document_id
        .map(|requested| revision.revision_document_id.as_deref() != Some(requested))
        .unwrap_or_default();
    if revision.revision_id != requested_revision_id
        || revision.source_digest != requested_digest
        || document_mismatch
    {
        let reason = if document_mismatch {
            "source_revision_document_mismatch"
        } else {
            "source_revision_mismatch"
        };
        return MaintenanceGuardDecision {
            status: "stale",
            reason: reason.to_owned(),
            source_document_id: revision.source_document_id.clone(),
            source_revision_id: requested_revision_id.to_owned(),
            source_digest: requested_digest.to_owned(),
            required_stage: required_stage.to_owned(),
        };
    }
    if !ready_revision_ids
        .iter()
        .any(|id| id == requested_revision_id)
    {
        return MaintenanceGuardDecision {
            status: "blocked",
            reason: format!("required_stage_missing:{required_stage}"),
            source_document_id: revision.source_document_id.clone(),
            source_revision_id: requested_revision_id.to_owned(),
            source_digest: requested_digest.to_owned(),
            required_stage: required_stage.to_owned(),
        };
    }
    MaintenanceGuardDecision {
        status: "ready",
        reason: "source_revision_and_readiness_verified".to_owned(),
        source_document_id: revision.source_document_id.clone(),
        source_revision_id: requested_revision_id.to_owned(),
        source_digest: requested_digest.to_owned(),
        required_stage: required_stage.to_owned(),
    }
}

#[must_use]
pub fn evaluate_parse_session_guard(
    decision: &MaintenanceGuardDecision,
    requested_session_id: &str,
    active_session_id: Option<&str>,
) -> MaintenanceGuardDecision {
    if decision.status != "ready" {
        return decision.clone();
    }
    let Some(active) = active_session_id else {
        return decision.clone();
    };
    if requested_session_id == active {
        return decision.clone();
    }
    MaintenanceGuardDecision {
        status: "stale",
        reason: "parse_session_superseded".to_owned(),
        source_document_id: decision.source_document_id.clone(),
        source_revision_id: decision.source_revision_id.clone(),
        source_digest: decision.source_digest.clone(),
        required_stage: decision.required_stage.clone(),
    }
}
