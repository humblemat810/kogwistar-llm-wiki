use kogwistar_llm_wiki_port::observation_evidence::{
    compact_entity_record, entity_metadata, entity_spans,
};
use serde_json::json;

#[test]
fn entity_metadata_unwraps_nested_metadata_and_properties() {
    let entity = json!({
        "id": "entity-1",
        "metadata": {
            "workspace_id": "demo",
            "metadata": {"relation": "related_to"},
            "properties": {"target_id": "node-1"}
        },
        "properties": {"quality_status": "verified"}
    });
    let metadata = entity_metadata(&entity);
    assert_eq!(metadata["relation"], json!("related_to"));
    assert_eq!(metadata["target_id"], json!("node-1"));
    assert_eq!(metadata["quality_status"], json!("verified"));
}

#[test]
fn compact_entity_record_keeps_bounded_review_fields() {
    let entity = json!({
        "id": "entity-1",
        "label": "Revenue increased",
        "summary": "Revenue grew.",
        "metadata": {
            "relation": "related_to",
            "source_document_id": "logical-source-1",
            "evidence_ids": ["node-1", "node-2"],
            "provenance": {
                "source_document_id": "logical-source-1",
                "source_revision_id": "revision-1",
                "source_digest": "digest"
            },
            "target_namespace": "ws:demo:g:source",
            "target_kind": "node",
            "target_id": "node-1"
        }
    });
    let record = compact_entity_record(&entity, "demo", "ws:demo:g:source");
    assert_eq!(record["id"], json!("entity-1"));
    assert_eq!(record["workspace_id"], json!("demo"));
    assert_eq!(record["metadata"]["relation"], json!("related_to"));
    assert_eq!(record["evidence_ids"], json!(["node-1", "node-2"]));
    assert_eq!(
        record["provenance"]["source_document_id"],
        json!("logical-source-1")
    );
    assert_eq!(record["properties"]["target_id"], json!("node-1"));
}

#[test]
fn entity_spans_reads_mentions_grounding() {
    let entity = json!({
        "mentions": [{
            "spans": [{
                "doc_id": "doc-1",
                "start_char": 0,
                "end_char": 35,
                "excerpt": "The authoritative source states that"
            }]
        }]
    });
    let spans = entity_spans(&entity);
    assert_eq!(spans.len(), 1);
    assert_eq!(spans[0]["doc_id"], json!("doc-1"));
}
