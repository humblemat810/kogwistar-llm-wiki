use serde::Serialize;
use serde_json::Value;

use crate::ids::stable_id;
use crate::models::JsonObject;

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct ObservationContinuationDecision {
    pub scheduled: bool,
    pub continuation_id: Option<String>,
    pub next_kind: Option<String>,
    pub next_payload: Option<JsonObject>,
}

fn int_value(value: Option<&Value>, default: i64) -> i64 {
    match value {
        Some(Value::Number(number)) => number.as_i64().unwrap_or(default),
        Some(Value::String(value)) => value.parse().unwrap_or(default),
        Some(Value::Bool(true)) => 1,
        Some(Value::Bool(false)) => 0,
        _ => default,
    }
}

#[must_use]
pub fn redacted_frame_payload(frame: &JsonObject) -> JsonObject {
    let mut payload = frame.clone();
    if let Some(Value::Array(records)) = payload.get_mut("source_context") {
        for record in records {
            if let Some(object) = record.as_object_mut() {
                object.remove("source_excerpt");
            }
        }
    }
    payload
}

fn next_kind_for_action(action: &str) -> Option<&'static str> {
    match action {
        "review_parent" => Some("review_maintenance_subject"),
        "expand_children" => Some("document_expand_parse_children"),
        "retry_same_strategy" => Some("document_reparse_region"),
        "switch_to_excerpt" => Some("document_reparse_region"),
        "switch_to_boundary" => Some("document_reparse_region"),
        "reparse_region" => Some("document_reparse_region"),
        "propose_relation_patch" => Some("document_propose_crosslinks"),
        "validate_crosslinks" => Some("document_validate_crosslinks"),
        _ => None,
    }
}

#[allow(clippy::too_many_lines)]
#[must_use]
pub fn schedule_observation_continuation(
    payload: &JsonObject,
    workspace_id: &str,
    request_node_id: &str,
    action: &str,
    continuation_allowed: bool,
) -> ObservationContinuationDecision {
    let next_kind = if continuation_allowed {
        next_kind_for_action(action)
    } else {
        None
    };
    let Some(next_kind) = next_kind else {
        return ObservationContinuationDecision {
            scheduled: false,
            continuation_id: None,
            next_kind: None,
            next_payload: None,
        };
    };
    let current_round = int_value(payload.get("maintenance_round"), 0);
    let max_rounds = int_value(payload.get("maintenance_max_rounds"), 0);
    if max_rounds > 0 && current_round + 1 >= max_rounds {
        return ObservationContinuationDecision {
            scheduled: false,
            continuation_id: None,
            next_kind: None,
            next_payload: None,
        };
    }
    if let Some(last_round) = payload.get("observation_continuation_round") {
        if int_value(Some(last_round), -1) == current_round {
            return ObservationContinuationDecision {
                scheduled: false,
                continuation_id: None,
                next_kind: None,
                next_payload: None,
            };
        }
    }
    let max_steps = int_value(
        payload
            .get("budgets")
            .and_then(Value::as_object)
            .and_then(|values| values.get("max_steps")),
        0,
    );
    let used_steps = int_value(
        payload
            .get("maintenance_budget_state")
            .and_then(Value::as_object)
            .and_then(|values| values.get("step_used")),
        0,
    );
    if max_steps > 0 && used_steps + 1 >= max_steps {
        return ObservationContinuationDecision {
            scheduled: false,
            continuation_id: None,
            next_kind: None,
            next_payload: None,
        };
    }
    if next_kind == "review_maintenance_subject"
        && payload
            .get("parent_subject_id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .trim()
            .is_empty()
        && payload
            .get("parent_member_id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .trim()
            .is_empty()
    {
        return ObservationContinuationDecision {
            scheduled: false,
            continuation_id: None,
            next_kind: None,
            next_payload: None,
        };
    }
    if next_kind == "document_expand_parse_children"
        && payload
            .get("parse_session_id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .trim()
            .is_empty()
    {
        return ObservationContinuationDecision {
            scheduled: false,
            continuation_id: None,
            next_kind: None,
            next_payload: None,
        };
    }
    let continuation_id = stable_id(
        "kogwistar_llm_wiki.observation_continuation",
        &[
            workspace_id,
            request_node_id,
            action,
            (current_round + 1).to_string().as_str(),
        ],
    )
    .to_string();
    let mut next_payload = payload.clone();
    if next_kind == "review_maintenance_subject" {
        let parent_subject_id = payload
            .get("parent_subject_id")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .trim();
        if parent_subject_id.is_empty() {
            next_payload.remove("subject_id");
            next_payload.insert(
                "parse_generation_member_id".to_owned(),
                Value::from(
                    payload
                        .get("parent_member_id")
                        .and_then(Value::as_str)
                        .unwrap_or_default(),
                ),
            );
        } else {
            next_payload.insert("subject_id".to_owned(), Value::from(parent_subject_id));
        }
    }
    next_payload.insert("maintenance_kind".to_owned(), Value::from(next_kind));
    next_payload.insert(
        "maintenance_origin".to_owned(),
        Value::from("observation_continuation"),
    );
    next_payload.insert(
        "maintenance_round".to_owned(),
        Value::from(current_round + 1),
    );
    next_payload.insert(
        "observation_continuation_scheduled".to_owned(),
        Value::from(true),
    );
    next_payload.insert(
        "observation_continuation_round".to_owned(),
        Value::from(current_round),
    );
    next_payload.insert(
        "observation_continuation_id".to_owned(),
        Value::from(continuation_id.as_str()),
    );
    ObservationContinuationDecision {
        scheduled: true,
        continuation_id: Some(continuation_id),
        next_kind: Some(next_kind.to_owned()),
        next_payload: Some(next_payload),
    }
}
