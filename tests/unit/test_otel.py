from __future__ import annotations

import kogwistar_llm_wiki.otel as otel_module
from kogwistar_llm_wiki.otel import LlmWikiTelemetry, _trace_exporter_endpoint


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


def test_otel_base_endpoint_gets_trace_path(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", raising=False)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://grafana:4318/")
    assert _trace_exporter_endpoint() == "http://grafana:4318/v1/traces"


def test_otel_signal_endpoint_is_used_verbatim(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://grafana:4318")
    monkeypatch.setenv(
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "http://collector:4318/custom/v1/traces",
    )
    assert _trace_exporter_endpoint() == "http://collector:4318/custom/v1/traces"


def test_otel_endpoint_precedence_matches_exporter(monkeypatch):
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://grafana:4318")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://collector:4318/v1/traces")
    assert _trace_exporter_endpoint() == "http://collector:4318/v1/traces"


def test_otel_runtime_toggle_is_process_wide(monkeypatch):
    monkeypatch.setattr(otel_module, "_runtime_enabled", None)
    monkeypatch.setenv("LLM_WIKI_OTEL_ENABLED", "false")
    first = LlmWikiTelemetry()
    second = LlmWikiTelemetry()

    first.set_enabled(True)
    assert first.enabled is first.packages_available
    assert LlmWikiTelemetry().enabled is LlmWikiTelemetry().packages_available

    first.set_enabled(False)
    assert first.enabled is False
    assert second.enabled is False
    assert LlmWikiTelemetry().enabled is False


def test_otel_runtime_disable_suppresses_previously_configured_facade(monkeypatch):
    monkeypatch.setattr(otel_module, "_runtime_enabled", None)
    monkeypatch.setenv("LLM_WIKI_OTEL_ENABLED", "true")
    telemetry = LlmWikiTelemetry()

    telemetry.set_enabled(False)
    with telemetry.span("disabled-after-construction") as current:
        assert current is None

    telemetry.set_enabled(True)
    with telemetry.span("enabled-after-reenable") as current:
        assert current is not None or not telemetry.packages_available
