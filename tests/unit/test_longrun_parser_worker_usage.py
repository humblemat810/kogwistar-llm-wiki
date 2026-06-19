from __future__ import annotations

from kg_doc_parser.workflow_ingest.providers import ProviderEndpointConfig, WorkflowProviderSettings
from kogwistar.runtime.budget import BudgetEvent
from kogwistar.runtime.budget_adapters import summarize_budget_events

from kogwistar_llm_wiki.longrun_parser_worker import _summarize_budget_events


def test_summarize_budget_events_tracks_tokens_time_and_cost() -> None:
    settings = WorkflowProviderSettings(
        parser=ProviderEndpointConfig(provider="openai", model="gpt4o", base_url="https://example.openai.azure.com/"),
    )
    events = [
        BudgetEvent(run_id="run-1", source="generic-usage", kind="token", amount=12, unit="input_tokens"),
        BudgetEvent(run_id="run-1", source="generic-usage", kind="token", amount=8, unit="output_tokens"),
        BudgetEvent(run_id="run-1", source="generic-usage", kind="cost", amount=0.42, unit="total_cost"),
        BudgetEvent(run_id="run-1", source="runtime", kind="debit", amount=3, unit="ms"),
    ]

    summary = _summarize_budget_events(events, provider_settings=settings)

    assert summary["provider"] == "openai"
    assert summary["model"] == "gpt4o"
    assert summary["input_tokens"] == 12
    assert summary["output_tokens"] == 8
    assert summary["total_tokens"] == 20
    assert summary["total_cost"] == 0.42
    assert summary["time_ms"] == 3


def test_longrun_usage_summary_merges_provider_metadata_with_runtime_summary() -> None:
    settings = WorkflowProviderSettings(
        parser=ProviderEndpointConfig(provider="azure", model="gpt-5-nano", base_url="https://example.openai.azure.com/"),
    )
    events = [
        BudgetEvent(run_id="run-1", source="generic-usage", kind="token", amount=4, unit="input_tokens"),
        BudgetEvent(run_id="run-1", source="generic-usage", kind="cost", amount=0.11, unit="total_cost"),
    ]

    summary = _summarize_budget_events(events, provider_settings=settings)

    assert summary["provider"] == "azure"
    assert summary["model"] == "gpt-5-nano"
    assert summary["input_tokens"] == summarize_budget_events(events)["input_tokens"]
    assert summary["total_cost"] == summarize_budget_events(events)["total_cost"]
