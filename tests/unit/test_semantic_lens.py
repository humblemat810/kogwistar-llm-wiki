from __future__ import annotations

import json
from pathlib import Path

import pytest
from kogwistar.engine_core import VectorSearchHit
from kogwistar.engine_core.models import Document, Edge, Grounding, Node, Span

from kogwistar_llm_wiki import (
    GraphSpace,
    SemanticLensRequest,
    SemanticLensService,
    WorkspaceNamespaces,
    build_in_memory_namespace_engines,
    validate_edit_proposal,
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


def test_retrieval_status_defaults_to_graph_mode():
    engines, workspace_id = _seed_graph()
    try:
        snapshot = SemanticLensService(engines).resolve(
            SemanticLensRequest(workspace_id=workspace_id, query_text="verifier")
        )
        assert snapshot.retrieval.mode == "graph"
        assert snapshot.retrieval.semantic_available is False
        assert snapshot.retrieval.degraded_reason is None
    finally:
        engines.close()


def test_auto_mode_activates_the_requested_vector_controls():
    assert SemanticLensRequest(
        workspace_id="w", similarity_threshold=0.5
    ).retrieval_mode == "auto"
    assert SemanticLensRequest(
        workspace_id="w", source_evidence_required=True
    ).retrieval_mode == "auto"
    with pytest.raises(ValueError, match="requires flat retrieval_mode"):
        SemanticLensRequest(
            workspace_id="w",
            retrieval_mode="semantic",
            source_evidence_required=True,
        )
    with pytest.raises(ValueError, match="requires semantic or flat"):
        SemanticLensRequest(
            workspace_id="w",
            retrieval_mode="graph",
            similarity_threshold=0.5,
        )


def test_semantic_degradation_is_visible_and_strict_mode_fails(monkeypatch: pytest.MonkeyPatch):
    engines, workspace_id = _seed_graph()
    try:
        read_type = type(engines.kg.read)

        def unavailable(*_args, **_kwargs):
            raise NotImplementedError("no embedding function configured")

        monkeypatch.setattr(read_type, "search_nodes_as_of", unavailable, raising=False)
        monkeypatch.setattr(read_type, "search_nodes_as_of_scored", unavailable, raising=False)

        service = SemanticLensService(engines)
        degraded = service.resolve(
            SemanticLensRequest(
                workspace_id=workspace_id,
                query_text="verifier",
                retrieval_mode="semantic",
            )
        )
        assert degraded.retrieval.semantic_available is False
        assert "no embedding function" in (degraded.retrieval.degraded_reason or "")

        with pytest.raises(ValueError, match="no embedding function"):
            service.resolve(
                SemanticLensRequest(
                    workspace_id=workspace_id,
                    query_text="verifier",
                    retrieval_mode="flat",
                    retrieval_required=True,
                )
            )
    finally:
        engines.close()


def test_flat_mode_returns_flat_hit_envelope_without_graph_hops():
    engines, workspace_id = _seed_graph()
    try:
        read_type = type(engines.kg.read)

        def scored(*_args, **_kwargs):
            return [
                (_node(workspace_id, "n:flat-1", "flat result 1"), 0.125),
                (_node(workspace_id, "n:flat-2", "flat result 2"), 0.25),
                (_node(workspace_id, "n:flat-3", "flat result 3"), 0.5),
            ]

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(read_type, "search_nodes_as_of_scored", scored, raising=False)
        try:
            snapshot = SemanticLensService(engines).resolve(
                SemanticLensRequest(
                    workspace_id=workspace_id,
                    query_text="flat",
                    retrieval_mode="flat",
                    hop_limit=0,
                    max_nodes=2,
                )
            )
        finally:
            monkeypatch.undo()
        assert snapshot.nodes == ()
        assert [item["node_id"] for item in snapshot.flat_hits] == [
            "n:flat-1", "n:flat-2"
        ]
        assert snapshot.flat_hits[0]["score"] == 0.125
        assert snapshot.retrieval.semantic_available is True
    finally:
        engines.close()


def test_flat_mode_applies_similarity_threshold_and_reports_normalized_score(
    monkeypatch: pytest.MonkeyPatch,
):
    engines, workspace_id = _seed_graph()
    try:
        read_type = type(engines.kg.read)
        calls: list[float | None] = []

        def scored(*_args, similarity_threshold=None, **_kwargs):
            calls.append(similarity_threshold)
            return [
                VectorSearchHit(
                    node=_node(workspace_id, "n:high", "high result"),
                    raw_distance=0.1,
                    similarity=0.9,
                    metric="cosine",
                    distance_kind="distance",
                ),
                VectorSearchHit(
                    node=_node(workspace_id, "n:low", "low result"),
                    raw_distance=0.8,
                    similarity=0.2,
                    metric="cosine",
                    distance_kind="distance",
                ),
            ]

        monkeypatch.setattr(read_type, "search_nodes_as_of_scored", scored, raising=False)
        snapshot = SemanticLensService(engines).resolve(
            SemanticLensRequest(
                workspace_id=workspace_id,
                query_text="result",
                retrieval_mode="flat",
                similarity_threshold=0.5,
                max_nodes=5,
            )
        )
        assert calls == [0.5]
        assert [hit["node_id"] for hit in snapshot.flat_hits] == ["n:high"]
        assert snapshot.flat_hits[0]["score"] == 0.9
        assert snapshot.retrieval.metric == "cosine"
        assert snapshot.retrieval.semantic_available is True
    finally:
        engines.close()


def test_flat_mode_verifies_source_revision_excerpt_in_source_namespace(
    monkeypatch: pytest.MonkeyPatch,
):
    engines, workspace_id = _seed_graph()
    source_namespace = WorkspaceNamespaces(workspace_id).source_space
    try:
        with _temporary_namespace(engines.kg, source_namespace):
            engines.kg.write.add_document(
                Document(
                    id="fixture-doc",
                    content="flat result 1",
                    type="text",
                    metadata={
                        "workspace_id": workspace_id,
                        "logical_source_document_id": "source-1",
                        "source_revision_id": "revision-1",
                    },
                )
            )
        read_type = type(engines.kg.read)

        def scored(*_args, **_kwargs):
            return [(_node(workspace_id, "n:source", "flat result 1"), 0.1)]

        monkeypatch.setattr(read_type, "search_nodes_as_of_scored", scored, raising=False)
        snapshot = SemanticLensService(engines).resolve(
            SemanticLensRequest(
                workspace_id=workspace_id,
                query_text="rabbit",
                retrieval_mode="flat",
                source_evidence_required=True,
            )
        )
        assert len(snapshot.flat_hits) == 1
        evidence = snapshot.flat_hits[0]["source_evidence"]
        assert evidence["status"] == "verified"
        assert evidence["source_id"] == "source-1"
        assert evidence["source_revision_id"] == "revision-1"
        assert evidence["excerpt"] == "flat result 1"
    finally:
        engines.close()


def test_flat_mode_rejects_stale_source_revision_and_excerpt(
    monkeypatch: pytest.MonkeyPatch,
):
    engines, workspace_id = _seed_graph()
    source_namespace = WorkspaceNamespaces(workspace_id).source_space
    try:
        with _temporary_namespace(engines.kg, source_namespace):
            engines.kg.write.add_document(
                Document(
                    id="fixture-doc",
                    content="new source revision",
                    type="text",
                    metadata={
                        "workspace_id": workspace_id,
                        "source_revision_id": "revision-2",
                    },
                )
            )
        read_type = type(engines.kg.read)

        def scored(*_args, **_kwargs):
            stale = _node(workspace_id, "n:stale", "old source")
            stale.metadata["source_revision_id"] = "revision-1"
            return [(stale, 0.1)]

        monkeypatch.setattr(read_type, "search_nodes_as_of_scored", scored, raising=False)
        snapshot = SemanticLensService(engines).resolve(
            SemanticLensRequest(
                workspace_id=workspace_id,
                query_text="source",
                retrieval_mode="flat",
                source_evidence_required=True,
            )
        )
        assert snapshot.flat_hits == ()
        assert snapshot.retrieval.degraded_reason == "no_verified_source_evidence"
    finally:
        engines.close()


def test_zero_hop_semantic_mode_keeps_vector_only_hits(monkeypatch: pytest.MonkeyPatch):
    engines, workspace_id = _seed_graph()
    try:
        read_type = type(engines.kg.read)

        def scored(*_args, **_kwargs):
            return [(_node(workspace_id, "n:vector", "unmatched label"), 0.125)]

        monkeypatch.setattr(read_type, "search_nodes_as_of_scored", scored, raising=False)
        snapshot = SemanticLensService(engines).resolve(
            SemanticLensRequest(
                workspace_id=workspace_id,
                query_text="rabbit",
                retrieval_mode="semantic",
                hop_limit=0,
                max_nodes=1,
            )
        )
        assert [node.id for node in snapshot.nodes] == ["n:vector"]
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


def test_textual_lens_reads_do_not_load_embedding_payloads(monkeypatch: pytest.MonkeyPatch):
    engines, workspace_id = _seed_graph()
    try:
        read_type = type(engines.kg.read)
        node_includes: list[list[str] | None] = []
        edge_includes: list[list[str] | None] = []
        original_get_nodes = read_type.get_nodes
        original_get_edges = read_type.get_edges

        def get_nodes(read_self, *args, **kwargs):
            node_includes.append(kwargs.get("include"))
            return original_get_nodes(read_self, *args, **kwargs)

        def get_edges(read_self, *args, **kwargs):
            edge_includes.append(kwargs.get("include"))
            return original_get_edges(read_self, *args, **kwargs)

        monkeypatch.setattr(read_type, "get_nodes", get_nodes)
        monkeypatch.setattr(read_type, "get_edges", get_edges)

        SemanticLensService(engines).resolve(
            SemanticLensRequest(workspace_id=workspace_id, query_text="verifier")
        )

        assert node_includes
        assert edge_includes
        assert all(include == ["documents", "metadatas"] for include in node_includes)
        assert all(include == ["documents", "metadatas"] for include in edge_includes)
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
