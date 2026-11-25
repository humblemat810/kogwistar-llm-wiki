use serde::Serialize;
use serde_json::Value;

use crate::models::JsonObject;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ArtifactTaxonomy;

impl ArtifactTaxonomy {
    pub const MAINTENANCE_JOB_REQUEST: &'static str = "maintenance_job_request";
    pub const CANDIDATE_LINK: &'static str = "candidate_link";
    pub const PROMOTION_CANDIDATE: &'static str = "promotion_candidate";
    pub const PROMOTED_KNOWLEDGE: &'static str = "promoted_knowledge";
    pub const DERIVED_KNOWLEDGE: &'static str = "derived_knowledge";
    pub const EXECUTION_WISDOM: &'static str = "execution_wisdom";
    pub const WORKFLOW_STEP_EXEC: &'static str = "workflow_step_exec";
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct PromotionDecision {
    pub should_promote: bool,
    pub reason: String,
    #[serde(default, skip_serializing_if = "JsonObject::is_empty")]
    pub metadata: JsonObject,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct PromotionPolicy {
    pub default_accept_threshold: f64,
}

impl Default for PromotionPolicy {
    fn default() -> Self {
        Self {
            default_accept_threshold: 0.95,
        }
    }
}

impl PromotionPolicy {
    #[must_use]
    pub fn decide(
        &self,
        promotion_mode: &str,
        auto_accept_threshold: f64,
        metadata: Option<&JsonObject>,
    ) -> PromotionDecision {
        let promotion_approved = promotion_mode.trim().eq_ignore_ascii_case("sync");
        if !promotion_approved {
            return PromotionDecision {
                should_promote: false,
                reason: "promotion was not explicitly approved".to_owned(),
                metadata: JsonObject::new(),
            };
        }
        if auto_accept_threshold > self.default_accept_threshold {
            return PromotionDecision {
                should_promote: false,
                reason: "auto_accept_threshold is above the default accept threshold".to_owned(),
                metadata: JsonObject::new(),
            };
        }
        let _ = metadata;
        PromotionDecision {
            should_promote: true,
            reason: "explicit promotion approval accepted by default policy".to_owned(),
            metadata: [
                ("promotion_approved", Value::Bool(true)),
                ("auto_accept_threshold", Value::from(auto_accept_threshold)),
                (
                    "default_accept_threshold",
                    Value::from(self.default_accept_threshold),
                ),
            ]
            .into_iter()
            .map(|(key, value)| (key.to_owned(), value))
            .collect(),
        }
    }
}

const VISIBILITY_VALUES: [&str; 5] = ["internal", "review", "knowledge", "projection", "wisdom"];

#[must_use]
fn core_visibility(metadata: &JsonObject) -> String {
    let explicit = python_string(metadata.get("visibility"))
        .trim()
        .to_ascii_lowercase();
    if VISIBILITY_VALUES.contains(&explicit.as_str()) {
        return explicit;
    }
    if metadata.get("projection_visible").and_then(Value::as_bool) == Some(true) {
        return "projection".to_owned();
    }
    let stage = python_string(metadata.get("knowledge_stage"))
        .trim()
        .to_ascii_lowercase();
    if VISIBILITY_VALUES.contains(&stage.as_str()) {
        return stage;
    }
    if matches!(stage.as_str(), "promoted" | "durable" | "knowledge") {
        return "knowledge".to_owned();
    }
    if matches!(stage.as_str(), "derived" | "wisdom") {
        return "wisdom".to_owned();
    }
    if matches!(stage.as_str(), "candidate" | "review") {
        return "review".to_owned();
    }
    "internal".to_owned()
}

#[must_use]
pub fn visibility_for(metadata: &JsonObject) -> String {
    let artifact_kind = python_string(metadata.get("artifact_kind"))
        .trim()
        .to_owned();
    if artifact_kind == ArtifactTaxonomy::PROMOTED_KNOWLEDGE {
        return if metadata.get("projection_visible").and_then(Value::as_bool) == Some(true) {
            "projection".to_owned()
        } else {
            "knowledge".to_owned()
        };
    }
    if matches!(
        artifact_kind.as_str(),
        ArtifactTaxonomy::CANDIDATE_LINK | ArtifactTaxonomy::PROMOTION_CANDIDATE
    ) {
        return "review".to_owned();
    }
    if matches!(
        artifact_kind.as_str(),
        ArtifactTaxonomy::MAINTENANCE_JOB_REQUEST
            | ArtifactTaxonomy::DERIVED_KNOWLEDGE
            | ArtifactTaxonomy::EXECUTION_WISDOM
    ) {
        let explicit = python_string(metadata.get("visibility")).trim().to_owned();
        if !explicit.is_empty() {
            return core_visibility(metadata);
        }
        return if artifact_kind == ArtifactTaxonomy::MAINTENANCE_JOB_REQUEST {
            "internal".to_owned()
        } else {
            "wisdom".to_owned()
        };
    }
    core_visibility(metadata)
}

#[must_use]
pub fn is_projection_eligible(metadata: &JsonObject) -> bool {
    if metadata.get("projection_visible").and_then(Value::as_bool) == Some(true) {
        return true;
    }
    visibility_for(metadata) == "projection"
}

fn python_string(value: Option<&Value>) -> String {
    match value {
        None => String::new(),
        Some(Value::String(value)) => value.clone(),
        Some(Value::Null) => String::new(),
        Some(Value::Bool(true)) => "True".to_owned(),
        Some(Value::Bool(false)) => "False".to_owned(),
        Some(other) => other.to_string(),
    }
}
