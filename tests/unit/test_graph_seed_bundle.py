from __future__ import annotations

from pathlib import Path

import pytest

from kogwistar_llm_wiki import (
    IngestPipeline,
    SemanticLensRequest,
    WorkbenchApi,
    build_in_memory_namespace_engines,
)
from kogwistar_llm_wiki.graph_seed_bundle import (
    GraphSeedBundle,
    dump_seed_bundle,
    export_graph_seed_bundle,
    load_seed_bundle,
    seed_graph_bundle,
)
from kogwistar_llm_wiki.workbench_cockpit import CockpitAction


BUNDLE_PATH = Path(__file__).parents[2] / "data" / "seed_bundles" / "rl_llm_agent_tool_use_v1.json"


class InspectThenAnswerCockpit:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, _request, snapshot, observations, _progress) -> CockpitAction:
        self.calls += 1
        if not observations:
            visible = [node.id for node in snapshot.nodes if node.id.startswith("model:")][:2]
            return CockpitAction(
                kind="inspect_evidence",
                entity_ids=visible,
                rationale="Inspect persisted model nodes before answering.",
            )
        return CockpitAction(
            kind="answer",
            answer="The persisted graph distinguishes long-context reasoning RL from agentic environment RL.",
            cited_entity_ids=[node.id for node in snapshot.nodes if node.id in {"model:kimi-k1.5", "model:kimi-k2"}],
            rationale="Answer from the inspected grounded lens without proposing a change.",
        )


def test_research_bundle_has_exact_half_open_grounding_and_hyperedges() -> None:
    bundle = load_seed_bundle(BUNDLE_PATH)
    sources = {source.id: source.text for source in bundle.sources}

    assert bundle.bundle_id == "rl_llm_agent_tool_use_v1"
    assert len(bundle.sources) == 9
    assert len(bundle.nodes) == 28
    assert len(bundle.edges) == 20
    assert len(bundle.hyperedges) == 5
    for entity in (*bundle.nodes, *bundle.edges, *bundle.hyperedges):
        for mention in entity.mentions:
            assert mention.start_char is not None
            assert mention.end_char is not None
            assert sources[mention.source_id][mention.start_char : mention.end_char] == mention.excerpt


def test_bundle_rejects_ambiguous_implicit_excerpt() -> None:
    payload = {
        "bundle_id": "ambiguous",
        "title": "Ambiguous",
        "description": "Ambiguous evidence should not silently select an occurrence.",
        "created_at": "2026-08-12",
        "sources": [{"id": "source:a", "title": "A", "url": "https://example.test/a", "revision": "1", "text": "same same"}],
        "nodes": [{"id": "node:a", "label": "A", "kind": "concept", "summary": "A", "mentions": [{"source_id": "source:a", "excerpt": "same"}]}],
    }

    with pytest.raises(ValueError, match="ambiguous within source:a"):
        GraphSeedBundle.model_validate(payload)


@pytest.mark.slow
def test_seed_cockpit_export_and_reseed_round_trip_end_to_end(tmp_path: Path) -> None:
    bundle = load_seed_bundle(BUNDLE_PATH)
    engines = build_in_memory_namespace_engines()
    restored_engines = build_in_memory_namespace_engines()
    try:
        first = seed_graph_bundle(engines, workspace_id="rl-seed", bundle=bundle)
        second = seed_graph_bundle(engines, workspace_id="rl-seed", bundle=bundle)
        assert first.total_added == 62
        assert second.total_added == 0
        assert second.existing_entities == 62

        responder = InspectThenAnswerCockpit()
        api = WorkbenchApi(IngestPipeline(engines), cockpit_responder=responder)
        response = api.ask(
            {
                "workspace_id": "rl-seed",
                "session_id": "seed-e2e",
                "interaction_id": "seed-e2e-turn-1",
                "mode": "codex",
                "query_text": "Compare Kimi k1.5 and Kimi K2 reinforcement learning.",
                "graph_spaces": ["curated_kg"],
                "max_nodes": 48,
                "max_edges": 48,
                "max_hyperedges": 12,
                "hop_limit": 2,
            }
        )
        assert response["agent_status"] == "cockpit_active"
        assert response["answer"]["outcome"] == "answer"
        assert responder.calls == 2
        assert response["cockpit_observations"][0]["kind"] == "inspect_evidence"
        assert {item["id"] for item in response["snapshot"]["hyperedges"]} >= {
            "hyperedge:k15-long-reasoning-recipe",
            "hyperedge:k2-agentic-posttraining",
        }

        exported = export_graph_seed_bundle(engines, workspace_id="rl-seed", bundle_id=bundle.bundle_id)
        output = dump_seed_bundle(exported, tmp_path / "exported.json")
        reloaded = load_seed_bundle(output)
        assert exported == bundle
        assert reloaded == bundle

        restored = seed_graph_bundle(restored_engines, workspace_id="rl-restored", bundle=reloaded)
        assert restored.total_added == 62
        snapshot = IngestPipeline(restored_engines).resolve_semantic_lens(
            SemanticLensRequest(
                workspace_id="rl-restored",
                query_text="DeepSeek R1 cold-start GRPO",
                graph_spaces=("curated_kg",),
                max_nodes=48,
                max_edges=48,
                max_hyperedges=12,
                hop_limit=2,
            )
        )
        assert {node.id for node in snapshot.nodes} >= {"model:deepseek-r1", "concept:cold-start-data", "concept:grpo"}
        assert "hyperedge:r1-training-recipe" in {edge.id for edge in snapshot.hyperedges}
    finally:
        engines.close()
        restored_engines.close()
