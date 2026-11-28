use crate::ingest::source_lifecycle::SourceRevision;
use crate::models::{IngestPipelineRequest, JsonObject};
use crate::policies::{requires_provenance, visibility_for};
use serde::Serialize;
use serde_json::Value;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
pub enum VerificationMethod {
    Llm,
    Levenshtein,
    Regex,
    Heuristic,
    Human,
    Ensemble,
    System,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct MentionVerification {
    pub method: VerificationMethod,
    pub is_verified: bool,
    pub score: Option<f64>,
    pub notes: String,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct IngestSpan {
    pub collection_page_url: String,
    pub document_page_url: String,
    pub doc_id: String,
    pub insertion_method: String,
    pub page_number: usize,
    pub start_char: usize,
    pub end_char: usize,
    pub excerpt: String,
    pub context_before: String,
    pub context_after: String,
    pub chunk_id: Option<String>,
    pub source_cluster_id: Option<String>,
    pub verification: MentionVerification,
}

#[must_use]
pub fn leading_span(
    source_document_id: &str,
    raw_text: &str,
    insertion_method: &str,
) -> IngestSpan {
    let mut chars = raw_text.chars();
    let excerpt = chars
        .next()
        .map_or_else(|| " ".to_owned(), |value| value.to_string());
    let remaining: String = chars.collect();
    let context_after = remaining.chars().take(80).collect();

    IngestSpan {
        collection_page_url: format!("document_collection/{source_document_id}"),
        document_page_url: format!("document/{source_document_id}"),
        doc_id: source_document_id.to_owned(),
        insertion_method: insertion_method.to_owned(),
        page_number: 1,
        start_char: 0,
        end_char: 1,
        excerpt,
        context_before: String::new(),
        context_after,
        chunk_id: None,
        source_cluster_id: None,
        verification: MentionVerification {
            method: VerificationMethod::Heuristic,
            is_verified: false,
            score: None,
            notes: "no explicit verification from LLM".to_owned(),
        },
    }
}

pub fn parse_document_id_for_request(
    revision: &SourceRevision,
    source_document_id: &str,
) -> String {
    if revision.revision_document_id.is_empty() {
        source_document_id.to_owned()
    } else {
        revision.revision_document_id.clone()
    }
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct ArtifactNode {
    pub id: Option<String>,
    pub label: String,
    #[serde(rename = "type")]
    pub kind: String,
    pub summary: String,
    pub doc_id: String,
    pub mentions: Vec<crate::ingest::artifacts::ArtifactGrounding>,
    pub metadata: JsonObject,
    pub domain_id: String,
    pub canonical_entity_id: Option<String>,
    pub properties: JsonObject,
    pub embedding: Option<()>,
    pub level_from_root: i64,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct ArtifactGrounding {
    pub spans: Vec<IngestSpan>,
    pub multimodal_spans: Vec<()>,
}

#[allow(clippy::too_many_arguments)]
pub fn artifact_node(
    request: &IngestPipelineRequest,
    source_document_id: &str,
    namespace: &str,
    node_id: Option<&str>,
    artifact_kind: &str,
    lane: &str,
    visibility: &str,
    label: &str,
    summary: &str,
    extra_metadata: Option<&JsonObject>,
) -> ArtifactNode {
    let span = leading_span(source_document_id, request.raw_text.as_str(), artifact_kind);
    let mut metadata = JsonObject::from([
        (
            "workspace_id".to_owned(),
            Value::from(request.workspace_id.clone()),
        ),
        (
            "source_document_id".to_owned(),
            Value::from(source_document_id),
        ),
        (
            "source_uri".to_owned(),
            Value::from(request.source_uri.clone()),
        ),
        ("artifact_kind".to_owned(), Value::from(artifact_kind)),
        ("namespace".to_owned(), Value::from(namespace)),
        ("conversation_lane".to_owned(), Value::from(lane)),
        ("visibility".to_owned(), Value::from(visibility)),
        ("title".to_owned(), Value::from(request.title.clone())),
        (
            "parser_mode".to_owned(),
            Value::from(request.parser_mode.clone()),
        ),
        (
            "requires_provenance".to_owned(),
            Value::from(requires_provenance(artifact_kind)),
        ),
    ]);
    metadata.insert(
        "visibility".to_owned(),
        Value::from(visibility_for(&metadata)),
    );
    if metadata.get("visibility") == Some(&Value::from("projection")) {
        metadata.insert("projection_visible".to_owned(), Value::Bool(true));
    }
    if let Some(extra) = extra_metadata {
        for (key, value) in extra {
            metadata.insert(key.clone(), value.clone());
        }
    }
    metadata.insert("lifecycle_status".to_owned(), Value::from("active"));
    metadata.insert("redirect_to_id".to_owned(), Value::Null);
    metadata.insert("level_from_root".to_owned(), Value::from(0));

    ArtifactNode {
        id: node_id.map(str::to_owned),
        label: label.to_owned(),
        kind: "entity".to_owned(),
        summary: summary.to_owned(),
        doc_id: source_document_id.to_owned(),
        mentions: vec![ArtifactGrounding {
            spans: vec![span],
            multimodal_spans: Vec::new(),
        }],
        metadata,
        domain_id: request.workspace_id.clone(),
        canonical_entity_id: node_id.map(str::to_owned),
        properties: JsonObject::new(),
        embedding: None,
        level_from_root: 0,
    }
}
