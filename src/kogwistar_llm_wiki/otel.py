"""Optional OpenTelemetry integration for the llm-wiki application.

The application remains usable without the OTel packages.  This module is an
observability adapter only: workflow history, usage events, and graph events
remain the authoritative stores.
"""

from __future__ import annotations

from contextlib import contextmanager
import os
from typing import Iterator, Mapping

try:
    from opentelemetry import trace
    from opentelemetry.trace import Span, Tracer
except ModuleNotFoundError:  # pragma: no cover - exercised by minimal installs
    trace = None  # type: ignore[assignment]
    Span = object  # type: ignore[assignment,misc]
    Tracer = object  # type: ignore[assignment,misc]


class LlmWikiTelemetry:
    """Small optional tracer facade with safe no-op behavior."""

    def __init__(self, *, service_name: str = "kogwistar-llm-wiki") -> None:
        self.enabled = _env_bool("LLM_WIKI_OTEL_ENABLED", False) and trace is not None
        self.service_name = service_name
        self._tracer: Tracer | None = None
        if self.enabled:
            self._tracer = trace.get_tracer(service_name)  # type: ignore[union-attr]

    @classmethod
    def from_environment(cls) -> "LlmWikiTelemetry":
        return cls(service_name=os.getenv("LLM_WIKI_OTEL_SERVICE_NAME", "kogwistar-llm-wiki"))

    @contextmanager
    def span(self, name: str, attributes: Mapping[str, object] | None = None) -> Iterator[Span | None]:
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


def _event_attributes(event: Mapping[str, object]) -> dict[str, str | int | float | bool]:
    result: dict[str, str | int | float | bool] = {}
    for key, value in event.items():
        if isinstance(value, (str, int, float, bool)):
            result[f"llm_wiki.{key}"] = value
    return result


__all__ = ["LlmWikiTelemetry"]
