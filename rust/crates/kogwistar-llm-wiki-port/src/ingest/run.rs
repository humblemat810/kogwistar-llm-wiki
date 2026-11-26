use std::time::Instant;

use serde::Serialize;
use serde_json::Value;

use crate::ingest::source_lifecycle::{maintenance_kind_for_operation_mode, operation_mode};
use crate::models::{IngestPipelineArtifacts, IngestPipelineRequest, JsonObject};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum IngestRunBranch {
    MaintenanceFirst,
    ParseFirst,
    Hybrid,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct IngestRunPlan {
    pub source_document_id: String,
    pub operation_mode: &'static str,
    pub maintenance_kind: &'static str,
    pub graph_status: &'static str,
    pub initialize_durable_parse_session: bool,
}

#[derive(Debug)]
pub enum IngestRunError {
    InvalidOperationMode,
}

impl std::fmt::Display for IngestRunError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::InvalidOperationMode => write!(
                formatter,
                "operation_mode must be one of: parse_first, maintenance_first, hybrid"
            ),
        }
    }
}

impl std::error::Error for IngestRunError {}

pub fn ingest_run_plan(
    request: &IngestPipelineRequest,
    source_document_id: &str,
) -> Result<IngestRunPlan, IngestRunError> {
    let operation_mode =
        operation_mode(request).map_err(|_| IngestRunError::InvalidOperationMode)?;
    let branch = match operation_mode {
        "maintenance_first" => IngestRunBranch::MaintenanceFirst,
        "hybrid" => IngestRunBranch::Hybrid,
        _ => IngestRunBranch::ParseFirst,
    };
    Ok(IngestRunPlan {
        source_document_id: source_document_id.to_owned(),
        operation_mode,
        maintenance_kind: maintenance_kind_for_operation_mode(operation_mode),
        graph_status: match branch {
            IngestRunBranch::MaintenanceFirst => "seeded",
            IngestRunBranch::Hybrid => "expanding",
            IngestRunBranch::ParseFirst => "stable",
        },
        initialize_durable_parse_session: match branch {
            IngestRunBranch::MaintenanceFirst | IngestRunBranch::Hybrid => true,
            IngestRunBranch::ParseFirst => request.parser_lane == "workflow_layered",
        },
    })
}

#[must_use]
pub fn maintenance_first_artifacts(
    plan: &IngestRunPlan,
    maintenance_job_id: &str,
) -> IngestPipelineArtifacts {
    IngestPipelineArtifacts {
        source_document_id: plan.source_document_id.clone(),
        maintenance_job_id: maintenance_job_id.to_owned(),
        candidate_link_id: String::new(),
        promotion_candidate_id: String::new(),
        promoted_entity_id: None,
        operation_mode: plan.operation_mode.to_owned(),
        graph_status: plan.graph_status.to_owned(),
    }
}

#[allow(clippy::too_many_arguments)]
#[must_use]
pub fn parsed_artifacts(
    plan: &IngestRunPlan,
    maintenance_job_id: &str,
    candidate_link_id: &str,
    promotion_candidate_id: &str,
    promoted_entity_id: Option<&str>,
) -> IngestPipelineArtifacts {
    IngestPipelineArtifacts {
        source_document_id: plan.source_document_id.clone(),
        maintenance_job_id: maintenance_job_id.to_owned(),
        candidate_link_id: candidate_link_id.to_owned(),
        promotion_candidate_id: promotion_candidate_id.to_owned(),
        promoted_entity_id: promoted_entity_id.map(str::to_owned),
        operation_mode: plan.operation_mode.to_owned(),
        graph_status: plan.graph_status.to_owned(),
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct IngestTraceEvent {
    pub timestamp_ms: u64,
    pub stage: String,
    #[serde(flatten)]
    pub fields: JsonObject,
}

#[must_use]
pub fn trace_event(stage: &str, timestamp_ms: u64, fields: JsonObject) -> IngestTraceEvent {
    IngestTraceEvent {
        timestamp_ms,
        stage: stage.to_owned(),
        fields,
    }
}

#[must_use]
pub fn run_start_event(
    request: &IngestPipelineRequest,
    plan: &IngestRunPlan,
    timestamp_ms: u64,
) -> IngestTraceEvent {
    trace_event(
        "ingest_run_start",
        timestamp_ms,
        JsonObject::from([
            (
                "workspace_id".to_owned(),
                Value::from(request.workspace_id.clone()),
            ),
            (
                "source_document_id".to_owned(),
                Value::from(plan.source_document_id.clone()),
            ),
            (
                "source_uri".to_owned(),
                Value::from(request.source_uri.clone()),
            ),
            (
                "parser_lane".to_owned(),
                Value::from(request.parser_lane.clone()),
            ),
            (
                "parser_mode".to_owned(),
                Value::from(request.parser_mode.clone()),
            ),
            (
                "operation_mode".to_owned(),
                Value::from(plan.operation_mode),
            ),
        ]),
    )
}

#[allow(clippy::too_many_arguments)]
#[must_use]
pub fn parse_complete_event(
    request: &IngestPipelineRequest,
    plan: &IngestRunPlan,
    timestamp_ms: u64,
    parse_runtime_ms: u64,
    promoted_entity_id: Option<&str>,
) -> IngestTraceEvent {
    trace_event(
        "ingest_run_complete",
        timestamp_ms,
        JsonObject::from([
            (
                "workspace_id".to_owned(),
                Value::from(request.workspace_id.clone()),
            ),
            (
                "source_document_id".to_owned(),
                Value::from(plan.source_document_id.clone()),
            ),
            (
                "operation_mode".to_owned(),
                Value::from(plan.operation_mode),
            ),
            ("graph_status".to_owned(), Value::from(plan.graph_status)),
            ("parse_runtime_ms".to_owned(), Value::from(parse_runtime_ms)),
            (
                "promoted_entity_id".to_owned(),
                promoted_entity_id.map(Value::from).unwrap_or(Value::Null),
            ),
        ]),
    )
}

#[must_use]
pub fn parse_runtime_ms(started_at: Instant) -> u64 {
    started_at.elapsed().as_millis() as u64
}
