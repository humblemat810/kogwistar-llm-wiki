from types import SimpleNamespace

from kogwistar_llm_wiki.maintenance_control import (
    MaintenanceControl,
    send_control_command,
)
from kogwistar_llm_wiki.maintenance_selection import (
    select_embedding_exploration,
    select_request_candidates,
)


def node(node_id: str, vector: list[float] | None = None, **metadata: object) -> SimpleNamespace:
    return SimpleNamespace(id=node_id, embedding=vector, metadata=metadata)


def test_request_selection_orders_neighbors_then_semantic_then_evidence() -> None:
    seed = node("seed", [1.0, 0.0])
    neighbor = node("neighbor", [0.0, 1.0])
    semantic = node("semantic", [0.9, 0.1])
    evidence = node("evidence", evidence_ids=["source-1"])
    edge = SimpleNamespace(source_ids=["seed"], target_ids=["neighbor"])

    selected = select_request_candidates([seed], [seed, neighbor, semantic, evidence], [edge])

    assert [item.candidate_id for item in selected] == ["neighbor", "semantic", "evidence"]
    assert [item.reason for item in selected] == ["connected_neighbor", "semantic_similar", "shared_evidence"]


def test_request_selection_does_not_call_opposite_vectors_semantically_similar() -> None:
    seed = node("seed", [1.0, 0.0])
    opposite = node("opposite", [-1.0, 0.0])
    evidence = node("evidence", evidence_ids=["source-1"])

    selected = select_request_candidates([seed], [seed, opposite, evidence], [])

    assert [item.candidate_id for item in selected] == ["evidence"]
    assert selected[0].reason == "shared_evidence"


def test_embedding_probe_is_seeded_and_not_row_order_random() -> None:
    nodes = [node("a", [1.0, 0.0]), node("b", [0.0, 1.0]), node("c", [-1.0, 0.0])]

    first, strategy = select_embedding_exploration(nodes, dimension=2, cycle_seed=41)
    repeat, repeat_strategy = select_embedding_exploration(list(reversed(nodes)), dimension=2, cycle_seed=41)
    different, _ = select_embedding_exploration(nodes, dimension=2, cycle_seed=42)

    assert strategy == repeat_strategy == "embedding_probe"
    assert [item.candidate_id for item in first] == [item.candidate_id for item in repeat]
    assert [item.candidate_id for item in first] != [item.candidate_id for item in different]


def test_embedding_outage_only_degrades_exploration() -> None:
    candidates, strategy = select_embedding_exploration([node("no-vector")], dimension=2, cycle_seed=1)

    assert candidates == []
    assert strategy == "embedding_unavailable"


def test_control_persists_independent_switches(tmp_path) -> None:
    control = MaintenanceControl(tmp_path)
    state = control.update(request_enabled=False, background_enabled=True)
    assert state.request_enabled is False
    assert state.background_enabled is True
    assert MaintenanceControl(tmp_path).get() == state

    result = send_control_command(tmp_path, request_enabled=True, background_enabled=False)
    assert result["ok"] is True
    assert MaintenanceControl(tmp_path).get().request_enabled is True
    assert MaintenanceControl(tmp_path).get().background_enabled is False


def test_runtime_only_control_does_not_revert_on_next_poll(tmp_path) -> None:
    control = MaintenanceControl(tmp_path)
    control.update(request_enabled=False, background_enabled=False)

    control.update(request_enabled=True, background_enabled=True, persist=False)
    assert control.get().request_enabled is True
    assert control.get().background_enabled is True
    assert MaintenanceControl(tmp_path).get().request_enabled is False
