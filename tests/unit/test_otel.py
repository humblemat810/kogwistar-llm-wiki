from __future__ import annotations

from kogwistar_llm_wiki.otel import LlmWikiTelemetry


def test_otel_facade_is_safe_noop_by_default(monkeypatch):
    monkeypatch.delenv("LLM_WIKI_OTEL_ENABLED", raising=False)
    telemetry = LlmWikiTelemetry.from_environment()
    assert telemetry.enabled is False
    with telemetry.span("test", {"count": 1}) as current:
        assert current is None
    telemetry.instrument_event({"stage": "test", "count": 1, "nested": {"ignored": True}})


def test_otel_event_adapter_is_safe_for_complex_payloads(monkeypatch):
    monkeypatch.delenv("LLM_WIKI_OTEL_ENABLED", raising=False)
    telemetry = LlmWikiTelemetry.from_environment()
    telemetry.instrument_event({"stage": "safe", "payload": ["large"], "secret": "not emitted by no-op"})
