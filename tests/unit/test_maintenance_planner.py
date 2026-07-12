from __future__ import annotations

from kogwistar_llm_wiki.maintenance_planner import (
    DEFAULT_DOCUMENT_MAINTENANCE_PLAN,
    decide_next_maintenance_phase,
    normalize_maintenance_plan,
)


def test_planner_advances_one_phase_at_a_time() -> None:
    payload = {
        "maintenance_plan": list(DEFAULT_DOCUMENT_MAINTENANCE_PLAN),
        "maintenance_phase_index": 0,
        "maintenance_round": 0,
    }

    decision = decide_next_maintenance_phase(payload, completed_kind="document_seed_graph")

    assert decision.should_continue
    assert decision.next_kind == "document_parse_graph"
    assert decision.next_index == 1


def test_planner_stops_when_plan_is_complete_or_explicitly_stopped() -> None:
    plan = list(DEFAULT_DOCUMENT_MAINTENANCE_PLAN)
    complete = decide_next_maintenance_phase(
        {"maintenance_plan": plan, "maintenance_phase_index": len(plan) - 1},
        completed_kind=plan[-1],
    )
    stopped = decide_next_maintenance_phase(
        {"maintenance_plan": plan, "maintenance_stop_requested": True},
        completed_kind=plan[0],
    )

    assert not complete.should_continue
    assert complete.reason == "plan_complete"
    assert not stopped.should_continue
    assert stopped.reason == "stop_requested"


def test_planner_stops_at_per_document_round_limit() -> None:
    decision = decide_next_maintenance_phase(
        {
            "maintenance_plan": list(DEFAULT_DOCUMENT_MAINTENANCE_PLAN),
            "maintenance_phase_index": 0,
            "maintenance_round": 2,
            "maintenance_max_rounds": 2,
        },
        completed_kind="document_seed_graph",
    )

    assert not decision.should_continue
    assert decision.reason == "max_rounds_reached"


def test_planner_normalizes_fake_payload_shape_without_mutating_it() -> None:
    payload = {"maintenance_plan": [" seed ", "", "document_parse_graph"]}

    assert normalize_maintenance_plan(payload["maintenance_plan"]) == (
        "seed",
        "document_parse_graph",
    )
    assert payload["maintenance_plan"] == [" seed ", "", "document_parse_graph"]
