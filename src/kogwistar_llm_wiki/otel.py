"""Optional OpenTelemetry integration for the llm-wiki application.

The application remains usable without the OTel packages.  This module is an
observability adapter only: workflow history, usage events, and graph events
remain the authoritative stores.
"""

from __future__ import annotations

from contextlib import contextmanager
import os
import threading
from typing import Iterator, Mapping
from urllib.parse import urlsplit, urlunsplit

try:
    from opentelemetry import trace
    from opentelemetry.trace import Span, Tracer
except ModuleNotFoundError:  # pragma: no cover - exercised by minimal installs
    trace = None  # type: ignore[assignment]
    Span = object  # type: ignore[assignment,misc]
    Tracer = object  # type: ignore[assignment,misc]


_provider_lock = threading.Lock()
_provider_configured = False
_runtime_enabled: bool | None = None


class LlmWikiTelemetry:
    """Small optional tracer facade with safe no-op behavior."""

    def __init__(self, *, service_name: str = "kogwistar-llm-wiki") -> None:
        global _runtime_enabled
        self.packages_available = trace is not None
        configured = _env_bool("LLM_WIKI_OTEL_ENABLED", False)
        self.enabled = (
            (_runtime_enabled if _runtime_enabled is not None else configured)
            and self.packages_available
        )
        self.service_name = service_name
        self._tracer: Tracer | None = None
        if self.enabled:
            self._tracer = self._configure_tracer(service_name)

    @staticmethod
    def _configure_tracer(service_name: str) -> Tracer | None:
        if trace is None:
            return None
        _ensure_tracer_provider(service_name)
        return trace.get_tracer(service_name)  # type: ignore[union-attr]

    def set_enabled(self, enabled: bool) -> None:
        """Toggle emission for the current process without changing config."""
        global _runtime_enabled
        _runtime_enabled = bool(enabled)
        self.enabled = _runtime_enabled and self.packages_available
        if self.enabled and trace is not None:
            self._tracer = self._configure_tracer(self.service_name) or trace.get_tracer(self.service_name)
        else:
            self._tracer = None

    @classmethod
    def from_environment(cls) -> "LlmWikiTelemetry":
        return cls(service_name=os.getenv("LLM_WIKI_OTEL_SERVICE_NAME", "kogwistar-llm-wiki"))

    @contextmanager
    def span(self, name: str, attributes: Mapping[str, object] | None = None) -> Iterator[Span | None]:
        # Facades are constructed by several application components. Consult
        # the process-wide switch here so a settings toggle also affects
        # instances that were created before the toggle.
        if _runtime_enabled is False:
            yield None
            return
        if self._tracer is None and _runtime_enabled is True:
            self.enabled = self.packages_available
            if self.enabled:
                self._tracer = self._configure_tracer(self.service_name)
        if self._tracer is None:
            yield None
            return
        with self._tracer.start_as_current_span(name, attributes=dict(attributes or {})) as current:
            yield current

    def record_exception(self, span: Span | None, error: BaseException) -> None:
        if span is not None:
            span.record_exception(error)
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(error)))  # type: ignore[union-attr]

    def instrument_event(self, event: Mapping[str, object]) -> None:
        """Represent an existing app trace event as a short OTel span."""
        name = str(event.get("type") or event.get("message") or "llm_wiki.event")
        with self.span(f"llm_wiki.{name}", _event_attributes(event)) as current:
            if current is not None:
                current.add_event("llm_wiki.event", attributes=_event_attributes(event))


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _trace_exporter_endpoint() -> str | None:
    """Resolve OTLP HTTP endpoints without relying on SDK constructor magic."""
    signal_endpoint = os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
    if signal_endpoint:
        # Signal-specific endpoints are already expected to include /v1/traces.
        return signal_endpoint

    base_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    if not base_endpoint:
        return None
    parts = urlsplit(base_endpoint)
    path = parts.path.rstrip("/")
    if not path.endswith("/v1/traces"):
        path += "/v1/traces"
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def _ensure_tracer_provider(service_name: str) -> None:
    """Install the optional exporter once for the whole Python process."""
    global _provider_configured
    if trace is None or _provider_configured:
        return
    with _provider_lock:
        if _provider_configured:
            return
        try:
            from opentelemetry import trace as trace_api
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor

            provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
            endpoint = _trace_exporter_endpoint()
            exporter = OTLPSpanExporter(endpoint=endpoint) if endpoint else OTLPSpanExporter()
            provider.add_span_processor(BatchSpanProcessor(exporter))
            trace_api.set_tracer_provider(provider)
        except (ImportError, RuntimeError, ValueError):
            # The API-only optional installation remains a valid no-op mode.
            pass
        finally:
            # Do not repeatedly attempt global provider installation after an
            # SDK or dependency rejects the optional exporter setup.
            _provider_configured = True


def _event_attributes(event: Mapping[str, object]) -> dict[str, str | int | float | bool]:
    result: dict[str, str | int | float | bool] = {}
    for key, value in event.items():
        if isinstance(value, (str, int, float, bool)):
            result[f"llm_wiki.{key}"] = value
    return result


__all__ = ["LlmWikiTelemetry"]
