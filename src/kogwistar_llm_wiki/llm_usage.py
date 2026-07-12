from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Callable, Sequence

from langchain_core.callbacks import BaseCallbackHandler
from kogwistar.runtime.budget import BudgetAttribution, BudgetEvent, StateBackedBudgetLedger
from kogwistar.runtime.budget_adapters import adapt_budget_events
from kogwistar.runtime.pricing import TokenPricing, estimate_token_cost_usd


_USAGE_KEYS = ("usage_metadata", "token_usage", "usage")


_REFERENCE_TOKEN_PRICING: dict[tuple[str, str], TokenPricing] = {
    # Public OpenAI standard rates, expressed per 1K tokens. Azure deployments
    # may differ by region/SKU; explicit environment rates always take priority.
    ("openai", "gpt-5-mini"): TokenPricing(
        input_per_1k=0.00025,
        cached_input_per_1k=0.000025,
        output_per_1k=0.002,
        source="reference:openai-public-standard:gpt-5-mini",
    ),
    ("azure", "gpt-5-mini"): TokenPricing(
        input_per_1k=0.00025,
        cached_input_per_1k=0.000025,
        output_per_1k=0.002,
        source="reference:openai-public-standard:gpt-5-mini:azure-estimate",
    ),
    ("openai", "gpt-5-nano"): TokenPricing(
        input_per_1k=0.00005,
        cached_input_per_1k=0.000005,
        output_per_1k=0.0004,
        source="reference:openai-public-standard:gpt-5-nano",
    ),
    ("azure", "gpt-5-nano"): TokenPricing(
        input_per_1k=0.00005,
        cached_input_per_1k=0.000005,
        output_per_1k=0.0004,
        source="reference:openai-public-standard:gpt-5-nano:azure-estimate",
    ),
}


def resolve_token_pricing(
    *,
    provider: str,
    model: str,
    prefixes: Sequence[str] = (
        "KOGWISTAR_LONGRUN_PARSER",
        "KOGWISTAR_PARSER",
        "KG_DOC_PARSER",
        "KOGWISTAR_LLM",
    ),
) -> TokenPricing:
    """Resolve overrides, then a labelled public reference rate card."""

    def read_rate(suffix: str) -> tuple[float | None, str | None]:
        for prefix in prefixes:
            name = f"{prefix}_{suffix}"
            raw = os.getenv(name)
            if raw in {None, ""}:
                continue
            try:
                value = float(raw)
            except ValueError as exc:
                raise ValueError(f"{name} must be a non-negative number") from exc
            if value < 0:
                raise ValueError(f"{name} must be a non-negative number")
            return value, name
        return None, None

    input_rate, input_source = read_rate("INPUT_COST_PER_1K_TOKENS")
    output_rate, output_source = read_rate("OUTPUT_COST_PER_1K_TOKENS")
    cached_rate, cached_source = read_rate("CACHED_INPUT_COST_PER_1K_TOKENS")
    reference = _REFERENCE_TOKEN_PRICING.get((provider.lower(), model.lower()))
    source = input_source or output_source or cached_source
    if source is None and reference is not None:
        return TokenPricing(
            input_per_1k=reference.input_per_1k,
            output_per_1k=reference.output_per_1k,
            cached_input_per_1k=reference.cached_input_per_1k,
            source=reference.source,
        )
    if source is None:
        source = f"unavailable:{provider}/{model}"
    return TokenPricing(
        input_per_1k=input_rate,
        output_per_1k=output_rate,
        cached_input_per_1k=cached_rate,
        source=source,
    )


def _as_mapping(value: object) -> Mapping[str, object] | None:
    if isinstance(value, Mapping):
        return value
    return None


def _usage_candidates(response: object) -> list[Mapping[str, object]]:
    candidates: list[Mapping[str, object]] = []

    def add(value: object) -> None:
        mapping = _as_mapping(value)
        if mapping is None:
            return
        candidates.append(mapping)
        for key in _USAGE_KEYS:
            nested = _as_mapping(mapping.get(key))
            if nested is not None:
                candidates.append(nested)

    add(getattr(response, "llm_output", None))
    add(getattr(response, "response_metadata", None))
    for generation_group in getattr(response, "generations", ()) or ():
        for generation in generation_group or ():
            message = getattr(generation, "message", None)
            add(getattr(message, "usage_metadata", None))
            add(getattr(message, "response_metadata", None))
            add(getattr(generation, "generation_info", None))
    for candidate in tuple(candidates):
        for key in ("prompt_tokens_details", "input_token_details", "input_tokens_details"):
            nested = _as_mapping(candidate.get(key))
            if nested is not None:
                candidates.append(nested)
    return candidates


def extract_provider_usage(response: object) -> dict[str, float]:
    """Normalize common LangChain/OpenAI/Azure usage payloads.

    This function intentionally accepts an opaque provider response and returns
    only the JSON-shaped usage contract consumed by Kogwistar's generic budget
    adapter. It does not calculate prices that the provider did not report.
    """

    aliases: dict[str, tuple[str, ...]] = {
        "input_tokens": ("input_tokens", "prompt_tokens", "input_token_count"),
        "output_tokens": ("output_tokens", "completion_tokens", "output_token_count"),
        "total_tokens": ("total_tokens", "token_count"),
        "total_cost": ("total_cost", "cost", "cost_usd"),
        "cached_input_tokens": ("cached_input_tokens", "cached_tokens"),
    }
    usage: dict[str, float] = {}
    for candidate in _usage_candidates(response):
        for normalized, keys in aliases.items():
            if normalized in usage:
                continue
            for key in keys:
                value = candidate.get(key)
                if isinstance(value, (int, float)) and value >= 0:
                    usage[normalized] = float(value)
                    break
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")
    if "total_tokens" not in usage and input_tokens is not None and output_tokens is not None:
        usage["total_tokens"] = input_tokens + output_tokens
    return usage


class ProviderUsageCallback(BaseCallbackHandler):
    """Bridge provider-native LangChain usage into a runtime budget ledger."""

    def __init__(
        self,
        *,
        ledger: StateBackedBudgetLedger,
        run_id: str,
        source_document_id: str,
        provider: str,
        model: str,
        pricing: TokenPricing | None = None,
        event_sink: Callable[[BudgetEvent], None] | None = None,
    ) -> None:
        self.ledger = ledger
        self.run_id = run_id
        self.source_document_id = source_document_id
        self.provider = provider
        self.model = model
        self.pricing = pricing
        self.event_sink = event_sink
        self._started_at: dict[str, float] = {}
        self._recorded_runs: set[str] = set()

    def on_llm_start(self, *args: object, run_id: object, **kwargs: object) -> None:
        self._started_at[str(run_id)] = time.monotonic()

    def on_llm_end(self, response: object, *, run_id: object, **kwargs: object) -> None:
        provider_run_id = str(run_id)
        if provider_run_id in self._recorded_runs:
            return
        self._recorded_runs.add(provider_run_id)
        usage = extract_provider_usage(response)
        attribution = BudgetAttribution(
            source_document_id=self.source_document_id,
            operation_id=provider_run_id,
            operation_kind="llm_call",
            provider=self.provider,
            model=self.model,
        )
        provider_events = adapt_budget_events(
            {"usage": usage},
            run_id=self.run_id,
            scope="run",
            attribution=attribution,
        )
        has_provider_cost = any(
            (event.kind == "cost" or event.unit == "total_cost")
            and event.meta.get("cost_status") not in {"estimated", "estimated_partial"}
            for event in provider_events
        )
        estimated_cost = (
            estimate_token_cost_usd(usage, self.pricing)
            if self.pricing is not None and not has_provider_cost
            else None
        )
        if estimated_cost is not None:
            amount, cost_status = estimated_cost
            provider_events.append(
                BudgetEvent(
                    run_id=self.run_id,
                    source="llm-wiki-cost-estimator",
                    kind="cost",
                    amount=amount,
                    unit="total_cost",
                    scope="run",
                    ts_ms=int(time.time() * 1000),
                    meta={
                        "cost_status": "estimated_from_tokens",
                        "cost_provenance": "estimated_from_tokens",
                        "estimator_status": cost_status,
                        "cost_source": self.pricing.source,
                        "input_cost_per_1k": self.pricing.input_per_1k,
                        "cached_input_cost_per_1k": self.pricing.cached_input_per_1k,
                        "output_cost_per_1k": self.pricing.output_per_1k,
                        "provider_run_id": provider_run_id,
                    },
                    event_id=_stable_event_id(self.run_id, provider_run_id, "total_cost"),
                    attribution=attribution,
                )
            )
        for event in provider_events:
            is_estimate = event.source == "llm-wiki-cost-estimator"
            event = BudgetEvent(
                run_id=event.run_id,
                source=event.source,
                kind=event.kind,
                amount=event.amount,
                unit=event.unit,
                scope=event.scope,
                ts_ms=int(time.time() * 1000),
                meta={
                    **event.meta,
                    "provider_run_id": provider_run_id,
                    **(
                        {"cost_provenance": "provider_reported", "cost_status": "provider_reported"}
                        if event.kind == "cost" and not is_estimate
                        else {}
                    ),
                    **(
                        {"provider_error": str(kwargs["_provider_error"])}
                        if kwargs.get("_provider_error")
                        else {}
                    ),
                },
                event_id=_stable_event_id(self.run_id, provider_run_id, event.unit),
                attribution=event.attribution,
            )
            self.ledger.ingest(event)
            if self.event_sink is not None:
                self.event_sink(event)
        if not any(event.kind == "cost" or event.unit == "total_cost" for event in provider_events):
            unavailable = BudgetEvent(
                run_id=self.run_id,
                source="llm-wiki-cost-estimator",
                kind="cost",
                amount=0.0,
                unit="total_cost",
                scope="run",
                ts_ms=int(time.time() * 1000),
                meta={
                    "cost_status": "unavailable_missing_tokens",
                    "cost_provenance": "unavailable_missing_tokens",
                    "provider_run_id": provider_run_id,
                },
                event_id=_stable_event_id(self.run_id, provider_run_id, "total_cost_unavailable"),
                attribution=attribution,
            )
            self.ledger.ingest(unavailable)
            if self.event_sink is not None:
                self.event_sink(unavailable)

        started_at = self._started_at.pop(provider_run_id, None)
        if started_at is not None:
            elapsed_ms = max(0, int((time.monotonic() - started_at) * 1000))
            self.ledger.ingest(
                BudgetEvent(
                    run_id=self.run_id,
                    source="langchain-provider",
                    kind="time",
                    amount=float(elapsed_ms),
                    unit="ms",
                    scope="run",
                    ts_ms=int(time.time() * 1000),
                    meta={
                        "provider_run_id": provider_run_id,
                        **(
                            {"provider_error": str(kwargs["_provider_error"])}
                            if kwargs.get("_provider_error")
                            else {}
                        ),
                    },
                    event_id=_stable_event_id(self.run_id, provider_run_id, "ms"),
                    attribution=attribution,
                )
            )
            if self.event_sink is not None:
                self.event_sink(self.ledger.events[-1])

    def on_llm_error(self, error: object, *, run_id: object, **kwargs: object) -> None:
        """Count failed provider attempts even when no usage payload exists."""
        provider_run_id = str(run_id)
        if provider_run_id in self._recorded_runs:
            return
        self.on_llm_end(
            SimpleNamespace(llm_output={}, response_metadata={}, generations=[]),
            run_id=run_id,
            _provider_error=type(error).__name__,
        )
        attribution = BudgetAttribution(
            source_document_id=self.source_document_id,
            operation_id=provider_run_id,
            operation_kind="llm_call",
            provider=self.provider,
            model=self.model,
        )
        self.ledger.ingest(
            BudgetEvent(
                run_id=self.run_id,
                source="langchain-provider",
                kind="error",
                amount=1.0,
                unit="llm_call",
                scope="run",
                ts_ms=int(time.time() * 1000),
                meta={
                    "provider_run_id": provider_run_id,
                    "provider_error": type(error).__name__,
                },
                event_id=_stable_event_id(self.run_id, provider_run_id, "llm_call_error"),
                attribution=attribution,
            )
        )
        if self.event_sink is not None:
            self.event_sink(self.ledger.events[-1])


def _stable_event_id(run_id: str, provider_run_id: str, unit: str) -> str:
    digest = hashlib.sha256(f"{run_id}|{provider_run_id}|{unit}".encode()).hexdigest()[:24]
    return f"llm-usage-{digest}"
