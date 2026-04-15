from kogwistar.engine_core.models import Grounding, Node, Span


def test_projection_reads_kg_visible_state_only(pipeline, ingest_request):
    pipeline.run(ingest_request)
    empty_snapshot = pipeline.build_projection_snapshot()
    assert empty_snapshot.entities == []

    source_document_id = pipeline._source_document_id(ingest_request)
    visible_span = Span.model_validate(
        {
            "collection_page_url": f"document_collection/{source_document_id}",
            "document_page_url": f"document/{source_document_id}",
            "doc_id": source_document_id,
            "insertion_method": "manual",
            "page_number": 1,
            "start_char": 0,
            "end_char": 1,
            "excerpt": "A",
            "context_before": "",
            "context_after": "cme",
            "chunk_id": None,
            "source_cluster_id": None,
        }
    )
    visible_node = Node(
        label="Manual Knowledge",
        type="entity",
        summary="Manual Knowledge",
        doc_id=source_document_id,
        mentions=[Grounding(spans=[visible_span])],
        metadata={"projection_visible": True, "workspace_id": ingest_request.workspace_id},
    )
    pipeline.engines.kg.write.add_node(visible_node)

    snapshot = pipeline.build_projection_snapshot()
    titles = {entity.title for entity in snapshot.entities}
    assert "Manual Knowledge" in titles
    assert ingest_request.title not in titles
