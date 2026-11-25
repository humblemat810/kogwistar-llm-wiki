use std::collections::BTreeMap;
use std::path::PathBuf;

use serde::{Deserialize, Serialize};
use serde_json::Value;

pub type JsonObject = BTreeMap<String, Value>;

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct IngestPipelineArtifacts {
    pub source_document_id: String,
    pub maintenance_job_id: String,
    pub candidate_link_id: String,
    pub promotion_candidate_id: String,
    pub promoted_entity_id: Option<String>,
    #[serde(default = "default_parse_first")]
    pub operation_mode: String,
    #[serde(default = "default_expanding")]
    pub graph_status: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MaintenanceJobRequest {
    pub job_type: String,
    pub workspace_id: String,
    pub trigger_type: String,
    pub candidate_ids: Vec<String>,
    #[serde(default = "default_system")]
    pub requested_by: String,
    #[serde(default = "default_priority")]
    pub priority: i64,
    #[serde(default = "default_policy_version")]
    pub policy_version: String,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct MaintenanceJobResult {
    pub job_id: String,
    pub job_type: String,
    pub outputs: JsonObject,
    pub review_required: bool,
    pub emitted_event_ids: Vec<String>,
    #[serde(default = "default_completed")]
    pub status: String,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct ObsidianBuildResult {
    pub vault_root: PathBuf,
    pub notes: usize,
    pub canvases: usize,
    pub dangling_links: usize,
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProvenancePolicy {
    Required,
    Optional,
    Disabled,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct IngestPipelineRequest {
    pub workspace_id: String,
    pub source_uri: String,
    pub title: String,
    pub raw_text: String,
    #[serde(default = "default_text")]
    pub source_format: String,
    #[serde(default = "default_parse_first")]
    pub operation_mode: String,
    #[serde(default = "default_heuristic")]
    pub parser_mode: String,
    #[serde(default = "default_page_index")]
    pub parser_lane: String,
    #[serde(default = "default_pending")]
    pub promotion_mode: String,
    #[serde(default = "default_auto_accept_threshold")]
    pub auto_accept_threshold: f64,
    #[serde(default)]
    pub llm_provider: Option<String>,
    #[serde(default)]
    pub llm_model: Option<String>,
    #[serde(default = "default_provenance_policy")]
    pub provenance_policy: ProvenancePolicy,
    #[serde(default)]
    pub provenance: Option<JsonObject>,
    #[serde(default)]
    pub parse_limits: JsonObject,
}

#[derive(Clone, Debug, Eq, PartialEq, Serialize, Deserialize)]
pub struct MentionSpan {
    #[serde(default)]
    pub doc_id: String,
    #[serde(default = "default_page_number")]
    pub page_number: usize,
    #[serde(default)]
    pub start_char: usize,
    #[serde(default)]
    pub end_char: usize,
    #[serde(default)]
    pub excerpt: String,
    #[serde(default)]
    pub document_page_url: String,
    #[serde(default)]
    pub context_before: String,
    #[serde(default)]
    pub context_after: String,
}

impl Default for MentionSpan {
    fn default() -> Self {
        Self {
            doc_id: String::new(),
            page_number: 1,
            start_char: 0,
            end_char: 0,
            excerpt: String::new(),
            document_page_url: String::new(),
            context_before: String::new(),
            context_after: String::new(),
        }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct SemanticRelationship {
    #[serde(default)]
    pub source_id: String,
    #[serde(default)]
    pub target_id: String,
    #[serde(default = "default_related")]
    pub relation_type: String,
    #[serde(default)]
    pub properties: JsonObject,
}

impl Default for SemanticRelationship {
    fn default() -> Self {
        Self {
            source_id: String::new(),
            target_id: String::new(),
            relation_type: "related".to_owned(),
            properties: JsonObject::new(),
        }
    }
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ProjectionEntity {
    pub kg_id: String,
    pub title: String,
    #[serde(default = "default_note")]
    pub entity_type: String,
    #[serde(default)]
    pub summary: String,
    #[serde(default)]
    pub metadata: JsonObject,
    #[serde(default)]
    pub source_ids: Vec<String>,
    #[serde(default)]
    pub target_ids: Vec<String>,
    #[serde(default)]
    pub relation: Option<String>,
    #[serde(default)]
    pub relationships: Vec<SemanticRelationship>,
    #[serde(default)]
    pub mentions: Vec<MentionSpan>,
    #[serde(default)]
    pub body: String,
    #[serde(default)]
    pub version: Option<usize>,
    #[serde(default)]
    pub event_seq: Option<u64>,
}

#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct ProjectionSnapshot {
    pub entities: Vec<ProjectionEntity>,
}

fn default_parse_first() -> String {
    "parse_first".to_owned()
}

fn default_expanding() -> String {
    "expanding".to_owned()
}

fn default_system() -> String {
    "system".to_owned()
}

fn default_priority() -> i64 {
    10
}

fn default_policy_version() -> String {
    "1.0".to_owned()
}

fn default_completed() -> String {
    "completed".to_owned()
}

fn default_text() -> String {
    "text".to_owned()
}

fn default_heuristic() -> String {
    "heuristic".to_owned()
}

fn default_page_index() -> String {
    "page_index".to_owned()
}

fn default_pending() -> String {
    "pending".to_owned()
}

fn default_auto_accept_threshold() -> f64 {
    0.95
}

fn default_provenance_policy() -> ProvenancePolicy {
    ProvenancePolicy::Optional
}

fn default_page_number() -> usize {
    1
}

fn default_related() -> String {
    "related".to_owned()
}

fn default_note() -> String {
    "note".to_owned()
}
