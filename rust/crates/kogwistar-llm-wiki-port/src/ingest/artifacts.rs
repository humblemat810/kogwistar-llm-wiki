use crate::ingest::source_lifecycle::SourceRevision;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum VerificationMethod {
    Llm,
    Levenshtein,
    Regex,
    Heuristic,
    Human,
    Ensemble,
    System,
}

#[derive(Clone, Debug, PartialEq)]
pub struct MentionVerification {
    pub method: VerificationMethod,
    pub is_verified: bool,
    pub score: Option<f64>,
    pub notes: String,
}

#[derive(Clone, Debug, PartialEq)]
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
