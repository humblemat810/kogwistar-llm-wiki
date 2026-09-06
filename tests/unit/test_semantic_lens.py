from __future__ import annotations

import json
from pathlib import Path

from kogwistar.engine_core.models import Edge, Grounding, Node, Span

from kogwistar_llm_wiki import (
    GraphSpace,
    SemanticLensRequest,
    SemanticLensService,
    validate_edit_proposal,
    WorkspaceNamespaces,
    build_in_memory_namespace_engines,
)
from kogwistar_llm_wiki.utils import _temporary_namespace


def _span(doc_id: str, excerpt: str) -> Span:
    return Span.model_validate(
        {
            "collection_page_url": f"collection/{doc_id}",
            "document_page_url": f"document/{doc_id}",
            "doc_id": doc_id,
            "insertion_method": "manual",
            "page_number": 1,
            "start_char": 0,
            "end_char": max(1, len(excerpt)),
            "excerpt": excerpt,
            "context_before": "",
            "context_after": "",
            "chunk_id": None,
            "source_cluster_id": None,
        }
    )


def _node(workspace_id: str, node_id: str, label: str) -> Node:
    return Node(
        id=node_id,
        label=label,
        type="entity",
        summary=label,
        doc_id="fixture-doc",
        mentions=[Grounding(spans=[_span("fixture-doc", label)])],
        metadata={
            "workspace_id": workspace_id,
            "graph_space": GraphSpace.CURATED_KG.value,
            "artifact_kind": "fixture_knowledge",
        },
    )


def _edge(workspace_id: str, edge_id: str, source_id: str, target_id: str, relation: str) -> Edge:
    return Edge(
        id=edge_id,
        label=relation,
        type="relationship",
        summary=relation,
        source_ids=[source_id],
        target_ids=[target_id],
        relation=relation,
        source_edge_ids=[],
        target_edge_ids=[],
        mentions=[Grounding(spans=[_span("fixture-doc", relation)])],
        metadata={
            "workspace_id": workspace_id,
            "graph_space": GraphSpace.CURATED_KG.value,
            "artifact_kind": "fixture_relation",
        },
    )


def _seed_graph():
    engines = build_in_memory_namespace_engines()
    workspace_id = "lens-test"
    namespace = WorkspaceNamespaces(workspace_id).curated_kg_space
    with _temporary_namespace(engines.kg, namespace):
        engines.kg.write.add_node(_node(workspace_id, "n:verifier", "Verifier"))
        engines.kg.write.add_node(_node(workspace_id, "n:reward", "Outcome reward"))
        engines.kg.write.add_node(_node(workspace_id, "n:example", "Code test"))
        engines.kg.write.add_node(_node(workspace_id, "n:unrelated", "Garden calendar"))
        engines.kg.write.add_edge(_edge(workspace_id, "e:verify", "n:verifier", "n:reward", "supports"))
        engines.kg.write.add_edge(_edge(workspace_id, "e:example", "n:example", "n:verifier", "implements"))
    return engines, workspace_id


def test_semantic_lens_is_deterministic_and_keeps_grounding():
    engines, workspace_id = _seed_graph()
    try:
        service = SemanticLensService(engines, clock_ms=lambda: 1_000)
        request = SemanticLensRequest(
            workspace_id=workspace_id,
            graph_spaces=(GraphSpace.CURATED_KG,),
            query_text="verifier reward",
            hop_limit=1,
            max_nodes=3,
            max_edges=2,
            source_watermark=17,
        )
        first = service.resolve(request)
        second = service.resolve(request)

        assert first.lens_id == second.lens_id
        assert {node.id for node in first.nodes} == {"n:verifier", "n:reward", "n:example"}
        assert first.source_watermark == 17
        assert all(node.grounding for node in first.nodes)
        assert first.selection_explanations[0].reason == "query_match"
        assert first.omitted_summary["candidate_nodes"] == 1
    finally:
        engines.close()


def test_semantic_lens_pins_are_retained_and_edges_are_bounded():
    engines, workspace_id = _seed_graph()
    try:
        service = SemanticLensService(engines, clock_ms=lambda: 2_000)
        snapshot = service.resolve(
            SemanticLensRequest(
                workspace_id=workspace_id,
                graph_spaces=("curated_kg",),
                query_text="verifier",
                pinned_node_ids=("n:example",),
                max_nodes=2,
                max_edges=1,
            )
        )
        assert "n:example" in {node.id for node in snapshot.nodes}
        assert len(snapshot.nodes) == 2
        assert len(snapshot.edges) <= 1
        assert any(item.reason == "pinned" for item in snapshot.selection_explanations)
    finally:
        engines.close()


def test_no_change_is_explicit_and_does_not_create_a_proposal():
    engines, workspace_id = _seed_graph()
    try:
        service = SemanticLensService(engines, clock_ms=lambda: 3_000)
        snapshot = service.resolve(
            SemanticLensRequest(workspace_id=workspace_id, query_text="not present")
        )
        outcome = service.investigate(
            session_id="session-1",
            snapshot=snapshot,
            insufficiency_reason="No grounded candidate matched the question.",
        )
        assert outcome.outcome == "no_change"
        assert outcome.proposal is None
        assert outcome.insufficiency_reason
    finally:
        engines.close()


def test_stale_edit_proposal_is_rejected_before_mutation():
    engines, workspace_id = _seed_graph()
    try:
        service = SemanticLensService(engines, clock_ms=lambda: 4_000)
        snapshot = service.resolve(
            SemanticLensRequest(workspace_id=workspace_id, query_text="verifier", source_watermark=4)
        )
        validation = validate_edit_proposal(
            snapshot,
            {
                "lens_id": snapshot.lens_id,
                "source_watermark": 3,
                "operation": "add_relation",
                "target_ids": ["n:verifier"],
                "evidence_ids": ["span:1"],
            },
        )
        assert validation.accepted is False
        assert validation.reason == "stale_source_watermark"
    finally:
        engines.close()


def test_rl_fixture_is_grounded_and_has_hyperedge_curriculum():
    fixture_path = Path(__file__).parents[1] / "fixtures" / "rl_verifiable_reasoning_v1.json"
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    source_ids = {source["id"] for source in fixture["sources"]}
    assert fixture["fixture_id"] == "rl_verifiable_reasoning_v1"
    assert fixture["hyperedges"]
    assert all(node["source_id"] in source_ids and node["span"]["excerpt"] for node in fixture["nodes"])
    assert {query["query"] for query in fixture["acceptance_queries"]}
