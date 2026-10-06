from __future__ import annotations

import pytest

from kogwistar_llm_wiki.workbench.prompt_aliases import (
    PromptAliasProjection,
    project_crosslink_payload,
    restore_cockpit_action,
    restore_crosslink_response,
)
from kogwistar_llm_wiki.workbench.semantic_lens import (
    LensEdge,
    LensNode,
    LensParticipation,
    RetrievalStatus,
    SelectionExplanation,
    SemanticLensSnapshot,
)
from kogwistar_llm_wiki.workbench.workbench_cockpit import (
    CockpitAction,
    CockpitMaintenancePatch,
)


def _snapshot() -> SemanticLensSnapshot:
    return SemanticLensSnapshot(
        lens_id="lens-1",
        workspace_id="workspace-1",
        source_watermark="w1",
        projected_at_ms=1,
        completeness="complete",
        nodes=(
            LensNode("node-a", "curated_kg", "ns", "A", "entity", 1, {}, (), {}),
            LensNode("node-b", "curated_kg", "ns", "B", "entity", 1, {}, (), {}),
        ),
        edges=(
            LensEdge("edge-a", "curated_kg", "ns", ("node-a",), ("node-b",), "rel", 1, {}, (), {}),
        ),
        hyperedges=(),
        participations=(LensParticipation("edge-a", "node-a", "source"),),
        anchor_explanations=(SelectionExplanation("node-a", "anchor", 1.0),),
        selection_explanations=(),
        omitted_summary={},
        query_timing_ms=1,
        retrieval=RetrievalStatus("graph", False, None, None),
    )


def test_snapshot_projection_is_deterministic_and_does_not_mutate_source():
    snapshot = _snapshot()
    projection = PromptAliasProjection.for_snapshot(snapshot)
    repeat = PromptAliasProjection.for_snapshot(snapshot)

    assert projection.context == repeat.context
    assert projection.context["nodes"][0]["id"].startswith("N")
    assert projection.context["edges"][0]["id"].startswith("E")
    assert projection.context["edges"][0]["source_ids"][0].startswith("N")
    assert snapshot.nodes[0].id == "node-a"
    assert snapshot.edges[0].id == "edge-a"


def test_projection_aliases_nested_observation_graph_references_deterministically():
    snapshot = _snapshot()
    observations = ({
        "kind": "query_history",
        "details": {
            "proposal": {
                "source_ids": ["node-b"],
                "target_ids": ["node-a"],
                "supersedes_ids": ["edge-a"],
            },
        },
    },)
    first = PromptAliasProjection.for_snapshot(snapshot, observations)
    second = PromptAliasProjection.for_snapshot(snapshot, tuple(reversed(observations)))

    first_proposal = first.context["observations"][0]["details"]["proposal"]
    second_proposal = second.context["observations"][0]["details"]["proposal"]
    assert first_proposal == second_proposal
    assert first_proposal["source_ids"] == ["N2"]
    assert first_proposal["target_ids"] == ["N1"]
    assert first_proposal["supersedes_ids"] == ["E1"]


def test_cockpit_action_restores_typed_graph_references():
    projection = PromptAliasProjection.for_snapshot(_snapshot())
    action = CockpitAction(
        kind="inspect_evidence",
        entity_ids=[projection.context["nodes"][0]["id"]],
        cited_entity_ids=[projection.context["nodes"][1]["id"]],
    )

    restored = restore_cockpit_action(action, projection)

    assert restored.entity_ids == ["node-a"]
    assert restored.cited_entity_ids == ["node-b"]


def test_cockpit_patch_endpoints_and_supersession_are_restored():
    projection = PromptAliasProjection.for_snapshot(_snapshot())
    node_a = projection.context["nodes"][0]["id"]
    node_b = projection.context["nodes"][1]["id"]
    edge_a = projection.context["edges"][0]["id"]
    action = CockpitAction(
        kind="propose_patch",
        patch=CockpitMaintenancePatch.model_validate({
            "patch_id": "patch-1",
            "intent": "add_crosslink",
            "scope": {"workspace_id": "workspace-1"},
            "operations": [{
                "operation_id": "op-1",
                "kind": "ADD_EDGE",
                "edge_id": "new-edge",
                "from_node_id": node_a,
                "to_node_id": node_b,
                "relation": "related",
                "supersedes_ids": [edge_a],
                "provenance": {
                    "source_document_id": "doc-1",
                    "maintenance_run_id": "run-1",
                    "confidence": 0.9,
                },
            }],
        }),
    )

    restored = restore_cockpit_action(action, projection)
    operation = restored.patch.operations[0]
    assert operation.from_node_id == "node-a"
    assert operation.to_node_id == "node-b"
    assert operation.supersedes_ids == ["edge-a"]


def test_unknown_or_wrong_kind_aliases_fail_before_host_action():
    projection = PromptAliasProjection.for_snapshot(_snapshot())
    with pytest.raises(ValueError):
        projection.resolve_node("N999")
    with pytest.raises(ValueError):
        projection.resolve_node("E1")


def test_patch_endpoint_kind_mismatch_fails_before_host_action():
    projection = PromptAliasProjection.for_snapshot(_snapshot())
    action = CockpitAction(
        kind="propose_patch",
        patch=CockpitMaintenancePatch.model_validate({
            "patch_id": "patch-kind-mismatch",
            "intent": "add_crosslink",
            "scope": {"workspace_id": "workspace-1"},
            "operations": [{
                "operation_id": "op-kind-mismatch",
                "kind": "ADD_EDGE",
                "edge_id": "N1",
                "from_node_id": "N1",
                "to_node_id": "N2",
                "relation": "related",
                "provenance": {
                    "source_document_id": "doc-1",
                    "maintenance_run_id": "run-1",
                    "confidence": 0.9,
                },
            }],
        }),
    )
    with pytest.raises(ValueError):
        restore_cockpit_action(action, projection)


def test_crosslink_projection_restores_superseded_edge_and_preserves_evidence_ids():
    projection, evidence, context = project_crosslink_payload(
        [
            {
                "evidence_id": "evidence-1",
                "node_id": "node-a",
                "source_document_id": "doc-1",
                "source_revision_id": "rev-1",
                "revision_document_id": "rev-doc-1",
                "source_digest": "a" * 64,
                "start_char": 0,
                "end_char": 1,
                "excerpt": "A",
            }
        ],
        {
            "nodes": [{"node_id": "node-b", "label": "B"}],
            "edges": [{"id": "edge-a", "source_ids": ["node-a"], "target_ids": ["node-b"]}],
        },
    )

    assert evidence[0]["evidence_id"] == "evidence-1"
    assert evidence[0]["node_id"].startswith("N")
    assert context["edges"][0]["id"].startswith("E")
    restored = restore_crosslink_response(
        {
            "groups": [{
                "operations": [{"supersedes_edge_id": context["edges"][0]["id"]}]
            }]
        },
        projection,
    )
    assert restored["groups"][0]["operations"][0]["supersedes_edge_id"] == "edge-a"
