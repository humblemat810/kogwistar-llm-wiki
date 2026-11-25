use kogwistar_llm_wiki_port::ingest::artifacts::{leading_span, parse_document_id_for_request};
use kogwistar_llm_wiki_port::ingest::source_lifecycle::{build_source_revision, SourceRevision};

#[test]
fn leading_span_matches_python_document_artifact() {
    let span = leading_span("source:1", "hello world", "artifact_kind");

    assert_eq!(span.collection_page_url, "document_collection/source:1");
    assert_eq!(span.document_page_url, "document/source:1");
    assert_eq!(span.doc_id, "source:1");
    assert_eq!(span.insertion_method, "artifact_kind");
    assert_eq!(span.page_number, 1);
    assert_eq!(span.start_char, 0);
    assert_eq!(span.end_char, 1);
    assert_eq!(span.excerpt, "h");
    assert_eq!(span.context_before, "");
    assert_eq!(span.context_after, "ello world");
    assert_eq!(span.chunk_id, None);
    assert_eq!(span.source_cluster_id, None);
    assert!(!span.verification.is_verified);
}

#[test]
fn leading_span_slices_python_characters_not_bytes() {
    let span = leading_span("source:1", "世界", "artifact_kind");

    assert_eq!(span.excerpt, "世");
    assert_eq!(span.context_after, "界");
}

#[test]
fn parse_document_id_prefers_immutable_revision() {
    let revision = build_source_revision("demo", "source:1", "hello");
    assert_eq!(
        parse_document_id_for_request(&revision, "source:1"),
        revision.revision_document_id
    );

    let fallback = SourceRevision {
        source_document_id: "source:1".to_owned(),
        revision_id: "revision:1".to_owned(),
        source_digest: "digest".to_owned(),
        revision_document_id: String::new(),
    };
    assert_eq!(
        parse_document_id_for_request(&fallback, "source:1"),
        "source:1"
    );
}
