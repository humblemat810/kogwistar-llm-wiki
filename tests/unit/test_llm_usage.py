from __future__ import annotations

from types import SimpleNamespace

from langchain_core.callbacks import BaseCallbackHandler
from langchain_openai import AzureChatOpenAI
from kogwistar.runtime.budget import StateBackedBudgetLedger
from kogwistar.runtime.budget_adapters import summarize_budget_events
from kogwistar_llm_wiki.llm_usage import ProviderUsageCallback, extract_provider_usage


def test_extract_provider_usage_accepts_azure_openai_response_metadata() -> None:
    response = SimpleNamespace(
        llm_output={
            "token_usage": {
                "prompt_tokens": 120,
                "completion_tokens": 35,
                "total_tokens": 155,
            }
        },
        generations=[],
    )

    assert extract_provider_usage(response) == {
        "input_tokens": 120.0,
        "output_tokens": 35.0,
        "total_tokens": 155.0,
    }


def test_provider_usage_callback_emits_attributed_budget_events() -> None:
    ledger = StateBackedBudgetLedger({"token_budget": 10_000, "budget_scope": "run"})
    callback = ProviderUsageCallback(
        ledger=ledger,
        run_id="parser:doc-001",
        source_document_id="doc-001",
        provider="azure",
        model="gpt-5-mini",
    )
    response = SimpleNamespace(
        response_metadata={
            "token_usage": {
                "prompt_tokens": 120,
                "completion_tokens": 35,
                "total_tokens": 155,
                "total_cost": 0.0042,
            }
        },
        generations=[],
    )

    callback.on_llm_start(run_id="provider-call-1")
    callback.on_llm_end(response, run_id="provider-call-1")
    callback.on_llm_end(response, run_id="provider-call-1")
    callback.on_llm_error(RuntimeError("provider rejected request"), run_id="provider-call-2")

    summary = summarize_budget_events(ledger.events)
    assert summary["input_tokens"] == 120
    assert summary["output_tokens"] == 35
    assert summary["total_tokens"] == 155
    assert summary["total_cost"] == 0.0042
    usage_events = [event for event in ledger.events if event.source == "langchain-provider"]
    assert usage_events
    assert all(event.attribution is not None for event in usage_events)
    assert {event.attribution.source_document_id for event in usage_events} == {"doc-001"}
    assert len({event.event_id for event in usage_events}) == len(usage_events)
    assert any(event.meta.get("provider_error") == "RuntimeError" for event in usage_events)


def test_provider_usage_callback_is_a_langchain_callback_handler() -> None:
    ledger = StateBackedBudgetLedger({"token_budget": 10_000, "budget_scope": "run"})

    callback = ProviderUsageCallback(
        ledger=ledger,
        run_id="parser:doc-001",
        source_document_id="doc-001",
        provider="azure",
        model="gpt-5-mini",
    )

    assert isinstance(callback, BaseCallbackHandler)


def test_provider_usage_callback_is_accepted_by_azure_chat_model() -> None:
    ledger = StateBackedBudgetLedger({"token_budget": 10_000, "budget_scope": "run"})
    callback = ProviderUsageCallback(
        ledger=ledger,
        run_id="parser:doc-001",
        source_document_id="doc-001",
        provider="azure",
        model="gpt-5-mini",
    )

    model = AzureChatOpenAI(
        azure_deployment="gpt-5-mini",
        azure_endpoint="https://example.openai.azure.com",
        api_version="2024-12-01-preview",
        api_key="test-key",
        callbacks=[callback],
    )

    assert model.callbacks == [callback]
