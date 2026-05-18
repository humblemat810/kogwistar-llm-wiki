from __future__ import annotations

from kogwistar.engine_core.models import Grounding, Node, Span

from kogwistar_llm_wiki.namespaces import GraphSpace, WorkspaceNamespaces
from kogwistar_llm_wiki.utils import _temporary_namespace


def _projection_span(doc_id: str) -> Span:
    return Span.model_validate(
        {
            "collection_page_url": f"document_collection/{doc_id}",
            "document_page_url": f"document/{doc_id}",
            "doc_id": doc_id,
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


def test_projection_reads_kg_visible_state_only(pipeline, ingest_request):
    pipeline.run(ingest_request)
    empty_snapshot = pipeline.build_projection_snapshot(workspace_id=ingest_request.workspace_id)
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
    ns = WorkspaceNamespaces(ingest_request.workspace_id)
    visible_node = Node(
        label="Manual Knowledge",
        type="entity",
        summary="Manual Knowledge",
        doc_id=source_document_id,
        mentions=[Grounding(spans=[visible_span])],
        metadata={"graph_space": "curated_kg", "visibility": "projection", "workspace_id": ingest_request.workspace_id},
    )
    with _temporary_namespace(pipeline.engines.kg, ns.curated_kg_space):
        pipeline.engines.kg.write.add_node(visible_node)

    snapshot = pipeline.build_projection_snapshot(workspace_id=ingest_request.workspace_id)
    titles = {entity.title for entity in snapshot.entities}
    assert "Manual Knowledge" in titles
    assert ingest_request.title not in titles


def test_projection_manifest_overrides_visibility_metadata(pipeline, ingest_request):
    workspace_id = ingest_request.workspace_id
    ns = WorkspaceNamespaces(workspace_id)

    source_document_id = pipeline._source_document_id(ingest_request)
    visible_node = Node(
        label="Visible Knowledge",
        type="entity",
        summary="Visible Knowledge",
        doc_id=source_document_id,
        mentions=[Grounding(spans=[_projection_span(source_document_id)])],
        metadata={"graph_space": "curated_kg", "visibility": "projection", "workspace_id": workspace_id},
    )
    hidden_node = Node(
        label="Manifest Knowledge",
        type="entity",
        summary="Manifest Knowledge",
        doc_id=source_document_id,
        mentions=[Grounding(spans=[_projection_span(source_document_id)])],
        metadata={"graph_space": "curated_kg", "visibility": "internal", "workspace_id": workspace_id},
    )
    with _temporary_namespace(pipeline.engines.kg, ns.curated_kg_space):
        pipeline.engines.kg.write.add_node(visible_node)
        pipeline.engines.kg.write.add_node(hidden_node)

    pipeline.engines.conversation.meta_sqlite.replace_named_projection(
        namespace=ns.projection_manifest,
        key=workspace_id,
        payload={
            "workspace_id": workspace_id,
            "ready_projected_ids": [str(hidden_node.id)],
            "projected_ids": [str(hidden_node.id)],
            "status": "ready",
        },
        last_authoritative_seq=1,
        last_materialized_seq=1,
        projection_schema_version=1,
        materialization_status="ready",
    )

    snapshot = pipeline.build_projection_snapshot(workspace_id=workspace_id)
    titles = {entity.title for entity in snapshot.entities}
    assert titles == {"Manifest Knowledge"}


def test_demo_projection_reifies_section_hyperedges(pipeline, ingest_request):
    request = ingest_request.model_copy(
        update={
            "title": "My Document",
            "source_uri": "file://demo.md",
            "raw_text": "# My Document\n\nThis is a starter document for the LLM-Wiki quickstart.\n\n## Contacts\n- Alice\n- Bob\n",
            "source_format": "markdown",
        }
    )
    workspace_id = request.workspace_id
    artifacts = pipeline.run(request)

    kg_nodes = pipeline.engines.kg.read.get_nodes(where={"workspace_id": workspace_id})
    assert not any(node.metadata.get("demo_graph_extraction") is True for node in kg_nodes)
    assert artifacts.promoted_entity_id is None

    curated_snapshot = pipeline.build_projection_snapshot(workspace_id=workspace_id)
    assert curated_snapshot.entities == []

    snapshot = pipeline.build_projection_snapshot(
        workspace_id=workspace_id,
        graph_spaces=[GraphSpace.BASE_KG],
        projection_filter="demo",
    )
    entities_by_title = {entity.title: entity for entity in snapshot.entities}

    assert {"Contacts", "Alice", "Bob"} <= set(entities_by_title)

    contacts = entities_by_title["Contacts"]
    alice = entities_by_title["Alice"]
    bob = entities_by_title["Bob"]

    assert alice.kg_id in contacts.source_ids
    assert bob.kg_id in contacts.source_ids
    assert any(relationship.target_id == alice.kg_id for relationship in contacts.relationships)
    assert any(relationship.target_id == bob.kg_id for relationship in contacts.relationships)


def test_demo_projection_hides_sentence_like_leaf_nodes(pipeline, ingest_request):
    request = ingest_request.model_copy(
        update={
            "title": "My Document",
            "source_uri": "file://demo.md",
            "raw_text": "# My Document\n\nThis is a starter document for the LLM-Wiki quickstart.\n\n## Contacts\n- Alice\n- Bob\n",
            "source_format": "markdown",
        }
    )
    workspace_id = request.workspace_id
    pipeline.run(request)

    kg_nodes = pipeline.engines.kg.read.get_nodes(where={"workspace_id": workspace_id})
    assert not any(node.metadata.get("demo_graph_extraction") is True for node in kg_nodes)

    snapshot = pipeline.build_projection_snapshot(
        workspace_id=workspace_id,
        graph_spaces=[GraphSpace.BASE_KG],
        projection_filter="demo",
    )
    titles = {entity.title for entity in snapshot.entities}

    assert "Contacts" in titles
    assert "Alice" in titles
    assert "Bob" in titles
    assert "This is a starter document for the LLM-Wiki quickstart." not in titles
