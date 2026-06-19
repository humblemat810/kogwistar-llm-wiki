from __future__ import annotations

from kogwistar.runtime.budget import BudgetEvent

from kogwistar_llm_wiki.maintenance_reporting import summarize_maintenance_costs


def test_summarize_maintenance_costs_groups_by_kind_and_model() -> None:
    events = [
        BudgetEvent(
            run_id="run-1",
            source="runtime",
            kind="token",
            amount=10,
            unit="input_tokens",
            meta={"maintenance_kind": "document_propose_crosslinks", "model": "gpt-5-nano"},
        ),
        BudgetEvent(
            run_id="run-1",
            source="runtime",
            kind="token",
            amount=5,
            unit="output_tokens",
            meta={"maintenance_kind": "document_propose_crosslinks", "model": "gpt-5-nano"},
        ),
        BudgetEvent(
            run_id="run-1",
            source="runtime",
            kind="cost",
            amount=0.12,
            unit="total_cost",
            meta={"maintenance_kind": "document_propose_crosslinks", "model": "gpt-5-nano"},
        ),
        BudgetEvent(
            run_id="run-2",
            source="runtime",
            kind="token",
            amount=7,
            unit="total_tokens",
            meta={"maintenance_kind": "graph_patch_apply", "model": "local"},
        ),
    ]

    summary = summarize_maintenance_costs(events)

    crosslink = summary["document_propose_crosslinks|gpt-5-nano"]
    apply = summary["graph_patch_apply|local"]
    assert crosslink["input_tokens"] == 10
    assert crosslink["output_tokens"] == 5
    assert crosslink["total_tokens"] == 15
    assert crosslink["total_cost"] == 0.12
    assert apply["total_tokens"] == 7
