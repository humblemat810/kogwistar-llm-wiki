from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
from langchain_core.callbacks import BaseCallbackHandler
from langchain_openai import AzureChatOpenAI
from kogwistar.runtime.budget import StateBackedBudgetLedger
from kogwistar.runtime.budget_adapters import summarize_budget_events
from kogwistar_llm_wiki.llm_usage import (
    ProviderUsageCallback,
    TokenPricing,
    extract_provider_usage,
    resolve_token_pricing,
)


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


def test_extract_provider_usage_reads_cached_input_details() -> None:
    response = SimpleNamespace(
        response_metadata={
            "token_usage": {
                "prompt_tokens": 1000,
                "completion_tokens": 100,
                "prompt_tokens_details": {"cached_tokens": 400},
            }
        },
        generations=[],
    )

    assert extract_provider_usage(response)["cached_input_tokens"] == 400.0


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


def test_provider_usage_callback_estimates_cost_when_provider_omits_price() -> None:
    ledger = StateBackedBudgetLedger({"token_budget": 10_000, "budget_scope": "run"})
    callback = ProviderUsageCallback(
        ledger=ledger,
        run_id="parser:doc-001",
        source_document_id="doc-001",
        provider="azure",
        model="gpt-5-mini",
        pricing=TokenPricing(input_per_1k=0.001, output_per_1k=0.002, source="test-rate-card"),
    )

    callback.on_llm_end(
        SimpleNamespace(
            response_metadata={
                "token_usage": {
                    "prompt_tokens": 1200,
                    "completion_tokens": 300,
                    "total_tokens": 1500,
                }
            },
            generations=[],
        ),
        run_id="provider-call-1",
    )

    summary = summarize_budget_events(ledger.events)
    assert summary["total_cost"] == 0.0018
    cost_events = [event for event in ledger.events if event.kind == "cost"]
    assert len(cost_events) == 1
    assert cost_events[0].meta["cost_status"] == "estimated_from_tokens"
    assert cost_events[0].meta["cost_provenance"] == "estimated_from_tokens"
    assert cost_events[0].meta["cost_source"] == "test-rate-card"


def test_resolve_token_pricing_uses_longrun_parser_rate_card(monkeypatch) -> None:
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PARSER_INPUT_COST_PER_1K_TOKENS", "0.003")
    monkeypatch.setenv("KOGWISTAR_LONGRUN_PARSER_OUTPUT_COST_PER_1K_TOKENS", "0.009")

    pricing = resolve_token_pricing(provider="azure", model="gpt-5-mini")

    assert pricing.input_per_1k == 0.003
    assert pricing.output_per_1k == 0.009
    assert pricing.source == "KOGWISTAR_LONGRUN_PARSER_INPUT_COST_PER_1K_TOKENS"


def test_resolve_token_pricing_uses_known_reference_card() -> None:
    pricing = resolve_token_pricing(provider="openai", model="gpt-5-mini")

    assert pricing.input_per_1k == 0.00025
    assert pricing.cached_input_per_1k == 0.000025
    assert pricing.output_per_1k == 0.002
    assert pricing.source.startswith("reference:openai-public-standard")


def test_provider_usage_callback_estimates_cached_input_at_cached_rate() -> None:
    ledger = StateBackedBudgetLedger({"token_budget": 10_000, "budget_scope": "run"})
    callback = ProviderUsageCallback(
        ledger=ledger,
        run_id="parser:doc-001",
        source_document_id="doc-001",
        provider="openai",
        model="gpt-5-mini",
        pricing=TokenPricing(
            input_per_1k=0.001,
            cached_input_per_1k=0.0001,
            output_per_1k=0.002,
            source="test-rate-card",
        ),
    )

    callback.on_llm_end(
        SimpleNamespace(
            response_metadata={
                "token_usage": {
                    "prompt_tokens": 1000,
                    "completion_tokens": 100,
                    "prompt_tokens_details": {"cached_tokens": 400},
                }
            },
            generations=[],
        ),
        run_id="provider-call-1",
    )

    summary = summarize_budget_events(ledger.events)
    assert summary["total_cost"] == 0.00084
    assert summary["cached_input_tokens"] == 400


def test_provider_usage_callback_labels_missing_token_cost_without_fabricating_estimate() -> None:
    ledger = StateBackedBudgetLedger({"token_budget": 10_000, "budget_scope": "run"})
    callback = ProviderUsageCallback(
        ledger=ledger,
        run_id="parser:doc-001",
        source_document_id="doc-001",
        provider="azure",
        model="unknown-model",
        pricing=TokenPricing(input_per_1k=0.001, output_per_1k=0.002, source="test-rate-card"),
    )

    callback.on_llm_end(SimpleNamespace(response_metadata={}, generations=[]), run_id="provider-call-1")

    cost_events = [event for event in ledger.events if event.kind == "cost"]
    assert len(cost_events) == 1
    assert cost_events[0].amount == 0
    assert cost_events[0].meta["cost_status"] == "unavailable_missing_tokens"


def test_provider_usage_callback_is_accepted_by_azure_chat_model() -> None:
    ledger = StateBackedBudgetLedger({"token_budget": 10_000, "budget_scope": "run"})
    callback = ProviderUsageCallback(
        ledger=ledger,
        run_id="parser:doc-001",
        source_document_id="doc-001",
        provider="azure",
        model="gpt-5-mini",
    )

    transport = httpx.MockTransport(lambda request: httpx.Response(200))
    client = httpx.Client(transport=transport)
    async_client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    try:
        model = AzureChatOpenAI(
            azure_deployment="gpt-5-mini",
            azure_endpoint="https://example.openai.azure.com",
            api_version="2024-12-01-preview",
            api_key="test-key",
            callbacks=[callback],
            http_client=client,
            http_async_client=async_client,
        )
        assert model.callbacks == [callback]
    finally:
        client.close()
        asyncio.run(async_client.aclose())
