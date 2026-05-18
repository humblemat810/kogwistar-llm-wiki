from __future__ import annotations

from kogwistar.engine_core.models import Grounding, Node, Span

from kogwistar_llm_wiki import GraphSpace, GraphSpaceQueryService, WorkspaceNamespaces, workspace_graph_spaces
from kogwistar_llm_wiki.utils import _temporary_namespace


def _span(doc_id: str) -> Span:
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
            "context_after": "B",
            "chunk_id": None,
            "source_cluster_id": None,
        }
    )


def _curated_node(*, workspace_id: str, doc_id: str, label: str) -> Node:
    return Node(
        label=label,
        type="entity",
        summary=label,
        doc_id=doc_id,
        mentions=[Grounding(spans=[_span(doc_id)])],
        metadata={
            "workspace_id": workspace_id,
            "artifact_kind": "promoted_knowledge",
            "visibility": "knowledge",
        },
    )


def test_workspace_graph_spaces_defaults_to_source_and_curated_kg():
    assert workspace_graph_spaces() == [GraphSpace.SOURCE, GraphSpace.CURATED_KG]
    assert workspace_graph_spaces(include_wisdom=True) == [
        GraphSpace.SOURCE,
        GraphSpace.CURATED_KG,
        GraphSpace.WISDOM,
    ]


def test_source_query_returns_parsed_source_nodes_before_promotion(pipeline, ingest_request):
    pipeline.run(ingest_request)

    results = pipeline.query_nodes(
        workspace_id=ingest_request.workspace_id,
        graph_spaces=["source"],
    )

    assert results
    assert all(result.graph_space == "source" for result in results)
    assert any(result.node.metadata.get("graph_space") == "source" for result in results)
    assert any(result.node.metadata.get("source_document_id") for result in results)


def test_curated_query_excludes_raw_source_by_default(pipeline, ingest_request):
    pipeline.run(ingest_request)

    results = pipeline.query_nodes(
        workspace_id=ingest_request.workspace_id,
        graph_spaces=["curated_kg"],
    )

    assert results == []


def test_legacy_curated_alias_support_reads_promoted_nodes_from_kg(pipeline, ingest_request):
    ns = WorkspaceNamespaces(ingest_request.workspace_id)
    curated_label = "Legacy Curated Knowledge"
    node = _curated_node(
        workspace_id=ingest_request.workspace_id,
        doc_id=pipeline._source_document_id(ingest_request),
        label=curated_label,
    )

    with _temporary_namespace(pipeline.engines.kg, ns.kg):
        pipeline.engines.kg.write.add_node(node)

    results = pipeline.query_nodes(
        workspace_id=ingest_request.workspace_id,
        graph_spaces=["curated_kg"],
    )
    alias_results = pipeline.query_nodes(
        workspace_id=ingest_request.workspace_id,
        graph_spaces=["kg"],
    )

    assert any(result.node.label == curated_label for result in results)
    assert any(result.node.label == curated_label for result in alias_results)
    assert all(result.graph_space == "curated_kg" for result in results)
    assert all(result.graph_space == "curated_kg" for result in alias_results)
    assert any(result.namespace in {ns.curated_kg_space, ns.kg} for result in results)


def test_workspace_query_preset_can_return_source_and_curated_results(pipeline, ingest_request):
    ns = WorkspaceNamespaces(ingest_request.workspace_id)
    curated_label = "Workspace Curated Knowledge"
    node = _curated_node(
        workspace_id=ingest_request.workspace_id,
        doc_id=pipeline._source_document_id(ingest_request),
        label=curated_label,
    )

    pipeline.run(ingest_request)
    with _temporary_namespace(pipeline.engines.kg, ns.kg):
        pipeline.engines.kg.write.add_node(node)

    results = pipeline.query_nodes(
        workspace_id=ingest_request.workspace_id,
        graph_spaces=workspace_graph_spaces(),
    )

    graph_spaces = {result.graph_space for result in results}
    titles = {result.node.label for result in results}

    assert graph_spaces == {"source", "curated_kg"}
    assert ingest_request.title in titles
    assert curated_label in titles


def test_base_kg_query_returns_reference_projection_and_hydrates_source(pipeline, ingest_request):
    pipeline.run(ingest_request)

    pointer_results = pipeline.query_nodes(
        workspace_id=ingest_request.workspace_id,
        graph_spaces=["base_kg"],
        resolve_mode="pointer_only",
    )
    hydrated_results = pipeline.query_nodes(
        workspace_id=ingest_request.workspace_id,
        graph_spaces=["base_kg"],
        resolve_mode="hydrate_target",
    )

    assert pointer_results
    assert all(result.graph_space == "base_kg" for result in pointer_results)
    assert all(result.node.metadata.get("graph_space") == "base_kg" for result in pointer_results)
    assert all(result.reference_node is not None for result in pointer_results)
    assert any(
        getattr(result.node, "mentions", None)
        and result.node.mentions[0].spans[0].insertion_method == "base_kg_projection"
        for result in pointer_results
    )

    assert hydrated_results
    assert all(result.graph_space == "base_kg" for result in hydrated_results)
    assert any(result.node.metadata.get("graph_space") == "source" for result in hydrated_results)
    assert any(result.reference_node is not None for result in hydrated_results)


def test_query_respects_workspace_scope(pipeline, ingest_request):
    ns = WorkspaceNamespaces(ingest_request.workspace_id)
    doc_id = pipeline._source_document_id(ingest_request)
    shared_label = "Shared Curated Knowledge"

    matching = _curated_node(workspace_id=ingest_request.workspace_id, doc_id=doc_id, label=shared_label)
    foreign = _curated_node(workspace_id="other-workspace", doc_id=doc_id, label=shared_label)

    with _temporary_namespace(pipeline.engines.kg, ns.kg):
        pipeline.engines.kg.write.add_node(matching)
        pipeline.engines.kg.write.add_node(foreign)

    results = pipeline.query_nodes(
        workspace_id=ingest_request.workspace_id,
        graph_spaces=["curated_kg"],
        where={"label": shared_label},
    )

    assert results
    assert all(result.node.metadata.get("workspace_id") == ingest_request.workspace_id for result in results)


def test_pipeline_query_nodes_delegates_to_query_service(pipeline, ingest_request):
    pipeline.run(ingest_request)

    direct = pipeline.query_service.get_nodes(
        workspace_id=ingest_request.workspace_id,
        graph_spaces=["source"],
    )
    via_pipeline = pipeline.query_nodes(
        workspace_id=ingest_request.workspace_id,
        graph_spaces=["source"],
    )

    assert [result.node.id for result in via_pipeline] == [result.node.id for result in direct]
    assert [result.namespace for result in via_pipeline] == [result.namespace for result in direct]
