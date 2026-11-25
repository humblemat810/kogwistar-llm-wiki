use crate::ingest::source_lifecycle::operation_mode;
use crate::maintenance::{bound_maintenance_context, DEFAULT_DOCUMENT_MAINTENANCE_PLAN};
use crate::models::{IngestPipelineRequest, JsonObject};
use crate::parsing::{parse_session_id, ParseTarget};
use serde_json::Value;

pub struct MaintenanceJobEnqueue {
    pub request: IngestPipelineRequest,
    pub request_node_id: String,
    pub source_document_id: String,
    pub maintenance_kind: String,
    pub topic: Option<String>,
    pub seed_node_ids: Option<Vec<String>>,
    pub maintenance_context: Option<JsonObject>,
    pub max_rounds: Option<i64>,
    pub lane_message_id: Option<String>,
    pub source_revision_id: String,
    pub source_digest: String,
    pub revision_document_id: String,
    pub required_stage: String,
    pub objective: Option<String>,
    pub budgets: Option<JsonObject>,
    pub parse_target: Option<ParseTarget>,
    pub parse_session_id_override: Option<String>,
    pub authority_claims: Option<JsonObject>,
}

pub fn maintenance_job_payload(enqueue: &MaintenanceJobEnqueue) -> Result<JsonObject, String> {
    let request = &enqueue.request;
    let session_id = enqueue
        .parse_session_id_override
        .clone()
        .unwrap_or_else(|| {
            parse_session_id(
                request.workspace_id.as_str(),
                enqueue.source_document_id.as_str(),
                enqueue.source_revision_id.as_str(),
                request.parser_lane.as_str(),
            )
            .to_string()
        });
    let topic = enqueue
        .topic
        .as_deref()
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned);
    let seed_node_ids = enqueue
        .seed_node_ids
        .clone()
        .unwrap_or_else(|| vec![enqueue.source_document_id.clone()])
        .into_iter()
        .filter(|seed_id| !seed_id.trim().is_empty())
        .collect::<Vec<_>>();
    let max_rounds = enqueue.max_rounds.unwrap_or_default().max(0);

    let mut payload: JsonObject = [
        ("workspace_id", Value::from(request.workspace_id.clone())),
        (
            "request_node_id",
            Value::from(enqueue.request_node_id.clone()),
        ),
        (
            "source_document_id",
            Value::from(enqueue.source_document_id.clone()),
        ),
        (
            "maintenance_kind",
            Value::from(enqueue.maintenance_kind.clone()),
        ),
        (
            "topic",
            topic.clone().map(Value::String).unwrap_or(Value::Null),
        ),
        ("mode", Value::from("request")),
        (
            "selection_strategy",
            Value::from("connected_semantic_evidence_history"),
        ),
        (
            "seed_node_ids",
            Value::Array(seed_node_ids.into_iter().map(Value::String).collect()),
        ),
        (
            "maintenance_context",
            json_object(bound_maintenance_context(
                enqueue.maintenance_context.as_ref(),
            )),
        ),
        ("maintenance_round", Value::from(0)),
        ("maintenance_max_rounds", Value::from(max_rounds)),
        (
            "lane_message_id",
            enqueue
                .lane_message_id
                .clone()
                .map(Value::String)
                .unwrap_or(Value::Null),
        ),
        (
            "source_revision_id",
            Value::from(enqueue.source_revision_id.clone()),
        ),
        ("source_digest", Value::from(enqueue.source_digest.clone())),
        (
            "revision_document_id",
            Value::from(if enqueue.revision_document_id.is_empty() {
                enqueue.source_document_id.clone()
            } else {
                enqueue.revision_document_id.clone()
            }),
        ),
        ("parse_session_id", Value::from(session_id)),
        (
            "parse_session_scope",
            Value::from(parse_session_scope_id(enqueue.parse_target.as_ref())),
        ),
        (
            "required_stage",
            Value::from(enqueue.required_stage.clone()),
        ),
        (
            "objective",
            enqueue
                .objective
                .clone()
                .map(Value::String)
                .unwrap_or(Value::Null),
        ),
        (
            "budgets",
            json_object(enqueue.budgets.clone().unwrap_or_default()),
        ),
        (
            "parse_target",
            enqueue
                .parse_target
                .as_ref()
                .map(serde_json::to_value)
                .transpose()
                .map_err(|error| format!("parse target serialization failed: {error}"))?
                .unwrap_or(Value::Null),
        ),
        (
            "durable_layered_parse",
            Value::from(
                request.parser_lane == "workflow_layered" || enqueue.parse_target.is_some(),
            ),
        ),
        (
            "authority_claims",
            enqueue
                .authority_claims
                .clone()
                .map(|value| Value::Object(value.into_iter().collect()))
                .unwrap_or(Value::Null),
        ),
        (
            "authority_required",
            Value::from(enqueue.authority_claims.is_some()),
        ),
    ]
    .into_iter()
    .map(|(key, value)| (key.to_owned(), value))
    .collect();

    if operation_mode(request).ok() == Some("maintenance_first")
        && enqueue.maintenance_kind == "document_seed_graph"
    {
        payload.insert(
            "maintenance_plan".to_owned(),
            Value::Array(
                DEFAULT_DOCUMENT_MAINTENANCE_PLAN
                    .iter()
                    .map(|kind| Value::from(*kind))
                    .collect(),
            ),
        );
        payload.insert("maintenance_phase_index".to_owned(), Value::from(0));
        payload.insert("maintenance_round".to_owned(), Value::from(0));
        payload.insert("maintenance_max_rounds".to_owned(), Value::from(max_rounds));
    }
    Ok(payload)
}

#[must_use]
fn parse_session_scope_id(parse_target: Option<&ParseTarget>) -> String {
    parse_target
        .map(|target| {
            format!(
                "region:{}:{}",
                target.region.start_char, target.region.end_char
            )
        })
        .unwrap_or_else(|| "full".to_owned())
}

#[must_use]
fn json_object(value: JsonObject) -> Value {
    Value::Object(value.into_iter().collect())
}
