from __future__ import annotations

from pathlib import Path
from collections.abc import Mapping
from typing import Protocol, cast

from kogwistar.json_types import JsonValue
from kogwistar.runtime.sinks import EventSinkLike, JsonlEventSink

from .diagnostics.debug_helpers import LiveTracePrinter
from .otel import LlmWikiTelemetry

JsonObject = dict[str, JsonValue]


class EventEnricher(Protocol):
    """Callback that adds structured fields to one trace event."""

    def __call__(self, event: JsonObject, /) -> JsonObject: ...


class LongRunJsonlTraceSink(JsonlEventSink):
    def __init__(
        self,
        *,
        jsonl_path: Path,
        downstream_sink: EventSinkLike | None = None,
        enrich_event: EventEnricher | None = None,
        live_trace: bool = False,
        telemetry: LlmWikiTelemetry | None = None,
    ) -> None:
        super().__init__(jsonl_path=jsonl_path, downstream_sink=downstream_sink)
        self.enrich_event = enrich_event
        self.live_trace_printer = LiveTracePrinter(prefix="longrun.runtime") if live_trace else None
        self.telemetry = telemetry or LlmWikiTelemetry.from_environment()

    def emit(self, event: dict[str, object]) -> None:
        event_json = cast(JsonObject, dict(event))
        enriched_event = (
            self.enrich_event(event_json) if self.enrich_event is not None else event_json
        )
        super().emit(cast(dict[str, object], enriched_event))
        self.telemetry.instrument_event(cast(Mapping[str, object], enriched_event))
        if self.live_trace_printer is not None:
            self.live_trace_printer.emit(enriched_event)


__all__ = ["EventEnricher", "LongRunJsonlTraceSink"]
